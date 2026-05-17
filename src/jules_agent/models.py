from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ExecutionStrategy = Literal["single_session", "parallel_subtasks", "sequential_subtasks"]

RunStatus = Literal["planned", "running", "completed", "failed", "cancelled"]

TaskStatus = Literal[
    "planned",
    "dispatching",
    "dispatched",
    "planning",
    "awaiting_plan_approval",
    "plan_approved",
    "in_progress",
    "awaiting_user_feedback",
    "paused",
    "completed",
    "pr_created",
    "reviewing",
    "needs_fix",
    "waiting_human_review",
    "merged",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class Subtask:
    title: str
    details: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionPlan:
    strategy: ExecutionStrategy
    tasks: list[Subtask]


@dataclass
class JulesSessionInfo:
    session_id: str
    session_name: str
    state: str
    session_url: str | None = None
    create_time: str | None = None
    update_time: str | None = None
    activities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "session_url": self.session_url,
            "state": self.state,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "activities": self.activities,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JulesSessionInfo:
        return cls(
            session_id=data["session_id"],
            session_name=data["session_name"],
            state=data["state"],
            session_url=data.get("session_url"),
            create_time=data.get("create_time"),
            update_time=data.get("update_time"),
            activities=data.get("activities", []),
        )


@dataclass
class PullRequestInfo:
    url: str
    title: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PullRequestInfo:
        return cls(
            url=data["url"],
            title=data.get("title"),
            description=data.get("description"),
        )


@dataclass
class Task:
    id: str
    title: str
    status: TaskStatus
    created_at: str
    updated_at: str
    description: str | None = None
    prompt: str | None = None
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    jules: JulesSessionInfo | None = None
    pull_request: PullRequestInfo | None = None
    attempts: int = 0
    max_attempts: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "prompt": self.prompt,
            "status": self.status,
            "depends_on": self.depends_on,
            "acceptance_criteria": self.acceptance_criteria,
            "out_of_scope": self.out_of_scope,
            "jules": self.jules.to_dict() if self.jules else None,
            "pull_request": self.pull_request.to_dict() if self.pull_request else None,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        jules_data = data.get("jules")
        pr_data = data.get("pull_request")
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description"),
            prompt=data.get("prompt"),
            status=data["status"],  # type: ignore
            depends_on=data.get("depends_on", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            out_of_scope=data.get("out_of_scope", []),
            jules=JulesSessionInfo.from_dict(jules_data) if jules_data else None,
            pull_request=PullRequestInfo.from_dict(pr_data) if pr_data else None,
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass
class Run:
    id: str
    original_task: str
    strategy: ExecutionStrategy
    status: RunStatus
    created_at: str
    updated_at: str
    tasks: list[Task] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original_task": self.original_task,
            "strategy": self.strategy,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        return cls(
            id=data["id"],
            original_task=data["original_task"],
            strategy=data["strategy"],  # type: ignore
            status=data["status"],  # type: ignore
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            tasks=[Task.from_dict(t) for t in data.get("tasks", [])],
        )


@dataclass
class ProjectState:
    root: str
    repo: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectState:
        return cls(
            root=data["root"],
            repo=data["repo"],
        )


@dataclass
class State:
    project: ProjectState
    runs: list[Run] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project": self.project.to_dict(),
            "runs": [r.to_dict() for r in self.runs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        return cls(
            schema_version=data.get("schema_version", 1),
            project=ProjectState.from_dict(data["project"]),
            runs=[Run.from_dict(r) for r in data.get("runs", [])],
        )


@dataclass(frozen=True)
class DispatchResult:
    index: int
    subtask: Subtask
    session_id: str | None
    raw_output: str
    returncode: int
    url: str | None = None
    error_message: str | None = None
