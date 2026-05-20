from __future__ import annotations

import argparse
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...pipeline import perform_task_review
from ...codex import resolve_tool_for_phase
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, sync_task_state
from typing import Any


def handle_review(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    codex_bin: str,
    parser: argparse.ArgumentParser,
    config: Any = None,
) -> int:
    if not github_client:
        parser.exit(1, "Error: GITHUB_TOKEN is required for review.\n")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
    else:
        candidates = get_candidates(state, "review")
        run, task = select_task_interactively(candidates, "review")

    sync_task_state(client, github_client, state, run, task, cwd)

    tool_name, tool_bin = resolve_tool_for_phase("review", config, args)

    perform_task_review(
        task=task,
        state=state,
        github_client=github_client,
        cwd=cwd,
        tool_name=tool_name,
        tool_bin=tool_bin,
    )

    return 0
