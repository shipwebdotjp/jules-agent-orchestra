from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jules_agent.cli import build_review_prompt, run_confirmation_loop


class CliTests(unittest.TestCase):
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
                    '{"subtasks":[{"title":"Plan"}]}'
                    if call_count == 0
                    else '{"subtasks":[{"title":"Plan revised"}]}'
                )
                last_message_path.write_text(payload, encoding="utf-8")
                call_count += 1
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            subtasks, feedback_history = run_confirmation_loop(
                "Build a CLI",
                cwd=Path(tmpdir),
                codex_bin="codex",
                runner=runner,
                input_func=input_func,
                output=output_func,
            )

        self.assertEqual([subtask.title for subtask in subtasks], ["Plan revised"])
        self.assertEqual(feedback_history, ["Add tests"])
        self.assertIn("Proposed subtasks:", outputs)
        self.assertIn("Revising subtasks with feedback...", outputs)
        self.assertEqual(len(calls), 2)
        self.assertIn("User feedback from the previous plan:", calls[1][-1])
        self.assertIn("Add tests", calls[1][-1])


if __name__ == "__main__":
    unittest.main()
