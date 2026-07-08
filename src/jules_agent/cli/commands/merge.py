from __future__ import annotations

import argparse
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from .sync import handle_sync
from ...codex import OperationError
from ...services.merge_service import MergeService, MergeOptions


def handle_merge(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> int:
    if not github_client:
        raise OperationError(1, "Error: GITHUB_TOKEN is not set. Merging requires GitHub access.")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
        task_id_for_print = args.task_id
    else:
        # Perform full state sync before computing and showing merge candidates
        handle_sync(args, state, client, github_client, cwd)
        candidates = get_candidates(state, "merge")
        run, task = select_task_interactively(candidates, "merge")
        task_id_for_print = f"{run.id}:{task.id}"

    # sync first
    sync_task_state(client, github_client, state, run, task, cwd)

    service = MergeService(state, client, github_client, cwd, config)
    options = MergeOptions(
        run=run,
        task=task,
        task_id_for_print=task_id_for_print,
        merge_method=args.merge_method,
        delete_branch=getattr(args, "delete_branch", None),
        pull=getattr(args, "pull", None),
    )

    result = service.execute(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Unknown error")

    if result.message:
        print(result.message)

    return 0
