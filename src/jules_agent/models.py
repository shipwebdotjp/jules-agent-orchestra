from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Subtask:
    title: str
    details: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    index: int
    subtask: Subtask
    session_id: str | None
    raw_output: str
    returncode: int
    error_message: str | None = None
