from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...config import Config
from ...github import GitHubClient
from ...models import State
from ...persistence import save_state
from ..state import resolve_task, extract_pull_request_number


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

    _run, task = resolve_task(state, args.task_id)

    if task.status != "pr_created":
        parser.exit(1, f"Error: Task {args.task_id} is in status {task.status!r}, but 'pr_created' is required to merge.\n")

    if not task.pull_request or not task.pull_request.url:
        parser.exit(1, f"Error: Task {args.task_id} does not have an associated pull request URL.\n")

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
    return 0
