from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .. import __version__
from .commands import (
    handle_advance,
    handle_approve,
    handle_delete,
    handle_feedback,
    handle_import,
    handle_merge,
    handle_next,
    handle_review,
    handle_review_pass,
    handle_run,
    handle_send,
    handle_status,
    handle_sync,
    run_clarification_loop,
    run_confirmation_loop,
    run_feedback_loop,
)
from .io import (
    build_review_prompt,
    prompt_for_review,
    render_plan,
    select_task_interactively,
)
from .state import (
    extract_pull_request_number,
    get_candidates,
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
    suggest_reply,
)
from ..codex import OperationError, PipelineError, SelectionCancelled, set_debug
from ..git import get_git_remote_repo, get_git_root
from ..persistence import load_state

# Re-exporting for backward compatibility and tests
__all__ = [
    "build_review_prompt",
    "extract_pull_request_number",
    "get_jules_state_mapping",
    "get_run_sync_status",
    "prompt_for_review",
    "get_candidates",
    "render_plan",
    "resolve_task",
    "run_clarification_loop",
    "run_confirmation_loop",
    "run_feedback_loop",
    "select_task_interactively",
    "sync_pr_created_task",
    "sync_task",
    "suggest_reply",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules-agent",
        description="Analyze a task and dispatch it to Jules.",
    )
    parser.add_argument(
        "--repo",
        help="Optional Jules repo override, for example owner/name.",
    )
    parser.add_argument(
        "--tool-bin",
        help="Path to the backend tool executable.",
    )
    parser.add_argument(
        "--tool",
        help="Backend tool to use (codex, claude, gemini, opencode, copilot, cline).",
    )
    parser.add_argument(
        "--gemini-skip-trust",
        action="store_true",
        default=None,
        help="Pass --skip-trust to the Gemini CLI adapter.",
    )
    parser.add_argument(
        "--plan-tool",
        help="Tool override for the planning phase.",
    )
    parser.add_argument(
        "--approve-tool",
        help="Tool override for the approval phase.",
    )
    parser.add_argument(
        "--feedback-tool",
        help="Tool override for the feedback phase.",
    )
    parser.add_argument(
        "--review-tool",
        help="Tool override for the review phase.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a custom configuration file.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"jules-agent {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    import_parser = subparsers.add_parser("import", help="Import an existing Jules session")
    import_parser.add_argument("session_id", help="Jules session ID or name")

    run_parser = subparsers.add_parser("run", help="Run a new task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
    )
    run_parser.add_argument(
        "--auto-plan-approval",
        action="store_true",
        help="Automatically approve the task plan (forces requirePlanApproval=false).",
    )
    run_parser.add_argument(
        "--automation-mode",
        help="Automation mode for the Jules session (e.g., AUTO_CREATE_PR).",
    )

    status_parser = subparsers.add_parser("status", help="Show local state")
    status_parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Show all runs (default shows only planned and running)",
    )
    status_parser.add_argument(
        "--show-activities",
        action="store_true",
        help="Show session activities",
    )

    subparsers.add_parser("sync", help="Sync with Jules API")

    approve_parser = subparsers.add_parser("approve", help="Approve a task plan")
    approve_parser.add_argument(
        "task_id", nargs="?", help="Task ID (RUN_ID:TASK_ID or TASK_ID)"
    )

    send_parser = subparsers.add_parser("send", help="Send a message to a task")
    send_parser.add_argument(
        "args",
        nargs="+",
        help="[TASK_ID] MESSAGE (if TASK_ID is omitted, message must be quoted if it contains spaces)",
    )

    feedback_parser = subparsers.add_parser(
        "feedback", help="Interactive feedback loop for a task"
    )
    feedback_parser.add_argument(
        "task_id", nargs="?", help="Task ID (RUN_ID:TASK_ID or TASK_ID)"
    )

    review_parser = subparsers.add_parser("review", help="Manually run a review for a task")
    review_parser.add_argument(
        "task_id", nargs="?", help="Task ID (RUN_ID:TASK_ID or TASK_ID)"
    )

    review_pass_parser = subparsers.add_parser(
        "review-pass", help="Manually mark a task as review_passed for current head SHA"
    )
    review_pass_parser.add_argument(
        "task_id", nargs="?", help="Task ID (RUN_ID:TASK_ID or TASK_ID)"
    )

    merge_parser = subparsers.add_parser("merge", help="Merge pull request for a task")
    merge_parser.add_argument(
        "task_id", nargs="?", help="Task ID (RUN_ID:TASK_ID or TASK_ID)"
    )
    merge_parser.add_argument(
        "--delete-branch",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Delete the local and remote source branch after a successful merge.",
    )
    merge_parser.add_argument(
        "--pull",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run git pull after switching to the target branch after merge.",
    )
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

    next_parser = subparsers.add_parser("next", help="Dispatch next task in sequential run")
    next_parser.add_argument("run_id", nargs="?", help="Run ID")
    next_parser.add_argument(
        "--automation-mode",
        help="Automation mode for the Jules session (e.g., AUTO_CREATE_PR).",
    )

    delete_parser = subparsers.add_parser("delete", aliases=["rm"], help="Delete a run or task from local state")
    delete_parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")
    delete_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    delete_subparsers = delete_parser.add_subparsers(dest="subcommand", required=True)

    delete_run_parser = delete_subparsers.add_parser("run", help="Delete a run and all its tasks")
    delete_run_parser.add_argument("run_id", nargs="?", help="Run ID to delete")

    delete_task_parser = delete_subparsers.add_parser("task", help="Delete a specific task")
    delete_task_parser.add_argument("task_id", nargs="?", help="Task ID to delete (RUN_ID:TASK_ID or TASK_ID)")

    advance_parser = subparsers.add_parser(
        "advance", help="Automatically or interactively advance work"
    )
    advance_parser.add_argument(
        "--auto-plan-approval",
        action="store_true",
        default=None,
        help="Automatically approve plans when recommended.",
    )
    advance_parser.add_argument(
        "--auto-feedback",
        action="store_true",
        default=None,
        help="Automatically send suggested feedback.",
    )
    advance_parser.add_argument(
        "--auto-merge",
        action="store_true",
        default=None,
        help="Automatically merge PRs.",
    )
    advance_parser.add_argument(
        "--auto",
        action="store_true",
        help="Enable all automatic behaviors (plan approval and feedback).",
    )
    advance_parser.add_argument(
        "--skip-review",
        action="store_true",
        default=None,
        help="Skip review gate and allow merging from pr_created or waiting_human_review.",
    )
    advance_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit result as JSON.",
    )

    cron_parser = subparsers.add_parser(
        "cron", help="Non-interactive background execution"
    )
    cron_parser.add_argument(
        "--auto-plan-approval",
        action="store_true",
        default=None,
        help="Automatically approve plans when recommended.",
    )
    cron_parser.add_argument(
        "--auto-feedback",
        action="store_true",
        default=None,
        help="Automatically send suggested feedback.",
    )
    cron_parser.add_argument(
        "--auto-merge",
        action="store_true",
        default=None,
        help="Automatically merge PRs.",
    )
    cron_parser.add_argument(
        "--auto",
        action="store_true",
        help="Enable all automatic behaviors (plan approval and feedback).",
    )
    cron_parser.add_argument(
        "--skip-review",
        action="store_true",
        default=None,
        help="Skip review gate and allow merging from pr_created or waiting_human_review.",
    )
    cron_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit result as JSON.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)

    # Configure logging
    debug_enabled = args.debug or config.debug
    logging.basicConfig(
        level=logging.DEBUG if debug_enabled else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    set_debug(debug_enabled)

    api_key = os.environ.get("JULES_API_KEY") or config.api_key
    if not api_key:
        parser.exit(
            1,
            "Error: JULES_API_KEY is not set in environment or configuration.\n",
        )

    github_token = os.environ.get("GITHUB_TOKEN") or config.github_token

    repo = args.repo or config.repo
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
            return handle_run(args, state, client, cwd, config)
        elif args.command == "import":
            return handle_import(args, state, client, cwd)
        elif args.command == "advance":
            return handle_advance(args, state, client, github_client, cwd, config)
        elif args.command == "cron":
            from .commands.advance import handle_cron
            return handle_cron(args, state, client, github_client, cwd, config)
        elif args.command == "status":
            return handle_status(args, state)
        elif args.command == "sync":
            return handle_sync(args, state, client, github_client, cwd)
        elif args.command == "approve":
            return handle_approve(args, state, client, cwd, config=config)
        elif args.command == "feedback":
            return handle_feedback(args, state, client, cwd, config=config)
        elif args.command == "review":
            return handle_review(args, state, client, github_client, cwd, config=config)
        elif args.command == "review-pass":
            return handle_review_pass(args, state, client, github_client, cwd, config=config)
        elif args.command == "send":
            return handle_send(args, state, client, github_client, cwd)
        elif args.command == "merge":
            return handle_merge(args, state, client, github_client, cwd, config)
        elif args.command == "next":
            return handle_next(args, state, client, cwd, config)
        elif args.command in ("delete", "rm"):
            return handle_delete(args, state, cwd)

    except SelectionCancelled:
        return 0
    except OperationError as exc:
        parser.exit(exc.exit_code, f"{exc.message}\n")
    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    return 0
