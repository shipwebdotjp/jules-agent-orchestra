from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from ..client import JulesClient
from ..github import GitHubClient
from ..models import State, Task, TaskReview
from ..persistence import save_state
from ..cli.state import extract_pull_request_number
from .options import Options
from .results import OperationResult

@dataclass
class ReviewPassOptions(Options):
    task: Task

class ReviewPassService:
    def __init__(self, state: State, client: JulesClient, github_client: GitHubClient, cwd: Path):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd

    def execute(self, options: ReviewPassOptions) -> OperationResult:
        task = options.task

        if task.status in ("merged", "pr_closed"):
            return OperationResult(exit_code=0, message=f"Task {task.id} is already in {task.status} status. Skipping review pass.")

        if not task.pull_request or not task.pull_request.url:
            return OperationResult(exit_code=1, message=f"Error: Task {task.id} has no pull request.")

        pull_number = extract_pull_request_number(task.pull_request.url)
        if not pull_number:
            return OperationResult(exit_code=1, message=f"Error: Could not extract PR number from {task.pull_request.url}")

        repo = self.state.project.repo
        try:
            pr_data = self.github_client.get_pull_request(repo, pull_number)
            head_sha = pr_data.get("head", {}).get("sha")
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to fetch PR details: {e}")

        if not head_sha:
            return OperationResult(exit_code=1, message="Error: Could not determine current head SHA.")

        if not task.review:
            task.review = TaskReview()

        task.review.passed_head_sha = head_sha
        task.status = "review_passed"
        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        save_state(self.cwd, self.state)
        return OperationResult(exit_code=0, message=f"Task {task.id} marked as review_passed for head SHA {head_sha}.")
