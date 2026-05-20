from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
import datetime

from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo, PullRequestInfo
from jules_agent.cli import build_parser
from jules_agent.cli.state import get_candidates
from jules_agent.cli.io import select_task_interactively

@pytest.fixture
def sample_state():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    task1 = Task(
        id="task1",
        title="Task 1",
        status="awaiting_plan_approval",
        created_at=now,
        updated_at=now,
        jules=JulesSessionInfo(session_id="s1", session_name="sn1", state="AWAITING_PLAN_APPROVAL")
    )
    task2 = Task(
        id="task2",
        title="Task 2",
        status="pr_created",
        created_at=now,
        updated_at=now,
        pull_request=PullRequestInfo(url="http://github.com/pull/1")
    )
    run1 = Run(
        id="run1",
        original_task="test run",
        strategy="single_session",
        status="running",
        created_at=now,
        updated_at=now,
        tasks=[task1, task2]
    )
    return State(
        project=ProjectState(root="/tmp", repo="owner/repo"),
        runs=[run1]
    )

def test_get_candidates_approve(sample_state):
    candidates = get_candidates(sample_state, "approve")
    assert len(candidates) == 1
    assert candidates[0][1].id == "task1"

def test_get_candidates_review(sample_state):
    candidates = get_candidates(sample_state, "review")
    assert len(candidates) == 1
    assert candidates[0][1].id == "task2"

def test_get_candidates_send(sample_state):
    candidates = get_candidates(sample_state, "send")
    assert len(candidates) == 1
    assert candidates[0][1].id == "task1"

@patch("sys.stdin.isatty", return_value=True)
def test_select_task_interactively_success(mock_isatty, sample_state):
    run1 = sample_state.runs[0]
    task1 = run1.tasks[0]
    task2 = run1.tasks[1]
    candidates = [(run1, task1), (run1, task2)]

    # Test selecting 1
    run, task = select_task_interactively(candidates, "approve", input_func=lambda _: "1")
    assert task.id == "task1"

    # Test selecting 2
    run, task = select_task_interactively(candidates, "approve", input_func=lambda _: "2")
    assert task.id == "task2"

@patch("sys.stdin.isatty", return_value=False)
def test_select_task_interactively_non_tty(mock_isatty, sample_state):
    from jules_agent.codex import PipelineError
    run1 = sample_state.runs[0]
    task1 = run1.tasks[0]
    candidates = [(run1, task1)]
    with pytest.raises(PipelineError, match="stdin is not interactive"):
        select_task_interactively(candidates, "approve")

def test_argparse_send_omitted_task_id():
    parser = build_parser()
    args = parser.parse_args(["send", "hello world"])
    assert args.command == "send"
    assert args.args == ["hello world"]

def test_argparse_send_with_task_id():
    parser = build_parser()
    args = parser.parse_args(["send", "run1:task1", "hello", "world"])
    assert args.command == "send"
    assert args.args == ["run1:task1", "hello", "world"]

@pytest.mark.parametrize("cmd", ["approve", "feedback", "review", "merge"])
def test_argparse_optional_task_id(cmd):
    parser = build_parser()
    args = parser.parse_args([cmd])
    assert args.command == cmd
    assert args.task_id is None
