from __future__ import annotations

import argparse
import pytest
from unittest.mock import MagicMock
from pathlib import Path
from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo
from jules_agent.cli.commands.sync import handle_sync

def test_handle_sync_output_on_change(capsys):
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
    # Sync moves it to COMPLETED with NO PR
    client.get_session.return_value = {
        "state": "COMPLETED",
        "outputs": []
    }
    client.list_activities.return_value = []

    github_client = None
    args = argparse.Namespace(json=False)

    with MagicMock() as mock_save:
        import jules_agent.services.sync_service as sync_mod
        original_save = sync_mod.save_state
        sync_mod.save_state = mock_save
        try:
            handle_sync(args, state, client, github_client, Path("."))
        finally:
            sync_mod.save_state = original_save

    captured = capsys.readouterr()
    assert "Run run-1: running -> completed" in captured.out
    assert "  Task TASK-1: in_progress -> completed" in captured.out
    assert "Synced 1 tasks." in captured.out

def test_handle_sync_no_output_on_no_change(capsys):
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
    # Sync returns same state
    client.get_session.return_value = {
        "state": "IN_PROGRESS",
        "outputs": []
    }
    client.list_activities.return_value = []

    github_client = None
    args = argparse.Namespace(json=False)

    with MagicMock() as mock_save:
        import jules_agent.services.sync_service as sync_mod
        original_save = sync_mod.save_state
        sync_mod.save_state = mock_save
        try:
            handle_sync(args, state, client, github_client, Path("."))
        finally:
            sync_mod.save_state = original_save

    captured = capsys.readouterr()
    assert captured.out == ""

def test_handle_sync_recomputes_run_status_without_task_changes(capsys):
    # Setup state
    task = Task(
        id="TASK-1",
        title="Test Task",
        status="completed",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        jules=JulesSessionInfo(
            session_id="sess-1",
            session_name="sessions/1",
            state="COMPLETED"
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

    client = MagicMock()
    client.get_session.return_value = {
        "state": "COMPLETED",
        "outputs": []
    }
    client.list_activities.return_value = []

    github_client = None
    args = argparse.Namespace(json=False)

    with MagicMock() as mock_save:
        import jules_agent.services.sync_service as sync_mod
        original_save = sync_mod.save_state
        sync_mod.save_state = mock_save
        try:
            handle_sync(args, state, client, github_client, Path("."))
        finally:
            sync_mod.save_state = original_save

    captured = capsys.readouterr()
    assert "Run run-1: running -> completed" in captured.out
    assert "  Task TASK-1:" not in captured.out
    assert run.status == "completed"

def test_handle_sync_json_no_output(capsys):
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
    # Sync moves it to COMPLETED
    client.get_session.return_value = {
        "state": "COMPLETED",
        "outputs": []
    }
    client.list_activities.return_value = []

    github_client = None
    args = argparse.Namespace(json=True)

    with MagicMock() as mock_save:
        import jules_agent.services.sync_service as sync_mod
        original_save = sync_mod.save_state
        sync_mod.save_state = mock_save
        try:
            handle_sync(args, state, client, github_client, Path("."))
        finally:
            sync_mod.save_state = original_save

    captured = capsys.readouterr()
    assert captured.out == ""
