from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jules_agent.pipeline import (
    codex_schema,
    decompose_task,
    dispatch_subtasks,
    extract_session_id,
    normalize_subtasks,
    parse_json_document,
    run_pipeline,
)


class FakeRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, input_text=None):
        self.calls.append(list(args))
        if not self.responses:
            raise AssertionError("Unexpected command call.")
        return self.responses.pop(0)


class PipelineTests(unittest.TestCase):
    def test_parse_json_document_accepts_code_fences(self) -> None:
        payload = parse_json_document(
            """```json
            {"subtasks":[{"title":"First"}]}
            ```"""
        )
        self.assertEqual(payload["subtasks"][0]["title"], "First")

    def test_normalize_subtasks_accepts_objects_and_strings(self) -> None:
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
            schema["properties"]["subtasks"]["items"]["required"],
            ["title"],
        )
        self.assertFalse(
            schema["properties"]["subtasks"]["items"]["additionalProperties"]
        )
        self.assertEqual(
            set(schema["properties"]["subtasks"]["items"]["properties"].keys()),
            {"title"},
        )

    def test_extract_session_id_prefers_session_pattern(self) -> None:
        output = "Created session 123456 for your task."
        self.assertEqual(extract_session_id(output), "123456")

    def test_extract_session_id_accepts_alphanumeric_session_ids(self) -> None:
        output = "Created session abc123-def for your task."
        self.assertEqual(extract_session_id(output), "abc123-def")

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
                        '{"subtasks":[{"title":"Plan"},{"title":"Implement"}]}',
                        encoding="utf-8",
                    )
                return responses.pop(0)

            subtasks = decompose_task(
                "build a cli",
                cwd=cwd,
                runner=runner,
            )

        self.assertEqual([subtask.title for subtask in subtasks], ["Plan", "Implement"])

    def test_dispatch_subtasks_invokes_jules_in_order(self) -> None:
        runner = FakeRunner(
            [
                subprocess.CompletedProcess(
                    args=["jules", "new"],
                    returncode=0,
                    stdout="Created session 111111\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["jules", "new"],
                    returncode=0,
                    stdout="Created session 222222\n",
                    stderr="",
                ),
            ]
        )
        results = dispatch_subtasks(
            [
                normalize_subtasks({"subtasks": [{"title": "One"}]})[0],
                normalize_subtasks({"subtasks": [{"title": "Two"}]})[0],
            ],
            cwd=Path("."),
            runner=runner,
        )
        self.assertEqual([result.session_id for result in results], ["111111", "222222"])
        self.assertEqual(len(runner.calls), 2)

    def test_run_pipeline_stops_on_jules_failure(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["jules", "new"],
                returncode=1,
                stdout="",
                stderr="boom",
            ),
        ]

        def runner(args, *, cwd=None, input_text=None):
            if "--output-last-message" in args:
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    '{"subtasks":[{"title":"One"}]}',
                    encoding="utf-8",
                )
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            outcome = run_pipeline(
                "task",
                cwd=Path(tmpdir),
                repo="example-org/example-repo",
                runner=runner,
            )

        self.assertEqual(len(outcome.dispatches), 1)
        self.assertEqual(outcome.dispatches[0].returncode, 1)
        self.assertIsNotNone(outcome.dispatches[0].error_message)


if __name__ == "__main__":
    unittest.main()
