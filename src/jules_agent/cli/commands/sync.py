from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...pipeline import save_state
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
        task.status == "pr_created"
        for run in state.runs
        for task in run.tasks
    ):
        print(
            "Warning: GITHUB_TOKEN is not set; skipping PR status checks.",
            file=sys.stderr,
        )

    for run in state.runs:
        has_pr_created_tasks = any(
            task.status == "pr_created" for task in run.tasks
        )
        should_sync_run = run.status in ("running", "planned", "failed")
        if github_client and has_pr_created_tasks and run.status == "completed":
            should_sync_run = True

        print(f"DEBUG: Processing run {run.id} (status: {run.status}), should_sync: {should_sync_run}")
        if should_sync_run:
            previous_status = run.status
            reopened_from_completed = (
                github_client is not None
                and previous_status == "completed"
                and has_pr_created_tasks
            )
            run_updated = reopened_from_completed

            for task in run.tasks:
                if task.status == "pr_created":
                    if not skip_pr_sync:
                        print(f"DEBUG: Syncing pr_created task {task.id}. GitHub client: {github_client is not None}")
                        if github_client and sync_pr_created_task(
                            github_client,
                            state.project.repo,
                            task,
                        ):
                            updated_count += 1
                            run_updated = True
                    continue

                if task.status not in (
                    "completed",
                    "merged",
                    "failed",
                    "cancelled",
                    "pr_closed",
                ):
                    if sync_task(client, task):
                        updated_count += 1
                        run_updated = True

            if run_updated:
                run.status = get_run_sync_status(
                    run,
                    previous_status=previous_status,
                    reopened_from_completed=reopened_from_completed,
                )
                run.updated_at = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )

    save_state(cwd, state)
    print(f"Synced {updated_count} tasks.")
    return 0
