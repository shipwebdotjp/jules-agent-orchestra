from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..client import JulesClient
from ..config import Config
from ..models import State, Run, Task
from .options import Options
from .results import OperationResult

@dataclass
class NextOptions(Options):
    run: Run
    task: Task
    automation_mode: Optional[str] = None
    output_func: Callable[[str], None] = print

class NextService:
    def __init__(self, state: State, client: JulesClient, cwd: Path, config: Config):
        self.state = state
        self.client = client
        self.cwd = cwd
        self.config = config

    def execute(self, options: NextOptions) -> OperationResult:
        from .advance_service import AdvanceService # Import inside to avoid circular dependency
        try:
            service = AdvanceService(self.state, self.client, None, self.cwd, self.config)
            service.dispatch_task_logic(
                task=options.task,
                run=options.run,
                automation_mode=options.automation_mode,
                output_func=options.output_func,
            )
            return OperationResult(exit_code=0)
        except Exception as e:
            return OperationResult(exit_code=1, message=str(e))
