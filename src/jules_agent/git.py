from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def get_git_root(cwd: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return Path(completed.stdout.strip())
    except OSError:
        pass
    return cwd


def is_git_repo(cwd: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def get_git_branch(cwd: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
    except OSError:
        pass
    return "main"


def get_git_remote_repo(cwd: Path) -> tuple[str, str] | None:
    try:
        completed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        url = completed.stdout.strip()
        # Matches patterns like:
        # https://github.com/owner/repo.git
        # git@github.com:owner/repo.git
        patterns = [
            r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$",
            r"https?://[^/]+/([^/]+)/([^/.]+)(?:\.git)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)
    except OSError:
        pass
    return None
