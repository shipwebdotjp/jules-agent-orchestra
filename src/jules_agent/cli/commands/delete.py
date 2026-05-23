from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...models import State, Run, Task
from ..io import select_task_interactively, select_run_interactively
from ..state import resolve_task, get_candidates
from ...persistence import save_state
from ...codex import SelectionCancelled, PipelineError

def handle_delete(args: argparse.Namespace, state: State, cwd: Path) -> int:
    if args.subcommand == "run":
        return handle_delete_run(args, state, cwd)
    elif args.subcommand == "task":
        return handle_delete_task(args, state, cwd)
    else:
        print("Error: Unknown delete subcommand. Use 'run' or 'task'.")
        return 1

def handle_delete_run(
    args: argparse.Namespace,
    state: State,
    cwd: Path,
    *,
    input_func=input,
) -> int:
    run_id = args.run_id
    target_run: Run | None = None

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

    tasks_count = len(target_run.tasks)
    if args.dry_run:
        print(f"[DRY RUN] Would delete run {target_run.id} and its {tasks_count} tasks.")
        for task in target_run.tasks:
            print(f"  - {task.id}: {task.title}")
        return 0

    if not args.yes:
        confirm = input_func(f"Are you sure you want to delete run {target_run.id} and its {tasks_count} tasks? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0

    state.runs.remove(target_run)
    save_state(cwd, state)
    print(f"Deleted run {target_run.id} and {tasks_count} tasks.")
    return 0

def handle_delete_task(
    args: argparse.Namespace,
    state: State,
    cwd: Path,
    *,
    input_func=input,
) -> int:
    task_id_arg = args.task_id
    target_run: Run | None = None
    target_task: Task | None = None

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

    if args.dry_run:
        print(f"[DRY RUN] Would delete task {target_task.id} from run {target_run.id}.")
        if len(target_run.tasks) == 1:
            print(f"[DRY RUN] Run {target_run.id} will become empty and will also be deleted.")
        return 0

    if not args.yes:
        confirm = input_func(f"Are you sure you want to delete task {target_task.id} from run {target_run.id}? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0

    target_run.tasks.remove(target_task)
    pruned_run = False
    if not target_run.tasks:
        state.runs.remove(target_run)
        pruned_run = True

    save_state(cwd, state)

    if pruned_run:
        print(f"Deleted task {target_task.id} and pruned empty run {target_run.id}.")
    else:
        print(f"Deleted task {target_task.id} from run {target_run.id}.")

    return 0
