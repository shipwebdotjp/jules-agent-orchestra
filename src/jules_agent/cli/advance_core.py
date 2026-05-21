from __future__ import annotations

import argparse
import datetime
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import sys
from pathlib import Path

from ..client import JulesClient
from ..config import Config
from ..github import GitHubClient
from ..models import State, Task, TaskStatus, Run, JulesSessionInfo
from ..persistence import save_state
from ..pipeline import find_source_name
from ..git import get_git_branch
from .state import (
    get_candidates,
    sync_task,
    extract_pull_request_number,
    get_jules_state_mapping,
)
from ..codex import resolve_tool_for_phase


def dispatch_task(
    task: Task,
    run: Run,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
    args: argparse.Namespace,
    *,
    verbose: bool = True,
) -> None:
    source_name = find_source_name(client, state.project.repo)
    starting_branch = get_git_branch(cwd)
    automation_mode = getattr(args, "automation_mode", None) or getattr(config, "automation_mode", None) or "AUTO_CREATE_PR"

    if verbose:
        print(f"Dispatching next task: {task.id} - {task.title}")
    task.status = "dispatching"
    save_state(cwd, state)
    try:
        session = client.create_session(
            prompt=task.prompt or task.title,
            source_name=source_name,
            starting_branch=starting_branch,
            title=task.title,
            require_plan_approval=False,
            automation_mode=automation_mode,
        )
        task.jules = JulesSessionInfo(
            session_id=session["id"],
            session_name=session["name"],
            state=session.get("state", "QUEUED"),
            session_url=session.get("url"),
            create_time=session.get("createTime"),
            update_time=session.get("updateTime"),
        )
        task.status = get_jules_state_mapping(task.jules.state, False)
        if verbose:
            print(f"  Success: {task.jules.session_url}")
    except Exception as e:
        task.status = "failed"
        if verbose:
            print(f"  Failed: {e}", file=sys.stderr)

    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)


