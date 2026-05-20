from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import JulesSessionInfo, State
from ...pipeline import (
    find_source_name,
)
from ...git import get_git_branch
from ...persistence import save_state
from ...codex import PipelineError
from ..state import get_jules_state_mapping, get_candidates
from ..io import select_task_interactively


def handle_next(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
) -> int:
    run_id = getattr(args, "run_id", None)
    if run_id:
        target_run = None
        for run in state.runs:
            if run.id == run_id:
                target_run = run
                break
        if not target_run:
            raise PipelineError(f"Run {run_id} not found.")

        if target_run.strategy != "sequential_subtasks" or target_run.status != "running":
            raise PipelineError(f"Run {run_id} is not an active sequential run.")

        next_task = None
        for task in target_run.tasks:
            if task.status == "planned":
                next_task = task
                break
        if not next_task:
            print(f"No more tasks to dispatch in run {run_id}.")
            return 0
    else:
        candidates = get_candidates(state, "next")
        if not candidates:
            print("No active sequential runs with planned tasks found.")
            return 0
        target_run, next_task = select_task_interactively(candidates, "next")

    source_name = find_source_name(client, state.project.repo)
    starting_branch = get_git_branch(cwd)

    print(f"Dispatching next task: {next_task.id} - {next_task.title}")
    next_task.status = "dispatching"
    save_state(cwd, state)
    try:
        session = client.create_session(
            prompt=next_task.prompt or next_task.title,
            source_name=source_name,
            starting_branch=starting_branch,
            title=next_task.title,
            require_plan_approval=False,
            automation_mode="AUTO_CREATE_PR",
        )
        next_task.jules = JulesSessionInfo(
            session_id=session["id"],
            session_name=session["name"],
            state=session.get("state", "QUEUED"),
            session_url=session.get("url"),
            create_time=session.get("createTime"),
            update_time=session.get("updateTime"),
        )
        next_task.status = get_jules_state_mapping(next_task.jules.state, False)
        print(f"  Success: {next_task.jules.session_url}")
    except Exception as e:
        next_task.status = "failed"
        print(f"  Failed: {e}", file=sys.stderr)

    next_task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    return 0
