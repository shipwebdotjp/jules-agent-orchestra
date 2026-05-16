from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExecutionStrategy = Literal["single_session", "parallel_subtasks", "sequential_subtasks"]


@dataclass(frozen=True)
class Subtask:
    title: str
    details: str | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    strategy: ExecutionStrategy
    tasks: list[Subtask]


@dataclass(frozen=True)
class DispatchResult:
    index: int
    subtask: Subtask
    session_id: str | None
    raw_output: str
    returncode: int
    url: str | None = None
    error_message: str | None = None
