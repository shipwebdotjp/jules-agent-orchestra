from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import PipelineError, run_pipeline


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        outcome = run_pipeline(
            args.task,
            cwd=Path.cwd(),
            repo=args.repo,
            codex_bin=args.codex_bin,
            jules_bin=args.jules_bin,
        )
    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    print(f"Created {len(outcome.dispatches)} Jules session(s):")
    for result in outcome.dispatches:
        session = result.session_id or "unknown"
        print(f"{result.index}. [{session}] {result.subtask.title}")
    return 0
