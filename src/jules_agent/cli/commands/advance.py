from __future__ import annotations
import argparse
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from .sync import handle_sync
from ..advance_core import AdvanceEngine
from ...services.advance_service import AdvanceService, AdvanceOptions


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
    sync_result = handle_sync(args, state, client, github_client, cwd, skip_pr_sync=True)
    if sync_result != 0:
        return sync_result

    service = AdvanceService(state, client, github_client, cwd, config)
    options = AdvanceOptions(
        interactive=True,
        output_json=getattr(args, "json", False),
        args=args,
    )
    result = service.execute(options)
    return result.exit_code


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
    sync_result = handle_sync(args, state, client, github_client, cwd, skip_pr_sync=False)
    if sync_result != 0:
        return sync_result

    service = AdvanceService(state, client, github_client, cwd, config)
    options = AdvanceOptions(
        interactive=False,
        output_json=getattr(args, "json", False),
        args=args,
    )
    result = service.execute(options)
    return result.exit_code
