from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..client import JulesClient
from ..github import GitHubClient
from ..models import State, Task
from ..pipeline import perform_task_review
from .options import Options
from .results import OperationResult

@dataclass
class ReviewOptions(Options):
    task: Task
    tool_name: str = "codex"
    tool_bin: Optional[str] = None
    gemini_skip_trust: bool = False

class ReviewService:
    def __init__(self, state: State, client: JulesClient, github_client: GitHubClient, cwd: Path):
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd

    def execute(self, options: ReviewOptions) -> OperationResult:
        try:
            perform_task_review(
                task=options.task,
                state=self.state,
                github_client=self.github_client,
                cwd=self.cwd,
                tool_name=options.tool_name,
                tool_bin=options.tool_bin,
                gemini_skip_trust=options.gemini_skip_trust,
            )
            return OperationResult(exit_code=0)
        except Exception as e:
            return OperationResult(exit_code=1, message=str(e))
