from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..client import JulesClient
from ..models import State, Run, Task, JulesSessionInfo, PullRequestInfo, gitPatchInfo
from ..persistence import generate_run_id, save_state
from ..cli.state import get_jules_state_mapping, get_run_sync_status
from .options import Options
from .results import OperationResult

@dataclass
class ImportOptions(Options):
    session_id_input: str
    output_func: Callable[[str], None] = print
    error_func: Callable[[str], None] = print

class ImportService:
    def __init__(self, state: State, client: JulesClient, cwd: Path):
        self.state = state
        self.client = client
        self.cwd = cwd

    def execute(self, options: ImportOptions) -> OperationResult:
        raw_input = options.session_id_input

        # Extract session ID from various formats
        match = re.search(r"(?:sessions?/|/session/)?(\d+)/?$", raw_input)
        if match:
            session_id = f"sessions/{match.group(1)}"
        else:
            session_id = raw_input
            if not session_id.startswith("sessions/"):
                session_id = f"sessions/{session_id}"

        # Deduplication check
        for run in self.state.runs:
            for task in run.tasks:
                if task.jules and task.jules.session_id == session_id:
                    return OperationResult(exit_code=0, message=f"Session {session_id} is already imported.")

        try:
            session = self.client.get_session(session_id)
        except Exception as e:
            return OperationResult(exit_code=1, message=f"Error: Failed to fetch session {session_id}: {e}")

        # Repository validation
        source_context = session.get("sourceContext", {})
        github_context = source_context.get("githubRepoContext", {})
        source_name = source_context.get("source", "")
        session_repo = ""
        owner = github_context.get("owner")
        repo = github_context.get("repo")
        if owner and repo:
            session_repo = f"{owner}/{repo}"

        found_repo = None
        try:
            for source in self.client.list_sources():
                if source.get("name") == source_name:
                    gh_repo = source.get("githubRepo", {})
                    owner = gh_repo.get("owner")
                    name = gh_repo.get("repo")
                    if owner and name:
                        found_repo = f"{owner}/{name}"
                    break
        except Exception as exc:
            options.error_func(f"Error: Failed to validate session repository: {exc}")

        chosen_repo = found_repo or session_repo
        if chosen_repo and chosen_repo != self.state.project.repo:
            options.error_func(
                f"Warning: Session repository ({chosen_repo}) does not match "
                f"local repository ({self.state.project.repo}).\n"
                "Note: Subsequent sync/review/merge commands may not work correctly "
                "if they depend on the local repository state."
            )

        now_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        prompt = session.get("prompt", "")
        title = session.get("title")

        original_task = title
        if not original_task:
            short_prompt = prompt.replace("\r", " ").replace("\n", " ")
            if len(short_prompt) > 100:
                original_task = short_prompt[:100] + "..."
            else:
                original_task = short_prompt

        run_id = generate_run_id(self.state)
        run = Run(
            id=run_id,
            original_task=original_task or "Imported Session",
            strategy="single_session",
            status="running",
            created_at=now_iso,
            updated_at=now_iso,
        )

        task = Task(
            id="TASK-001",
            title=title or "Imported Task",
            prompt=prompt,
            status="planned",
            created_at=now_iso,
            updated_at=now_iso,
        )

        has_pr = False
        pr_info = None
        code_changes = None
        outputs = session.get("outputs", [])
        for output in outputs:
            pr = output.get("pullRequest")
            if pr:
                pr_info = PullRequestInfo(
                    url=pr.get("url"),
                    title=pr.get("title"),
                    description=pr.get("description"),
                )
                has_pr = True
            changeSet = output.get("changeSet", {})
            if changeSet:
                gitPatch = changeSet.get("gitPatch", None)
                if gitPatch:
                    code_changes = gitPatchInfo(
                        unidiffPatch=gitPatch.get("unidiffPatch", ""),
                        baseCommitId=gitPatch.get("baseCommitId", ""),
                        suggestedCommitMessage=gitPatch.get("suggestedCommitMessage", ""),
                    )

        task.jules = JulesSessionInfo(
            session_id=session["id"],
            session_name=session["name"],
            state=session.get("state", "QUEUED"),
            session_url=session.get("url"),
            create_time=session.get("createTime"),
            update_time=session.get("updateTime"),
            code_changes=code_changes,
        )
        task.pull_request = pr_info
        task.status = get_jules_state_mapping(task.jules.state, has_pr)
        run.tasks.append(task)

        run.status = get_run_sync_status(
            run,
            previous_status="running",
            reopened_from_completed=False,
        )

        self.state.runs.append(run)
        save_state(self.cwd, self.state)

        return OperationResult(exit_code=0, message=f"Imported session {session_id} as Run {run_id}.")
