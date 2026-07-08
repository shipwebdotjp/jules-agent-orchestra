from __future__ import annotations

import datetime
import sys
from pathlib import Path

from ..client import JulesClient
from ..github import GitHubClient
from ..models import PR_SYNC_STATUSES, State
from ..persistence import save_state
from .state_utils import (
    get_run_sync_status,
    perform_task_sync_logic,
)
from .options import SyncOptions
from .results import OperationResult

class SyncService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        github_client: GitHubClient | None,
        cwd: Path,
    ):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd

    def execute(self, options: SyncOptions) -> OperationResult:
        updated_count = 0
        output = options.output_func

        if not options.skip_pr_sync and self.github_client is None and any(
            task.status in PR_SYNC_STATUSES
            for run in self.state.runs
            for task in run.tasks
        ):
            print(
                "Warning: GITHUB_TOKEN is not set; skipping PR status checks.",
                file=sys.stderr,
            )

        for run in self.state.runs:
            run_initial_status = run.status
            task_status_changes: list[tuple[str, str, str]] = []

            has_pr_sync_tasks = any(
                task.status in PR_SYNC_STATUSES for task in run.tasks
            )
            should_sync_run = run.status in ("running", "planned", "failed")
            if self.github_client and has_pr_sync_tasks and run.status == "completed":
                should_sync_run = True

            if should_sync_run:
                reopened_from_completed = (
                    self.github_client is not None
                    and run_initial_status == "completed"
                    and has_pr_sync_tasks
                )

                for task in run.tasks:
                    task_initial_status = task.status

                    task_updated = perform_task_sync_logic(
                        self.client,
                        self.github_client,
                        self.state.project.repo,
                        task,
                        skip_pr_sync=options.skip_pr_sync
                    )

                    if task_updated and task.status != task_initial_status:
                        updated_count += 1
                        task_status_changes.append(
                            (task.id, task_initial_status, task.status)
                        )

                new_run_status = get_run_sync_status(
                    run,
                    previous_status=run_initial_status,
                    reopened_from_completed=reopened_from_completed,
                )
                if new_run_status != run.status:
                    run.status = new_run_status
                    run.updated_at = (
                        datetime.datetime.now(datetime.timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )

            if not options.json_output:
                if run.status != run_initial_status or task_status_changes:
                    if run.status != run_initial_status:
                        output(f"Run {run.id}: {run_initial_status} -> {run.status}")
                    else:
                        output(f"Run {run.id}: (no change)")

                    for task_id, old_s, new_s in task_status_changes:
                        output(f"  Task {task_id}: {old_s} -> {new_s}")

        save_state(self.cwd, self.state)
        if not options.json_output and updated_count > 0:
            output(f"Synced {updated_count} tasks.")

        return OperationResult(exit_code=0)
