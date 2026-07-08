from __future__ import annotations

import argparse
import datetime
import logging
import json
import sys
from pathlib import Path

from ..client import JulesClient
from ..config import Config
from ..github import GitHubClient
from ..models import State, Task, Run
from ..persistence import save_state
from ..cli.state import (
    sync_task_state,
)
from ..pipeline import find_source_name # Re-added for tests
from ..git import get_git_branch # Re-added for tests
from ..services.advance_service import AdvanceService, AdvanceOptions

logger = logging.getLogger("jules_agent")


def dispatch_task(
    task: Task,
    run: Run,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
    args: argparse.Namespace,
    *,
    verbose: bool = True,
) -> None:
    """Backward compatible wrapper for dispatch_task."""
    service = AdvanceService(state, client, None, cwd, config)
    service.dispatch_task_logic(task, run, args, verbose=verbose)


class AdvanceEngine:
    """Backward compatible wrapper for AdvanceEngine."""
    def __init__(
        self,
        state: State,
        client: JulesClient,
        github_client: GitHubClient | None,
        cwd: Path,
        config: Config,
        args: argparse.Namespace,
        interactive: bool = True,
    ):
        self.service = AdvanceService(state, client, github_client, cwd, config)
        self.options = AdvanceOptions(
            interactive=interactive and sys.stdin.isatty(),
            output_json=getattr(args, "json", False),
            args=args,
        )

    def run(self) -> int:
        result = self.service.execute(self.options)
        return result.exit_code
