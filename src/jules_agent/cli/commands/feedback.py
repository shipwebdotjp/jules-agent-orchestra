from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...models import State
from ...codex import OperationError, resolve_tool_for_phase
from ...persistence import save_state
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task # re-added for tests
from ...services.feedback_service import FeedbackService, FeedbackOptions


def run_feedback_loop(
    task: Any,
    *,
    cwd: Path,
    client: JulesClient,
    tool_name: str = "codex",
    tool_bin: str | None = None,
    gemini_skip_trust: bool = False,
    auto_plan_approval: bool = False,
    auto_feedback: bool = False,
    allow_skip: bool = False,
    interactive: bool = True,
    input_func=input,
    output=print,
) -> Any:
    """Backward compatible wrapper for run_feedback_loop."""
    service = FeedbackService(State(project=None), client, cwd)
    options = FeedbackOptions(
        task=task,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
        auto_plan_approval=auto_plan_approval,
        auto_feedback=auto_feedback,
        allow_skip=allow_skip,
        interactive=interactive,
        input_func=input_func,
        output_func=output,
    )
    res = service.execute(options)
    if not res.success and res.message == "failed":
        return "failed"
    return res.data


def handle_feedback(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Any = None,
) -> int:
    if args.task_id:
        _run, task = resolve_task(state, args.task_id)
        task_id_for_print = args.task_id
    else:
        candidates = get_candidates(state, "feedback")
        _run, task = select_task_interactively(candidates, "feedback")
        task_id_for_print = f"{_run.id}:{task.id}"

    if not task.jules:
        raise OperationError(
            1, f"Error: Task {task_id_for_print} has not been dispatched yet."
        )

    is_awaiting_plan_approval = task.status == "awaiting_plan_approval"
    phase = "approve" if is_awaiting_plan_approval else "feedback"
    tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase(phase, config, args)

    service = FeedbackService(state, client, cwd)
    options = FeedbackOptions(
        task=task,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
        interactive=sys.stdin.isatty(),
        input_func=input,
        output_func=print,
    )

    result = service.execute(options)
    outcome = result.data

    if not result.success:
        if result.message == "failed":
            return 1
        raise OperationError(result.exit_code, result.message or "Unknown error")

    if outcome == "failed":
        return 1
    if outcome == "skipped":
        return 0

    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
