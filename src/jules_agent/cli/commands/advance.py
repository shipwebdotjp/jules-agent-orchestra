from __future__ import annotations
import argparse
import sys
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from .sync import handle_sync
from ...services.advance_service import AdvanceService, AdvanceOptions
from ...codex import OperationError


def handle_advance(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> int:
    # 1. Sync state at command start
    if not getattr(args, "json", False):
        print("Syncing state...")

    # We ignore the return value of handle_sync as it now raises OperationError on failure
    handle_sync(args, state, client, github_client, cwd, skip_pr_sync=True)

    service = AdvanceService(state, client, github_client, cwd, config)
    options = AdvanceOptions(
        interactive=sys.stdin.isatty(),
        output_json=getattr(args, "json", False),
        args=args,
        output_func=print,
    )
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Advance failed")

    return 0


def handle_cron(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
) -> int:
    # 1. Sync state at command start (don't skip PR sync for cron as it might handle merges)
    if not getattr(args, "json", False):
        print("Syncing state...")

    # We ignore the return value of handle_sync as it now raises OperationError on failure
    handle_sync(args, state, client, github_client, cwd, skip_pr_sync=False)

    service = AdvanceService(state, client, github_client, cwd, config)
    options = AdvanceOptions(
        interactive=False,
        output_json=getattr(args, "json", False),
        args=args,
        output_func=print,
    )
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Cron failed")

    return 0
