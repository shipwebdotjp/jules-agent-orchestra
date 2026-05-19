from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from ..client import JulesClient
from ..config import Config
from ..github import GitHubClient
from ..models import State, Task, TaskStatus
from ..persistence import save_state
from .state import sync_task, extract_pull_request_number


class AdvanceEngine:
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
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd
        self.config = config
        self.args = args
        self.interactive = interactive and sys.stdin.isatty()
        self.output_json = getattr(args, "json", False)

    def run(self) -> int:
        lock_path = self.cwd / ".jules-agent" / "advance.lock"
        if lock_path.exists():
            if not self.output_json:
                print("Advance lock already held. Skipping.")
            return 0

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_path.touch(exist_ok=False)
        except FileExistsError:
            if not self.output_json:
                print("Advance lock already held. Skipping.")
            return 0

        try:
            return self._execute()
        finally:
            if lock_path.exists():
                lock_path.unlink()

    def _execute(self) -> int:
        target_task = self._select_task()
        if not target_task:
            if self.output_json:
                print(json.dumps({"status": "no_tasks", "action": None}))
            else:
                print("No tasks found that require advancement.")
            return 0

        if not self.output_json:
            print(f"Advancing task: {target_task.id} - {target_task.title} (Status: {target_task.status})")

        # Determine auto flags with precedence: Config -> --auto -> explicit flag
        # Rule: --auto enables plan approval and feedback, not merge.
        # Rule: Explicit flag overrides everything.

        # 1. Start with Config
        auto_plan_approval = getattr(self.config, "auto_plan_approval", False)
        auto_feedback = getattr(self.config, "auto_feedback", False)
        auto_merge = getattr(self.config, "auto_merge", False)

        # 2. Apply --auto
        if getattr(self.args, "auto", False):
            auto_plan_approval = True
            auto_feedback = True

        # 3. Apply explicit flags (assume they are None if not set on CLI)
        if getattr(self.args, "auto_plan_approval", None) is not None:
            auto_plan_approval = self.args.auto_plan_approval
        if getattr(self.args, "auto_feedback", None) is not None:
            auto_feedback = self.args.auto_feedback
        if getattr(self.args, "auto_merge", None) is not None:
            auto_merge = self.args.auto_merge

        action_taken = False
        action_name = None

        if target_task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
            from .commands.feedback import run_feedback_loop
            outcome = run_feedback_loop(
                target_task,
                cwd=self.cwd,
                client=self.client,
                codex_bin=self.config.codex_bin,
                auto_plan_approval=auto_plan_approval,
                auto_feedback=auto_feedback,
                allow_skip=True,
                interactive=self.interactive,
            )
            if outcome == "completed":
                action_taken = True
                action_name = "feedback_provided"
            elif outcome == "skipped":
                action_name = "skipped"
                # In non-interactive mode, a skip means human judgment is needed.
                # Mark as blocked to avoid selection loop.
                if not self.interactive:
                    target_task.status = "blocked"
                    action_taken = True # Status changed, so we should save

        elif target_task.status in ("pr_created", "waiting_human_review"):
            if auto_merge:
                if self._attempt_merge(target_task):
                    action_taken = True
                    action_name = "merged"
                else:
                    action_name = "merge_failed"
                    if not self.interactive:
                        target_task.status = "blocked"
                        action_taken = True
            else:
                action_name = "manual_merge_required"
                if not self.interactive:
                    target_task.status = "blocked"
                    action_taken = True

        if action_taken:
            # Sync to get latest state from server before saving
            if sync_task(self.client, target_task):
                target_task.updated_at = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                save_state(self.cwd, self.state)
            else:
                if not self.output_json:
                    print(f"Failed to sync task {target_task.id} after action.")

        if self.output_json:
            print(json.dumps({
                "task_id": target_task.id,
                "status": target_task.status,
                "action": action_name,
                "action_taken": action_taken
            }))

        return 0

    def _select_task(self) -> Task | None:
        ADVANCEABLE_STATUSES: set[TaskStatus] = {
            "awaiting_plan_approval",
            "awaiting_user_feedback",
            "pr_created",
            "waiting_human_review",
        }

        eligible_tasks: list[Task] = []
        for run in self.state.runs:
            for task in run.tasks:
                if task.status in ADVANCEABLE_STATUSES:
                    eligible_tasks.append(task)

        if not eligible_tasks:
            return None

        # Sort by updated_at (descending) then id (ascending) for stable tie-breaking
        eligible_tasks.sort(key=lambda t: t.id) # stable tie-breaker
        eligible_tasks.sort(key=lambda t: t.updated_at, reverse=True)

        return eligible_tasks[0]

    def _attempt_merge(self, task: Task) -> bool:
        if not self.github_client:
            if not self.output_json:
                print(f"Skipping merge for {task.id}: GITHUB_TOKEN not set.")
            return False

        if not task.pull_request or not task.pull_request.url:
            return False

        # Verify PR URL matches repository
        repo = self.state.project.repo
        if f"github.com/{repo}/" not in task.pull_request.url:
            if not self.output_json:
                print(f"Skipping merge for {task.id}: PR URL {task.pull_request.url} does not match repo {repo}.")
            return False

        pull_number = extract_pull_request_number(task.pull_request.url)
        if pull_number is None:
            return False

        try:
            pr = self.github_client.get_pull_request(repo, pull_number)
        except Exception:
            return False

        if pr.get("merged"):
            task.status = "merged"
            return True

        if pr.get("state") != "open" or pr.get("draft"):
            return False

        if not pr.get("mergeable"):
            return False

        merge_method = getattr(self.args, "merge_method", None) or self.config.merge_method or "merge"

        try:
            self.github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
            task.status = "merged"
            return True
        except Exception as e:
            if not self.output_json:
                print(f"Failed to merge PR #{pull_number}: {e}")
            return False
