from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..models import State, Run
from ..pipeline import format_activities
from .options import Options
from .results import OperationResult

@dataclass
class StatusOptions(Options):
    show_all: bool = False
    show_activities: bool = False
    output_func: Callable[[str], None] = print

class StatusService:
    def __init__(self, state: State):
        self.state = state

    def execute(self, options: StatusOptions) -> OperationResult:
        output = options.output_func
        if not self.state.runs:
            output("No runs found.")
            return OperationResult(exit_code=0)

        runs = self.state.runs
        if not options.show_all:
            runs = [r for r in runs if r.status in ("planned", "running")]

        if not runs:
            output("No planned or running runs found. Use --all to see all runs.")
            return OperationResult(exit_code=0)

        for run in reversed(runs):
            normalized_run_title = self._normalize_run_title(run.original_task)
            output(f"Run: {run.id} [{run.status}] - {normalized_run_title}")
            for task in run.tasks:
                status_str = f"  {task.id}: [{task.status}] {task.title}"
                if task.jules and task.jules.session_url:
                    status_str += f" ({task.jules.session_url})"
                if task.pull_request:
                    status_str += f" -> PR: {task.pull_request.url}"
                output(status_str)

                if options.show_activities and task.jules and task.jules.activities:
                    formatted = format_activities(task.jules.activities)
                    for line in formatted.splitlines():
                        output(f"    {line}")
            output("")

        return OperationResult(exit_code=0)

    def _normalize_run_title(self, title: str) -> str:
        normalized = title.replace("\r", " ").replace("\n", " ")
        if len(normalized) > 100:
            return normalized[:97] + "..."
        return normalized
