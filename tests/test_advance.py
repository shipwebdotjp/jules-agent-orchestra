import unittest
from unittest.mock import MagicMock, patch
import argparse
from pathlib import Path
from jules_agent.cli import build_parser
from jules_agent.config import Config
from jules_agent.models import State, ProjectState, Run, Task
from jules_agent.cli.commands.advance import handle_advance

class TestAdvance(unittest.TestCase):
    def test_parser_advance(self):
        parser = build_parser()
        args = parser.parse_args(["advance", "--auto"])
        self.assertEqual(args.command, "advance")
        self.assertEqual(args.advance_mode, "auto")

    def test_config_advance_mode(self):
        config = Config.from_dict({"advance_mode": "auto"})
        self.assertEqual(config.advance_mode, "auto")

        config_default = Config.from_dict({})
        self.assertEqual(config_default.advance_mode, "interactive")

    @patch("jules_agent.cli.commands.advance.handle_sync")
    @patch("jules_agent.cli.commands.advance.save_state")
    def test_handle_advance_no_tasks(self, mock_save, mock_sync):
        mock_sync.return_value = 0
        state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[])
        args = argparse.Namespace(advance_mode=None)
        client = MagicMock()
        github_client = MagicMock()
        config = Config()

        result = handle_advance(args, state, client, github_client, Path("/tmp"), config)
        self.assertEqual(result, 0)
        mock_sync.assert_called_once()

    @patch("jules_agent.cli.commands.advance.handle_sync")
    @patch("jules_agent.cli.commands.advance.sync_task")
    @patch("jules_agent.cli.commands.advance._handle_interactive")
    def test_handle_advance_picks_latest_task(self, mock_handle_int, mock_sync_task, mock_sync):
        mock_sync.return_value = 0
        mock_sync_task.return_value = True
        mock_handle_int.return_value = False # Stop loop

        task1 = Task(id="1", title="T1", status="awaiting_plan_approval",
                     created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T00:00:00Z")
        task2 = Task(id="2", title="T2", status="awaiting_plan_approval",
                     created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T01:00:00Z")

        run = Run(id="run1", original_task="test", strategy="single_session", status="running",
                  created_at="2023-01-01T00:00:00Z", updated_at="2023-01-01T00:00:00Z",
                  tasks=[task1, task2])

        state = State(project=ProjectState(root="/tmp", repo="owner/repo"), runs=[run])
        args = argparse.Namespace(advance_mode="interactive")
        client = MagicMock()
        github_client = MagicMock()
        config = Config()

        handle_advance(args, state, client, github_client, Path("/tmp"), config)

        # Verify that task2 (later updated_at) was passed to _handle_interactive
        mock_handle_int.assert_called_once()
        called_task = mock_handle_int.call_args[0][0]
        self.assertEqual(called_task.id, "2")

if __name__ == "__main__":
    unittest.main()
