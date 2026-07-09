from __future__ import annotations

import datetime
import logging
from pathlib import Path

from ..client import JulesClient
from ..config import Config
from ..models import State, Task, JulesSessionInfo, RunStatus
from ..persistence import save_state
from ..pipeline import find_source_name
from ..git import get_git_branch
from .options import RetryOptions
from .results import OperationResult
from .state_utils import get_jules_state_mapping, get_run_sync_status

logger = logging.getLogger("jules_agent")

class RetryService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        cwd: Path,
        config: Config,
    ):
        self.state = state
        self.client = client
        self.cwd = cwd
        self.config = config

    def execute(self, options: RetryOptions) -> OperationResult:
        task = options.task
        run = options.run
        output = options.output_func

        if task.status != "failed":
            return OperationResult(exit_code=1, message=f"Task {task.id} is not in 'failed' status.")

        retry_count = int(task.advance_state.get("retry_count", 0)) + 1

        # Reset task state for retry
        task.status = "dispatching"
        task.pull_request = None
        task.review = None
        task.attempts = 0
        task.advance_state["retry_count"] = retry_count

        # Update run status to running
        previous_run_status = run.status
        run.status = "running"

        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        output(f"Retrying task: {task.id} - {task.title} (attempt {retry_count})")

        try:
            source_name = find_source_name(self.client, self.state.project.repo)
            starting_branch = get_git_branch(self.cwd)
            automation_mode = (
                getattr(options.args, "automation_mode", None)
                or run.automation_mode
                or getattr(self.config, "automation_mode", None)
                or "AUTO_CREATE_PR"
            )
            require_plan_approval = (
                run.require_plan_approval
                if run.require_plan_approval is not None
                else False
            )

            session = self.client.create_session(
                prompt=task.prompt or task.title,
                source_name=source_name,
                starting_branch=starting_branch,
                title=task.title,
                require_plan_approval=require_plan_approval,
                automation_mode=automation_mode,
            )
            task.jules = JulesSessionInfo(
                session_id=session["id"],
                session_name=session["name"],
                state=session.get("state", "QUEUED"),
                session_url=session.get("url"),
                create_time=session.get("createTime"),
                update_time=session.get("updateTime"),
            )
            task.status = get_jules_state_mapping(task.jules.state, False)

            # Recalculate run status based on all tasks
            run.status = get_run_sync_status(
                run,
                previous_status="running",
                reopened_from_completed=(previous_run_status == "completed"),
            )

            output(f"  Success: {task.jules.session_url}")
            exit_code = 0
            message = None
        except Exception as e:
            task.status = "failed"
            run.status = get_run_sync_status(
                run,
                previous_status="failed",
                reopened_from_completed=(previous_run_status == "completed"),
            )
            logger.exception(f"  Failed: {e}")
            exit_code = 1
            message = str(e)

        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        return OperationResult(exit_code=exit_code, message=message)
