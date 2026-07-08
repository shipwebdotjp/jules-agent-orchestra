from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from ...codex import OperationError
from ...services.review_pass_service import ReviewPassService, ReviewPassOptions


def handle_review_pass(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Any = None,
) -> int:
    if not github_client:
        raise OperationError(1, "Error: GITHUB_TOKEN is required for review-pass.")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
    else:
        candidates = get_candidates(state, "merge")
        if not candidates:
            print("No tasks found eligible for manual review pass.")
            return 0
        run, task = select_task_interactively(candidates, "review-pass")

    sync_task_state(client, github_client, state, run, task, cwd)

    service = ReviewPassService(state, client, github_client, cwd)
    options = ReviewPassOptions(task=task)

    result = service.execute(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Review-pass failed")

    if result.message:
        print(result.message)

    return 0
