from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    api_key: str | None = None
    repo: str | None = None
    github_token: str | None = None
    tool_bin: str | None = None
    tool: str = "codex"
    gemini_skip_trust: bool = False
    plan_tool: str | None = None
    approve_tool: str | None = None
    feedback_tool: str | None = None
    review_tool: str | None = None
    base_url: str = "https://jules.googleapis.com/v1alpha"
    merge_method: str = "merge"
    merge_delete_branch: bool = False
    merge_pull: bool = False
    auto_plan_approval: bool = True
    auto_feedback: bool = False
    auto_merge: bool = False
    auto: bool = False
    automation_mode: str | None = None
    skip_review: bool = False
    debug: bool = False


    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(
            api_key=data.get("api_key"),
            repo=data.get("repo"),
            github_token=data.get("github_token"),
            tool_bin=data.get("tool_bin"),
            tool=data.get("tool", "codex"),
            gemini_skip_trust=data.get("gemini_skip_trust", False),
            plan_tool=data.get("plan_tool"),
            approve_tool=data.get("approve_tool"),
            feedback_tool=data.get("feedback_tool"),
            review_tool=data.get("review_tool"),
            base_url=data.get("base_url", "https://jules.googleapis.com/v1alpha"),
            merge_method=data.get("merge_method", "merge"),
            merge_delete_branch=data.get("merge_delete_branch", False),
            merge_pull=data.get("merge_pull", False),
            auto_plan_approval=data.get("auto_plan_approval", True),
            auto_feedback=data.get("auto_feedback", False),
            auto_merge=data.get("auto_merge", False),
            auto=data.get("auto", False),
            automation_mode=data.get("automation_mode"),
            skip_review=data.get("skip_review", False),
            debug=data.get("debug", False),
        )


def load_config(config_path: Path | None = None) -> Config:
    config_data: dict[str, Any] = {}

    # Paths to search in order of increasing priority
    search_paths = [
        Path.home() / ".jules-agent.toml",
        Path.home() / ".config" / "jules-agent" / "config.toml",
        Path.cwd() / ".jules-agent.toml",
        Path.cwd() / "jules-agent.toml",
    ]

    if config_path:
        search_paths.append(config_path)

    for path in search_paths:
        if path.exists() and path.is_file():
            try:
                with path.open("rb") as f:
                    data = tomllib.load(f)
                    config_data.update(data)
            except (tomllib.TOMLDecodeError, OSError):
                continue

    return Config.from_dict(config_data)
