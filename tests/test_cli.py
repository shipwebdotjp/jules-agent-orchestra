from __future__ import annotations

import subprocess
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from jules_agent.cli import (
    build_review_prompt,
    extract_pull_request_number,
    get_run_sync_status,
    run_clarification_loop,
    run_confirmation_loop,
    sync_pr_created_task,
)
from jules_agent.models import PullRequestInfo, Task

def make_pr_created_task(url: str = "https://github.com/owner/repo/pull/5") -> Task:
    return Task(
        id="TASK-001",
        title="Example task",
        status="pr_created",
        created_at="2026-05-17T00:00:00Z",
        updated_at="2026-05-17T00:00:00Z",
        pull_request=PullRequestInfo(url=url),
    )

def test_build_review_prompt_includes_feedback_history() -> None:
    prompt = build_review_prompt(
        "Build a CLI",
        ["The plan needs more tests.", "Reorder the steps."],
    )

    assert "Build a CLI" in prompt
    assert "1. The plan needs more tests." in prompt
    assert "2. Reorder the steps." in prompt

def test_run_confirmation_loop_retries_until_approved() -> None:
    responses = [
        subprocess.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    ]
    outputs: list[str] = []
    calls: list[list[str]] = []
    prompts = iter(["n", "Add tests", "y"])
    call_count = 0

    def input_func(prompt: str) -> str:
        assert isinstance(prompt, str)
        return next(prompts)

    def output_func(message: str) -> None:
        outputs.append(message)

    def runner(args, *, cwd=None, input_text=None):
        nonlocal call_count
        calls.append(list(args))
        if "--output-last-message" in args:
            last_message_path = Path(args[args.index("--output-last-message") + 1])
            payload = (
                '{"strategy":"single_session","tasks":[{"title":"Plan"}]}'
                if call_count == 0
                else '{"strategy":"single_session","tasks":[{"title":"Plan revised"}]}'
            )
            last_message_path.write_text(payload, encoding="utf-8")
            call_count += 1
        return responses.pop(0)

    with tempfile.TemporaryDirectory() as tmpdir:
        plan = run_confirmation_loop(
            "Build a CLI",
            cwd=Path(tmpdir),
            tool_name="codex",
            tool_bin="codex",
            runner=runner,
            input_func=input_func,
            output=output_func,
        )

    assert plan.strategy == "single_session"
    assert [task.title for task in plan.tasks] == ["Plan revised"]
    assert "Proposed strategy: single_session" in outputs
    assert "Proposed tasks:" in outputs
    assert "Revising plan with feedback..." in outputs
    assert len(calls) == 2
    assert "User feedback from the previous plan:" in calls[1][-1]
    assert "Add tests" in calls[1][-1]

def test_run_clarification_loop_collects_answers() -> None:
    responses = [
        subprocess.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    ]
    outputs: list[str] = []
    calls: list[list[str]] = []
    prompts = iter(["1", "n"])
    call_count = 0

    def input_func(prompt: str) -> str:
        assert isinstance(prompt, str)
        return next(prompts)

    def output_func(message: str) -> None:
        outputs.append(message)

    def runner(args, *, cwd=None, input_text=None):
        nonlocal call_count
        calls.append(list(args))
        if "--output-last-message" in args:
            last_message_path = Path(args[args.index("--output-last-message") + 1])
            payload = (
                '{"has_questions":true,"questions":[{"question":"Which platform?","options":["macOS","Linux"]}]}'
                if call_count == 0
                else '{"has_questions":false,"questions":[]}'
            )
            last_message_path.write_text(payload, encoding="utf-8")
            call_count += 1
        return responses.pop(0)

    with tempfile.TemporaryDirectory() as tmpdir:
        clarified_task = run_clarification_loop(
            "Build a CLI",
            cwd=Path(tmpdir),
            tool_name="codex",
            tool_bin="codex",
            runner=runner,
            input_func=input_func,
            output=output_func,
        )

    assert "Clarifications gathered:" in clarified_task
    assert "Which platform?" in clarified_task
    assert "macOS" in clarified_task
    assert len(calls) == 2
    assert "Clarification round 1/5:" in outputs
    assert "No further clarification is needed." in outputs

def test_extract_pull_request_number_accepts_common_github_urls() -> None:
    assert extract_pull_request_number("https://github.com/owner/repo/pull/5") == 5
    assert extract_pull_request_number("https://api.github.com/repos/owner/repo/pulls/42") == 42

@pytest.mark.parametrize("payload, expected_changed, expected_status", [
    ({"state": "open", "merged_at": None}, False, "pr_created"),
    (
        {"state": "closed", "merged_at": "2026-05-17T00:00:00Z"},
        True,
        "merged",
    ),
    ({"state": "closed", "merged_at": None}, True, "pr_closed"),
])
def test_sync_pr_created_task_updates_status_from_github(payload, expected_changed, expected_status) -> None:
    github_client = MagicMock()
    github_client.get_pull_request.return_value = payload
    task = make_pr_created_task()

    changed = sync_pr_created_task(github_client, "owner/repo", task)

    assert changed == expected_changed
    assert task.status == expected_status
    github_client.get_pull_request.assert_called_once_with("owner/repo", 5)

def test_get_run_sync_status_reopens_completed_runs_with_pending_prs() -> None:
    from jules_agent.models import Run, Task
    run = Run(
        id="run-1",
        original_task="Example",
        strategy="single_session",
        status="completed",
        created_at="2026-05-17T00:00:00Z",
        updated_at="2026-05-17T00:00:00Z",
        tasks=[
            Task(
                id="TASK-001",
                title="Example task",
                status="pr_created",
                created_at="2026-05-17T00:00:00Z",
                updated_at="2026-05-17T00:00:00Z",
                pull_request=PullRequestInfo(
                    url="https://github.com/owner/repo/pull/5"
                ),
            )
        ],
    )

    assert get_run_sync_status(
        run,
        previous_status="completed",
        reopened_from_completed=True,
    ) == "running"
