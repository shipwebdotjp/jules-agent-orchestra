from __future__ import annotations

import abc
import json
import re
import shlex
import sys
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git import CommandRunner, is_git_repo, run_command


class PipelineError(RuntimeError):
    pass


def debug_command(args: list[str], cwd: Path, *, label: str | None = None) -> None:
    prefix = "DEBUG" if label is None else f"DEBUG[{label}]"
    print(
        f"{prefix}: running command (cwd={cwd}): {shlex.join(args)}",
        file=sys.stderr,
        flush=True,
    )


def run_opencode_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    print(
        "DEBUG[opencode]: stdin=DEVNULL stdout=PIPE stderr=PIPE (live streaming enabled)",
        file=sys.stderr,
        flush=True,
    )
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    print(f"DEBUG[opencode]: spawned pid={proc.pid}", file=sys.stderr, flush=True)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def pump(stream: Any, sink: list[str], stream_name: str) -> None:
        assert stream is not None
        for line in iter(stream.readline, ""):
            sink.append(line)
            print(
                f"DEBUG[opencode:{stream_name}] {line.rstrip()}",
                file=sys.stderr,
                flush=True,
            )
        stream.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, stdout_lines, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, stderr_lines, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    returncode = proc.wait()
    for thread in threads:
        thread.join()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    print(
        "DEBUG[opencode]: process exited "
        f"returncode={returncode} stdout_bytes={len(stdout)} stderr_bytes={len(stderr)}",
        file=sys.stderr,
        flush=True,
    )
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class BackendAdapter(abc.ABC):
    @abc.abstractmethod
    def exec(
        self,
        prompt: str,
        schema: dict[str, object],
        cwd: Path,
        runner: CommandRunner,
    ) -> object:
        pass


