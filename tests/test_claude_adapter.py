from __future__ import annotations

import subprocess
import json
import pytest
from pathlib import Path
from jules_agent.codex import ClaudeAdapter, PipelineError

def test_exec_invokes_correct_command():
    runner_calls = []

    def mock_runner(args, cwd=None):
        runner_calls.append((args, cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"result": "success"}',
            stderr=""
        )

    adapter = ClaudeAdapter(binary="claude-custom")
    cwd = Path("/fake/cwd")
    prompt = "Hello, Claude!"
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}

    result = adapter.exec(prompt, schema, cwd, mock_runner)

    assert len(runner_calls) == 1
    args, call_cwd = runner_calls[0]

    # Expected command shape based on requirements:
    # claude -p --json-schema '<schema>' --output-format json "prompt"
    expected_args = [
        "claude-custom",
        "-p",
        "--json-schema",
        json.dumps(schema),
        "--output-format",
        "json",
        prompt
    ]
    assert args == expected_args
    assert call_cwd == cwd
    assert result == {"result": "success"}

def test_exec_raises_pipeline_error_on_failure():
    def mock_runner(args, cwd=None):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="Some error",
            stderr="Detailed error info"
        )

    adapter = ClaudeAdapter(binary="claude")
    cwd = Path("/fake/cwd")
    prompt = "Fail me"
    schema = {}

    with pytest.raises(PipelineError) as excinfo:
        adapter.exec(prompt, schema, cwd, mock_runner)

    assert "claude call failed" in str(excinfo.value)
    assert "<REDACTED_PROMPT>" in str(excinfo.value)
    assert "Detailed error info" in str(excinfo.value)
    # Ensure the actual prompt is NOT in the error message
    assert "Fail me" not in str(excinfo.value)
