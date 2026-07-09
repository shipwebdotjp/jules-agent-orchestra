from __future__ import annotations

import argparse
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from ...codex import OperationError
from ...services.send_service import SendService, SendOptions


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

    service = SendService(state, client, cwd)
    options = SendOptions(
        run=run,
        task=task,
        message=message,
        task_id_for_print=task_id_for_print,
        output_func=print,
    )

    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Send failed")

    if result.message:
        options.output_func(result.message)

    return 0
