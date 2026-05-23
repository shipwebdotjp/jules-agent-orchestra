from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

from ..client import JulesClient
from ..github import GitHubClient
from ..models import (
    PR_SYNC_STATUSES,
    PullRequestInfo,
    Run,
    RunStatus,
    State,
    Task,
    TaskStatus,
    gitPatchInfo,
)
from ..codex import PipelineError


def resolve_task(state: State, task_id_arg: str) -> tuple[Run, Task]:
    if ":" in task_id_arg:
        run_id, task_id = task_id_arg.split(":", 1)
        for run in state.runs:
            if run.id == run_id:
                for task in run.tasks:
                    if task.id == task_id:
                        return run, task
        raise PipelineError(f"Task {task_id_arg} not found.")

    candidates: list[tuple[Run, Task]] = []
    for run in state.runs:
        for task in run.tasks:
            if task.id == task_id_arg:
                candidates.append((run, task))

    if not candidates:
        raise PipelineError(f"Task {task_id_arg} not found.")
    if len(candidates) > 1:
        run_ids = ", ".join(r.id for r, t in candidates)
        raise PipelineError(
            f"Task {task_id_arg} is ambiguous. Found in runs: {run_ids}. "
            "Please use RUN_ID:TASK_ID format."
        )
    return candidates[0]


PULL_REQUEST_NUMBER_RE = re.compile(r"/pulls?/(\d+)(?:[/?#]|$)")


def extract_pull_request_number(url: str | None) -> int | None:
    if not url:
        return None

    match = PULL_REQUEST_NUMBER_RE.search(url)
    if not match:
        return None

    return int(match.group(1))


def sync_pr_created_task(
    github_client: GitHubClient,
    repo: str,
    task: Task,
) -> bool:
    if not task.pull_request or not task.pull_request.url:
        print(
            f"Warning: Task {task.id} is pr_created but has no pull request URL.",
            file=sys.stderr,
        )
        return False

    pull_number = extract_pull_request_number(task.pull_request.url)
    if pull_number is None:
        print(
            "Warning: Could not parse pull request number from "
            f"{task.pull_request.url!r} for task {task.id}.",
            file=sys.stderr,
        )
        return False

    try:
        pull_request = github_client.get_pull_request(repo, pull_number)
    except Exception as exc:
        print(
            f"Warning: Failed to fetch PR details for task {task.id}: {exc}",
            file=sys.stderr,
        )
        return False

    state = pull_request.get("state")
    if state == "open":
        return False

    if state == "closed":
        task.status = "merged" if pull_request.get("merged_at") else "pr_closed"
        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return True

    print(
        f"Warning: Unexpected PR state {state!r} for task {task.id}.",
        file=sys.stderr,
    )
    return False


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


def sync_task(client: JulesClient, task: Task) -> bool:
    if not task.jules:
        return False

    try:
        session = client.get_session(task.jules.session_name)
        task.jules.state = session.get("state", task.jules.state)
        task.jules.update_time = session.get("updateTime", task.jules.update_time)
        task.jules.session_url = session.get("url", task.jules.session_url)

        try:
            task.jules.activities = list(client.list_activities(task.jules.session_name))
        except Exception as e:
            print(
                f"Warning: Failed to fetch activities for task {task.id}: {e}",
                file=sys.stderr,
            )

        has_pr = False
        outputs = session.get("outputs", [])
        for output in outputs:
            pr = output.get("pullRequest")
            if pr:
                task.pull_request = PullRequestInfo(
                    url=pr.get("url"),
                    title=pr.get("title"),
                    description=pr.get("description"),
                )
                has_pr = True
            changeSet = output.get("changeSet", [])
            if changeSet:
                gitPatch = changeSet.get("gitPatch", None)
                if gitPatch:
                    gitPatch_info = gitPatchInfo(
                        unidiffPatch=gitPatch.get("unidiffPatch", ""),
                        baseCommitId=gitPatch.get("baseCommitId", ""),
                        suggestedCommitMessage=gitPatch.get("suggestedCommitMessage", ""),
                    )
                    task.jules.code_changes = gitPatch_info

        new_status = get_jules_state_mapping(task.jules.state, has_pr)
        if task.status != new_status:
            # Prevent status regression: if we are already in a post-PR state,
            # don't go back to pr_created even if Jules says COMPLETED.
            if (
                task.status in ("waiting_human_review", "codex_reviewing", "needs_fix")
                and new_status == "pr_created"
            ):
                pass
            else:
                task.status = new_status
                task.updated_at = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
        return True
    except Exception as e:
        print(f"Failed to sync task {task.id}: {e}", file=sys.stderr)
        return False


def get_candidates(state: State, command: str) -> list[tuple[Run, Task]]:
    candidates: list[tuple[Run, Task]] = []
    for run in state.runs:
        for task in run.tasks:
            eligible = False
            if command == "approve":
                eligible = task.jules is not None and task.status == "awaiting_plan_approval"
            elif command == "feedback":
                eligible = task.jules is not None and task.status in (
                    "awaiting_plan_approval",
                    "awaiting_user_feedback",
                )
            elif command == "send":
                eligible = task.jules is not None and task.status not in (
                    "completed",
                    "merged",
                    "pr_closed",
                    "failed",
                    "cancelled",
                )
            elif command in ("review", "merge"):
                eligible = task.pull_request is not None and task.status in (
                    "pr_created",
                    "waiting_human_review",
                )
            elif command == "next":
                # For 'next', we want the first 'planned' task of a running sequential run.
                # Since get_candidates iterates over all tasks, we need to be careful.
                # We only want to return the FIRST planned task for each eligible run.
                if (
                    run.strategy == "sequential_subtasks"
                    and run.status == "running"
                    and task.status == "planned"
                ):
                    # Check if this is the first planned task in this run
                    first_planned = None
                    for t in run.tasks:
                        if t.status == "planned":
                            first_planned = t
                            break
                    if first_planned and first_planned.id == task.id:
                        eligible = True

            if eligible:
                candidates.append((run, task))

    # Sort by updated_at descending
    candidates.sort(key=lambda x: x[1].updated_at, reverse=True)
    return candidates


def sync_task_state(
    client: JulesClient,
    github_client: GitHubClient | None,
    state: State,
    run: Run,
    task: Task,
    cwd: Path,
) -> bool:
    """
    Syncs the state of a single task (and its parent run) from Jules and/or GitHub.
    Returns True if any updates occurred.
    """
    from ..persistence import save_state

    updated = False
    previous_run_status = run.status
    task_initial_status = task.status

    if (
        task.status
        not in (
            "completed",
            "merged",
            "failed",
            "cancelled",
            "pr_closed",
        )
        and task.status not in PR_SYNC_STATUSES
    ):
        sync_task(client, task)

    if task.status in PR_SYNC_STATUSES and github_client:
        if sync_pr_created_task(github_client, state.project.repo, task):
            updated = True

    if task.status != task_initial_status:
        updated = True

    if updated:
        reopened_from_completed = previous_run_status == "completed"
        run.status = get_run_sync_status(
            run,
            previous_status=previous_run_status,
            reopened_from_completed=reopened_from_completed,
        )
        run.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(cwd, state)

    return updated
