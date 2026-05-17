from __future__ import annotations

import json
import re
import subprocess
import tempfile
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .client import JulesAPIError, JulesClient
from .models import (
    DispatchResult,
    ExecutionPlan,
    ProjectState,
    Run,
    State,
    Subtask,
    Task,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

SUPPORTED_STRATEGIES = {
    "single_session",
    "parallel_subtasks",
    "sequential_subtasks",
}


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


def get_git_root(cwd: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return Path(completed.stdout.strip())
    except OSError:
        pass
    return cwd


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


def get_git_branch(cwd: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
    except OSError:
        pass
    return "main"


def get_git_remote_repo(cwd: Path) -> tuple[str, str] | None:
    try:
        completed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        url = completed.stdout.strip()
        # Matches patterns like:
        # https://github.com/owner/repo.git
        # git@github.com:owner/repo.git
        patterns = [
            r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$",
            r"https?://[^/]+/([^/]+)/([^/.]+)(?:\.git)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)
    except OSError:
        pass
    return None


def build_codex_prompt(task: str) -> str:
    return (
        "Analyze the task and return a JSON object matching the supplied schema.\n"
        "Choose exactly one strategy:\n"
        "  - single_session: one cohesive Jules session; use this for a small change that should be handled together\n"
        "  - parallel_subtasks: multiple independent tasks that can be dispatched concurrently\n"
        "  - sequential_subtasks: tasks that depend on each other; this mode is currently rejected by the CLI\n"
        "For single_session, return exactly one task.\n"
        "For parallel_subtasks, return only tasks that do not overlap in responsibility.\n"
        "Return only JSON.\n\n"
        f"Task:\n{task.strip()}"
    )


def codex_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy": {
                "type": "string",
                "enum": sorted(SUPPORTED_STRATEGIES),
            },
            "tasks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "details": {"type": "string"},
                        "acceptance_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "out_of_scope": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "title",
                        "details",
                        "acceptance_criteria",
                        "out_of_scope",
                    ],
                },
            },
        },
        "required": ["strategy", "tasks"],
    }


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


def normalize_subtasks(payload: object) -> list[Subtask]:
    if isinstance(payload, dict):
        raw_items = payload.get("tasks")
        if raw_items is None:
            raw_items = payload.get("subtasks")
        if raw_items is None:
            raise PipelineError("Codex output did not include a 'tasks' field.")
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raise PipelineError("Codex output must be a JSON object or array.")

    return _normalize_tasks(raw_items)


def normalize_plan(payload: object) -> ExecutionPlan:
    if not isinstance(payload, dict):
        raise PipelineError("Codex output must be a JSON object.")

    raw_strategy = payload.get("strategy")
    if not isinstance(raw_strategy, str) or not raw_strategy.strip():
        raise PipelineError("Codex output did not include a valid 'strategy' field.")
    strategy = raw_strategy.strip()
    if strategy not in SUPPORTED_STRATEGIES:
        raise PipelineError(f"Codex returned unsupported strategy: {strategy}")

    raw_items = payload.get("tasks")
    if raw_items is None:
        raw_items = payload.get("subtasks")
    if raw_items is None:
        raise PipelineError("Codex output did not include a 'tasks' field.")

    tasks = _normalize_tasks(raw_items)
    if strategy == "single_session" and len(tasks) != 1:
        raise PipelineError(
            "Codex returned a single_session plan with more than one task."
        )

    return ExecutionPlan(strategy=strategy, tasks=tasks)


def _normalize_tasks(raw_items: object) -> list[Subtask]:
    if not isinstance(raw_items, list):
        raise PipelineError("Codex output field 'tasks' must be an array.")

    tasks: list[Subtask] = []
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
            details = _first_non_empty_text(item.get("details"))
        else:
            raise PipelineError(f"Task {index} is not a string or object.")

        if not title:
            raise PipelineError(f"Task {index} is missing a title.")

        acceptance_criteria = (
            item.get("acceptance_criteria", []) if isinstance(item, dict) else []
        )
        out_of_scope = item.get("out_of_scope", []) if isinstance(item, dict) else []

        tasks.append(
            Subtask(
                title=title,
                details=details,
                acceptance_criteria=acceptance_criteria,
                out_of_scope=out_of_scope,
            )
        )

    if not tasks:
        raise PipelineError("Codex returned zero tasks.")

    return tasks


