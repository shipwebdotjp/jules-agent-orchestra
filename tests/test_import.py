from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from jules_agent.cli.commands.import_command import handle_import
from jules_agent.models import ProjectState, Run, State, Task, JulesSessionInfo


def test_handle_import_success() -> None:
    state = State(project=ProjectState(root="/root", repo="owner/repo"), runs=[])
    client = MagicMock()
    client.get_session.return_value = {
        "id": "sessions/12345",
        "name": "sessions/12345",
        "state": "COMPLETED",
        "prompt": "Test prompt",
        "title": "Test title",
        "outputs": [],
    }
    client.list_sources.return_value = [
        {
            "name": "sources/1",
            "githubRepo": {"owner": "owner", "repo": "repo"}
        }
    ]

    args = argparse.Namespace(session_id="12345")

    with patch("jules_agent.services.import_service.save_state") as mock_save_state, \
         patch("jules_agent.services.import_service.generate_run_id", return_value="run_20240101_001"):
        result = handle_import(args, state, client, Path("/root"))

    assert result == 0
    assert len(state.runs) == 1
    run = state.runs[0]
    assert run.id == "run_20240101_001"
    assert run.original_task == "Test title"
    assert len(run.tasks) == 1
    task = run.tasks[0]
    assert task.id == "TASK-001"
    assert task.title == "Test title"
    assert task.status == "completed"
    assert task.jules.session_id == "sessions/12345"
    mock_save_state.assert_called_once()


def test_handle_import_duplicate() -> None:
    state = State(
        project=ProjectState(root="/root", repo="owner/repo"),
        runs=[
            Run(
                id="run-1",
                original_task="task",
                strategy="single_session",
                status="completed",
                created_at="now",
                updated_at="now",
                tasks=[
                    Task(
                        id="TASK-001",
                        title="title",
                        status="completed",
                        created_at="now",
                        updated_at="now",
                        jules=JulesSessionInfo(
                            session_id="sessions/12345",
                            session_name="sessions/12345",
                            state="COMPLETED",
                        )
                    )
                ]
            )
        ]
    )
    client = MagicMock()
    args = argparse.Namespace(session_id="12345")

    result = handle_import(args, state, client, Path("/root"))

    assert result == 0
    assert len(state.runs) == 1  # No new run added
    client.get_session.assert_not_called()


def test_handle_import_repo_mismatch(capsys) -> None:
    state = State(project=ProjectState(root="/root", repo="owner/repo"), runs=[])
    client = MagicMock()
    client.get_session.return_value = {
        "id": "sessions/12345",
        "name": "sessions/12345",
        "state": "IN_PROGRESS",
        "prompt": "Test prompt",
        "sourceContext": {"source": "sources/other"}
    }
    client.list_sources.return_value = [
        {
            "name": "sources/other",
            "githubRepo": {"owner": "other", "repo": "other"}
        }
    ]

    args = argparse.Namespace(session_id="sessions/12345")

    with patch("jules_agent.services.import_service.save_state"), \
         patch("jules_agent.services.import_service.generate_run_id", return_value="run-1"):
        result = handle_import(args, state, client, Path("/root"))

    assert result == 0
    # The warning message is now passed to options.error_func, which is print(..., file=sys.stderr) in handle_import
    stderr = capsys.readouterr().err
    assert "Warning: Session repository (other/other) does not match local repository (owner/repo)." in stderr


def test_handle_import_prompt_shortening() -> None:
    long_prompt = "Line 1\nLine 2\n" + "a" * 200
    state = State(project=ProjectState(root="/root", repo="owner/repo"), runs=[])
    client = MagicMock()
    client.get_session.return_value = {
        "id": "sessions/12345",
        "name": "sessions/12345",
        "state": "IN_PROGRESS",
        "prompt": long_prompt,
        "title": None,
    }

    args = argparse.Namespace(session_id="12345")

    with patch("jules_agent.services.import_service.save_state"), \
         patch("jules_agent.services.import_service.generate_run_id", return_value="run-1"):
        handle_import(args, state, client, Path("/root"))

    run = state.runs[0]
    assert len(run.original_task) == 103  # 100 + "..."
    assert run.original_task.endswith("...")
    assert "\n" not in run.original_task


def test_handle_import_with_pr() -> None:
    state = State(project=ProjectState(root="/root", repo="owner/repo"), runs=[])
    client = MagicMock()
    client.get_session.return_value = {
        "id": "sessions/12345",
        "name": "sessions/12345",
        "state": "COMPLETED",
        "prompt": "Test prompt",
        "outputs": [
            {
                "pullRequest": {
                    "url": "https://github.com/owner/repo/pull/1",
                    "title": "PR Title"
                }
            }
        ],
    }

    args = argparse.Namespace(session_id="12345")

    with patch("jules_agent.services.import_service.save_state"), \
         patch("jules_agent.services.import_service.generate_run_id", return_value="run-1"):
        handle_import(args, state, client, Path("/root"))

    task = state.runs[0].tasks[0]
    assert task.status == "pr_created"
    assert task.pull_request is not None
    assert task.pull_request.url == "https://github.com/owner/repo/pull/1"
