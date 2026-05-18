from __future__ import annotations
import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State, Task, TaskStatus
from ...pipeline import save_state, suggest_reply
from ..state import sync_task
from .sync import handle_sync


def handle_advance(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> int:
    # 1. Sync state at command start (skipping PR sync as advance no longer handles pr_created tasks)
    print("Syncing state...")
    sync_result = handle_sync(args, state, client, github_client, cwd, skip_pr_sync=True)
    if sync_result != 0:
        return sync_result

    # 3. Pick the highest-priority task across all runs by latest update time
    ADVANCEABLE_STATUSES: set[TaskStatus] = {
        "awaiting_plan_approval",
        "awaiting_user_feedback",
    }

    target_task: Task | None = None
    pr_created_tasks_exist = False
    for run in state.runs:
        for task in run.tasks:
            if task.status in ADVANCEABLE_STATUSES:
                if target_task is None or task.updated_at > target_task.updated_at:
                    target_task = task
            elif task.status == "pr_created":
                pr_created_tasks_exist = True

    if not target_task:
        if pr_created_tasks_exist:
            print("No tasks found that require advancement (found tasks in 'pr_created' status; use 'merge' or 'sync' to handle them).")
        else:
            print("No tasks found that require advancement.")
        return 0

    print(f"\nAdvancing task: {target_task.id} - {target_task.title} (Status: {target_task.status})")

    # 4. Guided step (at most one step)
    use_auto = False
    if args.auto:
        use_auto = True
    elif target_task.status == "awaiting_plan_approval" and args.auto_plan_approval:
        use_auto = True
    elif target_task.status == "awaiting_user_feedback" and args.auto_feedback:
        use_auto = True

    step_completed = False
    if use_auto:
        success = _handle_auto(target_task, client, cwd, config)
        if success:
            # Action taken automatically
            if sync_task(client, target_task):
                step_completed = True
            else:
                print(f"Failed to sync task {target_task.id} after auto action.")
        else:
            # Codex didn't recommend action or it failed, try interactive fallback
            print(f"Auto-advance for {target_task.status} did not perform an action. Falling back to interactive mode.")
            if _handle_interactive(
                target_task, client, cwd, config
            ):
                if sync_task(client, target_task):
                    step_completed = True
                else:
                    print(f"Failed to sync task {target_task.id} after interactive action.")
    else:
        if _handle_interactive(
            target_task, client, cwd, config
        ):
            if sync_task(client, target_task):
                step_completed = True
            else:
                print(f"Failed to sync task {target_task.id} after action.")

    if step_completed:
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
    client: JulesClient,
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

    return False

def _handle_interactive(
    task: Task,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> bool:
    if task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
        return _handle_interactive_feedback(task, client, cwd, config)
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

