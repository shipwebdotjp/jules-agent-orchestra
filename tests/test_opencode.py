from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from jules_agent.codex import OpenCodeAdapter, PipelineError

class TestOpenCodeAdapter(unittest.TestCase):
    def test_exec_invokes_correct_command(self):
        runner_calls = []

        def mock_runner(args, cwd=None):
            runner_calls.append((args, cwd))
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"result": "success"}',
                stderr=""
            )

        adapter = OpenCodeAdapter(binary="opencode-custom")
        cwd = Path("/fake/cwd")
        prompt = "Hello, OpenCode!"
        schema = {"type": "object"}

        result = adapter.exec(prompt, schema, cwd, mock_runner)

        self.assertEqual(len(runner_calls), 1)
        args, call_cwd = runner_calls[0]
        self.assertEqual(args, ["opencode-custom", "run", "--format", "json", prompt])
        self.assertEqual(call_cwd, cwd)
        self.assertEqual(result, {"result": "success"})

    def test_exec_raises_pipeline_error_on_failure(self):
        def mock_runner(args, cwd=None):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="Some error",
                stderr="Detailed error info"
            )

        adapter = OpenCodeAdapter(binary="opencode")
        cwd = Path("/fake/cwd")
        prompt = "Fail me"
        schema = {}

        with self.assertRaises(PipelineError) as cm:
            adapter.exec(prompt, schema, cwd, mock_runner)

        self.assertIn("opencode run call failed", str(cm.exception))
        self.assertIn("<REDACTED_PROMPT>", str(cm.exception))
        self.assertIn("Detailed error info", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
