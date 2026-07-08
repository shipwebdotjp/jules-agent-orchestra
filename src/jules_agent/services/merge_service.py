from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from ..client import JulesClient
from ..config import Config
from ..github import GitHubClient
from ..models import State, Run, Task
from ..persistence import save_state
from ..git import CommandRunner, run_command, get_git_branch
from ..cli.state import extract_pull_request_number
from .options import Options
from .results import OperationResult

@dataclass
class MergeOptions(Options):
    run: Run
    task: Task
    task_id_for_print: str
    merge_method: str | None = None
    delete_branch: bool | None = None
    pull: bool | None = None
    output_func: Callable[[str], None] = print

class MergeService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        github_client: GitHubClient,
        cwd: Path,
        config: Config,
        runner: CommandRunner = run_command,
    ):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd
        self.config = config
        self.runner = runner

    def execute(self, options: MergeOptions) -> OperationResult:
        task = options.task
        task_id_for_print = options.task_id_for_print
        output = options.output_func

        if task.status not in ("pr_created", "review_passed", "waiting_human_review", "needs_fix"):
            return OperationResult(
                exit_code=1,
                message=f"Error: Task {task_id_for_print} is in status {task.status!r}, but 'pr_created', 'review_passed', 'waiting_human_review', or 'needs_fix' is required to merge."
            )

        if not task.pull_request or not task.pull_request.url:
            return OperationResult(
                exit_code=1,
                message=f"Error: Task {task_id_for_print} does not have an associated pull request URL."
            )

        pull_number = extract_pull_request_number(task.pull_request.url)
        if pull_number is None:
            return OperationResult(
                exit_code=1,
                message=f"Error: Could not extract pull request number from {task.pull_request.url}."
            )

        repo = self.state.project.repo
        try:
            pr_details = self.github_client.get_pull_request(repo, pull_number)
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to fetch PR details: {e}")

        if not pr_details.get("mergeable"):
            return OperationResult(exit_code=1, message=f"Error: PR #{pull_number} is not mergeable at this time.")

        merge_method = options.merge_method or self.config.merge_method or "merge"

        try:
            self.github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to merge PR: {e}")

        task.status = "merged"
        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

        # Post-merge cleanup
        output("Successfully merged PR.")
        delete_branch = options.delete_branch
        if delete_branch is None:
            delete_branch = self.config.merge_delete_branch

        pull_after_merge = options.pull
        if pull_after_merge is None:
            pull_after_merge = self.config.merge_pull

        if delete_branch or pull_after_merge:
            self._cleanup(pr_details, delete_branch, pull_after_merge)

        return OperationResult(exit_code=0, message="Successfully merged and updated state.")

    def _cleanup(self, pr_details: dict, delete_branch: bool, pull_after_merge: bool):
        repo = self.state.project.repo
        head_branch = pr_details.get("head", {}).get("ref")
        base_branch = pr_details.get("base", {}).get("ref")

        if not head_branch or not base_branch:
            return

        current_branch = get_git_branch(self.cwd)

        if pull_after_merge:
            res = self.runner(["git", "checkout", base_branch], cwd=self.cwd)
            if res.returncode == 0:
                self.runner(["git", "pull"], cwd=self.cwd)
                current_branch = base_branch

        if delete_branch:
            if current_branch == head_branch:
                res = self.runner(["git", "checkout", base_branch], cwd=self.cwd)
                if res.returncode == 0:
                    current_branch = base_branch

            # Local deletion
            self.runner(["git", "branch", "-d", head_branch], cwd=self.cwd)

            # Remote deletion
            head_repo = (pr_details.get("head", {}).get("repo") or {}).get("full_name")
            if head_repo == repo:
                self.runner(["git", "push", "origin", "--delete", head_branch], cwd=self.cwd)
