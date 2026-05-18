from __future__ import annotations

from ..models import ExecutionPlan
from ..pipeline import PipelineError, format_subtask_for_jules


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
