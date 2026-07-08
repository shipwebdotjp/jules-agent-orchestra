from __future__ import annotations

import logging
from pathlib import Path

from ..models import (
    Run,
    State,
    Task,
)
from ..services.state_utils import (
    resolve_task as resolve_task_service,
    extract_pull_request_number as extract_pull_request_number_service,
    sync_pr_created_task as sync_pr_created_task_service,
    sync_task as sync_task_service,
    get_candidates as get_candidates_service,
    sync_task_state as sync_task_state_service,
    get_jules_state_mapping as get_jules_state_mapping_service,
    get_run_sync_status as get_run_sync_status_service,
)
from ..client import JulesClient
from ..github import GitHubClient

logger = logging.getLogger("jules_agent")

def resolve_task(state: State, task_id_arg: str) -> tuple[Run, Task]:
    return resolve_task_service(state, task_id_arg)

def extract_pull_request_number(url: str | None) -> int | None:
    return extract_pull_request_number_service(url)

def sync_pr_created_task(
    github_client: GitHubClient,
    repo: str,
    task: Task,
) -> bool:
    return sync_pr_created_task_service(github_client, repo, task)

def sync_task(client: JulesClient, task: Task) -> bool:
    return sync_task_service(client, task)

def get_candidates(state: State, command: str) -> list[tuple[Run, Task]]:
    return get_candidates_service(state, command)

def sync_task_state(
    client: JulesClient,
    github_client: GitHubClient | None,
    state: State,
    run: Run,
    task: Task,
    cwd: Path,
) -> bool:
    return sync_task_state_service(client, github_client, state, run, task, cwd)

def get_jules_state_mapping(jules_state: str, has_pr: bool):
    return get_jules_state_mapping_service(jules_state, has_pr)

def get_run_sync_status(run: Run, previous_status, reopened_from_completed: bool):
    return get_run_sync_status_service(run, previous_status=previous_status, reopened_from_completed=reopened_from_completed)
