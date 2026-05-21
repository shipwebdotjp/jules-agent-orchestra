from __future__ import annotations

import argparse
from jules_agent.models import State, ProjectState, Run
from jules_agent.cli.commands.status import handle_status

def test_handle_status_filtering(capsys):
    state = State(
        project=ProjectState(root=".", repo="owner/repo"),
        runs=[
            Run(
                id="run-completed",
                original_task="Completed Task",
                strategy="single_session",
                status="completed",
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
                tasks=[]
            ),
            Run(
                id="run-running",
                original_task="Running Task",
                strategy="single_session",
                status="running",
                created_at="2024-01-01T01:00:00Z",
                updated_at="2024-01-01T01:00:00Z",
                tasks=[]
            )
        ]
    )

    # Default: only running and planned
    args = argparse.Namespace(all=False, show_activities=False)
    handle_status(args, state)
    captured = capsys.readouterr()
    assert "Run: run-running [running] - Running Task" in captured.out
    assert "Run: run-completed" not in captured.out

    # --all: show everything
    args = argparse.Namespace(all=True, show_activities=False)
    handle_status(args, state)
    captured = capsys.readouterr()
    assert "Run: run-running [running] - Running Task" in captured.out
    assert "Run: run-completed [completed] - Completed Task" in captured.out

def test_handle_status_no_active_runs(capsys):
    state = State(
        project=ProjectState(root=".", repo="owner/repo"),
        runs=[
            Run(
                id="run-completed",
                original_task="Completed Task",
                strategy="single_session",
                status="completed",
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
                tasks=[]
            )
        ]
    )

    args = argparse.Namespace(all=False, show_activities=False)
    handle_status(args, state)
    captured = capsys.readouterr()
    assert "No planned or running runs found. Use --all to see all runs." in captured.out
