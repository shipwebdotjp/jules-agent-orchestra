from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .client import JulesAPIError, JulesClient
from .codex import (
    ClarificationExchange,
    ClarificationPrompt,
    ClarificationQuestion,
    PipelineError,
    call_codex,
    parse_json_document,
)
from .git import (
    CommandRunner,
    get_git_branch,
    get_git_remote_repo,
    get_git_root,
    is_git_repo,
    run_command,
)
from .github import GitHubClient
from .models import (
    DispatchResult,
    ExecutionPlan,
    Run,
    State,
    Subtask,
    Task,
)
from .persistence import generate_run_id, load_state, save_state
from .review import (
    apply_review_result,
    format_review_sticky_comment,
    get_review_diff,
    is_task_eligible_for_review,
    run_codex_review,
    update_sticky_comment,
)


SUPPORTED_STRATEGIES = {
    "single_session",
    "parallel_subtasks",
    "sequential_subtasks",
}


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


def clarification_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "has_questions": {"type": "boolean"},
            "questions": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": "string", "minLength": 1},
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "required": ["question", "options"],
                },
            },
        },
        "required": ["has_questions", "questions"],
    }


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


def normalize_clarification(payload: object) -> ClarificationPrompt:
    if not isinstance(payload, dict):
        raise PipelineError("Codex output must be a JSON object.")

    raw_has_questions = payload.get("has_questions")
    if not isinstance(raw_has_questions, bool):
        raise PipelineError(
            "Codex output did not include a valid 'has_questions' field."
        )

    raw_questions = payload.get("questions")
    if raw_questions is None:
        raise PipelineError("Codex output did not include a 'questions' field.")
    if not isinstance(raw_questions, list):
        raise PipelineError("Codex output field 'questions' must be an array.")

    questions: list[ClarificationQuestion] = []
    for index, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            raise PipelineError(f"Clarification question {index} is not an object.")

        question = _first_non_empty_text(item.get("question"))
        if not question:
            raise PipelineError(f"Clarification question {index} is missing text.")

        raw_options = item.get("options")
        if not isinstance(raw_options, list):
            raise PipelineError(
                f"Clarification question {index} options must be an array."
            )

        options: list[str] = []
        for option_index, option in enumerate(raw_options, start=1):
            if not isinstance(option, str) or not option.strip():
                raise PipelineError(
                    f"Clarification question {index} option {option_index} is invalid."
                )
            options.append(option.strip())

        if not options:
            raise PipelineError(
                f"Clarification question {index} must include at least one option."
            )

        questions.append(ClarificationQuestion(question=question, options=options))

    if raw_has_questions and not questions:
        raise PipelineError(
            "Codex reported that clarification is needed, but returned no questions."
        )
    if not raw_has_questions and questions:
        raise PipelineError(
            "Codex reported that no clarification is needed, but returned questions."
        )

    return ClarificationPrompt(has_questions=raw_has_questions, questions=questions)


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


def build_clarification_prompt(
    task: str,
    clarification_history: list[ClarificationExchange],
) -> str:
    prompt = (
        "Analyze the task and decide whether any clarification is needed before "
        "creating a plan for Jules.\n"
        "If no clarification is needed, return has_questions=false and an empty questions list.\n"
        "If clarification is needed, return has_questions=true and up to 5 concise questions.\n"
        "Ask only unresolved questions. Do not repeat questions that have already been answered.\n"
        "Each question must include a question string and 2-5 mutually exclusive answer options.\n"
        "Return only JSON.\n\n"
        f"Task:\n{task.strip()}\n"
    )

    if clarification_history:
        prompt += "\nClarification history:\n"
        for index, item in enumerate(clarification_history, start=1):
            prompt += f"{index}. Question: {item.question}\n"
            if item.options:
                prompt += "   Options:\n"
                for option_index, option in enumerate(item.options, start=1):
                    prompt += f"   {option_index}. {option}\n"
            prompt += f"   Answer: {item.answer}\n"

    return prompt


