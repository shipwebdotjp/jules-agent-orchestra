from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import JulesSessionInfo, State
from ...pipeline import (
    find_source_name,
    get_git_branch,
    save_state,
)
from ..state import get_jules_state_mapping


def handle_next(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
) -> int:
    target_run = None
    for run in reversed(state.runs):
        if run.strategy == "sequential_subtasks" and run.status == "running":
            target_run = run
            break

    if not target_run:
        print("No active sequential run found.")
        return 0

    next_task = None
    for task in target_run.tasks:
        if task.status == "planned":
            next_task = task
            break

    if not next_task:
        print("No more tasks to dispatch in this run.")
        return 0

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