class CodexAdapter(BackendAdapter):
    def __init__(self, binary: str = "codex"):
        self.binary = binary

    def exec(
        self,
        prompt: str,
        schema: dict[str, object],
        cwd: Path,
        runner: CommandRunner,
    ) -> object:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            schema_path = tmpdir_path / "codex-schema.json"
            last_message_path = tmpdir_path / "codex-last-message.txt"
            schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

            args = [
                self.binary,
                "exec",
                "--ephemeral",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(last_message_path),
            ]
            if not is_git_repo(cwd):
                args.append("--skip-git-repo-check")
            args.append(prompt)

            debug_command(args, cwd, label="codex")
            completed = runner(args, cwd=cwd)
            if completed.returncode != 0:
                sanitized_args = list(args[:-1]) + ["<REDACTED_PROMPT>"]
                raise PipelineError(
                    "Codex call failed.\n"
                    f"Command: {' '.join(sanitized_args)}\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )

            response_text = ""
            if last_message_path.exists():
                response_text = last_message_path.read_text(encoding="utf-8").strip()
            if not response_text:
                response_text = (completed.stdout or "").strip()
            return parse_json_document(response_text)


class GenericBackendAdapter(BackendAdapter):
    def __init__(self, binary: str):
        self.binary = binary

    def exec(
        self,
        prompt: str,
        schema: dict[str, object],
        cwd: Path,
        runner: CommandRunner,
    ) -> object:
        args = [self.binary, prompt]
        debug_command(args, cwd, label=self.binary)
        completed = runner(args, cwd=cwd)
        if completed.returncode != 0:
            sanitized_args = [self.binary, "<REDACTED_PROMPT>"]
            raise PipelineError(
                f"{self.binary} call failed.\n"
                f"Command: {' '.join(sanitized_args)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return parse_json_document(completed.stdout or "")


class ClaudeAdapter(GenericBackendAdapter):
    def __init__(self, binary: str = "claude"):
        super().__init__(binary)


class GeminiAdapter(GenericBackendAdapter):
    def __init__(self, binary: str = "gemini", skip_trust: bool = False):
        super().__init__(binary)
        self.skip_trust = skip_trust

    def exec(
        self,
        prompt: str,
        schema: dict[str, object],
        cwd: Path,
        runner: CommandRunner,
    ) -> object:
        args = [self.binary]
        if self.skip_trust:
            args.append("--skip-trust")
        args.append(prompt)

        debug_command(args, cwd, label="gemini")
        completed = runner(args, cwd=cwd)
        if completed.returncode != 0:
            sanitized_args = [self.binary]
            if self.skip_trust:
                sanitized_args.append("--skip-trust")
            sanitized_args.append("<REDACTED_PROMPT>")
            raise PipelineError(
                f"{self.binary} call failed.\n"
                f"Command: {' '.join(sanitized_args)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return parse_json_document(completed.stdout or "")


class OpenCodeAdapter(GenericBackendAdapter):
    def __init__(self, binary: str = "opencode"):
        super().__init__(binary)

    def exec(
        self,
        prompt: str,
        schema: dict[str, object],
        cwd: Path,
        runner: CommandRunner,
    ) -> object:
        args = [self.binary, "run", "--format", "json", prompt]
        debug_command(args, cwd, label="opencode")
        print(
            "DEBUG[opencode]: entering subprocess wait loop "
            f"(prompt_chars={len(prompt)}, runner_is_default={runner is run_command})",
            file=sys.stderr,
            flush=True,
        )
        if runner is run_command:
            completed = run_opencode_command(args, cwd)
        else:
            completed = runner(args, cwd=cwd)
        if completed.returncode != 0:
            sanitized_args = [self.binary, "run", "--format", "json", "<REDACTED_PROMPT>"]
            raise PipelineError(
                f"{self.binary} run call failed.\n"
                f"Command: {' '.join(sanitized_args)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return parse_json_document(completed.stdout or "")


class CopilotAdapter(GenericBackendAdapter):
    def __init__(self, binary: str = "copilot"):
        super().__init__(binary)


class ClineAdapter(GenericBackendAdapter):
    def __init__(self, binary: str = "cline"):
        super().__init__(binary)


@dataclass(frozen=True)
class ClarificationQuestion:
    question: str
    options: list[str]


@dataclass(frozen=True)
class ClarificationExchange:
    question: str
    options: list[str]
    answer: str


@dataclass(frozen=True)
class ClarificationPrompt:
    has_questions: bool
    questions: list[ClarificationQuestion]


def parse_json_document(text: str) -> object:
    stripped = text.strip()
    if not stripped:
        raise PipelineError("Codex returned an empty response.")

    if stripped.startswith("```"):
        stripped = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL
        ).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(stripped):
            if char not in "{[":
                continue
            try:
                payload, _ = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            return payload
    raise PipelineError("Could not parse JSON from Codex output.")


def call_backend(
    prompt: str,
    schema: dict[str, object],
    *,
    cwd: Path,
    tool_name: str = "codex",
    tool_bin: str | None = None,
    gemini_skip_trust: bool = False,
    runner: CommandRunner = run_command,
) -> object:
    adapters: dict[str, type[BackendAdapter]] = {
        "codex": CodexAdapter,
        "claude": ClaudeAdapter,
        "gemini": GeminiAdapter,
        "opencode": OpenCodeAdapter,
        "copilot": CopilotAdapter,
        "cline": ClineAdapter,
    }

    adapter_cls = adapters.get(tool_name.lower())
    if not adapter_cls:
        raise PipelineError(f"Unknown tool: {tool_name}")

    if tool_name == "gemini":
        adapter = adapter_cls(
            binary=tool_bin or "gemini",
            skip_trust=gemini_skip_trust,
        )
    elif tool_bin:
        adapter = adapter_cls(binary=tool_bin)
    else:
        adapter = adapter_cls()

    return adapter.exec(prompt, schema, cwd, runner)


def call_codex(
    prompt: str,
    schema: dict[str, object],
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> object:
    # Legacy wrapper for backward compatibility during refactoring
    return call_backend(
        prompt,
        schema,
        cwd=cwd,
        tool_name="codex",
        tool_bin=codex_bin,
        runner=runner,
    )


def resolve_tool_for_phase(
    phase: str,
    config: Any,
    args: Any = None,
) -> tuple[str, str | None, bool]:
    # 1. Resolve tool name
    tool_name = None
    if args:
        tool_name = getattr(args, f"{phase}_tool", None)
    if not tool_name:
        tool_name = getattr(config, f"{phase}_tool", None)
    if not tool_name:
        if args:
            tool_name = getattr(args, "tool", None)
    if not tool_name:
        tool_name = getattr(config, "tool", "codex")

    tool_name = tool_name.lower()

    # 2. Resolve tool binary
    tool_bin = None
    if args:
        tool_bin = getattr(args, "tool_bin", None)
        if not tool_bin:
            tool_bin = getattr(args, "codex_bin", None)

    if not tool_bin:
        tool_bin = getattr(config, "tool_bin", None)

    if not tool_bin:
        if tool_name == "codex":
            tool_bin = getattr(config, "codex_bin", "codex")

    gemini_skip_trust = getattr(args, "gemini_skip_trust", None) if args else None
    if gemini_skip_trust is None:
        gemini_skip_trust = getattr(config, "gemini_skip_trust", False)

    return tool_name, tool_bin, bool(gemini_skip_trust) if tool_name == "gemini" else False
