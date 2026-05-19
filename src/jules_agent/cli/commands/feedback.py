from __future__ import annotations

import argparse
import datetime
import hashlib
from pathlib import Path
from typing import Literal

from ...client import JulesClient
from ...models import State, Task
from ...pipeline import (
    suggest_reply,
)
from ...codex import PipelineError
from ...git import CommandRunner, run_command
from ...persistence import save_state
from ..state import resolve_task, sync_task

FeedbackOutcome = Literal["completed", "skipped", "failed"]

def run_feedback_loop(
    task: Task,
    *,
    cwd: Path,
    client: JulesClient,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
    auto_plan_approval: bool = False,
    auto_feedback: bool = False,
    allow_skip: bool = False,
    interactive: bool = True,
) -> FeedbackOutcome:
    if not task.jules:
        raise PipelineError("Task has no Jules session info.")

    feedback_history: list[str] = []
    first_iteration = True

    while True:
        if not sync_task(client, task):
            if interactive:
                output(
                    "Error: Failed to sync task state. Please check your connection and try again."
                )
            return "failed"

        is_awaiting_plan_approval = task.status == "awaiting_plan_approval"

        if interactive:
            output("\nFetching suggestion from Codex...")
        try:
            activities = list(client.list_activities(task.jules.session_name))
            result = suggest_reply(
                task.prompt or task.title,
                activities,
                feedback_history,
                cwd=cwd,
                is_awaiting_plan_approval=is_awaiting_plan_approval,
                codex_bin=codex_bin,
                runner=runner,
            )
        except Exception as e:
            output(f"Error fetching suggestion: {e}")
            return "failed"

        suggestion = result["suggestion"]
        explanation = result["explanation"]
        approval_recommended = result.get("approval_recommended", False)

        # Idempotency check using advance_state
        latest_activity = activities[-1] if activities else {}
        activity_id = latest_activity.get("id") or latest_activity.get("name")
        suggestion_hash = hashlib.sha256(suggestion.encode("utf-8")).hexdigest()

        if first_iteration:
            last_activity_id = task.advance_state.get("last_activity_id")
            last_feedback_hash = task.advance_state.get("last_feedback_hash")
            last_advance_action = task.advance_state.get("last_advance_action")

            if is_awaiting_plan_approval and auto_plan_approval:
                if approval_recommended:
                    if last_activity_id == activity_id and last_advance_action == "approve_plan":
                        if interactive:
                            output("Plan already approved for this activity. Skipping.")
                        return "completed"

                    if interactive:
                        output("Auto-approving plan as recommended by Codex...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                    task.advance_state["last_activity_id"] = activity_id
                    task.advance_state["last_advance_action"] = "approve_plan"
                    task.advance_state["last_advanced_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    return "completed"
                else:
                    if interactive:
                        output("Codex does not recommend auto-approval. Falling back to interactive mode.")
            elif not is_awaiting_plan_approval and auto_feedback:
                if last_activity_id == activity_id and last_feedback_hash == suggestion_hash:
                    if interactive:
                        output("Feedback already sent for this activity. Skipping.")
                    return "completed"

                if interactive:
                    output(f"Sending auto-reply:\n{suggestion}")
                client.send_message(task.jules.session_name, suggestion)
                task.advance_state["last_activity_id"] = activity_id
                task.advance_state["last_feedback_hash"] = suggestion_hash
                task.advance_state["last_advance_action"] = "send_message"
                task.advance_state["last_advanced_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                return "completed"

            first_iteration = False

        if not interactive:
            output("Non-interactive mode: human judgment needed.")
            return "skipped"

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
                    if approval_recommended:
                        choices = "y/f/m/s" if allow_skip else "y/f/m"
                        prompt_msg = f"\nApprove the plan as recommended? (y), provide feedback (f), write manual message (m){', or skip (s)' if allow_skip else ''}? [{choices}]: "
                    else:
                        choices = "y/f/m/s" if allow_skip else "y/f/m"
                        prompt_msg = f"\nApprove suggestion (y), provide feedback (f), write manual message (m){', or skip (s)' if allow_skip else ''}? [{choices}]: "
                else:
                    choices = "y/f/m/s" if allow_skip else "y/f/m"
                    prompt_msg = f"\nSend suggestion (y), provide feedback (f), write manual message (m){', or skip (s)' if allow_skip else ''}? [{choices}]: "

                answer = input_func(prompt_msg).strip().lower()
            except EOFError as exc:
                raise PipelineError("Feedback loop needs interactive input.") from exc

            if answer in {"y", "yes"}:
                if is_awaiting_plan_approval and approval_recommended:
                    output("Approving plan...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                else:
                    output("Sending message to Jules...")
                    client.send_message(task.jules.session_name, suggestion)
                return "completed"
            elif answer == "f":
                try:
                    feedback = input_func("Feedback for revision: ").strip()
                except EOFError as exc:
                    raise PipelineError("Feedback input was closed.") from exc
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
                except EOFError as exc:
                    raise PipelineError("Manual message input was closed.") from exc
                if message:
                    output("Sending manual message to Jules...")
                    client.send_message(task.jules.session_name, message)
                    return "completed"
                output("Message cannot be empty.")
            elif allow_skip and answer == "s":
                output("Skipping task.")
                return "skipped"
            else:
                output(f"Please answer with {choices}.")


def handle_feedback(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    codex_bin: str,
    parser: argparse.ArgumentParser,
) -> int:
    _run, task = resolve_task(state, args.task_id)
    if not task.jules:
        parser.exit(
            1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
        )

    outcome = run_feedback_loop(
        task,
        cwd=cwd,
        client=client,
        codex_bin=codex_bin,
    )
    if outcome == "failed":
        return 1
    if outcome == "skipped":
        return 0

    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
