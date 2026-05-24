from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from ...persistence import save_state
from ...git import run_command, get_git_branch
from ..io import select_task_interactively
from ..state import get_candidates, resolve_task, extract_pull_request_number, sync_task_state
from .sync import handle_sync


def handle_merge(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Config,
    parser: argparse.ArgumentParser,
) -> int:
    if not github_client:
        parser.exit(1, "Error: GITHUB_TOKEN is not set. Merging requires GitHub access.\n")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
        task_id_for_print = args.task_id
    else:
        # Perform full state sync before computing and showing merge candidates
        handle_sync(args, state, client, github_client, cwd)
        candidates = get_candidates(state, "merge")
        run, task = select_task_interactively(candidates, "merge")
        task_id_for_print = f"{run.id}:{task.id}"

    # sync first
    sync_task_state(client, github_client, state, run, task, cwd)

    if task.status not in ("pr_created", "waiting_human_review"):
        parser.exit(1, f"Error: Task {task_id_for_print} is in status {task.status!r}, but 'pr_created' or 'waiting_human_review' is required to merge.\n")

    if not task.pull_request or not task.pull_request.url:
        parser.exit(1, f"Error: Task {task_id_for_print} does not have an associated pull request URL.\n")

    pull_number = extract_pull_request_number(task.pull_request.url)
    if pull_number is None:
        parser.exit(1, f"Error: Could not extract pull request number from {task.pull_request.url}.\n")

    repo = state.project.repo
    print(f"Checking mergeability for PR #{pull_number} in {repo}...")

    try:
        pr_details = github_client.get_pull_request(repo, pull_number)
    except Exception as e:
        parser.exit(1, f"Error: Failed to fetch PR details: {e}\n")

    if not pr_details.get("mergeable"):
        parser.exit(1, f"Error: PR #{pull_number} is not mergeable at this time.\n")

    merge_method = args.merge_method or config.merge_method or "merge"

    print(f"Merging PR #{pull_number} using {merge_method} strategy...")
    try:
        github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
    except Exception as e:
        parser.exit(1, f"Error: Failed to merge PR: {e}\n")

    task.status = "merged"
    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Successfully merged and updated state.")

    # Post-merge cleanup
    # Use flag from args if specified, otherwise fall back to config
    delete_branch = getattr(args, "delete_branch", None)
    if delete_branch is None:
        delete_branch = config.merge_delete_branch

    pull_after_merge = getattr(args, "pull", None)
    if pull_after_merge is None:
        pull_after_merge = config.merge_pull

    if delete_branch or pull_after_merge:
        head_branch = pr_details.get("head", {}).get("ref")
        base_branch = pr_details.get("base", {}).get("ref")

        if not head_branch or not base_branch:
            print("Warning: Could not determine branches for post-merge cleanup.")
            return 0

        current_branch = get_git_branch(cwd)

        if pull_after_merge:
            print(f"Switching to {base_branch} and pulling latest changes...")
            res = run_command(["git", "checkout", base_branch], cwd=cwd)
            if res.returncode != 0:
                print(f"Error: Failed to checkout {base_branch}: {res.stderr.strip()}")
                return 0
            res = run_command(["git", "pull"], cwd=cwd)
            if res.returncode != 0:
                print(f"Error: Failed to pull latest changes: {res.stderr.strip()}")
                return 0
            current_branch = base_branch

        if delete_branch:
            if current_branch == head_branch:
                print(f"Switching to {base_branch} before deleting {head_branch}...")
                res = run_command(["git", "checkout", base_branch], cwd=cwd)
                if res.returncode != 0:
                    print(f"Error: Failed to checkout {base_branch} before deletion: {res.stderr.strip()}")
                    return 0
                current_branch = base_branch

            print(f"Deleting local branch {head_branch}...")
            # Use -d first, as it should be merged. Fallback to -D if requested or necessary?
            # Existing CLI style is minimal, so we stick to -d.
            res = run_command(["git", "branch", "-d", head_branch], cwd=cwd)
            if res.returncode != 0:
                print(f"Warning: Failed to delete local branch {head_branch}: {res.stderr.strip()}")

            # Remote deletion
            head_repo = (pr_details.get("head", {}).get("repo") or {}).get("full_name")
            if head_repo == repo:
                print(f"Deleting remote branch {head_branch}...")
                res = run_command(["git", "push", "origin", "--delete", head_branch], cwd=cwd)
                if res.returncode != 0:
                    print(f"Warning: Failed to delete remote branch {head_branch}: {res.stderr.strip()}")
            else:
                print(f"Skipping remote branch deletion for cross-repo PR (head: {head_repo})")

    return 0
