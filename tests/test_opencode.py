from __future__ import annotations

import contextlib
import io
import subprocess
import pytest
from pathlib import Path

from jules_agent.codex import OpenCodeAdapter, PipelineError

def test_exec_invokes_correct_command():
    runner_calls = []

    def mock_runner(args, cwd=None):
        runner_calls.append((args, cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"result": "success"}',
            stderr="",
        )

    adapter = OpenCodeAdapter(binary="opencode-custom")
    cwd = Path("/fake/cwd")
    prompt = "Hello, OpenCode!"
    schema = {"type": "object"}
    expected_prompt = (
        'Hello, OpenCode!\n\n'
        'Respond only with a JSON object matching the following schema:\n'
        '{\n'
        '  "type": "object"\n'
        '}'
    )

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        result = adapter.exec(prompt, schema, cwd, mock_runner)

    assert len(runner_calls) == 1
    args, call_cwd = runner_calls[0]
    assert args == ["opencode-custom", "run", "--format", "default", expected_prompt]
    assert call_cwd == cwd
    assert result == {"result": "success"}
    assert "DEBUG[opencode]" not in stderr.getvalue()

def test_exec_raises_pipeline_error_on_failure():
    def mock_runner(args, cwd=None):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="Some error",
            stderr="Detailed error info",
        )

    adapter = OpenCodeAdapter(binary="opencode")
    cwd = Path("/fake/cwd")
    prompt = "Fail me"
    schema = {}

    with pytest.raises(PipelineError) as excinfo:
        adapter.exec(prompt, schema, cwd, mock_runner)

    assert "opencode run call failed" in str(excinfo.value)
    assert "opencode run --format default <REDACTED_PROMPT>" in str(excinfo.value)
    assert "<REDACTED_PROMPT>" in str(excinfo.value)
    assert "Detailed error info" in str(excinfo.value)
