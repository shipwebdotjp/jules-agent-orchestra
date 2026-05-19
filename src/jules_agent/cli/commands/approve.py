from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...models import State
from ...persistence import save_state
from ..state import resolve_task


def handle_approve(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    parser: argparse.ArgumentParser,
) -> int:
    _run, task = resolve_task(state, args.task_id)
    if not task.jules:
        parser.exit(
            1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
        )

    print(f"Approving plan for task {args.task_id}...")
    client.approve_plan(task.jules.session_name)
    task.status = "plan_approved"
    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
