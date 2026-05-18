from __future__ import annotations

import argparse
import os
from pathlib import Path

from .commands import (
    handle_advance,
    handle_approve,
    handle_feedback,
    handle_merge,
    handle_next,
    handle_run,
    handle_send,
    handle_status,
    handle_sync,
    run_confirmation_loop,
    run_feedback_loop,
)
from .io import build_review_prompt, prompt_for_review, render_plan
from .state import (
    extract_pull_request_number,
    get_jules_state_mapping,
    get_run_sync_status,
    resolve_task,
    sync_pr_created_task,
    sync_task,
)
from ..client import JulesClient
from ..config import load_config
from ..github import GitHubClient
from ..models import (
    ProjectState,
    State,
)
from ..pipeline import (
    PipelineError,
    get_git_remote_repo,
    get_git_root,
    load_state,
    suggest_reply,
)

# Re-exporting for backward compatibility and tests
__all__ = [
    "build_review_prompt",
    "extract_pull_request_number",
    "get_jules_state_mapping",
    "get_run_sync_status",
    "prompt_for_review",
    "render_plan",
    "resolve_task",
    "run_confirmation_loop",
    "run_feedback_loop",
    "sync_pr_created_task",
    "sync_task",
    "suggest_reply",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules-agent",
        description="Analyze a task with Codex and dispatch it to Jules.",
    )
    parser.add_argument(
        "--repo",
        help="Optional Jules repo override, for example owner/name.",
    )
    parser.add_argument(
        "--codex-bin",
        help="Path to the codex executable.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a custom configuration file.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    run_parser = subparsers.add_parser("run", help="Run a new task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
    )

    status_parser = subparsers.add_parser("status", help="Show local state")
    status_parser.add_argument(
        "--show-activities",
        action="store_true",
        help="Show session activities",
    )

    subparsers.add_parser("sync", help="Sync with Jules API")

    approve_parser = subparsers.add_parser("approve", help="Approve a task plan")
    approve_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")

    send_parser = subparsers.add_parser("send", help="Send a message to a task")
    send_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")
    send_parser.add_argument("message", help="Message to send")

    feedback_parser = subparsers.add_parser(
        "feedback", help="Interactive feedback loop for a task"
    )
    feedback_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")

    merge_parser = subparsers.add_parser("merge", help="Merge pull request for a task")
    merge_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")
    merge_group = merge_parser.add_mutually_exclusive_group()
    merge_group.add_argument(
        "--merge",
        action="store_const",
        dest="merge_method",
        const="merge",
        help="Use merge commit (default)",
    )
    merge_group.add_argument(
        "--squash",
        action="store_const",
        dest="merge_method",
        const="squash",
        help="Squash and merge",
    )
    merge_group.add_argument(
        "--rebase",
        action="store_const",
        dest="merge_method",
        const="rebase",
        help="Rebase and merge",
    )

    subparsers.add_parser("next", help="Dispatch next task in sequential run")

    advance_parser = subparsers.add_parser(
        "advance", help="Automatically or interactively advance work"
    )
    advance_parser.add_argument(
        "--auto",
        action="store_const",
        dest="advance_mode",
        const="auto",
        help="Run in automatic mode (auto-approve/auto-reply)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)

    api_key = os.environ.get("JULES_API_KEY") or config.api_key
    if not api_key:
        parser.exit(
            1,
            "Error: JULES_API_KEY is not set in environment or configuration.\n",
        )

    github_token = os.environ.get("GITHUB_TOKEN") or config.github_token

    repo = args.repo or config.repo
    codex_bin = args.codex_bin or config.codex_bin
    base_url = config.base_url

    client = JulesClient(api_key=api_key, base_url=base_url)
    github_client = GitHubClient(token=github_token) if github_token else None
    cwd = Path.cwd()

    if args.command is None:
        parser.print_help()
        return 0

    try:
        state = load_state(cwd)
        if state is None:
            git_root = get_git_root(cwd)
            if repo is None:
                repo_info = get_git_remote_repo(cwd)
                if repo_info:
                    repo = f"{repo_info[0]}/{repo_info[1]}"
            if repo is None:
                parser.exit(
                    1, "Error: Could not determine repository. Use --repo owner/name.\n"
                )

            state = State(project=ProjectState(root=str(git_root), repo=repo))

        if args.command == "run":
            return handle_run(args, state, client, cwd, codex_bin)
        elif args.command == "advance":
            return handle_advance(args, state, client, github_client, cwd, config)
        elif args.command == "status":
            return handle_status(args, state)
        elif args.command == "sync":
            return handle_sync(args, state, client, github_client, cwd)
        elif args.command == "approve":
            return handle_approve(args, state, client, cwd, parser)
        elif args.command == "feedback":
            return handle_feedback(args, state, client, cwd, codex_bin, parser)
        elif args.command == "send":
            return handle_send(args, state, client, cwd, parser)
        elif args.command == "merge":
            return handle_merge(args, state, client, github_client, cwd, config, parser)
        elif args.command == "next":
            return handle_next(args, state, client, cwd)

    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    return 0
