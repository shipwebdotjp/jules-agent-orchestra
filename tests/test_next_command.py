from __future__ import annotations

import pytest
import datetime
from unittest.mock import MagicMock, patch
from pathlib import Path
import argparse

from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo
from jules_agent.config import Config
from jules_agent.cli.commands.next import handle_next
from jules_agent.cli.state import get_candidates
from jules_agent.codex import PipelineError

@pytest.fixture
def sequential_state():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    task1 = Task(
        id="task1",
        title="Task 1",
        status="completed",
        created_at=now,
        updated_at=now,
    )
    task2 = Task(
        id="task2",
        title="Task 2",
        status="planned",
        created_at=now,
        updated_at=now,
    )
    run1 = Run(
        id="run1",
        original_task="test run",
        strategy="sequential_subtasks",
        status="running",
        created_at=now,
        updated_at=now,
        tasks=[task1, task2]
    )
    return State(
        project=ProjectState(root="/tmp", repo="owner/repo"),
        runs=[run1]
    )

def test_get_candidates_next(sequential_state):
    candidates = get_candidates(sequential_state, "next")
    assert len(candidates) == 1
    run, task = candidates[0]
    assert run.id == "run1"
    assert task.id == "task2"

@patch("jules_agent.cli.commands.next.select_task_interactively")
@patch("jules_agent.cli.advance_core.save_state")
@patch("jules_agent.cli.advance_core.get_git_branch", return_value="main")
@patch("jules_agent.cli.advance_core.find_source_name", return_value="test_source")
def test_handle_next_interactive(
    mock_find_source, mock_get_branch, mock_save_state, mock_select, sequential_state
):
    args = argparse.Namespace(run_id=None, automation_mode=None)
    mock_client = MagicMock()
    mock_client.create_session.return_value = {
        "id": "new_sess_id",
        "name": "new_sess_name",
        "state": "QUEUED",
        "url": "http://jules/new"
    }

    # Mock interactive selection to return (run1, task2)
    run1 = sequential_state.runs[0]
    task2 = run1.tasks[1]
    mock_select.return_value = (run1, task2)

    handle_next(args, sequential_state, mock_client, Path("/tmp"), Config())

    assert task2.status == "dispatched"
    assert task2.jules.session_id == "new_sess_id"
    mock_client.create_session.assert_called_once()

def test_handle_next_with_run_id(sequential_state):
    args = argparse.Namespace(run_id="run1", automation_mode=None)
    mock_client = MagicMock()
    mock_client.create_session.return_value = {
        "id": "new_sess_id",
        "name": "new_sess_name",
        "state": "QUEUED",
        "url": "http://jules/new"
    }

    with patch("jules_agent.cli.advance_core.save_state"), \
          patch("jules_agent.cli.advance_core.get_git_branch", return_value="main"), \
           patch("jules_agent.cli.advance_core.find_source_name", return_value="test_source"):
        handle_next(args, sequential_state, mock_client, Path("/tmp"), Config())

    task2 = sequential_state.runs[0].tasks[1]
    assert task2.status == "dispatched"
    assert task2.jules.session_id == "new_sess_id"

def test_handle_next_invalid_run_id(sequential_state):
    args = argparse.Namespace(run_id="invalid_run", automation_mode=None)
    mock_client = MagicMock()
    with pytest.raises(PipelineError, match="Run invalid_run not found"):
        handle_next(args, sequential_state, mock_client, Path("/tmp"), Config())

def test_handle_next_not_sequential(sequential_state):
    sequential_state.runs[0].strategy = "single_session"
    args = argparse.Namespace(run_id="run1", automation_mode=None)
    mock_client = MagicMock()
    with pytest.raises(PipelineError, match="is not an active sequential run"):
        handle_next(args, sequential_state, mock_client, Path("/tmp"), Config())
