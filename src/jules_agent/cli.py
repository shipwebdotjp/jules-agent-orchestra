from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .client import JulesClient
from .config import load_config
from .models import ExecutionPlan
from .pipeline import (
    CommandRunner,
    PipelineError,
    PipelineOutcome,
    decompose_task,
    dispatch_subtasks,
    run_command,
    run_pipeline,
    validate_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules-agent",
        description="Analyze a task with Codex and dispatch it to Jules.",
    )
    parser.add_argument("task", help="Task to analyze and dispatch.")
    parser.add_argument(
        "--repo",
        help="Optional Jules repo override, for example owner/name.",
    )
    parser.add_argument(
        "--codex-bin",
        help="Path to the codex executable.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a custom configuration file.",
    )
    return parser


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
        output(f"{index}. {task.title}")
        if task.details:
            output(f"   Details: {task.details}")


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


def run_confirmation_loop(
    task: str,
    *,
    cwd: Path,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
) -> tuple[ExecutionPlan, list[str]]:
    feedback_history: list[str] = []
    while True:
        review_task = build_review_prompt(task, feedback_history)
        plan = decompose_task(
            review_task,
            cwd=cwd,
            codex_bin=codex_bin,
            runner=runner,
        )
        validate_plan(plan)
        render_plan(plan, output=output)

        feedback = prompt_for_review(input_func=input_func, output=output)
        if feedback is None:
            return plan, feedback_history

        feedback_history.append(feedback)
        output("Revising plan with feedback...")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)

    api_key = os.environ.get("JULES_API_KEY") or config.api_key
    if not api_key:
        parser.exit(
            1,
            "Error: JULES_API_KEY is not set in environment or configuration.\n",
        )

    repo = args.repo or config.repo
    codex_bin = args.codex_bin or config.codex_bin
    base_url = config.base_url

    client = JulesClient(api_key=api_key, base_url=base_url)

    try:
        if args.no_confirm:
            outcome = run_pipeline(
                args.task,
                cwd=Path.cwd(),
                client=client,
                repo=repo,
                codex_bin=codex_bin,
            )
        else:
            plan, _ = run_confirmation_loop(
                args.task,
                cwd=Path.cwd(),
                codex_bin=codex_bin,
                runner=run_command,
            )
            dispatches = dispatch_subtasks(
                plan.tasks,
                cwd=Path.cwd(),
                client=client,
                repo=repo,
                strategy=plan.strategy,
            )
            outcome = PipelineOutcome(
                task=args.task,
                plan=plan,
                dispatches=dispatches,
            )
    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    print(f"Jules dispatch result(s): {len(outcome.dispatches)}")
    for result in outcome.dispatches:
        status = "success" if result.returncode == 0 else "failure"
        session = result.session_id or "unknown"
        print(f"{result.index}. [{status}] [{session}] {result.subtask.title}")

    failures = [result for result in outcome.dispatches if result.returncode != 0]
    if failures:
        print("", file=sys.stderr)
        for result in failures:
            if result.error_message:
                print(result.error_message, file=sys.stderr)
        return 1

    return 0
