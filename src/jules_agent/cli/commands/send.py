from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...persistence import save_state
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from ...codex import OperationError


def handle_send(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
) -> int:
    # args.args is a list due to nargs="+"
    if len(args.args) >= 2:
        task_id = args.args[0]
        message = " ".join(args.args[1:])
        run, task = resolve_task(state, task_id)
        task_id_for_print = task_id
    else:
        message = args.args[0]
        candidates = get_candidates(state, "send")
        run, task = select_task_interactively(candidates, "send")
        task_id_for_print = f"{run.id}:{task.id}"

    sync_task_state(client, github_client, state, run, task, cwd)
    if not task.jules:
        raise OperationError(
            1, f"Error: Task {task_id_for_print} has not been dispatched yet."
        )

    print(f"Sending message to task {task_id_for_print}...")
    client.send_message(task.jules.session_name, message)
    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
