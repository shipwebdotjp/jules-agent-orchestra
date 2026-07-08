from __future__ import annotations

import argparse
from pathlib import Path

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State
from ...services.sync_service import SyncService, SyncOptions
from ...codex import OperationError


def handle_sync(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    skip_pr_sync: bool = False,
) -> int:
    service = SyncService(state, client, github_client, cwd)
    options = SyncOptions(
        skip_pr_sync=skip_pr_sync,
        json_output=getattr(args, "json", False),
        output_func=print,
    )
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Sync failed")

    return 0
