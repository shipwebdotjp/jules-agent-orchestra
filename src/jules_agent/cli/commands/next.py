from __future__ import annotations

import argparse
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...models import State
from ...codex import PipelineError, OperationError
from ..state import get_candidates
from ..io import select_task_interactively
from ...services.next_service import NextService, NextOptions


def handle_next(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> int:
    run_id = getattr(args, "run_id", None)
    if run_id:
        target_run = None
        for run in state.runs:
            if run.id == run_id:
                target_run = run
                break
        if not target_run:
            raise PipelineError(f"Run {run_id} not found.")

        if target_run.strategy != "sequential_subtasks" or target_run.status != "running":
            raise PipelineError(f"Run {run_id} is not an active sequential run.")

        next_task = None
        for task in target_run.tasks:
            if task.status == "planned":
                next_task = task
                break
        if not next_task:
            print(f"No more tasks to dispatch in run {run_id}.")
            return 0
    else:
        candidates = get_candidates(state, "next")
        if not candidates:
            print("No active sequential runs with planned tasks found.")
            return 0
        target_run, next_task = select_task_interactively(candidates, "next")

    service = NextService(state, client, cwd, config)
    options = NextOptions(run=target_run, task=next_task, args=args, output_func=print)

    result = service.execute(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Next dispatch failed")

    return 0
