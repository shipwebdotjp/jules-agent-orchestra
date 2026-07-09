from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...config import Config
from ...models import State
from ...codex import OperationError
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task
from ...services.retry_service import RetryService, RetryOptions

logger = logging.getLogger("jules_agent")

def handle_retry(
    args: Any,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> int:
    if getattr(args, "task_id", None):
        run, task = resolve_task(state, args.task_id)
        if task.status != "failed":
            raise OperationError(1, f"Task {args.task_id} is not in 'failed' status.")
    else:
        candidates = get_candidates(state, "retry")
        if not candidates:
            print("No failed tasks found to retry.")
            return 0
        run, task = select_task_interactively(candidates, "retry")

    service = RetryService(state, client, cwd, config)
    options = RetryOptions(
        run=run,
        task=task,
        automation_mode=getattr(args, "automation_mode", None),
    )
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Retry failed")

    return 0
