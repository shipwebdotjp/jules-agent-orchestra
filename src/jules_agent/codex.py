from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .git import CommandRunner, is_git_repo, run_command


class PipelineError(RuntimeError):
    pass


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


def call_codex(
    prompt: str,
    schema: dict[str, object],
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> object:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        schema_path = tmpdir_path / "codex-schema.json"
        last_message_path = tmpdir_path / "codex-last-message.txt"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

        args = [
            codex_bin,
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
