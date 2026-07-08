from __future__ import annotations

import argparse
import datetime
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...github import GitHubClient
from ...models import State, TaskReview
from ...persistence import save_state
from ..io import select_task_interactively
from ...codex import OperationError
from ..state import get_candidates, resolve_task, sync_task_state
from ...utils import extract_pull_request_number


def handle_review_pass(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    github_client: GitHubClient | None,
    cwd: Path,
    config: Any = None,
) -> int:
    if not github_client:
        raise OperationError(1, "Error: GITHUB_TOKEN is required for review-pass.")

    if args.task_id:
        run, task = resolve_task(state, args.task_id)
    else:
        candidates = get_candidates(state, "merge")
        if not candidates:
            print("No tasks found eligible for manual review pass.")
            return 0
        run, task = select_task_interactively(candidates, "review-pass")

    sync_task_state(client, github_client, state, run, task, cwd)

    if task.status in ("merged", "pr_closed"):
        print(f"Task {task.id} is already in {task.status} status. Skipping review pass.")
        return 0

    if not task.pull_request or not task.pull_request.url:
         raise OperationError(1, f"Error: Task {task.id} has no pull request.")

    pull_number = extract_pull_request_number(task.pull_request.url)
    if not pull_number:
         raise OperationError(1, f"Error: Could not extract PR number from {task.pull_request.url}")

    repo = state.project.repo
    pr_data = github_client.get_pull_request(repo, pull_number)
    head_sha = pr_data.get("head", {}).get("sha")

    if not head_sha:
         raise OperationError(1, "Error: Could not determine current head SHA.")

    if not task.review:
        task.review = TaskReview()

    task.review.passed_head_sha = head_sha
    task.status = "review_passed"
    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    save_state(cwd, state)
    print(f"Task {task.id} marked as review_passed for head SHA {head_sha}.")

    return 0