def identify_clarifications(
    task: str,
    clarification_history: list[ClarificationExchange],
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> ClarificationPrompt:
    prompt = build_clarification_prompt(task, clarification_history)
    payload = call_codex(
        prompt,
        clarification_schema(),
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )
    return normalize_clarification(payload)


def build_clarified_task_prompt(
    task: str,
    clarification_history: list[ClarificationExchange],
) -> str:
    prompt = task.strip()
    if not clarification_history:
        return prompt

    prompt += "\n\nClarifications gathered:\n"
    for index, item in enumerate(clarification_history, start=1):
        prompt += f"{index}. {item.question}\n"
        if item.options:
            prompt += "   Options:\n"
            for option_index, option in enumerate(item.options, start=1):
                prompt += f"   {option_index}. {option}\n"
        prompt += f"   Answer: {item.answer}\n"

    prompt += "\nUse the clarifications above when creating the plan."
    return prompt


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
    task_description: str,
    activities_formatted: str,
    feedback_history: list[str],
    is_awaiting_plan_approval: bool = False,
) -> str:
    prompt = (
        "<role>\n"
        "You are an assistant helping a user provide feedback to Jules, "
        "an AI software engineer.\n"
        "</role>\n\n"
        "<task>\n"
        f"{task_description}\n"
        "</task>\n\n"
        "<activity_history>\n"
        f"{activities_formatted}\n"
        "</activity_history>\n\n"
    )

    if is_awaiting_plan_approval:
        prompt += (
            "<instruction>\n"
            "The session is currently awaiting plan approval. "
            "Evaluate the generated plan. If it looks correct and ready to proceed, "
            "set 'approval_recommended' to true. Otherwise, set it to false and provide "
            "a suggestion to fix the plan.\n"
            "</instruction>\n\n"
        )

    if feedback_history:
        prompt += "<previous_feedback>\n"
        for i, feedback in enumerate(feedback_history, start=1):
            prompt += f"{i}. {feedback}\n"
        prompt += "</previous_feedback>\n\n"
        prompt += (
            "<instruction>\n"
            "Provide a revised suggestion that addresses the feedback above.\n"
            "</instruction>\n\n"
        )
    else:
        if "I have implemented" in activities_formatted:
            prompt += (
                "<instruction>\n"
                "Jules has completed the implementation and is asking for confirmation "
                "before proceeding to final validation.\n"
                "Evaluate whether Jules's proposed next steps are appropriate. "
                "If they are, suggest a message that confirms and tells Jules to proceed. "
                "If there are concerns, suggest a message that provides specific corrections.\n"
                "Do NOT suggest expanding the scope beyond what was originally requested.\n"
                "</instruction>\n\n"
            )
        else:
            prompt += (
                "<instruction>\n"
                "Based on the activity history, suggest a message for the user "
                "to send to Jules to move the task forward.\n"
                "</instruction>\n\n"
            )

    prompt += "<output_format>\nReturn your suggestion in JSON format.\n</output_format>"
    return prompt


def suggestion_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "suggestion": {"type": "string", "minLength": 1},
            "explanation": {"type": "string"},
            "approval_recommended": {"type": "boolean"},
        },
        "required": ["suggestion", "explanation", "approval_recommended"],
    }

