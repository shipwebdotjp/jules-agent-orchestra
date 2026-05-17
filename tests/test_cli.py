from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jules_agent.cli import (
    build_review_prompt,
    extract_pull_request_number,
    get_run_sync_status,
    run_confirmation_loop,
    sync_pr_created_task,
)
from jules_agent.models import PullRequestInfo, Run, Task


class CliTests(unittest.TestCase):
    def _make_pr_created_task(self, url: str = "https://github.com/owner/repo/pull/5") -> Task:
        return Task(
            id="TASK-001",
            title="Example task",
            status="pr_created",
            created_at="2026-05-17T00:00:00Z",
            updated_at="2026-05-17T00:00:00Z",
            pull_request=PullRequestInfo(url=url),
        )

    def test_build_review_prompt_includes_feedback_history(self) -> None:
        prompt = build_review_prompt(
            "Build a CLI",
            ["The plan needs more tests.", "Reorder the steps."],
        )

        self.assertIn("Build a CLI", prompt)
        self.assertIn("1. The plan needs more tests.", prompt)
        self.assertIn("2. Reorder the steps.", prompt)

    def test_run_confirmation_loop_retries_until_approved(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]
        outputs: list[str] = []
        calls: list[list[str]] = []
        prompts = iter(["n", "Add tests", "y"])
        call_count = 0

        def input_func(prompt: str) -> str:
            self.assertIsInstance(prompt, str)
            return next(prompts)

        def output_func(message: str) -> None:
            outputs.append(message)

        def runner(args, *, cwd=None, input_text=None):
            nonlocal call_count
            calls.append(list(args))
            if "--output-last-message" in args:
                last_message_path = Path(args[args.index("--output-last-message") + 1])
                payload = (
                    '{"strategy":"single_session","tasks":[{"title":"Plan"}]}'
                    if call_count == 0
                    else '{"strategy":"single_session","tasks":[{"title":"Plan revised"}]}'
                )
                last_message_path.write_text(payload, encoding="utf-8")
                call_count += 1
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            plan = run_confirmation_loop(
                "Build a CLI",
                cwd=Path(tmpdir),
                codex_bin="codex",
                runner=runner,
                input_func=input_func,
                output=output_func,
            )

        self.assertEqual(plan.strategy, "single_session")
        self.assertEqual([task.title for task in plan.tasks], ["Plan revised"])
        self.assertIn("Proposed strategy: single_session", outputs)
        self.assertIn("Proposed tasks:", outputs)
        self.assertIn("Revising plan with feedback...", outputs)
        self.assertEqual(len(calls), 2)
        self.assertIn("User feedback from the previous plan:", calls[1][-1])
        self.assertIn("Add tests", calls[1][-1])

    def test_extract_pull_request_number_accepts_common_github_urls(self) -> None:
        self.assertEqual(
            extract_pull_request_number("https://github.com/owner/repo/pull/5"),
            5,
        )
        self.assertEqual(
            extract_pull_request_number(
                "https://api.github.com/repos/owner/repo/pulls/42"
            ),
            42,
        )

    def test_sync_pr_created_task_updates_status_from_github(self) -> None:
        cases = [
            ({"state": "open", "merged_at": None}, False, "pr_created"),
            (
                {"state": "closed", "merged_at": "2026-05-17T00:00:00Z"},
                True,
                "merged",
            ),
            ({"state": "closed", "merged_at": None}, True, "pr_closed"),
        ]

        for payload, expected_changed, expected_status in cases:
            with self.subTest(payload=payload):
                github_client = MagicMock()
                github_client.get_pull_request.return_value = payload
                task = self._make_pr_created_task()

                changed = sync_pr_created_task(github_client, "owner/repo", task)

                self.assertEqual(changed, expected_changed)
                self.assertEqual(task.status, expected_status)
                github_client.get_pull_request.assert_called_once_with("owner/repo", 5)

    def test_get_run_sync_status_reopens_completed_runs_with_pending_prs(self) -> None:
        run = Run(
            id="run-1",
            original_task="Example",
            strategy="single_session",
            status="completed",
            created_at="2026-05-17T00:00:00Z",
            updated_at="2026-05-17T00:00:00Z",
            tasks=[
                Task(
                    id="TASK-001",
                    title="Example task",
                    status="pr_created",
                    created_at="2026-05-17T00:00:00Z",
                    updated_at="2026-05-17T00:00:00Z",
                    pull_request=PullRequestInfo(
                        url="https://github.com/owner/repo/pull/5"
                    ),
                )
            ],
        )

        self.assertEqual(
            get_run_sync_status(
                run,
                previous_status="completed",
                reopened_from_completed=True,
            ),
            "running",
        )


if __name__ == "__main__":
    unittest.main()
