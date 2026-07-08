from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..models import State, Run, Task
from ..persistence import save_state
from .options import Options
from .results import OperationResult

@dataclass
class DeleteOptions(Options):
    target_run: Optional[Run] = None
    target_task: Optional[Task] = None
    dry_run: bool = False
    yes: bool = False
    input_func: Callable[[str], str] = input
    output_func: Callable[[str], None] = print

class DeleteService:
    def __init__(self, state: State, cwd: Path):
        self.state = state
        self.cwd = cwd

    def delete_run(self, options: DeleteOptions) -> OperationResult:
        target_run = options.target_run
        if not target_run:
            return OperationResult(exit_code=1, message="Error: Run not found.")

        tasks_count = len(target_run.tasks)
        if options.dry_run:
            options.output_func(f"[DRY RUN] Would delete run {target_run.id} and its {tasks_count} tasks.")
            for task in target_run.tasks:
                options.output_func(f"  - {task.id}: {task.title}")
            return OperationResult(exit_code=0)

        if not options.yes:
            confirm = options.input_func(f"Are you sure you want to delete run {target_run.id} and its {tasks_count} tasks? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                return OperationResult(exit_code=0, message="Aborted.")

        self.state.runs.remove(target_run)
        save_state(self.cwd, self.state)
        return OperationResult(exit_code=0, message=f"Deleted run {target_run.id} and {tasks_count} tasks.")

    def delete_task(self, options: DeleteOptions) -> OperationResult:
        target_run = options.target_run
        target_task = options.target_task
        if not target_run or not target_task:
            return OperationResult(exit_code=1, message="Error: Task not found.")

        if options.dry_run:
            options.output_func(f"[DRY RUN] Would delete task {target_task.id} from run {target_run.id}.")
            if len(target_run.tasks) == 1:
                options.output_func(f"[DRY RUN] Run {target_run.id} will become empty and will also be deleted.")
            return OperationResult(exit_code=0)

        if not options.yes:
            confirm = options.input_func(f"Are you sure you want to delete task {target_task.id} from run {target_run.id}? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                return OperationResult(exit_code=0, message="Aborted.")

        target_run.tasks.remove(target_task)
        pruned_run = False
        if not target_run.tasks:
            self.state.runs.remove(target_run)
            pruned_run = True

        save_state(self.cwd, self.state)

        if pruned_run:
            return OperationResult(exit_code=0, message=f"Deleted task {target_task.id} and pruned empty run {target_run.id}.")
        else:
            return OperationResult(exit_code=0, message=f"Deleted task {target_task.id} from run {target_run.id}.")
