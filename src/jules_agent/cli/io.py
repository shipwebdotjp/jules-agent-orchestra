from __future__ import annotations

import re

import sys
from typing import TYPE_CHECKING

from ..models import ExecutionPlan
from ..pipeline import format_subtask_for_jules
from ..codex import ClarificationQuestion, PipelineError, SelectionCancelled

if TYPE_CHECKING:
    from ..models import Run, Task


def build_review_prompt(task: str, feedback_history: list[str]) -> str:
    prompt = task.strip()
    if not feedback_history:
        return prompt

    feedback_lines = "\n".join(
        f"{index}. {feedback}" for index, feedback in enumerate(feedback_history, start=1)
    )
    return (
        f"{prompt}\n\n"
        "User feedback from the previous plan:\n"
        f"{feedback_lines}\n\n"
        "Revise the plan to address the feedback."
    )


def render_plan(plan: ExecutionPlan, *, output=print) -> None:
    output(f"Proposed strategy: {plan.strategy}")
    output("Proposed tasks:")
    for index, task in enumerate(plan.tasks, start=1):
        rendered_lines = format_subtask_for_jules(task).splitlines()
        output(f"{index}. {rendered_lines[0]}")
        for line in rendered_lines[1:]:
            output(f"   {line}" if line else "")


def prompt_for_review(
    *,
    input_func=input,
    output=print,
) -> str | None:
    while True:
        try:
            answer = input_func("Approve this plan? [y/n]: ").strip().lower()
        except EOFError as exc:
            raise PipelineError(
                "Confirmation mode needs interactive input. Re-run with --no-confirm to skip it."
            ) from exc

        if answer in {"", "y", "yes"}:
            return None
        if answer in {"n", "no"}:
            try:
                feedback = input_func("Feedback for revision: ").strip()
            except EOFError as exc:
                raise PipelineError(
                    "Feedback input was closed. Re-run with --no-confirm to skip confirmation mode."
                ) from exc
            if feedback:
                return feedback
            output("Feedback cannot be empty.")
            continue
        output("Please answer with y or n.")


def render_clarification_question(
    question: ClarificationQuestion,
    *,
    output=print,
    index: int | None = None,
    total: int | None = None,
) -> None:
    if index is not None and total is not None:
        output(f"Question {index}/{total}: {question.question}")
    else:
        output(f"Question: {question.question}")

    for option_index, option in enumerate(question.options, start=1):
        output(f"  {option_index}. {option}")
    output("Enter a number, or type a custom answer.")


def select_task_interactively(
    candidates: list[tuple[Run, Task]],
    command: str,
    *,
    input_func=input,
    output=print,
) -> tuple[Run, Task]:
    if not sys.stdin.isatty():
        raise PipelineError(
            "Error: task_id is required when stdin is not interactive. Please pass TASK_ID explicitly."
        )

    if not candidates:
        raise PipelineError(f"No eligible tasks for {command}.")

    output(f"Select a task for {command}:")
    for i, (run, task) in enumerate(candidates, start=1):
        output(f"  {i}. {run.id}:{task.id} [{task.status}] {task.title}")

    while True:
        try:
            choice = input_func(f"Select task (1-{len(candidates)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SelectionCancelled()

        if not choice:
            continue

        try:
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
            output(f"Invalid selection. Please enter a number between 1 and {len(candidates)}.")
        except ValueError:
            output("Invalid input. Please enter a number.")


def select_run_interactively(
    state: State,
    *,
    input_func=input,
    output=print,
) -> Run:
    if not sys.stdin.isatty():
        raise PipelineError(
            "Error: run_id is required when stdin is not interactive. Please pass RUN_ID explicitly."
        )

    if not state.runs:
        raise PipelineError("No runs found in local state.")

    output("Select a run to delete:")
    # Show runs in reverse chronological order (newest first)
    sorted_runs = sorted(state.runs, key=lambda r: r.updated_at, reverse=True)
    for i, run in enumerate(sorted_runs, start=1):
        output(f"  {i}. {run.id} [{run.status}] {run.original_task[:50]}")

    while True:
        try:
            choice = input_func(f"Select run (1-{len(sorted_runs)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SelectionCancelled()

        if not choice:
            continue

        try:
            idx = int(choice)
            if 1 <= idx <= len(sorted_runs):
                return sorted_runs[idx - 1]
            output(f"Invalid selection. Please enter a number between 1 and {len(sorted_runs)}.")
        except ValueError:
            output("Invalid input. Please enter a number.")


def prompt_for_clarification_answer(
    question: ClarificationQuestion,
    *,
    input_func=input,
    output=print,
) -> str:
    while True:
        try:
            answer = input_func("Answer: ").strip()
        except EOFError as exc:
            raise PipelineError(
                "Clarification mode needs interactive input. Re-run with --no-confirm to skip it."
            ) from exc

        if not answer:
            output("Answer cannot be empty.")
            continue

        match = re.fullmatch(r"(\d+)(?:\s+(.*))?", answer)
        if match:
            option_index = int(match.group(1))
            if 1 <= option_index <= len(question.options):
                selected = question.options[option_index - 1]
                note = match.group(2)
                if note:
                    return f"{selected} (additional detail: {note.strip()})"
                return selected
            output(f"Please answer with a number from 1 to {len(question.options)} or free text.")
            continue

        return answer
