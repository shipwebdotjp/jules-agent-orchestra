from __future__ import annotations

import argparse
from pathlib import Path

from ...github import GitHubClient
from ...models import State
from ...pipeline import perform_task_review
from ..state import resolve_task


def handle_review(
    args: argparse.Namespace,
    state: State,
    github_client: GitHubClient | None,
    cwd: Path,
    codex_bin: str,
    parser: argparse.ArgumentParser,
) -> int:
    if not github_client:
        parser.exit(1, "Error: GITHUB_TOKEN is required for review.\n")

    _, task = resolve_task(state, args.task_id)

    perform_task_review(
        task=task,
        state=state,
        github_client=github_client,
        cwd=cwd,
        codex_bin=codex_bin,
    )

    return 0
