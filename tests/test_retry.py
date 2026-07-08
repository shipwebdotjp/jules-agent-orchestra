import pytest
from unittest.mock import MagicMock
from pathlib import Path
from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo
from jules_agent.services.retry_service import RetryService
from jules_agent.services.options import RetryOptions
from jules_agent.cli.state import get_candidates

@pytest.fixture
def mock_state():
    return State(
        project=ProjectState(root="/tmp", repo="owner/repo"),
        runs=[
            Run(
                id="RUN-001",
                original_task="Test Task",
                strategy="single_session",
                status="failed",
                created_at="2023-01-01T00:00:00Z",
                updated_at="2023-01-01T00:00:00Z",
                tasks=[
                    Task(id="TASK-001", title="Task 1", status="failed", created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T00:00:00Z"),
                    Task(id="TASK-002", title="Task 2", status="completed", created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T00:00:01Z"),
                ]
            ),
            Run(
                id="RUN-002",
                original_task="Another Task",
                strategy="single_session",
                status="running",
                created_at="2023-01-01T00:00:00Z",
                updated_at="2023-01-01T00:00:00Z",
                tasks=[
                    Task(id="TASK-003", title="Task 3", status="pr_closed", created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T00:00:02Z"),
                ]
            )
        ]
    )

def test_get_candidates_retry(mock_state):
    candidates = get_candidates(mock_state, "retry")
    assert len(candidates) == 1
    run, task = candidates[0]
    assert task.id == "TASK-001"
    assert task.status == "failed"

def test_retry_service_success(mock_state, mocker):
    mock_client = MagicMock()
    mock_client.create_session.return_value = {
        "id": "new-session-id",
        "name": "sessions/new-session-id",
        "state": "QUEUED",
        "url": "http://jules/sessions/new-session-id"
    }

    mocker.patch("jules_agent.services.retry_service.save_state")
    mocker.patch("jules_agent.services.retry_service.get_git_branch", return_value="main")
    mocker.patch("jules_agent.services.retry_service.find_source_name", return_value="sources/123")

    service = RetryService(mock_state, mock_client, Path("/tmp"), MagicMock())
    run = mock_state.runs[0]
    task = run.tasks[0]

    options = RetryOptions(run=run, task=task, output_func=lambda x: None)
    result = service.execute(options)

    assert result.exit_code == 0
    assert task.status == "dispatched"
    assert task.jules.session_id == "new-session-id"
    assert task.advance_state["retry_count"] == 1
    assert task.pull_request is None
    assert task.review is None
    assert task.attempts == 0
    assert run.status == "running"

def test_retry_service_failure(mock_state, mocker):
    mock_client = MagicMock()
    # Mock find_source_name to succeed so it reaches create_session
    mocker.patch("jules_agent.services.retry_service.find_source_name", return_value="sources/123")
    mock_client.create_session.side_effect = Exception("API Error")

    mocker.patch("jules_agent.services.retry_service.save_state")
    mocker.patch("jules_agent.services.retry_service.get_git_branch", return_value="main")

    service = RetryService(mock_state, mock_client, Path("/tmp"), MagicMock())
    run = mock_state.runs[0]
    task = run.tasks[0]

    options = RetryOptions(run=run, task=task, output_func=lambda x: None)
    result = service.execute(options)

    assert result.exit_code == 1
    assert task.status == "failed"
    assert task.advance_state["retry_count"] == 1
    assert run.status == "failed"

def test_retry_service_invalid_status(mock_state):
    mock_client = MagicMock()
    service = RetryService(mock_state, mock_client, Path("/tmp"), MagicMock())
    run = mock_state.runs[0]
    task = run.tasks[1] # completed task

    options = RetryOptions(run=run, task=task, output_func=lambda x: None)
    result = service.execute(options)

    assert result.exit_code == 1
    assert "not in 'failed' status" in result.message
