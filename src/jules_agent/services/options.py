from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RunOptions:
    task: str
    no_confirm: bool = False
    auto_plan_approval: bool = False
    automation_mode: str | None = None


@dataclass(frozen=True)
class ImportOptions:
    session_id: str


@dataclass(frozen=True)
class AdvanceOptions:
    auto_plan_approval: bool | None = None
    auto_feedback: bool | None = None
    auto_merge: bool | None = None
    auto: bool = False
    skip_review: bool | None = None
    json: bool = False


@dataclass(frozen=True)
class StatusOptions:
    all: bool = False
    show_activities: bool = False


@dataclass(frozen=True)
class ApproveOptions:
    task_id: str | None = None


@dataclass(frozen=True)
class SendOptions:
    args: list[str]


@dataclass(frozen=True)
class FeedbackOptions:
    task_id: str | None = None


@dataclass(frozen=True)
class ReviewOptions:
    task_id: str | None = None


@dataclass(frozen=True)
class ReviewPassOptions:
    task_id: str | None = None


@dataclass(frozen=True)
class MergeOptions:
    task_id: str | None = None
    delete_branch: bool | None = None
    pull: bool | None = None
    merge_method: Literal["merge", "squash", "rebase"] | None = None


@dataclass(frozen=True)
class NextOptions:
    run_id: str | None = None
    automation_mode: str | None = None


@dataclass(frozen=True)
class DeleteOptions:
    subcommand: Literal["run", "task"]
    run_id: str | None = None
    task_id: str | None = None
    dry_run: bool = False
    yes: bool = False
