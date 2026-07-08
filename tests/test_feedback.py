from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from jules_agent.pipeline import suggest_reply, build_suggestion_prompt, PipelineError
from jules_agent.cli.commands.feedback import run_feedback_loop
from jules_agent.models import Task, JulesSessionInfo

def test_build_suggestion_prompt_includes_approval_instruction() -> None:
    prompt = build_suggestion_prompt(
        "Task description",
        "Activities",
        [],
        is_awaiting_plan_approval=True
    )
    assert "awaiting plan approval" in prompt
    assert "approval_recommended" in prompt

@patch("jules_agent.pipeline.call_backend")
def test_suggest_reply_handles_approval_recommended(mock_call_backend) -> None:
    mock_call_backend.return_value = {
        "suggestion": "Looks good!",
        "explanation": "The plan is complete.",
        "approval_recommended": True
    }

    result = suggest_reply(
        "Task",
        [],
        [],
        cwd=Path("/tmp"),
        is_awaiting_plan_approval=True
    )

    assert result["suggestion"] == "Looks good!"
    assert result["approval_recommended"]

@patch("jules_agent.pipeline.call_backend")
def test_suggest_reply_raises_error_on_missing_approval_recommended(mock_call_backend) -> None:
    mock_call_backend.return_value = {
        "suggestion": "Looks good!",
        "explanation": "The plan is complete."
    }

    with pytest.raises(PipelineError, match="approval_recommended.*missing"):
        suggest_reply(
            "Task",
            [],
            [],
            cwd=Path("/tmp"),
            is_awaiting_plan_approval=True
        )

@patch("jules_agent.pipeline.call_backend")
def test_suggest_reply_raises_error_on_invalid_approval_recommended(mock_call_backend) -> None:
    mock_call_backend.return_value = {
        "suggestion": "Looks good!",
        "explanation": "The plan is complete.",
        "approval_recommended": "yes"
    }

    with pytest.raises(PipelineError, match="approval_recommended.*boolean"):
        suggest_reply(
            "Task",
            [],
            [],
            cwd=Path("/tmp"),
            is_awaiting_plan_approval=True
        )

@patch("jules_agent.services.feedback_service.sync_task")
@patch("jules_agent.services.feedback_service.suggest_reply")
def test_run_feedback_loop_approves_plan(mock_suggest, mock_sync) -> None:
    mock_sync.return_value = True
    task = Task(
        id="T1",
        title="Task",
        status="awaiting_plan_approval",
        created_at="now",
        updated_at="now",
        jules=JulesSessionInfo(
            session_id="s1",
            session_name="sessions/s1",
            state="AWAITING_PLAN_APPROVAL"
        )
    )
    client = MagicMock()
    client.list_activities.return_value = []

    mock_suggest.return_value = {
        "suggestion": "Approve it",
        "explanation": "Ready",
        "approval_recommended": True
    }

    input_func = MagicMock(return_value="y")
    output_func = MagicMock()

    outcome = run_feedback_loop(
        task,
        cwd=Path("/tmp"),
        client=client,
        tool_name="codex",
        tool_bin="codex",
        input_func=input_func,
        output=output_func
    )

    assert outcome == "completed"
    client.approve_plan.assert_called_once_with("sessions/s1")
    client.send_message.assert_not_called()
    assert task.status == "plan_approved"

@patch("jules_agent.services.feedback_service.sync_task")
@patch("jules_agent.services.feedback_service.suggest_reply")
def test_run_feedback_loop_sends_message_when_not_recommended(mock_suggest, mock_sync) -> None:
    mock_sync.return_value = True
    task = Task(
        id="T1",
        title="Task",
        status="awaiting_plan_approval",
        created_at="now",
        updated_at="now",
        jules=JulesSessionInfo(
            session_id="s1",
            session_name="sessions/s1",
            state="AWAITING_PLAN_APPROVAL"
        )
    )
    client = MagicMock()
    client.list_activities.return_value = []

    mock_suggest.return_value = {
        "suggestion": "Fix this",
        "explanation": "Missing something",
        "approval_recommended": False
    }

    input_func = MagicMock(return_value="y")
    output_func = MagicMock()

    outcome = run_feedback_loop(
        task,
        cwd=Path("/tmp"),
        client=client,
        tool_name="codex",
        tool_bin="codex",
        input_func=input_func,
        output=output_func
    )

    assert outcome == "completed"
    client.approve_plan.assert_not_called()
    client.send_message.assert_called_once_with("sessions/s1", "Fix this")

@patch("jules_agent.services.feedback_service.sync_task")
def test_run_feedback_loop_handles_sync_failure(mock_sync) -> None:
    mock_sync.return_value = False
    task = Task(
        id="T1",
        title="Task",
        status="awaiting_plan_approval",
        created_at="now",
        updated_at="now",
        jules=JulesSessionInfo(
            session_id="s1",
            session_name="sessions/s1",
            state="AWAITING_PLAN_APPROVAL"
        )
    )
    client = MagicMock()
    output_func = MagicMock()

    outcome = run_feedback_loop(
        task,
        cwd=Path("/tmp"),
        client=client,
        tool_name="codex",
        tool_bin="codex",
        output=output_func
    )

    assert outcome == "failed"
    mock_sync.assert_called_once()
    output_func.assert_any_call("Error: Failed to sync task state. Please check your connection and try again.")
