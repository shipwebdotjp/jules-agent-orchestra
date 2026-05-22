from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import JulesSessionInfo, PullRequestInfo, Run, State, Task, gitPatchInfo
from ...persistence import generate_run_id, save_state
from ..state import get_jules_state_mapping, get_run_sync_status


def handle_import(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
) -> int:
    session_id = args.session_id
    if not session_id.startswith("sessions/"):
        session_id = f"sessions/{session_id}"

    # Deduplication check
    for run in state.runs:
        for task in run.tasks:
            if task.jules and task.jules.session_id == session_id:
                print(f"Session {session_id} is already imported.")
                return 0

    try:
        session = client.get_session(session_id)
    except Exception as e:
        print(f"Error: Failed to fetch session {session_id}: {e}", file=sys.stderr)
        return 1

    # Repository validation
    source_context = session.get("sourceContext", {})
    github_context = source_context.get("githubRepoContext", {})
    source_name = source_context.get("source", "")
    session_repo = ""
    owner = github_context.get("owner")
    repo = github_context.get("repo")
    if owner and repo:
        session_repo = f"{owner}/{repo}"

    # Try to extract repo from source name if it looks like projects/.../sources/...
    # but the API typically returns a source name we can use list_sources on.
    # Alternatively, we can check if we can find the repo in the session metadata.
    # The requirement says "local state's repo and Jules session's repo".

    # Let's try to find the repo by listing sources and matching the source name
    found_repo = None
    try:
        for source in client.list_sources():
            if source.get("name") == source_name:
                gh_repo = source.get("githubRepo", {})
                owner = gh_repo.get("owner")
                name = gh_repo.get("repo")
                if owner and name:
                    found_repo = f"{owner}/{name}"
                break

    except Exception as exc:
        print(
            f"Error: Failed to validate session repository: {exc}",
            file=sys.stderr,
        )

    chosen_repo = found_repo or session_repo
    if chosen_repo and chosen_repo != state.project.repo:
        print(
            f"Warning: Session repository ({chosen_repo}) does not match "
            f"local repository ({state.project.repo}).",
            file=sys.stderr,
        )
        print(
            "Note: Subsequent sync/review/merge commands may not work correctly "
            "if they depend on the local repository state.",
            file=sys.stderr,
        )

    now_iso = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    # Prompt shortening for Run.original_task
    prompt = session.get("prompt", "")
    title = session.get("title")

    original_task = title
    if not original_task:
        # Replace newlines/carriage returns with space
        short_prompt = prompt.replace("\r", " ").replace("\n", " ")
        if len(short_prompt) > 100:
            original_task = short_prompt[:100] + "..."
        else:
            original_task = short_prompt

    run_id = generate_run_id(state)
    run = Run(
        id=run_id,
        original_task=original_task or "Imported Session",
        strategy="single_session",
        status="running", # Initial status, will be updated below
        created_at=now_iso,
        updated_at=now_iso,
    )

    # Task creation
    task = Task(
        id="TASK-001",
        title=title or "Imported Task",
        prompt=prompt,
        status="planned", # Initial status, will be updated below
        created_at=now_iso,
        updated_at=now_iso,
    )

    # Jules Info
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

    # Map status
    task.status = get_jules_state_mapping(task.jules.state, has_pr)
    run.tasks.append(task)

    # Sync run status
    run.status = get_run_sync_status(
        run,
        previous_status="running",
        reopened_from_completed=False,
    )

    state.runs.append(run)
    save_state(cwd, state)

    print(f"Imported session {session_id} as Run {run_id}.")
    return 0