def suggest_reply(
    task_description: str,
    activities: list[dict[str, Any]],
    feedback_history: list[str],
    *,
    cwd: Path,
    is_awaiting_plan_approval: bool = False,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> dict[str, Any]:
    activities_formatted = format_activities(activities)
    prompt = build_suggestion_prompt(
        task_description,
        activities_formatted,
        feedback_history,
        is_awaiting_plan_approval=is_awaiting_plan_approval,
    )
    print(f"DEBUG: prompt for Codex suggestion:\n{prompt}\n")
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

    if is_awaiting_plan_approval:
        if "approval_recommended" not in payload:
            raise PipelineError(
                "Codex suggestion failed: 'approval_recommended' field is missing."
            )
        approval_recommended = payload["approval_recommended"]
        if not isinstance(approval_recommended, bool):
            raise PipelineError(
                "Codex suggestion failed: 'approval_recommended' must be a boolean."
            )
    else:
        approval_recommended = bool(payload.get("approval_recommended", False))

    if not isinstance(suggestion, str) or not suggestion.strip():
        raise PipelineError(
            "Codex suggestion failed: 'suggestion' must be a non-empty string."
        )
    if not isinstance(explanation, str):
        raise PipelineError("Codex suggestion failed: 'explanation' must be a string.")

    return {
        "suggestion": suggestion.strip(),
        "explanation": explanation,
        "approval_recommended": bool(approval_recommended),
    }


def format_activities(activities: list[dict[str, Any]]) -> str:
    lines = []
    for activity in activities:
        timestamp = activity.get("createTime", "unknown time")
        if "agentMessaged" in activity:
            msg = activity["agentMessaged"].get("agentMessage", "")
            lines.append(f"[{timestamp}] Jules: {msg}")
        elif "userMessaged" in activity:
            msg = activity["userMessaged"].get("userMessage", "")
            lines.append(f"[{timestamp}] User: {msg}")
        elif "planGenerated" in activity:
            plan = activity["planGenerated"].get("plan", {})
            steps = plan.get("steps", [])
            lines.append(f"[{timestamp}] Jules generated a plan with {len(steps)} steps.")
            for i, step in enumerate(steps, 1):
                title = step.get("title", "")
                description = step.get("description", "")
                lines.append(f"  {i}. {title}: {description}")
        elif "planApproved" in activity:
            lines.append(f"[{timestamp}] Plan was approved.")
        elif "progressUpdated" in activity:
            title = activity["progressUpdated"].get("title", "")
            description = activity["progressUpdated"].get("description", "")
            if not title and description:
                lines.append(f"[{timestamp}] Progress: {description}")
            elif title:
                lines.append(f"[{timestamp}] Progress: {title}")
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


def perform_task_review(
    task: Task,
    state: State,
    github_client: GitHubClient,
    cwd: Path,
    codex_bin: str = "codex",
) -> None:
    if not task.pull_request:
        raise PipelineError(f"Task {task.id} has no pull request associated.")

    # Lazy import to avoid circular dependency
    from .cli.state import extract_pull_request_number
    issue_number = extract_pull_request_number(task.pull_request.url)
    if not issue_number:
        raise PipelineError(f"Could not extract PR number from {task.pull_request.url}")

    repo = state.project.repo
    pr_data = github_client.get_pull_request(repo, issue_number)

    eligible, reason = is_task_eligible_for_review(task, pr_data)
    if not eligible:
        raise PipelineError(f"Task {task.id} is not eligible for review: {reason}")

    base_sha = pr_data["base"]["sha"]
    head_sha = pr_data["head"]["sha"]

    previous_head_sha = None
    if task.review and task.review.attempts:
        previous_head_sha = task.review.attempts[-1].head_sha

    print(f"Generating diff for task {task.id} (PR #{issue_number})...")
    diff = get_review_diff(cwd, repo, base_sha, head_sha, previous_head_sha, github_client)
    if not diff.strip():
        raise PipelineError("Generated diff is empty. Nothing to review.")

    prompt = (
        f"You are performing a first-pass code review for the following task.\n\n"
        f"## Task\n"
        f"**Title:** {task.title}\n"
        f"**Description:** {task.description}\n\n"
        f"## Diff\n"
        f"```diff\n{diff}\n```\n\n"
        f"## Review Criteria\n"
        f"Before raising a finding, confirm that ALL of the following conditions are met:\n"
        f"1. It clearly violates the task description or acceptance criteria, "
        f"OR the diff demonstrably breaks existing functionality, "
        f"OR it will highly likely cause an exception, incorrect behavior, or data corruption during normal execution.\n"
        f"2. The fix is entirely within the scope of this PR (no external changes required).\n"
        f"3. The implementer can fix it without additional design decisions.\n\n"
        f"Do NOT report findings for: style preferences, naming conventions, "
        f"speculative edge cases, architectural suggestions, or anything requiring "
        f"changes outside this PR.\n\n"
        f"## Output Format\n"
        f"Return a JSON object with:\n"
        f"- `status`: 'pass' if no findings, 'changes_requested' if there are findings\n"
        f"- `summary`: brief overall assessment\n"
        f"- `findings`: list of {{file, line, message}} — only include if criteria above are met\n"
        f"- `next_steps`: concrete next action for the implementer\n"
    )

    print(f"Posting 'In Progress' sticky comment for task {task.id}...")
    in_progress_body = format_review_sticky_comment(
        task=task,
        status="in_progress",
        attempt=task.attempts + 1,
        head_sha=head_sha,
        summary="Codex review is currently in progress...",
        next_steps="Please wait for the review to complete.",
    )
    update_sticky_comment(github_client, repo, issue_number, in_progress_body, task)

    print(f"Calling Codex for review of task {task.id}...")
    prev_status = task.status
    task.status = "codex_reviewing"
    save_state(cwd, state)

    try:
        result = run_codex_review(prompt, cwd=cwd, codex_bin=codex_bin)
    except Exception as e:
        # Post error sticky comment
        error_body = format_review_sticky_comment(
            task=task,
            status="error",
            attempt=task.attempts + 1,
            head_sha=head_sha,
            summary="Codex review failed",
            next_steps="See pipeline logs and retry or investigate.",
        )
        update_sticky_comment(github_client, repo, issue_number, error_body, task)
        # Revert status on failure
        task.status = prev_status
        save_state(cwd, state)
        raise PipelineError(f"Codex review failed: {e}") from e

    print(f"Review completed for task {task.id}. Status: {result['status']}")

    # Update sticky comment
    body = format_review_sticky_comment(
        task=task,
        status=result["status"],
        attempt=task.attempts + 1,
        head_sha=head_sha,
        summary=result["summary"],
        next_steps=result["next_steps"],
        findings=result.get("findings"),
    )
    update_sticky_comment(github_client, repo, issue_number, body, task)

    # Apply results to task state
    apply_review_result(task, result, head_sha, github_client, repo, issue_number)
    save_state(cwd, state)
