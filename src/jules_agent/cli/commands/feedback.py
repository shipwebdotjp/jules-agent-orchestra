from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ...client import JulesClient
from ...models import State, Task
from ...pipeline import (
    CommandRunner,
    PipelineError,
    run_command,
    save_state,
    suggest_reply,
)
from ..state import resolve_task, sync_task


def run_feedback_loop(
    task: Task,
    *,
    cwd: Path,
    client: JulesClient,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
) -> bool:
    if not task.jules:
        raise PipelineError("Task has no Jules session info.")

    feedback_history: list[str] = []
    while True:
        if not sync_task(client, task):
            output(
                "Error: Failed to sync task state. Please check your connection and try again."
            )
            return False

        output("\nFetching approval recommendation from Codex..." if task.status == "awaiting_plan_approval" else "\nFetching next suggestion from Codex...")
        is_awaiting_plan_approval = task.status == "awaiting_plan_approval"
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
        suggestion = result["suggestion"]
        explanation = result["explanation"]
        approval_recommended = result.get("approval_recommended", False)

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
                if is_awaiting_plan_approval and approval_recommended:
                    prompt_msg = (
                        "\nApprove the plan as recommended? (y), provide feedback (f), or write manual message (m)? [y/f/m]: "
                    )
                else:
                    prompt_msg = (
                        "\nApprove suggestion (y), provide feedback (f), or write manual message (m)? [y/f/m]: "
                    )
                answer = input_func(prompt_msg).strip().lower()
            except EOFError as exc:
                raise PipelineError("Feedback loop needs interactive input.") from exc

            if answer in {"y", "yes"}:
                if is_awaiting_plan_approval and approval_recommended:
                    output("Approving plan...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                else:
                    output("Sending suggestion to Jules...")
                    client.send_message(task.jules.session_name, suggestion)
                return True
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
                    return True
                output("Message cannot be empty.")
            else:
                output("Please answer with y, f, or m.")


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

    success = run_feedback_loop(
        task,
        cwd=cwd,
        client=client,
        codex_bin=codex_bin,
    )
    if not success:
        return 1

    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    save_state(cwd, state)
    print("Done.")
    return 0
