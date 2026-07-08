from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from ..client import JulesClient
from ..models import State, Run, Task
from ..persistence import save_state
from .options import Options
from .results import OperationResult

@dataclass
class SendOptions(Options):
    run: Run
    task: Task
    message: str
    task_id_for_print: str

class SendService:
    def __init__(self, state: State, client: JulesClient, cwd: Path):
        self.state = state
        self.client = client
        self.cwd = cwd

    def execute(self, options: SendOptions) -> OperationResult:
        task = options.task
        task_id_for_print = options.task_id_for_print

        if not task.jules:
            return OperationResult(
                exit_code=1,
                message=f"Error: Task {task_id_for_print} has not been dispatched yet."
            )

        try:
            self.client.send_message(task.jules.session_name, options.message)
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to send message: {e}")

        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        return OperationResult(exit_code=0, message="Done.")
