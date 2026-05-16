from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .models import DispatchResult, Subtask


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class PipelineError(RuntimeError):
    pass


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(cwd: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def build_codex_prompt(task: str) -> str:
    return (
        "Break the task into a JSON object matching the supplied schema.\n"
        "Return only JSON. Keep subtasks concise, independently actionable,\n"
        "and ordered from first to last.\n\n"
        f"Task:\n{task.strip()}"
    )


def codex_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subtasks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                    },
                    "required": ["title"],
                },
            }
        },
        "required": ["subtasks"],
    }


def parse_json_document(text: str) -> object:
    stripped = text.strip()
    if not stripped:
        raise PipelineError("Codex returned an empty response.")

    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL).strip()

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


def normalize_subtasks(payload: object) -> list[Subtask]:
    if isinstance(payload, dict):
        raw_items = payload.get("subtasks")
        if raw_items is None:
            raise PipelineError("Codex output did not include a 'subtasks' field.")
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raise PipelineError("Codex output must be a JSON object or array.")

    subtasks: list[Subtask] = []
    for index, item in enumerate(raw_items, start=1):
        if isinstance(item, str):
            title = item.strip()
            details = None
        elif isinstance(item, dict):
            title = _first_non_empty_text(
                item.get("title"),
                item.get("task"),
                item.get("prompt"),
                item.get("name"),
            )
            details = _first_non_empty_text(
                item.get("details"),
                item.get("description"),
                item.get("body"),
            )
        else:
            raise PipelineError(f"Subtask {index} is not a string or object.")

        if not title:
            raise PipelineError(f"Subtask {index} is missing a title.")

        subtasks.append(Subtask(title=title, details=details))

    if not subtasks:
        raise PipelineError("Codex returned zero subtasks.")

    return subtasks


def _first_non_empty_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def decompose_task(
    task: str,
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> list[Subtask]:
    prompt = build_codex_prompt(task)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        schema_path = tmpdir_path / "codex-schema.json"
        last_message_path = tmpdir_path / "codex-last-message.txt"
        schema_path.write_text(json.dumps(codex_schema(), indent=2), encoding="utf-8")

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
            raise PipelineError(
                "Codex decomposition failed.\n"
                f"Command: {' '.join(args)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

        response_text = ""
        if last_message_path.exists():
            response_text = last_message_path.read_text(encoding="utf-8").strip()
        if not response_text:
            response_text = (completed.stdout or "").strip()
        payload = parse_json_document(response_text)
        return normalize_subtasks(payload)


def format_subtask_for_jules(subtask: Subtask) -> str:
    if subtask.details:
        return f"{subtask.title}\n\nDetails:\n{subtask.details}"
    return subtask.title


def extract_session_id(output: str) -> str | None:
    session_patterns = [
        re.compile(
            r"\bsession(?:\s+id)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bcreated\s+session\s+([A-Za-z0-9][A-Za-z0-9_-]{3,})\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bsession\s+([A-Za-z0-9][A-Za-z0-9_-]{3,})\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(\d{5,})\b"),
    ]
    for pattern in session_patterns:
        match = pattern.search(output)
        if match:
            return match.group(1)
    return None


def dispatch_subtasks(
    subtasks: Sequence[Subtask],
    *,
    cwd: Path,
    repo: str | None = None,
    jules_bin: str = "jules",
    runner: CommandRunner = run_command,
) -> list[DispatchResult]:
    results: list[DispatchResult] = []
    for index, subtask in enumerate(subtasks, start=1):
        args = [jules_bin, "new"]
        if repo:
            args.extend(["--repo", repo])
        args.append(format_subtask_for_jules(subtask))

        completed = runner(args, cwd=cwd)
        combined_output = "\n".join(
            piece for piece in (completed.stdout, completed.stderr) if piece
        ).strip()
        results.append(
            DispatchResult(
                index=index,
                subtask=subtask,
                session_id=extract_session_id(combined_output),
                raw_output=combined_output,
                returncode=completed.returncode,
                error_message=(
                    None
                    if completed.returncode == 0
                    else (
                        f"Jules failed on subtask {index}.\n"
                        f"Command: {' '.join(args)}\n"
                        f"stdout:\n{completed.stdout}\n"
                        f"stderr:\n{completed.stderr}"
                    )
                ),
            )
        )
        if completed.returncode != 0:
            break
    return results


@dataclass(frozen=True)
class PipelineOutcome:
    task: str
    subtasks: list[Subtask]
    dispatches: list[DispatchResult]


def run_pipeline(
    task: str,
    *,
    cwd: Path,
    repo: str | None = None,
    codex_bin: str = "codex",
    jules_bin: str = "jules",
    runner: CommandRunner = run_command,
) -> PipelineOutcome:
    if repo is None and not is_git_repo(cwd):
        raise PipelineError(
            "Current directory is not a git repository. Pass --repo owner/name "
            "or run the CLI inside a git repo."
        )
    subtasks = decompose_task(
        task,
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )
    dispatches = dispatch_subtasks(
        subtasks,
        cwd=cwd,
        repo=repo,
        jules_bin=jules_bin,
        runner=runner,
    )
    return PipelineOutcome(task=task, subtasks=subtasks, dispatches=dispatches)
