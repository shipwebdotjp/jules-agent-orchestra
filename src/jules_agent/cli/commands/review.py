from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...codex import resolve_tool_for_phase, OperationError
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from ...services.review_service import ReviewService, ReviewOptions


def handle_review(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Any = None,
) -> int:
    if not github_client:
        raise OperationError(1, "Error: GITHUB_TOKEN is required for review.")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
    else:
        candidates = get_candidates(state, "review")
        run, task = select_task_interactively(candidates, "review")

    sync_task_state(client, github_client, state, run, task, cwd)

    tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase("review", config, args)

    service = ReviewService(state, client, github_client, cwd)
    options = ReviewOptions(
        task=task,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
    )

    result = service.execute(options)
    if not result.success:
        raise OperationError(result.exit_code, result.message or "Review failed")

    return 0
