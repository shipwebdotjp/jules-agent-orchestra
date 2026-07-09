from __future__ import annotations

import datetime
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Callable

try:
    import fcntl
except ImportError:
    fcntl = None

from ..client import JulesClient
from ..config import Config
from ..github import GitHubClient
from ..models import State, Task, TaskStatus, Run, JulesSessionInfo
from ..persistence import save_state
from ..pipeline import find_source_name, perform_task_review
from ..git import get_git_branch
from ..cli.state import (
    get_candidates,
    sync_task,
    extract_pull_request_number,
    sync_task_state,
)
from .state_utils import (
    get_jules_state_mapping,
    resolve_dispatch_options,
    update_run_status_after_task_change,
)
from ..codex import resolve_tool_for_phase
from .options import Options
from .results import OperationResult
from .feedback_service import FeedbackService, FeedbackOptions

logger = logging.getLogger("jules_agent")

@dataclass
class AdvanceOptions(Options):
    interactive: bool = True
    output_json: bool = False
    args: Any = None # Needed for resolve_tool_for_phase
    output_func: Callable[[str], None] = print

class AdvanceService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        github_client: GitHubClient | None,
        cwd: Path,
        config: Config,
    ):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd
        self.config = config

    def execute(self, options: AdvanceOptions) -> OperationResult:
        lock_path = self.cwd / ".jules-agent" / "advance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        output = options.output_func

        with open(lock_path, "a") as lock_file:
            if fcntl is not None:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (IOError, OSError):
                    if not options.output_json:
                        output("Advance lock already held. Skipping.")
                    return OperationResult(exit_code=0)

            return self._execute_advance(options)

    def _execute_advance(self, options: AdvanceOptions) -> OperationResult:
        output = options.output_func
        selected = self._select_task()
        if not selected:
            if options.output_json:
                output(json.dumps({"status": "no_tasks", "action": None, "exit_code": 0}))
            else:
                output("No tasks found that require advancement.")
            return OperationResult(exit_code=0)

        target_run, target_task = selected
        previous_status = target_task.status

        if target_task.status == "planned":
            self.dispatch_task_logic(
                task=target_task,
                run=target_run,
                args=options.args,
                verbose=not options.output_json,
                output_func=output,
            )

            if options.output_json:
                output(json.dumps({
                    "status": target_task.status,
                    "action": "dispatched_planned",
                    "run_id": target_run.id,
                    "task_id": target_task.id,
                    "previous_status": previous_status,
                    "next_status": target_task.status,
                    "reason": None,
                    "exit_code": 0,
                }))
            return OperationResult(exit_code=0)

        if not options.output_json:
            output(f"Advancing task: {target_task.id} - {target_task.title} (Status: {target_task.status})")

        auto_plan_approval = getattr(self.config, "auto_plan_approval", True)
        auto_feedback = getattr(self.config, "auto_feedback", False)
        auto_merge = getattr(self.config, "auto_merge", False)
        skip_review = getattr(self.config, "skip_review", False)

        if getattr(options.args, "auto", False):
            auto_plan_approval = True
            auto_feedback = True

        if getattr(options.args, "auto_plan_approval", None) is not None:
            auto_plan_approval = options.args.auto_plan_approval
        if getattr(options.args, "auto_feedback", None) is not None:
            auto_feedback = options.args.auto_feedback
        if getattr(options.args, "auto_merge", None) is not None:
            auto_merge = options.args.auto_merge
        if getattr(options.args, "skip_review", None) is not None:
            skip_review = options.args.skip_review

        action_taken = False
        action_name = None
        exit_code = 0
        reason = None

        try:
            if target_task.status in ("awaiting_plan_approval", "awaiting_user_feedback"):
                phase = "approve" if target_task.status == "awaiting_plan_approval" else "feedback"
                tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase(
                    phase, self.config, options.args
                )

                feedback_service = FeedbackService(self.state, self.client, self.cwd)
                feedback_options = FeedbackOptions(
                    task=target_task,
                    tool_name=tool_name,
                    tool_bin=tool_bin,
                    gemini_skip_trust=gemini_skip_trust,
                    auto_plan_approval=auto_plan_approval,
                    auto_feedback=auto_feedback,
                    allow_skip=True,
                    interactive=options.interactive,
                    output_func=output,
                )
                res = feedback_service.execute(feedback_options)
                outcome = res.data
                if outcome == "completed":
                    action_taken = True
                    action_name = "feedback_provided"
                elif outcome == "skipped":
                    action_name = "skipped"
                    if not options.interactive:
                        target_task.status = "blocked"
                        action_taken = True
                        reason = "Human judgment required"
                elif outcome == "failed":
                    action_name = "feedback_failed"
                    exit_code = 2

            elif target_task.status in (
                "pr_created",
                "reviewing",
                "review_passed",
                "needs_fix",
                "waiting_human_review",
            ):
                sync_task_state(
                    self.client,
                    self.github_client,
                    self.state,
                    target_run,
                    target_task,
                    self.cwd,
                )

                if target_task.status == "merged":
                    action_taken = True
                    action_name = "already_merged"
                else:
                    # Sha check and reset
                    if target_task.pull_request and self.github_client:
                        pull_number = extract_pull_request_number(target_task.pull_request.url)
                        if pull_number:
                            pr_data = self.github_client.get_pull_request(
                                self.state.project.repo, pull_number
                            )
                            head_sha = pr_data.get("head", {}).get("sha")
                            if head_sha:
                                needs_re_review = False
                                if target_task.status == "review_passed":
                                    if not target_task.review or target_task.review.passed_head_sha != head_sha:
                                        needs_re_review = True
                                elif target_task.status in ("needs_fix", "waiting_human_review"):
                                    if target_task.review and target_task.review.attempts:
                                        last_reviewed_sha = target_task.review.attempts[-1].head_sha
                                        if last_reviewed_sha != head_sha:
                                            needs_re_review = True
                                if needs_re_review:
                                    target_task.status = "reviewing"
                                    target_task.attempts = 0
                                    action_taken = True
                                    action_name = "re_review_triggered"

                    if (target_task.status == "pr_created" and not skip_review) or target_task.status == "reviewing":
                        tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase(
                            "review", self.config, options.args
                        )
                        try:
                            perform_task_review(
                                task=target_task,
                                state=self.state,
                                github_client=self.github_client,
                                cwd=self.cwd,
                                tool_name=tool_name,
                                tool_bin=tool_bin,
                                gemini_skip_trust=gemini_skip_trust,
                            )
                            action_taken = True
                            action_name = "reviewed"
                        except Exception as e:
                            action_name = "review_failed"
                            reason = str(e)
                            if not options.interactive:
                                target_task.status = "blocked"
                                action_taken = True
                            else:
                                exit_code = 2

                    if target_task.status in (
                        "pr_created",
                        "review_passed",
                        "waiting_human_review",
                    ):
                        if auto_merge:
                            merge_result = self._attempt_merge(target_task, options, skip_review=skip_review)
                            if merge_result is True:
                                action_taken = True
                                action_name = "merged"
                            elif merge_result is False:
                                action_name = "merge_failed"
                                if not options.interactive and target_task.status != "review_passed" and not skip_review:
                                    target_task.status = "blocked"
                                    action_taken = True
                                    reason = "Merge conditions not met"
                            else:
                                action_name = "merge_error"
                                exit_code = 2
                        else:
                            if not options.interactive and target_task.status == "review_passed":
                                target_task.status = "blocked"
                                action_taken = True
                                action_name = "manual_merge_required"
                                reason = "Auto-merge disabled"

        except Exception as e:
            action_name = "error"
            reason = str(e)
            exit_code = 2

        if action_taken:
            target_task.updated_at = (
                (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z")
            )
            local_status = target_task.status
            sync_task(self.client, target_task)
            if local_status in ("blocked", "merged"):
                target_task.status = local_status
            update_run_status_after_task_change(target_run)
            save_state(self.cwd, self.state)

            if action_name == "merged" and target_run.strategy == "sequential_subtasks":
                self._dispatch_next_planned(target_run, options)

        if options.output_json:
            output(json.dumps({
                "status": target_task.status,
                "action": action_name,
                "run_id": target_run.id,
                "task_id": target_task.id,
                "previous_status": previous_status,
                "next_status": target_task.status,
                "reason": reason,
                "exit_code": exit_code
            }))

        return OperationResult(exit_code=exit_code)

    def _select_task(self) -> tuple[Run, Task] | None:
        ADVANCEABLE_STATUSES: set[TaskStatus] = {
            "awaiting_plan_approval",
            "awaiting_user_feedback",
            "pr_created",
            "reviewing",
            "review_passed",
            "needs_fix",
            "waiting_human_review",
        }

        eligible_tasks: list[tuple[Run, Task, int]] = []
        counter = 0
        for run in self.state.runs:
            for task in run.tasks:
                if task.status in ADVANCEABLE_STATUSES:
                    eligible_tasks.append((run, task, counter))
                counter += 1

        if not eligible_tasks:
            planned_candidates = get_candidates(self.state, "next")
            if not planned_candidates:
                return None
            return planned_candidates[0]

        eligible_tasks.sort(key=lambda x: (x[1].updated_at, -x[2]), reverse=True)
        return eligible_tasks[0][0], eligible_tasks[0][1]

    def _dispatch_next_planned(self, run: Run, options: AdvanceOptions) -> None:
        if run.strategy != "sequential_subtasks" or run.status != "running":
            return

        next_task = None
        for task in run.tasks:
            if task.status == "planned":
                prior_done = True
                for t in run.tasks:
                    if t.id == task.id:
                        break
                    if t.status not in ("completed", "merged"):
                        prior_done = False
                        break
                if prior_done:
                    next_task = task
                break

        if not next_task:
            return

        self.dispatch_task_logic(
            task=next_task,
            run=run,
            args=options.args,
            verbose=not options.output_json,
            output_func=options.output_func,
        )

    def dispatch_task_logic(
        self,
        task: Task,
        run: Run,
        args: Any,
        verbose: bool = True,
        output_func: Callable[[str], None] = print,
    ) -> None:
        source_name = find_source_name(self.client, self.state.project.repo)
        starting_branch = get_git_branch(self.cwd)
        automation_mode, require_plan_approval = resolve_dispatch_options(
            run, self.config, args
        )

        if verbose:
            output_func(f"Dispatching next task: {task.id} - {task.title}")
        task.status = "dispatching"
        save_state(self.cwd, self.state)
        try:
            session = self.client.create_session(
                prompt=task.prompt or task.title,
                source_name=source_name,
                starting_branch=starting_branch,
                title=task.title,
                require_plan_approval=require_plan_approval,
                automation_mode=automation_mode,
            )
            task.jules = JulesSessionInfo(
                session_id=session["id"],
                session_name=session["name"],
                state=session.get("state", "QUEUED"),
                session_url=session.get("url"),
                create_time=session.get("createTime"),
                update_time=session.get("updateTime"),
            )
            task.status = get_jules_state_mapping(task.jules.state, False)
            if verbose:
                output_func(f"  Success: {task.jules.session_url}")
        except Exception as e:
            task.status = "failed"
            if verbose:
                logger.error(f"  Failed: {e}")

        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        save_state(self.cwd, self.state)

    def _attempt_merge(self, task: Task, options: AdvanceOptions, skip_review: bool = False) -> bool | None:
        output = options.output_func
        if not self.github_client:
            if not options.output_json:
                output(f"Skipping merge for {task.id}: GITHUB_TOKEN not set.")
            return False

        if not task.pull_request or not task.pull_request.url:
            return False

        repo = self.state.project.repo
        if f"github.com/{repo}/" not in task.pull_request.url:
            if not options.output_json:
                output(f"Skipping merge for {task.id}: PR URL {task.pull_request.url} does not match repo {repo}.")
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
            task.advance_state["last_advance_action"] = "merged"
            return True

        if pr.get("state") != "open" or pr.get("draft"):
            return False

        if not pr.get("mergeable"):
            return False

        head_sha = pr.get("head", {}).get("sha")
        can_merge = False
        if task.status == "review_passed":
            if task.review and task.review.passed_head_sha == head_sha:
                can_merge = True

        if not can_merge and skip_review:
            if task.status in ("pr_created", "waiting_human_review"):
                can_merge = True

        if not can_merge:
            return False

        merge_method = getattr(options.args, "merge_method", None) or self.config.merge_method or "merge"
        try:
            self.github_client.merge_pull_request(repo, pull_number, merge_method=merge_method)
            task.status = "merged"
            task.advance_state["last_advance_action"] = "merged"
            return True
        except Exception as e:
            if not options.output_json:
                output(f"Failed to merge PR #{pull_number}: {e}")
            raise
