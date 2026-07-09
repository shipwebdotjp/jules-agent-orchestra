from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jules_agent.cli import main

@patch("jules_agent.cli.sys.stdin.isatty")
@patch("jules_agent.cli.load_state")
@patch("jules_agent.cli.JulesClient")
@patch("jules_agent.cli.GitHubClient")
@patch("jules_agent.cli.tui.start_tui")
def test_main_starts_tui_when_no_command_in_tty(
    mock_start_tui,
    mock_github_client,
    mock_jules_client,
    mock_load_state,
    mock_isatty,
    tmp_path,
) -> None:
    # Set up mocks
    mock_isatty.return_value = True
    mock_load_state.return_value = MagicMock()
    mock_start_tui.return_value = 0

    # Run main with no arguments
    with patch("os.environ", {"JULES_API_KEY": "test-key"}):
        with patch("jules_agent.cli.Path.cwd", return_value=tmp_path):
            # We also need to mock get_git_root and get_git_remote_repo if state is None,
            # but here we return a mock state.
            exit_code = main([])

    assert exit_code == 0
    mock_start_tui.assert_called_once()

@patch("jules_agent.cli.sys.stdin.isatty")
@patch("jules_agent.cli.argparse.ArgumentParser.print_help")
def test_main_prints_help_when_no_command_not_in_tty(
    mock_print_help,
    mock_isatty,
) -> None:
    mock_isatty.return_value = False

    with patch("os.environ", {"JULES_API_KEY": "test-key"}):
        exit_code = main([])

    assert exit_code == 0
    mock_print_help.assert_called_once()

@patch("jules_agent.cli.tui.JulesTUI")
def test_tui_smoke(mock_jules_tui_class):
    from jules_agent.cli.tui import start_tui
    from jules_agent.models import State, ProjectState

    mock_app = MagicMock()
    mock_jules_tui_class.return_value = mock_app

    state = State(project=ProjectState(root="/tmp", repo="owner/repo"))
    client = MagicMock()
    github_client = MagicMock()
    cwd = Path("/tmp")
    config = MagicMock()

    start_tui(state, client, github_client, cwd, config)

    mock_jules_tui_class.assert_called_once_with(state, client, github_client, cwd, config)
    mock_app.run.assert_called_once()
