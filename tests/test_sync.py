from __future__ import annotations

import argparse
import pytest
from unittest.mock import MagicMock
from pathlib import Path
from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo
from jules_agent.cli.commands.sync import handle_sync

def test_handle_sync_multi_step_transition():
    # Setup state
    task = Task(
        id="TASK-1",
        title="Test Task",
        status="in_progress",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        jules=JulesSessionInfo(
            session_id="sess-1",
            session_name="sessions/1",
            state="IN_PROGRESS"
        )
    )
    run = Run(
        id="run-1",
        original_task="Test",
        strategy="single_session",
        status="running",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        tasks=[task]
    )
    state = State(
        project=ProjectState(root=".", repo="owner/repo"),
        runs=[run]
    )

    # Mock JulesClient
    client = MagicMock()
    # First sync moves it to COMPLETED with a PR
    client.get_session.return_value = {
        "state": "COMPLETED",
        "outputs": [{"pullRequest": {"url": "https://github.com/owner/repo/pull/1"}}]
    }
    client.list_activities.return_value = []

    # Mock GitHubClient
    github_client = MagicMock()
    # GitHub sync then moves it to merged
    github_client.get_pull_request.return_value = {
        "state": "closed",
        "merged_at": "2024-01-01T01:00:00Z"
    }

    args = argparse.Namespace(json=False)

    # We need to mock save_state because it writes to disk
    with MagicMock() as mock_save:
        import jules_agent.cli.commands.sync as sync_mod
        original_save = sync_mod.save_state
        sync_mod.save_state = mock_save
        try:
            handle_sync(args, state, client, github_client, Path("."))
        finally:
            sync_mod.save_state = original_save

    # Verify that it reached "merged" in ONE call to handle_sync
    assert task.status == "merged"

def test_sync_task_prevents_regression():
    from jules_agent.cli.state import sync_task

    task = Task(
        id="TASK-1",
        title="Test Task",
        status="waiting_human_review",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        jules=JulesSessionInfo(
            session_id="sess-1",
            session_name="sessions/1",
            state="COMPLETED"
        )
    )

    client = MagicMock()
    client.get_session.return_value = {
        "state": "COMPLETED",
        "outputs": [{"pullRequest": {"url": "https://github.com/owner/repo/pull/1"}}]
    }
    client.list_activities.return_value = []

    sync_task(client, task)

    # Should NOT go back to "pr_created"
    assert task.status == "waiting_human_review"


def test_sync_task_returns_success_when_status_does_not_change():
    from jules_agent.cli.state import sync_task

    task = Task(
        id="TASK-1",
        title="Test Task",
        status="awaiting_user_feedback",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        jules=JulesSessionInfo(
            session_id="sess-1",
            session_name="sessions/1",
            state="AWAITING_USER_FEEDBACK"
        )
    )

    client = MagicMock()
    client.get_session.return_value = {
        "state": "AWAITING_USER_FEEDBACK",
        "outputs": [],
    }
    client.list_activities.return_value = []

    assert sync_task(client, task) is True
    assert task.status == "awaiting_user_feedback"
