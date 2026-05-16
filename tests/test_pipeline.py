from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jules_agent.pipeline import (
    codex_schema,
    decompose_task,
    dispatch_subtasks,
    PipelineError,
    normalize_plan,
    normalize_subtasks,
    parse_json_document,
    run_pipeline,
)
from jules_agent.models import Subtask


class PipelineTests(unittest.TestCase):
    def test_parse_json_document_accepts_code_fences(self) -> None:
        payload = parse_json_document(
            """```json
            {"strategy":"single_session","tasks":[{"title":"First"}]}
            ```"""
        )
        self.assertEqual(payload["tasks"][0]["title"], "First")

    def test_normalize_plan_accepts_objects_and_strings(self) -> None:
        plan = normalize_plan(
            {
                "strategy": "single_session",
                "tasks": [{"title": "Alpha", "details": "Do the first thing."}],
            }
        )
        self.assertEqual(plan.strategy, "single_session")
        self.assertEqual(plan.tasks[0].title, "Alpha")
        self.assertEqual(plan.tasks[0].details, "Do the first thing.")

    def test_normalize_subtasks_accepts_legacy_payloads(self) -> None:
        subtasks = normalize_subtasks(
            {
                "subtasks": [
                    {"title": "Alpha", "details": "Do the first thing."},
                    "Beta",
                ]
            }
        )
        self.assertEqual(subtasks[0].title, "Alpha")
        self.assertEqual(subtasks[0].details, "Do the first thing.")
        self.assertEqual(subtasks[1].title, "Beta")

    def test_codex_schema_closes_subtask_objects(self) -> None:
        schema = codex_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["strategy"]["enum"]),
            {"single_session", "parallel_subtasks", "sequential_subtasks"},
        )
        self.assertEqual(
            schema["properties"]["tasks"]["items"]["required"],
            ["title"],
        )
        self.assertFalse(
            schema["properties"]["tasks"]["items"]["additionalProperties"]
        )
        self.assertEqual(
            set(schema["properties"]["tasks"]["items"]["properties"].keys()),
            {"title", "details", "description", "body"},
        )

    def test_decompose_task_invokes_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            responses = [
                subprocess.CompletedProcess(
                    args=["codex"],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
            ]

            def runner(args, *, cwd=None, input_text=None):
                if "--output-last-message" in args:
                    last_message_path = Path(args[args.index("--output-last-message") + 1])
                    last_message_path.write_text(
                        '{"strategy":"parallel_subtasks","tasks":[{"title":"Plan"},{"title":"Implement"}]}',
                        encoding="utf-8",
                    )
                return responses.pop(0)

            plan = decompose_task(
                "build a cli",
                cwd=cwd,
                runner=runner,
            )

        self.assertEqual(plan.strategy, "parallel_subtasks")
        self.assertEqual([task.title for task in plan.tasks], ["Plan", "Implement"])

    def test_dispatch_subtasks_invokes_api(self) -> None:
        client = MagicMock()
        client.list_sources.return_value = [
            {"name": "sources/1", "githubRepo": {"owner": "o1", "repo": "r1"}}
        ]
        client.create_session.side_effect = [
            {"id": "s1", "url": "u1"},
            {"id": "s2", "url": "u2"},
        ]

        # Mock git commands
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=cwd, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/o1/r1.git"], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=cwd, check=True)

            subtasks = [Subtask(title="One"), Subtask(title="Two")]
            results = dispatch_subtasks(
                subtasks,
                cwd=cwd,
                client=client,
                strategy="parallel_subtasks",
            )

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].session_id, "s1")
            self.assertEqual(results[1].session_id, "s2")
            self.assertEqual(client.create_session.call_count, 2)
            self.assertTrue(
                client.create_session.call_args_list[0].kwargs["require_plan_approval"]
            )

    def test_run_pipeline_uses_api(self) -> None:
        client = MagicMock()
        client.list_sources.return_value = [
            {"name": "sources/1", "githubRepo": {"owner": "o1", "repo": "r1"}}
        ]
        client.create_session.return_value = {"id": "s1"}

        responses = [
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )
        ]

        def runner(args, *, cwd=None, input_text=None):
            if "--output-last-message" in args:
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    '{"strategy":"single_session","tasks":[{"title":"One"}]}',
                    encoding="utf-8",
                )
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=cwd, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/o1/r1.git"], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=cwd, check=True)

            outcome = run_pipeline(
                "task",
                cwd=cwd,
                client=client,
                runner=runner,
            )

        self.assertEqual(len(outcome.dispatches), 1)
        self.assertEqual(outcome.plan.strategy, "single_session")
        self.assertEqual([task.title for task in outcome.subtasks], ["One"])
        self.assertEqual(outcome.dispatches[0].session_id, "s1")

    def test_run_pipeline_rejects_sequential_strategy(self) -> None:
        client = MagicMock()
        responses = [
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )
        ]

        def runner(args, *, cwd=None, input_text=None):
            if "--output-last-message" in args:
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    '{"strategy":"sequential_subtasks","tasks":[{"title":"One"}]}',
                    encoding="utf-8",
                )
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            with self.assertRaises(PipelineError) as excinfo:
                run_pipeline(
                    "task",
                    cwd=cwd,
                    client=client,
                    runner=runner,
                )

        self.assertIn("sequential_subtasks", str(excinfo.exception))


if __name__ == "__main__":
    unittest.main()
