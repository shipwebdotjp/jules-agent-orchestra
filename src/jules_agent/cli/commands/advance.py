from __future__ import annotations
import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State, Task, TaskStatus
from ...pipeline import save_state
from ..state import sync_task
from .sync import handle_sync
from .feedback import run_feedback_loop


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

    # 4. Determine auto flags
    auto_plan_approval = args.auto or args.auto_plan_approval
    auto_feedback = args.auto or args.auto_feedback

    # 5. Run the feedback flow (shared implementation)
    outcome = run_feedback_loop(
        target_task,
        cwd=cwd,
        client=client,
        codex_bin=config.codex_bin,
        auto_plan_approval=auto_plan_approval,
        auto_feedback=auto_feedback,
        allow_skip=True,
    )

    if outcome == "completed":
        # Re-sync task to get final state and update updated_at
        if sync_task(client, target_task):
            target_task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(cwd, state)
        else:
            print(f"Failed to sync task {target_task.id} after action.")

    return 0
