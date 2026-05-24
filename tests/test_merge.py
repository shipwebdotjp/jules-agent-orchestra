import argparse
import datetime
from pathlib import Path
import pytest
import respx
import httpx
from jules_agent.cli.commands.merge import handle_merge
from jules_agent.models import State, ProjectState, Run, Task, PullRequestInfo
from jules_agent.config import Config
from jules_agent.github import GitHubClient

@pytest.fixture
def state():
    return State(
        project=ProjectState(root=".", repo="owner/repo"),
        runs=[
            Run(
                id="run_1",
                original_task="test",
                strategy="single_session",
                status="running",
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
                tasks=[
                    Task(
                        id="task_1",
                        title="Task 1",
                        status="pr_created",
                        created_at="2024-01-01T00:00:00Z",
                        updated_at="2024-01-01T00:00:00Z",
                        pull_request=PullRequestInfo(url="https://github.com/owner/repo/pull/123")
                    ),
                    Task(
                        id="task_2",
                        title="Task 2",
                        status="in_progress",
                        created_at="2024-01-01T00:00:00Z",
                        updated_at="2024-01-01T00:00:00Z"
                    )
                ]
            )
        ]
    )

@respx.mock
def test_handle_merge_success(state, tmp_path):
    repo = "owner/repo"
    pull_number = 123

    # Mock GET PR
    respx.get(f"https://api.github.com/repos/{repo}/pulls/{pull_number}").mock(
        return_value=httpx.Response(200, json={"mergeable": True})
    )

    # Mock PUT merge
    respx.put(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(200, json={"merged": True})
    )

    args = argparse.Namespace(task_id="run_1:task_1", merge_method=None)
    config = Config()
    github_client = GitHubClient(token="test-token")

    # We need a parser that doesn't actually exit the process
    parser = argparse.ArgumentParser()

    result = handle_merge(args, state, None, github_client, tmp_path, config, parser)

    assert result == 0
    assert state.runs[0].tasks[0].status == "merged"

@respx.mock
def test_handle_merge_not_mergeable(state, tmp_path):
    repo = "owner/repo"
    pull_number = 123

    # Mock GET PR
    respx.get(f"https://api.github.com/repos/{repo}/pulls/{pull_number}").mock(
        return_value=httpx.Response(200, json={"mergeable": False})
    )

    args = argparse.Namespace(task_id="run_1:task_1", merge_method="squash")
    config = Config()
    github_client = GitHubClient(token="test-token")

    parser = argparse.ArgumentParser()
    # Mock parser.exit to raise an exception instead of exiting
    def mock_exit(status=0, message=None):
        raise SystemExit(message)
    parser.exit = mock_exit

    with pytest.raises(SystemExit) as excinfo:
        handle_merge(args, state, None, github_client, tmp_path, config, parser)

    assert "not mergeable" in str(excinfo.value)
    assert state.runs[0].tasks[0].status == "pr_created"

@respx.mock
def test_handle_merge_wrong_status(state, tmp_path):
    args = argparse.Namespace(task_id="run_1:task_2", merge_method=None)
    config = Config()
    github_client = GitHubClient(token="test-token")

    parser = argparse.ArgumentParser()
    def mock_exit(status=0, message=None):
        raise SystemExit(message)
    parser.exit = mock_exit

    with pytest.raises(SystemExit) as excinfo:
        handle_merge(args, state, None, github_client, tmp_path, config, parser)

    assert "required to merge" in str(excinfo.value)

@respx.mock
def test_handle_merge_no_task_id_calls_sync(state, tmp_path, mocker):
    args = argparse.Namespace(task_id=None, merge_method=None)
    config = Config()
    github_client = GitHubClient(token="test-token")

    # Mock handle_sync
    mock_sync = mocker.patch("jules_agent.cli.commands.merge.handle_sync")

    # Mock select_task_interactively
    mocker.patch("jules_agent.cli.commands.merge.select_task_interactively", return_value=(state.runs[0], state.runs[0].tasks[0]))

    # Mock sync_task_state
    mocker.patch("jules_agent.cli.commands.merge.sync_task_state")

    # Mock github_client methods
    repo = "owner/repo"
    pull_number = 123
    respx.get(f"https://api.github.com/repos/{repo}/pulls/{pull_number}").mock(
        return_value=httpx.Response(200, json={"mergeable": True})
    )
    respx.put(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(200, json={"merged": True})
    )

    parser = argparse.ArgumentParser()

    result = handle_merge(args, state, None, github_client, tmp_path, config, parser)

    assert result == 0
    mock_sync.assert_called_once_with(args, state, None, github_client, tmp_path)