def validate_plan(plan: ExecutionPlan) -> None:
    pass


def _first_non_empty_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


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


def decompose_task(
    task: str,
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> ExecutionPlan:
    prompt = build_codex_prompt(task)
    payload = call_codex(
        prompt,
        codex_schema(),
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )
    return normalize_plan(payload)


def format_subtask_for_jules(subtask: Subtask) -> str:
    parts: list[str] = [subtask.title]

    if subtask.details:
        parts.extend(["", "Details:", subtask.details])

    if subtask.acceptance_criteria:
        parts.append("")
        parts.append("Acceptance criteria:")
        parts.extend(f"- {item}" for item in subtask.acceptance_criteria)

    if subtask.out_of_scope:
        parts.append("")
        parts.append("Out of scope:")
        parts.extend(f"- {item}" for item in subtask.out_of_scope)

    return "\n".join(parts)


def build_suggestion_prompt(
    task_description: str, activities_formatted: str, feedback_history: list[str]
) -> str:
    prompt = (
        "You are an assistant helping a user provide feedback to Jules, an AI software engineer.\n"
        "Jules is working on the following task:\n"
        f"{task_description}\n\n"
        "Here is the activity history of the Jules session:\n"
        f"{activities_formatted}\n\n"
    )
    if feedback_history:
        prompt += "The user has provided the following feedback on your previous suggestions:\n"
        for i, feedback in enumerate(feedback_history, start=1):
            prompt += f"{i}. {feedback}\n"
        prompt += "\nPlease provide a revised suggestion that addresses this feedback.\n"
    else:
        prompt += "Based on the activity history, suggest a message for the user to send to Jules to move the task forward.\n"

    prompt += "\nReturn your suggestion in JSON format."
    return prompt


def suggestion_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "suggestion": {"type": "string", "minLength": 1},
            "explanation": {"type": "string"},
        },
        "required": ["suggestion", "explanation"],
    }


