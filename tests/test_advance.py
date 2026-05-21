from unittest.mock import MagicMock, patch
import argparse
from pathlib import Path
from jules_agent.cli import build_parser
from jules_agent.config import Config
from jules_agent.models import State, ProjectState, Run, Task
from jules_agent.cli.commands.advance import handle_advance

def test_parser_advance():
    parser = build_parser()
    args = parser.parse_args(["advance", "--auto"])
    assert args.command == "advance"
    assert args.auto
    assert not args.auto_plan_approval
    assert not args.auto_feedback

    args = parser.parse_args(
        [
            "advance",
            "--auto-plan-approval",
            "--auto-feedback",
        ]
    )
    assert not args.auto
    assert args.auto_plan_approval
    assert args.auto_feedback

@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.save_state")
def test_handle_advance_no_tasks(mock_save, mock_sync):
    mock_sync.return_value = 0
    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[])
    args = argparse.Namespace(
        auto=False,
        auto_plan_approval=False,
        auto_feedback=False,
    )
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    result = handle_advance(args, state, client, github_client, Path("/tmp"), config)
    assert result == 0
    mock_sync.assert_called_once()

@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.sync_task")
@patch("jules_agent.cli.commands.feedback.run_feedback_loop")
def test_handle_advance_picks_latest_task(
    mock_feedback_loop, mock_sync_task, mock_sync
):
    mock_sync.return_value = 0
    mock_sync_task.return_value = True
    mock_feedback_loop.return_value = "skipped"  # Stop loop

    task1 = Task(
        id="1",
        title="T1",
        status="awaiting_plan_approval",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
    )
    task2 = Task(
        id="2",
        title="T2",
        status="awaiting_plan_approval",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T01:00:00Z",
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="single_session",
        status="running",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
        tasks=[task1, task2],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(
        auto=False,
        auto_plan_approval=False,
        auto_feedback=False,
    )
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    handle_advance(args, state, client, github_client, Path("/tmp"), config)

    # Verify that task2 (later updated_at) was passed to run_feedback_loop
    mock_feedback_loop.assert_called_once()
    called_task = mock_feedback_loop.call_args[0][0]
    assert called_task.id == "2"

@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.sync_task")
@patch("jules_agent.cli.commands.feedback.run_feedback_loop")
@patch("jules_agent.cli.advance_core.save_state")
def test_handle_advance_stops_after_one_step(
    mock_save, mock_feedback_loop, mock_sync_task, mock_sync
):
    mock_sync.return_value = 0
    mock_sync_task.return_value = True
    mock_feedback_loop.return_value = "completed"  # Action taken

    task = Task(
        id="1",
        title="T1",
        status="awaiting_plan_approval",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="single_session",
        status="running",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
        tasks=[task],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(
        auto=False,
        auto_plan_approval=False,
        auto_feedback=False,
    )
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    handle_advance(args, state, client, github_client, Path("/tmp"), config)

    # Now it must call it exactly once.
    mock_feedback_loop.assert_called_once()
    mock_sync_task.assert_called_once()
    mock_save.assert_called_once()

@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.sync_task")
@patch("jules_agent.cli.commands.feedback.run_feedback_loop")
@patch("jules_agent.cli.advance_core.save_state")
def test_handle_advance_save_even_on_sync_failure(
    mock_save, mock_feedback_loop, mock_sync_task, mock_sync
):
    mock_sync.return_value = 0
    mock_sync_task.return_value = False # Sync fails
    mock_feedback_loop.return_value = "completed"  # Action taken

    task = Task(
        id="1",
        title="T1",
        status="awaiting_plan_approval",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="single_session",
        status="running",
        created_at="2023-01-01T00:00:00Z",
        updated_at="2023-01-01T00:00:00Z",
        tasks=[task],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(
        auto=False,
        auto_plan_approval=False,
        auto_feedback=False,
    )
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    handle_advance(args, state, client, github_client, Path("/tmp"), config)

    mock_feedback_loop.assert_called_once()
    mock_sync_task.assert_called_once()
    mock_save.assert_called_once()


@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.dispatch_task")
def test_handle_advance_dispatches_planned_fallback(mock_dispatch_task, mock_sync):
    mock_sync.return_value = 0

    now = "2023-01-01T00:00:00Z"
    task1 = Task(
        id="1",
        title="T1",
        status="merged",
        created_at=now,
        updated_at=now,
    )
    task2 = Task(
        id="2",
        title="T2",
        status="planned",
        created_at=now,
        updated_at=now,
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="sequential_subtasks",
        status="running",
        created_at=now,
        updated_at=now,
        tasks=[task1, task2],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(auto=False, auto_plan_approval=False, auto_feedback=False)
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    handle_advance(args, state, client, github_client, Path("/tmp"), config)

    mock_dispatch_task.assert_called_once()
    assert mock_dispatch_task.call_args.kwargs["task"].id == "2"


@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.dispatch_task")
@patch("jules_agent.cli.advance_core.sync_task")
@patch("jules_agent.cli.advance_core.save_state")
@patch("jules_agent.cli.advance_core.AdvanceEngine._attempt_merge")
def test_handle_advance_dispatches_next_after_merge(
    mock_attempt_merge,
    mock_save,
    mock_sync_task,
    mock_dispatch_task,
    mock_sync,
):
    mock_sync.return_value = 0
    mock_sync_task.return_value = True
    mock_attempt_merge.return_value = True

    now = "2023-01-01T00:00:00Z"
    task1 = Task(
        id="1",
        title="T1",
        status="pr_created",
        created_at=now,
        updated_at=now,
    )
    task2 = Task(
        id="2",
        title="T2",
        status="planned",
        created_at=now,
        updated_at=now,
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="sequential_subtasks",
        status="running",
        created_at=now,
        updated_at=now,
        tasks=[task1, task2],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(
        auto=False,
        auto_plan_approval=False,
        auto_feedback=False,
        auto_merge=True,
    )
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    handle_advance(args, state, client, github_client, Path("/tmp"), config)

    mock_attempt_merge.assert_called_once()
    mock_dispatch_task.assert_called_once()
    assert mock_dispatch_task.call_args.kwargs["task"].id == "2"
    mock_save.assert_called_once()


@patch("jules_agent.cli.commands.advance.handle_sync")
@patch("jules_agent.cli.advance_core.dispatch_task")
def test_handle_advance_does_not_dispatch_single_session_planned(
    mock_dispatch_task, mock_sync
):
    mock_sync.return_value = 0

    now = "2023-01-01T00:00:00Z"
    task1 = Task(
        id="1",
        title="T1",
        status="planned",
        created_at=now,
        updated_at=now,
    )

    run = Run(
        id="run1",
        original_task="test",
        strategy="single_session",
        status="running",
        created_at=now,
        updated_at=now,
        tasks=[task1],
    )

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
    args = argparse.Namespace(auto=False, auto_plan_approval=False, auto_feedback=False)
    client = MagicMock()
    github_client = MagicMock()
    config = Config()

    result = handle_advance(args, state, client, github_client, Path("/tmp"), config)

    assert result == 0
    mock_dispatch_task.assert_not_called()
