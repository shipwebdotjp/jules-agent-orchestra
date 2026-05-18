from __future__ import annotations

import re

from ..models import ExecutionPlan
from ..pipeline import ClarificationQuestion, PipelineError, format_subtask_for_jules


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
