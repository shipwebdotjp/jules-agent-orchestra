from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import PR_SYNC_STATUSES, State
from ...persistence import save_state
from ..state import (
    get_run_sync_status,
    sync_pr_created_task,
    sync_task,
)


def handle_sync(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    skip_pr_sync: bool = False,
) -> int:
    updated_count = 0
    if not skip_pr_sync and github_client is None and any(
        task.status in PR_SYNC_STATUSES
        for run in state.runs
        for task in run.tasks
    ):
        print(
            "Warning: GITHUB_TOKEN is not set; skipping PR status checks.",
            file=sys.stderr,
        )

    for run in state.runs:
        run_initial_status = run.status
        task_status_changes: list[tuple[str, str, str]] = []

        has_pr_sync_tasks = any(
            task.status in PR_SYNC_STATUSES for task in run.tasks
        )
        should_sync_run = run.status in ("running", "planned", "failed")
        if github_client and has_pr_sync_tasks and run.status == "completed":
            should_sync_run = True

        if should_sync_run:
            reopened_from_completed = (
                github_client is not None
                and run_initial_status == "completed"
                and has_pr_sync_tasks
            )
            run_updated = reopened_from_completed

            for task in run.tasks:
                task_initial_status = task.status
                task_updated = False
                if (
                    task.status
                    not in (
                        "completed",
                        "merged",
                        "failed",
                        "cancelled",
                        "pr_closed",
                    )
                    and task.status not in PR_SYNC_STATUSES
                ):
                    if sync_task(client, task):
                        task_updated = True

                if (
                    task.status in PR_SYNC_STATUSES
                    and not skip_pr_sync
                    and github_client
                ):
                    if sync_pr_created_task(
                        github_client,
                        state.project.repo,
                        task,
                    ):
                        task_updated = True

                if task_updated and task.status != task_initial_status:
                    updated_count += 1
                    run_updated = True
                    task_status_changes.append(
                        (task.id, task_initial_status, task.status)
                    )

            if run_updated:
                run.status = get_run_sync_status(
                    run,
                    previous_status=run_initial_status,
                    reopened_from_completed=reopened_from_completed,
                )
                run.updated_at = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )

        if not getattr(args, "json", False):
            if run.status != run_initial_status or task_status_changes:
                if run.status != run_initial_status:
                    print(f"Run {run.id}: {run_initial_status} -> {run.status}")
                else:
                    print(f"Run {run.id}: (no change)")

                for task_id, old_s, new_s in task_status_changes:
                    print(f"  Task {task_id}: {old_s} -> {new_s}")

    save_state(cwd, state)
    if not getattr(args, "json", False) and updated_count > 0:
        print(f"Synced {updated_count} tasks.")
    return 0
