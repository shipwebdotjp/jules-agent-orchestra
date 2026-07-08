from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from ..client import JulesClient
from ..models import State, Task
from ..pipeline import suggest_reply
from ..codex import PipelineError, display_tool_name
from ..git import CommandRunner, run_command
from ..persistence import save_state
from .state_utils import sync_task
from .options import Options
from .results import OperationResult

FeedbackOutcome = Literal["completed", "skipped", "failed"]

@dataclass
class FeedbackOptions(Options):
    task: Task
    tool_name: str = "codex"
    tool_bin: Optional[str] = None
    gemini_skip_trust: bool = False
    auto_plan_approval: bool = False
    auto_feedback: bool = False
    allow_skip: bool = False
    interactive: bool = True
    input_func: Callable[[str], str] = input
    output_func: Callable[[str], None] = print

class FeedbackService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        cwd: Path,
        runner: CommandRunner = run_command,
    ):
        self.state = state
        self.client = client
        self.cwd = cwd
        self.runner = runner

    def execute(self, options: FeedbackOptions) -> OperationResult:
        task = options.task
        if not task.jules:
            return OperationResult(exit_code=1, message="Error: Task has no Jules session info.")

        tool_label = display_tool_name(options.tool_name)
        feedback_history: list[str] = []
        first_iteration = True
        output = options.output_func
        input_func = options.input_func

        while True:
            if not sync_task(self.client, task):
                if options.interactive:
                    output("Error: Failed to sync task state. Please check your connection and try again.")
                return OperationResult(exit_code=1, message="failed")

            is_awaiting_plan_approval = task.status == "awaiting_plan_approval"

            if options.interactive:
                output(f"\nFetching suggestion from {tool_label}...")

            try:
                activities = list(self.client.list_activities(task.jules.session_name))
                result = suggest_reply(
                    task.prompt or task.title,
                    activities,
                    feedback_history,
                    cwd=self.cwd,
                    is_awaiting_plan_approval=is_awaiting_plan_approval,
                    tool_name=options.tool_name,
                    tool_bin=options.tool_bin,
                    gemini_skip_trust=options.gemini_skip_trust,
                    runner=self.runner,
                )
            except Exception as e:
                output(f"Error fetching suggestion: {e}")
                return OperationResult(exit_code=1, message="failed")

            suggestion = result["suggestion"]
            explanation = result["explanation"]
            approval_recommended = result.get("approval_recommended", False)

            latest_activity = activities[-1] if activities else {}
            activity_id = latest_activity.get("id") or latest_activity.get("name")
            suggestion_hash = hashlib.sha256(suggestion.encode("utf-8")).hexdigest()

            def mark_advanced(action: str, feedback_hash: str | None = None):
                task.advance_state["last_activity_id"] = activity_id
                task.advance_state["last_advance_action"] = action
                task.advance_state["last_advanced_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                if feedback_hash:
                    task.advance_state["last_feedback_hash"] = feedback_hash

            if first_iteration:
                last_activity_id = task.advance_state.get("last_activity_id")
                last_feedback_hash = task.advance_state.get("last_feedback_hash")
                last_advance_action = task.advance_state.get("last_advance_action")

                if is_awaiting_plan_approval and options.auto_plan_approval:
                    if approval_recommended:
                        if last_activity_id == activity_id and last_advance_action == "approve_plan":
                            if options.interactive:
                                output("Plan already approved for this activity. Skipping.")
                            self._update_task_and_save(task)
                            return OperationResult(exit_code=0, data="completed")

                        if options.interactive:
                            output(f"Auto-approving plan as recommended by {tool_label}...")
                        self.client.approve_plan(task.jules.session_name)
                        task.status = "plan_approved"
                        mark_advanced("approve_plan")
                        self._update_task_and_save(task)
                        return OperationResult(exit_code=0, data="completed")
                    else:
                        if options.interactive:
                            output(f"{tool_label} does not recommend auto-approval. Falling back to interactive mode.")
                elif not is_awaiting_plan_approval and options.auto_feedback:
                    if last_activity_id == activity_id and last_feedback_hash == suggestion_hash:
                        if options.interactive:
                            output("Feedback already sent for this activity. Skipping.")
                        self._update_task_and_save(task)
                        return OperationResult(exit_code=0, data="completed")

                    if options.interactive:
                        output(f"Sending auto-reply:\n{suggestion}")
                    self.client.send_message(task.jules.session_name, suggestion)
                    mark_advanced("send_message", suggestion_hash)
                    self._update_task_and_save(task)
                    return OperationResult(exit_code=0, data="completed")

                first_iteration = False

            if not options.interactive:
                output("Non-interactive mode: human judgment needed.")
                return OperationResult(exit_code=0, data="skipped")

            output("-" * 40)
            output(f"Explanation: {explanation}")
            if is_awaiting_plan_approval:
                rec_str = "YES" if approval_recommended else "NO"
                output(f"Approval recommended: {rec_str}")
            output("-" * 40)
            output(f"Suggested message:\n{suggestion}")
            output("-" * 40)

            while True:
                try:
                    if is_awaiting_plan_approval:
                        choices = "y/f/m/s" if options.allow_skip else "y/f/m"
                        if approval_recommended:
                            prompt_msg = f"\nApprove the plan as recommended? (y), provide feedback (f), write manual message (m){', or skip (s)' if options.allow_skip else ''}? [{choices}]: "
                        else:
                            prompt_msg = f"\nApprove suggestion (y), provide feedback (f), write manual message (m){', or skip (s)' if options.allow_skip else ''}? [{choices}]: "
                    else:
                        choices = "y/f/m/s" if options.allow_skip else "y/f/m"
                        prompt_msg = f"\nSend suggestion (y), provide feedback (f), write manual message (m){', or skip (s)' if options.allow_skip else ''}? [{choices}]: "

                    answer = input_func(prompt_msg).strip().lower()
                except EOFError:
                    return OperationResult(exit_code=1, message="Feedback loop needs interactive input.")

                if answer in {"y", "yes"}:
                    if is_awaiting_plan_approval and approval_recommended:
                        output("Approving plan...")
                        self.client.approve_plan(task.jules.session_name)
                        task.status = "plan_approved"
                        mark_advanced("approve_plan")
                    else:
                        output("Sending message to Jules...")
                        self.client.send_message(task.jules.session_name, suggestion)
                        mark_advanced("send_message", suggestion_hash)
                    self._update_task_and_save(task)
                    return OperationResult(exit_code=0, data="completed")
                elif answer == "f":
                    try:
                        feedback = input_func("Feedback for revision: ").strip()
                    except EOFError:
                        return OperationResult(exit_code=1, message="Feedback input was closed.")
                    if feedback:
                        feedback_history.append(feedback)
                        output("Revising suggestion...")
                        break
                    output("Feedback cannot be empty.")
                elif answer == "m":
                    try:
                        output("Enter your message to Jules (Enter a blank line to finish):")
                        lines = []
                        while True:
                            line = input_func("> ")
                            if not line:
                                break
                            lines.append(line)
                        message = "\n".join(lines).strip()
                    except EOFError:
                        return OperationResult(exit_code=1, message="Manual message input was closed.")
                    if message:
                        output("Sending manual message to Jules...")
                        self.client.send_message(task.jules.session_name, message)
                        self._update_task_and_save(task)
                        return OperationResult(exit_code=0, data="completed")
                    output("Message cannot be empty.")
                elif options.allow_skip and answer == "s":
                    output("Skipping task.")
                    return OperationResult(exit_code=0, data="skipped")
                else:
                    output(f"Please answer with {choices}.")

    def _update_task_and_save(self, task: Task):
        task.updated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if self.state and self.state.project and self.cwd:
            save_state(self.cwd, self.state)
