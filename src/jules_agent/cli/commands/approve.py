from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...models import State
from ...persistence import save_state
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task


from typing import Any


def handle_approve(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    parser: argparse.ArgumentParser,
    config: Any = None,
) -> int:
    if args.task_id:
        _run, task = resolve_task(state, args.task_id)
        task_id_for_print = args.task_id
    else:
        candidates = get_candidates(state, "approve")
        _run, task = select_task_interactively(candidates, "approve")
        task_id_for_print = f"{_run.id}:{task.id}"

    if not task.jules:
        parser.exit(
            1, f"Error: Task {task_id_for_print} has not been dispatched yet.\n"
        )

    print(f"Approving plan for task {task_id_for_print}...")
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
