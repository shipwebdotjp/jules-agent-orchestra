from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import argparse
import sys
import datetime
from pathlib import Path

from jules_agent.models import State, ProjectState, Run, Task, JulesSessionInfo, PullRequestInfo
from jules_agent.cli import build_parser
from jules_agent.cli.state import get_candidates
from jules_agent.cli.io import select_task_interactively

class TestInteractiveSelection(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        self.task1 = Task(
            id="task1",
            title="Task 1",
            status="awaiting_plan_approval",
            created_at=self.now,
            updated_at=self.now,
            jules=JulesSessionInfo(session_id="s1", session_name="sn1", state="AWAITING_PLAN_APPROVAL")
        )
        self.task2 = Task(
            id="task2",
            title="Task 2",
            status="pr_created",
            created_at=self.now,
            updated_at=self.now,
            pull_request=PullRequestInfo(url="http://github.com/pull/1")
        )
        self.run1 = Run(
            id="run1",
            original_task="test run",
            strategy="single_session",
            status="running",
            created_at=self.now,
            updated_at=self.now,
            tasks=[self.task1, self.task2]
        )
        self.state = State(
            project=ProjectState(root="/tmp", repo="owner/repo"),
            runs=[self.run1]
        )

    def test_get_candidates_approve(self):
        candidates = get_candidates(self.state, "approve")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1].id, "task1")

    def test_get_candidates_review(self):
        candidates = get_candidates(self.state, "review")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1].id, "task2")

    def test_get_candidates_send(self):
        candidates = get_candidates(self.state, "send")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1].id, "task1")

    @patch("sys.stdin.isatty", return_value=True)
    def test_select_task_interactively_success(self, mock_isatty):
        candidates = [(self.run1, self.task1), (self.run1, self.task2)]

        # Test selecting 1
        run, task = select_task_interactively(candidates, "approve", input_func=lambda _: "1")
        self.assertEqual(task.id, "task1")

        # Test selecting 2
        run, task = select_task_interactively(candidates, "approve", input_func=lambda _: "2")
        self.assertEqual(task.id, "task2")

    @patch("sys.stdin.isatty", return_value=False)
    def test_select_task_interactively_non_tty(self, mock_isatty):
        from jules_agent.codex import PipelineError
        candidates = [(self.run1, self.task1)]
        with self.assertRaises(PipelineError) as cm:
            select_task_interactively(candidates, "approve")
        self.assertIn("stdin is not interactive", str(cm.exception))

    def test_argparse_send_omitted_task_id(self):
        parser = build_parser()
        args = parser.parse_args(["send", "hello world"])
        self.assertEqual(args.command, "send")
        self.assertEqual(args.args, ["hello world"])

    def test_argparse_send_with_task_id(self):
        parser = build_parser()
        args = parser.parse_args(["send", "run1:task1", "hello", "world"])
        self.assertEqual(args.command, "send")
        self.assertEqual(args.args, ["run1:task1", "hello", "world"])

    def test_argparse_optional_task_id(self):
        parser = build_parser()
        for cmd in ["approve", "feedback", "review", "merge"]:
            args = parser.parse_args([cmd])
            self.assertEqual(args.command, cmd)
            self.assertIsNone(args.task_id)

if __name__ == "__main__":
    unittest.main()