def suggest_reply(
    task_description: str,
    activities: list[dict[str, Any]],
    feedback_history: list[str],
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> dict[str, str]:
    activities_formatted = format_activities(activities)
    prompt = build_suggestion_prompt(
        task_description, activities_formatted, feedback_history
    )
    payload = call_codex(
        prompt,
        suggestion_schema(),
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )

    if not isinstance(payload, dict):
        raise PipelineError("Codex suggestion failed: payload is not a dictionary.")

    suggestion = payload.get("suggestion")
    explanation = payload.get("explanation")

    if not isinstance(suggestion, str) or not suggestion.strip():
        raise PipelineError("Codex suggestion failed: 'suggestion' must be a non-empty string.")
    if not isinstance(explanation, str):
        raise PipelineError("Codex suggestion failed: 'explanation' must be a string.")

    return {
        "suggestion": suggestion.strip(),
        "explanation": explanation,
    }


def format_activities(activities: list[dict[str, Any]]) -> str:
    lines = []
    for activity in activities:
        timestamp = activity.get("createTime", "unknown time")
        if "agentMessaged" in activity:
            msg = activity["agentMessaged"].get("message", "")
            lines.append(f"[{timestamp}] Jules: {msg}")
        elif "userMessaged" in activity:
            msg = activity["userMessaged"].get("message", "")
            lines.append(f"[{timestamp}] User: {msg}")
        elif "planGenerated" in activity:
            plan = activity["planGenerated"].get("plan", {})
            steps = plan.get("steps", [])
            lines.append(f"[{timestamp}] Jules generated a plan with {len(steps)} steps.")
            for i, step in enumerate(steps, 1):
                description = step.get("description", "")
                lines.append(f"  {i}. {description}")
        elif "planApproved" in activity:
            lines.append(f"[{timestamp}] Plan was approved.")
        elif "progressUpdated" in activity:
            description = activity["progressUpdated"].get("description", "")
            lines.append(f"[{timestamp}] Progress: {description}")
        elif "sessionCompleted" in activity:
            lines.append(f"[{timestamp}] Session completed successfully.")
        elif "sessionFailed" in activity:
            reason = activity["sessionFailed"].get("reason", "Unknown reason")
            lines.append(f"[{timestamp}] Session failed: {reason}")

    return "\n".join(lines)


def find_source_name(client: JulesClient, repo: str) -> str:
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        raise PipelineError(f"Invalid repo format: {repo}. Expected owner/repo.")

    for source in client.list_sources():
        gh_repo = source.get("githubRepo")
        if gh_repo and gh_repo.get("owner") == owner and gh_repo.get("repo") == name:
            return source["name"]

    raise PipelineError(f"Could not find source for repo: {repo}")


def dispatch_subtasks(
    subtasks: Sequence[Subtask],
    *,
    cwd: Path,
    client: JulesClient,
    repo: str | None = None,
    strategy: str = "parallel_subtasks",
    require_plan_approval: bool = True,
) -> list[DispatchResult]:
    if strategy == "sequential_subtasks":
        raise PipelineError(
            "Codex selected sequential_subtasks, which this CLI does not support yet."
        )
    if strategy == "single_session" and len(subtasks) != 1:
        raise PipelineError("single_session plans must contain exactly one task.")

    if repo is None:
        repo_info = get_git_remote_repo(cwd)
        if repo_info:
            repo = f"{repo_info[0]}/{repo_info[1]}"

    if repo is None:
        raise PipelineError(
            "Could not determine repository. Pass --repo owner/name or run in a git repo with an origin remote."
        )

    source_name = find_source_name(client, repo)
    starting_branch = get_git_branch(cwd)

    results: list[DispatchResult] = []
    for index, subtask in enumerate(subtasks, start=1):
        prompt = format_subtask_for_jules(subtask)
        try:
            session = client.create_session(
                prompt=prompt,
                source_name=source_name,
                starting_branch=starting_branch,
                title=subtask.title,
                require_plan_approval=require_plan_approval,
            )
            results.append(
                DispatchResult(
                    index=index,
                    subtask=subtask,
                    session_id=session.get("id"),
                    url=session.get("url"),
                    raw_output=json.dumps(session, indent=2),
                    returncode=0,
                )
            )
        except JulesAPIError as exc:
            results.append(
                DispatchResult(
                    index=index,
                    subtask=subtask,
                    session_id=None,
                    raw_output=exc.response_body or str(exc),
                    returncode=1,
                    error_message=f"Jules API failed on subtask {index}: {exc}",
                )
            )
            break
    return results


@dataclass(frozen=True)
class PipelineOutcome:
    task: str
    plan: ExecutionPlan
    dispatches: list[DispatchResult]

    @property
    def subtasks(self) -> list[Subtask]:
        return self.plan.tasks


def load_state(cwd: Path) -> State | None:
    root = get_git_root(cwd)
    state_path = root / ".jules-agent" / "state.json"
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return State.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_state(cwd: Path, state: State) -> None:
    root = get_git_root(cwd)
    state_dir = root / ".jules-agent"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    # Use atomic write: write to .tmp then rename
    tmp_path = state_path.with_suffix(".json.tmp")
    data = state.to_dict()
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp_path.rename(state_path)


def generate_run_id(state: State) -> str:
    now = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")

    # Count existing runs for today to determine sequence number
    today_prefix = f"run_{date_str}_"
    max_seq = 0
    for run in state.runs:
        if run.id.startswith(today_prefix):
            try:
                seq = int(run.id[len(today_prefix) :])
                if seq > max_seq:
                    max_seq = seq
            except ValueError:
                continue

    return f"run_{date_str}_{max_seq + 1:03d}"


def run_pipeline(
    task: str,
    *,
    cwd: Path,
    client: JulesClient,
    repo: str | None = None,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> PipelineOutcome:
    plan = decompose_task(
        task,
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )
    validate_plan(plan)
    dispatches = dispatch_subtasks(
        plan.tasks,
        cwd=cwd,
        client=client,
        repo=repo,
        strategy=plan.strategy,
    )
    return PipelineOutcome(task=task, plan=plan, dispatches=dispatches)
