from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...models import State
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task
from ...services.approve_service import ApproveService, ApproveOptions
from ...codex import OperationError


def handle_approve(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Any = None,
) -> int:
    if args.task_id:
        run, task = resolve_task(state, args.task_id)
        task_id_for_print = args.task_id
    else:
        candidates = get_candidates(state, "approve")
        run, task = select_task_interactively(candidates, "approve")
        task_id_for_print = f"{run.id}:{task.id}"

    service = ApproveService(state, client, cwd)
    options = ApproveOptions(run=run, task=task, task_id_for_print=task_id_for_print)

    print(f"Approving plan for task {task_id_for_print}...")
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Approval failed")

    if result.message:
        print(result.message)

    return 0