class AdvanceEngine:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        github_client: GitHubClient | None,
        cwd: Path,
        config: Config,
        args: argparse.Namespace,
        interactive: bool = True,
    ):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd
        self.config = config
        self.args = args
        self.interactive = interactive and sys.stdin.isatty()
        self.output_json = getattr(args, "json", False)

    def run(self) -> int:
        lock_path = self.cwd / ".jules-agent" / "advance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "a") as lock_file:
            if fcntl is not None:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (IOError, OSError):
                    if not self.output_json:
                        print("Advance lock already held. Skipping.")
                    return 0

            return self._execute()

    def _execute(self) -> int:
        selected = self._select_task()
        if not selected:
            if self.output_json:
                print(json.dumps({"status": "no_tasks", "action": None, "exit_code": 0}))
            else:
                print("No tasks found that require advancement.")
            return 0

        target_run, target_task = selected
        previous_status = target_task.status

        if target_task.status == "planned":
            dispatch_task(
                task=target_task,
                run=target_run,
                state=self.state,
                client=self.client,
                cwd=self.cwd,
                config=self.config,
                args=self.args,
                verbose=not self.output_json,
            )

            if self.output_json:
                print(json.dumps({
                    "status": target_task.status,
                    "action": "dispatched_planned",
                    "run_id": target_run.id,
                    "task_id": target_task.id,
                    "previous_status": previous_status,
                    "next_status": target_task.status,
                    "reason": None,
                    "exit_code": 0,
                }))
            return 0

        if not self.output_json:
            print(f"Advancing task: {target_task.id} - {target_task.title} (Status: {target_task.status})")

        # Determine auto flags with precedence: Config -> --auto -> explicit flag
        # Rule: --auto enables plan approval and feedback, not merge.
        # Rule: Explicit flag overrides everything.

        # 1. Start with Config
        auto_plan_approval = getattr(self.config, "auto_plan_approval", True)
        auto_feedback = getattr(self.config, "auto_feedback", False)
        auto_merge = getattr(self.config, "auto_merge", False)

        # 2. Apply --auto
        if getattr(self.args, "auto", False):
            auto_plan_approval = True
            auto_feedback = True

        # 3. Apply explicit flags (assume they are None if not set on CLI)
        if getattr(self.args, "auto_plan_approval", None) is not None:
            auto_plan_approval = self.args.auto_plan_approval
        if getattr(self.args, "auto_feedback", None) is not None:
            auto_feedback = self.args.auto_feedback
        if getattr(self.args, "auto_merge", None) is not None:
            auto_merge = self.args.auto_merge

        action_taken = False
        action_name = None
        exit_code = 0
        reason = None

        try:
            if target_task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
                from .commands.feedback import run_feedback_loop
                phase = (
                    "approve"
                    if target_task.status == "awaiting_plan_approval"
                    else "feedback"
                )
                tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase(
                    phase, self.config, self.args
                )

                outcome = run_feedback_loop(
                    target_task,
                    cwd=self.cwd,
                    client=self.client,
                    tool_name=tool_name,
                    tool_bin=tool_bin,
                    gemini_skip_trust=gemini_skip_trust,
                    auto_plan_approval=auto_plan_approval,
                    auto_feedback=auto_feedback,
                    allow_skip=True,
                    interactive=self.interactive,
                )
                if outcome == "completed":
                    action_taken = True
                    action_name = "feedback_provided"
                elif outcome == "skipped":
                    action_name = "skipped"
                    if not self.interactive:
                        target_task.status = "blocked"
                        action_taken = True
                        reason = "Human judgment required"
                elif outcome == "failed":
                    action_name = "feedback_failed"
                    exit_code = 2

            elif target_task.status in ("pr_created", "waiting_human_review"):
                if auto_merge:
                    merge_result = self._attempt_merge(target_task)
                    if merge_result is True:
                        action_taken = True
                        action_name = "merged"
                    elif merge_result is False:
                        action_name = "merge_failed"
                        if not self.interactive:
                            target_task.status = "blocked"
                            action_taken = True
                            reason = "Merge conditions not met"
                    else: # Transient error (should be handled by exception, but just in case)
                        action_name = "merge_error"
                        exit_code = 2
                else:
                    action_name = "manual_merge_required"
                    if not self.interactive:
                        target_task.status = "blocked"
                        action_taken = True
                        reason = "Auto-merge disabled"

        except Exception as e:
            action_name = "error"
            reason = str(e)
            exit_code = 2

        if action_taken:
            # Update updated_at to a small delta in the past to lower selection priority
            # (Selection logic is updated_at descending)
            target_task.updated_at = (
                (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z")
            )
            # Sync to get latest state from server, but keep local status if it was changed to blocked or merged
            local_status = target_task.status
            sync_task(self.client, target_task)
            if local_status in ("blocked", "merged"):
                target_task.status = local_status

            # Always save state after an action to persist advance_state updates (e.g. idempotency keys)
            save_state(self.cwd, self.state)

            # For sequential_subtasks, dispatch next planned task after merge
            if action_name == "merged" and target_run.strategy == "sequential_subtasks":
                self._dispatch_next_planned(target_run)

        if self.output_json:
            print(json.dumps({
                "status": target_task.status,
                "action": action_name,
                "run_id": target_run.id,
                "task_id": target_task.id,
                "previous_status": previous_status,
                "next_status": target_task.status,
                "reason": reason,
                "exit_code": exit_code
            }))

        return exit_code

    def _select_task(self) -> tuple[Run, Task] | None:
        ADVANCEABLE_STATUSES: set[TaskStatus] = {
            "awaiting_plan_approval",
            "awaiting_user_feedback",
            "pr_created",
            "waiting_human_review",
        }

        eligible_tasks: list[tuple[Run, Task, int]] = []
        counter = 0
        for run in self.state.runs:
            for task in run.tasks:
                if task.status in ADVANCEABLE_STATUSES:
                    eligible_tasks.append((run, task, counter))
                counter += 1

        if not eligible_tasks:
            planned_candidates = get_candidates(self.state, "next")
            if not planned_candidates:
                return None
            return planned_candidates[0]

        # Sort by updated_at (descending) then traversal order (ascending)
        # Using -index for ascending order when reverse=True
        eligible_tasks.sort(key=lambda x: (x[1].updated_at, -x[2]), reverse=True)

        return eligible_tasks[0][0], eligible_tasks[0][1]

    def _dispatch_next_planned(self, run: Run) -> None:
        if run.strategy != "sequential_subtasks" or run.status != "running":
            return

        next_task = None
        for task in run.tasks:
            if task.status == "planned":
                next_task = task
                break

        if not next_task:
            return

        dispatch_task(
            task=next_task,
            run=run,
            state=self.state,
            client=self.client,
            cwd=self.cwd,
            config=self.config,
            args=self.args,
            verbose=not self.output_json,
        )

    def _attempt_merge(self, task: Task) -> bool | None:
        if not self.github_client:
            if not self.output_json:
                print(f"Skipping merge for {task.id}: GITHUB_TOKEN not set.")
            return False

        if not task.pull_request or not task.pull_request.url:
            return False

        # Verify PR URL matches repository
        repo = self.state.project.repo
        if f"github.com/{repo}/" not in task.pull_request.url:
            if not self.output_json:
                print(f"Skipping merge for {task.id}: PR URL {task.pull_request.url} does not match repo {repo}.")
            return False

        pull_number = extract_pull_request_number(task.pull_request.url)
        if pull_number is None:
            return False

        try:
            pr = self.github_client.get_pull_request(repo, pull_number)
        except Exception:
            return False

        if pr.get("merged"):
            task.status = "merged"
            task.advance_state["last_advance_action"] = "merged"
            return True

        if pr.get("state") != "open" or pr.get("draft"):
            return False

        if not pr.get("mergeable"):
            return False

        merge_method = getattr(self.args, "merge_method", None) or self.config.merge_method or "merge"

        try:
            self.github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
            task.status = "merged"
            task.advance_state["last_advance_action"] = "merged"
            return True
        except Exception as e:
            if not self.output_json:
                print(f"Failed to merge PR #{pull_number}: {e}")
            # Raising here so _execute can catch it and set exit_code=2
            raise
