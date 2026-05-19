from __future__ import annotations
import argparse
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from .sync import handle_sync
from ..advance_core import AdvanceEngine


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

    engine = AdvanceEngine(
        state=state,
        client=client,
        github_client=github_client,
        cwd=cwd,
        config=config,
        args=args,
        interactive=True,
    )
    return engine.run()


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

    engine = AdvanceEngine(
        state=state,
        client=client,
        github_client=github_client,
        cwd=cwd,
        config=config,
        args=args,
        interactive=False,
    )
    return engine.run()
