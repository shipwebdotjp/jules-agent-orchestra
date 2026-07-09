from __future__ import annotations

import datetime
from pathlib import Path

from ..client import JulesClient
from ..models import State
from ..persistence import save_state
from .options import ApproveOptions
from .results import OperationResult

class ApproveService:
    def __init__(self, state: State, client: JulesClient, cwd: Path):
        self.state = state
        self.client = client
        self.cwd = cwd

    def execute(self, options: ApproveOptions) -> OperationResult:
        task = options.task
        task_id_for_print = options.task_id_for_print
        output = options.output_func

        if not task.jules:
            return OperationResult(
                exit_code=1,
                message=f"Error: Task {task_id_for_print} has not been dispatched yet."
            )

        # Business logic: Approve in Jules
        output(f"Approving plan for task {task_id_for_print} in Jules...")
        try:
            self.client.approve_plan(task.jules.session_name)
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to approve plan: {e}")

        # Update local state
        task.status = "plan_approved"
        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        return OperationResult(exit_code=0, message="Done.")
