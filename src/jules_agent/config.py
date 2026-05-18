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
    codex_bin: str = "codex"
    base_url: str = "https://jules.googleapis.com/v1alpha"
    merge_method: str = "merge"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(
            api_key=data.get("api_key"),
            repo=data.get("repo"),
            github_token=data.get("github_token"),
            codex_bin=data.get("codex_bin", "codex"),
            base_url=data.get("base_url", "https://jules.googleapis.com/v1alpha"),
            merge_method=data.get("merge_method", "merge"),
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
