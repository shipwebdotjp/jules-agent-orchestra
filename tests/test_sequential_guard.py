from __future__ import annotations

import argparse
from pathlib import Path
import pytest
from jules_agent.models import State, ProjectState, Run, Task
from jules_agent.cli.state import get_candidates

def test_get_candidates_next_blocks_if_prior_not_terminal():
    # Setup a sequential run where the first task is in_progress
    # and the second task is planned.
    task1 = Task(
        id="T1", title="Task 1", status="in_progress",
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z"
    )
    task2 = Task(
        id="T2", title="Task 2", status="planned",
        created_at="2024-01-01T00:00:00Z", updated_at="2024-01-01T00:00:00Z"
    )
    run = Run(
        id="R1", strategy="sequential_subtasks", status="running",
        original_task="test", created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z", tasks=[task1, task2]
    )
    state = State(project=ProjectState(root=".", repo="owner/repo"), runs=[run])

    # T2 should NOT be a candidate for 'next' because T1 is still in_progress
    candidates = get_candidates(state, "next")
    assert candidates == []

    # Mark T1 as terminal
    task1.status = "merged"
    candidates = get_candidates(state, "next")
    assert len(candidates) == 1
    assert candidates[0][1].id == "T2"

    # Setup another case where an earlier task is 'failed'
    task1.status = "failed"
    # Even if failed, it should probably block the next task?
    # Current terminal check is ("completed", "merged")
    candidates = get_candidates(state, "next")
    assert candidates == []
