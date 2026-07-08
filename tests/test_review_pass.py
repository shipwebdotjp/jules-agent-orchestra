from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jules_agent.models import State, ProjectState, Run, Task, TaskReview, PullRequestInfo
from jules_agent.config import Config
from jules_agent.cli.commands.review_pass import handle_review_pass


def make_merge_task(task_id: str = "t1", status: str = "merge") -> Task:
    return Task(
        id=task_id,
        title="Task 1",
        status=status,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        pull_request=PullRequestInfo(url="https://github.com/owner/repo/pull/5"),
    )


def make_state(task: Task | None = None) -> State:
    if task is None:
        task = make_merge_task()
    run = Run(
        id="run1",
        original_task="test",
        strategy="single_session",
        status="running",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        tasks=[task],
    )
    return State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])


@patch("jules_agent.services.review_pass_service.save_state")
@patch("jules_agent.cli.commands.review_pass.sync_task_state")
@patch("jules_agent.cli.commands.review_pass.resolve_task")
def test_handle_review_pass_with_task_id(mock_resolve, mock_sync, mock_save):
    task = make_merge_task()
    run = Run(
        id="run1", original_task="test", strategy="single_session",
        status="running", created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z", tasks=[task],
    )
    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    mock_resolve.return_value = ("run1", task)
    args = argparse.Namespace(task_id="t1")
    client = MagicMock()
    github_client = MagicMock()
    github_client.get_pull_request.return_value = {"head": {"sha": "abc123"}}

    result = handle_review_pass(args, state, client, github_client, Path("/tmp"))

    assert result == 0
    assert task.status == "review_passed"
    assert task.review.passed_head_sha == "abc123"
    mock_save.assert_called_once()


@patch("jules_agent.services.review_pass_service.save_state")
@patch("jules_agent.cli.commands.review_pass.sync_task_state")
@patch("jules_agent.cli.commands.review_pass.select_task_interactively")
@patch("jules_agent.cli.commands.review_pass.get_candidates")
def test_handle_review_pass_interactive(mock_candidates, mock_select, mock_sync, mock_save):
    task = make_merge_task()
    mock_candidates.return_value = [("run1", task)]
    mock_select.return_value = ("run1", task)
    state = make_state(task)
    args = argparse.Namespace(task_id=None)
    client = MagicMock()
    github_client = MagicMock()
    github_client.get_pull_request.return_value = {"head": {"sha": "def456"}}

    result = handle_review_pass(args, state, client, github_client, Path("/tmp"))

    assert result == 0
    assert task.status == "review_passed"
    assert task.review.passed_head_sha == "def456"
    mock_save.assert_called_once()


def test_handle_review_pass_no_github_client():
    args = argparse.Namespace(task_id=None)
    state = make_state()
    client = MagicMock()

    from jules_agent.codex import OperationError
    with pytest.raises(OperationError) as excinfo:
        handle_review_pass(args, state, client, None, Path("/tmp"))

    assert "GITHUB_TOKEN is required for review-pass" in str(excinfo.value)


@patch("jules_agent.cli.commands.review_pass.sync_task_state")
@patch("jules_agent.cli.commands.review_pass.resolve_task")
def test_handle_review_pass_already_merged(mock_resolve, mock_sync):
    task = make_merge_task(status="merged")
    run = Run(
        id="run1", original_task="test", strategy="single_session",
        status="running", created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z", tasks=[task],
    )
    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    mock_resolve.return_value = ("run1", task)
    args = argparse.Namespace(task_id="t1")
    client = MagicMock()
    github_client = MagicMock()

    result = handle_review_pass(args, state, client, github_client, Path("/tmp"))

    assert result == 0
    assert task.status == "merged"


@patch("jules_agent.cli.commands.review_pass.sync_task_state")
@patch("jules_agent.cli.commands.review_pass.resolve_task")
def test_handle_review_pass_no_pull_request(mock_resolve, mock_sync):
    task = Task(
        id="t1", title="Task 1", status="merge",
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z",
    )
    run = Run(
        id="run1", original_task="test", strategy="single_session",
        status="running", created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z", tasks=[task],
    )
    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    mock_resolve.return_value = ("run1", task)
    args = argparse.Namespace(task_id="t1")
    client = MagicMock()
    github_client = MagicMock()

    from jules_agent.codex import OperationError
    with pytest.raises(OperationError) as excinfo:
        handle_review_pass(args, state, client, github_client, Path("/tmp"))

    assert f"Error: Task {task.id} has no pull request." in str(excinfo.value)


@patch("jules_agent.cli.commands.review_pass.sync_task_state")
@patch("jules_agent.cli.commands.review_pass.resolve_task")
def test_handle_review_pass_no_head_sha(mock_resolve, mock_sync):
    task = make_merge_task()
    run = Run(
        id="run1", original_task="test", strategy="single_session",
        status="running", created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z", tasks=[task],
    )
    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    mock_resolve.return_value = ("run1", task)
    args = argparse.Namespace(task_id="t1")
    client = MagicMock()
    github_client = MagicMock()
    github_client.get_pull_request.return_value = {"head": {}}

    from jules_agent.codex import OperationError
    with pytest.raises(OperationError) as excinfo:
        handle_review_pass(args, state, client, github_client, Path("/tmp"))

    assert "Error: Could not determine current head SHA." in str(excinfo.value)
