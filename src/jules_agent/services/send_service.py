from __future__ import annotations

import datetime
import httpx
from pathlib import Path

from ..client import JulesClient, JulesAPIError
from ..models import State
from ..persistence import save_state
from .options import SendOptions
from .results import OperationResult

class SendService:
    def __init__(self, state: State, client: JulesClient, cwd: Path):
        self.state = state
        self.client = client
        self.cwd = cwd

    def execute(self, options: SendOptions) -> OperationResult:
        task = options.task
        task_id_for_print = options.task_id_for_print
        output = options.output_func

        if not task.jules:
            return OperationResult(
                exit_code=1,
                message=f"Error: Task {task_id_for_print} has not been dispatched yet."
            )

        output(f"Sending message to task {task_id_for_print} in Jules...")
        try:
            self.client.send_message(task.jules.session_name, options.message)
        except (JulesAPIError, httpx.RequestError) as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to send message: {e}")

        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        return OperationResult(exit_code=0, message="Done.")
