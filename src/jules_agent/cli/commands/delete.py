from __future__ import annotations

import argparse
from pathlib import Path

from ...models import State
from ..io import select_task_interactively, select_run_interactively
from ..state import resolve_task, get_candidates
from ...codex import SelectionCancelled, PipelineError, OperationError
from ...services.delete_service import DeleteService, DeleteOptions

def handle_delete(args: argparse.Namespace, state: State, cwd: Path) -> int:
    service = DeleteService(state, cwd)

    if args.subcommand == "run":
        return handle_delete_run(args, state, service)
    elif args.subcommand == "task":
        return handle_delete_task(args, state, service)
    else:
        print("Error: Unknown delete subcommand. Use 'run' or 'task'.")
        return 1

def handle_delete_run(
    args: argparse.Namespace,
    state: State,
    service: DeleteService,
) -> int:
    run_id = args.run_id
    target_run = None

    if run_id:
        for run in state.runs:
            if run.id == run_id:
                target_run = run
                break
        if not target_run:
            print(f"Error: Run {run_id} not found.")
            return 1
    else:
        try:
            target_run = select_run_interactively(state)
        except SelectionCancelled:
            return 0
        except PipelineError as e:
            print(e)
            return 1

    options = DeleteOptions(
        target_run=target_run,
        dry_run=args.dry_run,
        yes=args.yes,
        input_func=input,
        output_func=print,
    )

    result = service.delete_run(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Unknown error")

    if result.message:
        print(result.message)

    return 0

def handle_delete_task(
    args: argparse.Namespace,
    state: State,
    service: DeleteService,
) -> int:
    task_id_arg = args.task_id
    target_run = None
    target_task = None

    if task_id_arg:
        try:
            target_run, target_task = resolve_task(state, task_id_arg)
        except PipelineError as e:
            print(e)
            return 1
    else:
        candidates = get_candidates(state, "delete task")
        try:
            target_run, target_task = select_task_interactively(candidates, "delete task")
        except SelectionCancelled:
            return 0
        except PipelineError as e:
            print(e)
            return 1

    options = DeleteOptions(
        target_run=target_run,
        target_task=target_task,
        dry_run=args.dry_run,
        yes=args.yes,
        input_func=input,
        output_func=print,
    )

    result = service.delete_task(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Unknown error")

    if result.message:
        print(result.message)

    return 0
