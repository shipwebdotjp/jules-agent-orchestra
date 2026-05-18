from __future__ import annotations

import argparse
import datetime
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State, Task, TaskStatus
from ...pipeline import save_state, suggest_reply, PipelineError
from ..state import extract_pull_request_number, sync_task
from .sync import handle_sync

def handle_advance(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> int:
    # 1. Load advance_mode from config with precedence flag > config > default
    advance_mode = getattr(args, "advance_mode", None) or config.advance_mode or "interactive"

    # 2. Sync state at command start
    print("Syncing state...")
    sync_result = handle_sync(args, state, client, github_client, cwd)
    if sync_result != 0:
        return sync_result

    # 3. Pick the highest-priority task across all runs by latest update time
    ADVANCEABLE_STATUSES: set[TaskStatus] = {
        "awaiting_plan_approval",
        "awaiting_user_feedback",
        "pr_created",
    }

    target_task: Task | None = None
    for run in state.runs:
        for task in run.tasks:
            if task.status in ADVANCEABLE_STATUSES:
                if target_task is None or task.updated_at > target_task.updated_at:
                    target_task = task

    if not target_task:
        print("No tasks found that require advancement.")
        return 0

    print(f"\nAdvancing task: {target_task.id} - {target_task.title} (Status: {target_task.status})")

    # 4. Guided loop
    while target_task.status in ADVANCEABLE_STATUSES:
        if advance_mode == "auto":
            # Guard to stop repeated external writes by detecting no-status-change
            prev_status = target_task.status
            success = _handle_auto(target_task, state, client, github_client, cwd, config)
            if success is None: # User skipped in PR check
                break

            # Sync and check for status change
            if not sync_task(client, target_task):
                print("Failed to sync task after auto run.")
                advance_mode = "interactive"
                continue

            if not success or target_task.status == prev_status:
                print("Auto-advance did not result in a status change. Falling back to interactive mode.")
                advance_mode = "interactive"
                continue # Re-evaluate in interactive mode
        else:
            if not _handle_interactive(target_task, state, client, github_client, cwd, config):
                # User skipped or some other reason to stop the loop
                break

            # After an action, sync and see if we should continue the loop for THIS task
            if not sync_task(client, target_task):
                print(f"Failed to sync task {target_task.id} after action.")
                break

        # Update updated_at and save state
        target_task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(cwd, state)

    return 0

def _handle_auto(
    task: Task,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> bool | None:
    if task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
        is_plan = task.status == "awaiting_plan_approval"
        print(f"Fetching suggestion from Codex for {task.status}...")
        try:
            activities = list(client.list_activities(task.jules.session_name))
            result = suggest_reply(
                task.prompt or task.title,
                activities,
                [], # No feedback history in auto mode
                cwd=cwd,
                is_awaiting_plan_approval=is_plan,
                codex_bin=config.codex_bin,
            )

            if is_plan:
                if result.get("approval_recommended"):
                    print("Auto-approving plan as recommended by Codex...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                    return True
                else:
                    print("Codex does not recommend auto-approval.")
                    return False
            else:
                suggestion = result["suggestion"]
                print(f"Sending auto-reply:\n{suggestion}")
                client.send_message(task.jules.session_name, suggestion)
                return True
        except Exception as e:
            print(f"Error during auto-advance: {e}")
            return False

    elif task.status == "pr_created":
        if not github_client:
            print("GITHUB_TOKEN is not set. Cannot auto-merge PR.")
            return False

        if not task.pull_request or not task.pull_request.url:
            print("Task has no pull request URL.")
            return False

        pull_number = extract_pull_request_number(task.pull_request.url)
        if pull_number is None:
            print(f"Could not extract pull request number from {task.pull_request.url}")
            return False

        repo = state.project.repo
        if not repo:
            print("Error: Repository not set in project state.")
            return False

        print(f"Checking mergeability for PR #{pull_number} in {repo}...")
        try:
            pr_details = github_client.get_pull_request(repo, pull_number)
            if not pr_details.get("mergeable"):
                print(f"PR #{pull_number} is not mergeable at this time.")
                return False
        except Exception as e:
            print(f"Failed to fetch PR details: {e}")
            return False

        merge_method = config.merge_method or "merge"
        print(f"Auto-merging PR #{pull_number} using {merge_method} strategy...")
        try:
            github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
            task.status = "merged"
            print("Successfully merged.")
            return True
        except Exception as e:
            print(f"Failed to merge PR: {e}")
            return False

    return False

def _handle_interactive(
    task: Task,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> bool:
    if task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
        return _handle_interactive_feedback(task, client, cwd, config)
    elif task.status == "pr_created":
        res = _handle_interactive_pr(task, state, client, github_client, config)
        return res is True
    return False

def _handle_interactive_feedback(
    task: Task,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> bool:
    if not task.jules:
        print("Task has no Jules session info.")
        return False

    is_awaiting_plan_approval = task.status == "awaiting_plan_approval"
    feedback_history: list[str] = []

    while True:
        print("\nFetching suggestion from Codex...")
        try:
            activities = list(client.list_activities(task.jules.session_name))
            result = suggest_reply(
                task.prompt or task.title,
                activities,
                feedback_history,
                cwd=cwd,
                is_awaiting_plan_approval=is_awaiting_plan_approval,
                codex_bin=config.codex_bin,
            )
        except Exception as e:
            print(f"Error fetching suggestion: {e}")
            return False

        suggestion = result["suggestion"]
        explanation = result["explanation"]
        approval_recommended = result.get("approval_recommended", False)

        print("-" * 40)
        print(f"Explanation: {explanation}")
        if is_awaiting_plan_approval:
            rec_str = "YES" if approval_recommended else "NO"
            print(f"Approval recommended: {rec_str}")
        print("-" * 40)
        print(f"Suggested message:\n{suggestion}")
        print("-" * 40)

        while True:
            if is_awaiting_plan_approval:
                if approval_recommended:
                    prompt_msg = "\nApprove plan as recommended? (y), revise suggestion (f), write manual message (m), or skip (s)? [y/f/m/s]: "
                else:
                    prompt_msg = "\nSend suggestion (y), revise suggestion (f), write manual message (m), or skip (s)? [y/f/m/s]: "
            else:
                prompt_msg = "\nSend suggestion (y), revise suggestion (f), write manual message (m), or skip (s)? [y/f/m/s]: "

            try:
                answer = input(prompt_msg).strip().lower()
            except EOFError:
                return False

            if answer in {"y", "yes"}:
                if is_awaiting_plan_approval and approval_recommended:
                    print("Approving plan...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                else:
                    print("Sending message to Jules...")
                    client.send_message(task.jules.session_name, suggestion)
                return True
            elif answer == "f":
                feedback = input("Feedback for revision: ").strip()
                if feedback:
                    feedback_history.append(feedback)
                    break
                print("Feedback cannot be empty.")
            elif answer == "m":
                print("Enter your message to Jules (Enter a blank line to finish):")
                lines = []
                while True:
                    line = input("> ")
                    if not line:
                        break
                    lines.append(line)
                message = "\n".join(lines).strip()
                if message:
                    client.send_message(task.jules.session_name, message)
                    return True
                print("Message cannot be empty.")
            elif answer == "s":
                print("Skipping task.")
                return False
            else:
                print("Please answer with y, f, m, or s.")

def _handle_interactive_pr(
    task: Task,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    config: Config,
) -> bool | None:
    if not github_client:
        print("GITHUB_TOKEN is not set. Cannot check/merge PR.")
        return False

    if not task.pull_request or not task.pull_request.url:
        print("Task has no pull request URL.")
        return False

    pull_number = extract_pull_request_number(task.pull_request.url)
    if pull_number is None:
        print(f"Could not extract pull request number from {task.pull_request.url}")
        return False

    repo = state.project.repo
    if not repo:
        print("Error: Repository not set in project state.")
        return False

    print(f"\nPull Request created: {task.pull_request.url}")

    while True:
        try:
            answer = input("Merge this pull request? (y/n/s) [y/n/s]: ").strip().lower()
        except EOFError:
            return None

        if answer in {"y", "yes"}:
            merge_method = config.merge_method or "merge"
            print(f"Merging PR #{pull_number} using {merge_method} strategy...")
            try:
                github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
                task.status = "merged"
                print("Successfully merged.")
                return True
            except Exception as e:
                print(f"Failed to merge PR: {e}")
                return False
        elif answer in {"n", "no", "s"}:
            print("Skipping merge.")
            return None
        else:
            print("Please answer with y, n, or s.")
