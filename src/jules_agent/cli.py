from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Subtask
from .pipeline import (
    CommandRunner,
    PipelineError,
    PipelineOutcome,
    decompose_task,
    dispatch_subtasks,
    is_git_repo,
    run_command,
    run_pipeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules-agent",
        description="Decompose a task with Codex and dispatch subtasks to Jules.",
    )
    parser.add_argument("task", help="Task to split and dispatch.")
    parser.add_argument(
        "--repo",
        help="Optional Jules repo override, for example owner/name.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Path to the codex executable.",
    )
    parser.add_argument(
        "--jules-bin",
        default="jules",
        help="Path to the jules executable.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
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
        "Revise the subtasks to address the feedback."
    )


def render_subtasks(subtasks: list[Subtask], *, output=print) -> None:
    output("Proposed subtasks:")
    for index, subtask in enumerate(subtasks, start=1):
        output(f"{index}. {subtask.title}")
        if subtask.details:
            output(f"   Details: {subtask.details}")


def prompt_for_review(
    *,
    input_func=input,
    output=print,
) -> str | None:
    while True:
        try:
            answer = input_func("Approve this plan? [ok/ng]: ").strip().lower()
        except EOFError as exc:
            raise PipelineError(
                "Confirmation mode needs interactive input. Re-run with --no-confirm to skip it."
            ) from exc

        if answer in {"", "ok", "o", "y", "yes"}:
            return None
        if answer in {"ng", "n", "no"}:
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
        output("Please answer with ok or ng.")


def run_confirmation_loop(
    task: str,
    *,
    cwd: Path,
    repo: str | None,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
) -> tuple[list[Subtask], list[str]]:
    if repo is None and not is_git_repo(cwd):
        raise PipelineError(
            "Current directory is not a git repository. Pass --repo owner/name "
            "or run the CLI inside a git repo."
        )

    feedback_history: list[str] = []
    while True:
        review_task = build_review_prompt(task, feedback_history)
        subtasks = decompose_task(
            review_task,
            cwd=cwd,
            codex_bin=codex_bin,
            runner=runner,
        )
        render_subtasks(subtasks, output=output)

        feedback = prompt_for_review(input_func=input_func, output=output)
        if feedback is None:
            return subtasks, feedback_history

        feedback_history.append(feedback)
        output("Revising subtasks with feedback...")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.no_confirm:
            outcome = run_pipeline(
                args.task,
                cwd=Path.cwd(),
                repo=args.repo,
                codex_bin=args.codex_bin,
                jules_bin=args.jules_bin,
            )
        else:
            subtasks, _ = run_confirmation_loop(
                args.task,
                cwd=Path.cwd(),
                repo=args.repo,
                codex_bin=args.codex_bin,
                runner=run_command,
            )
            dispatches = dispatch_subtasks(
                subtasks,
                cwd=Path.cwd(),
                repo=args.repo,
                jules_bin=args.jules_bin,
                runner=run_command,
            )
            outcome = PipelineOutcome(
                task=args.task,
                subtasks=subtasks,
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
