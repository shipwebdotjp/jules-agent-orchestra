from __future__ import annotations

from ..models import Run, RunStatus, TaskStatus

def get_run_sync_status(
    run: Run,
    *,
    previous_status: RunStatus,
    reopened_from_completed: bool,
) -> RunStatus:
    if any(t.status in ("failed", "pr_closed") for t in run.tasks):
        return "failed"

    if all(t.status in ("completed", "merged") for t in run.tasks):
        return "completed"

    if reopened_from_completed:
        return "running"

    return previous_status


def get_jules_state_mapping(jules_state: str, has_pr: bool) -> TaskStatus:
    mapping: dict[str, TaskStatus] = {
        "QUEUED": "dispatched",
        "PLANNING": "planning",
        "AWAITING_PLAN_APPROVAL": "awaiting_plan_approval",
        "AWAITING_USER_FEEDBACK": "awaiting_user_feedback",
        "IN_PROGRESS": "in_progress",
        "PAUSED": "paused",
        "FAILED": "failed",
    }
    if jules_state == "COMPLETED":
        return "pr_created" if has_pr else "completed"
    return mapping.get(jules_state, "dispatched")
