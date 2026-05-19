from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...persistence import save_state
from ..state import resolve_task, sync_task_state


def handle_send(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    parser: argparse.ArgumentParser,
) -> int:
    run, task = resolve_task(state, args.task_id)

    sync_task_state(client, github_client, state, run, task, cwd)
    if not task.jules:
        parser.exit(
            1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
        )

    print(f"Sending message to task {args.task_id}...")
    client.send_message(task.jules.session_name, args.message)
    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
