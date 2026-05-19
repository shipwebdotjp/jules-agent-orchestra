from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from typing import Any

from .codex import call_codex, PipelineError
from .git import CommandRunner, run_command
from .github import GitHubClient
from .models import State, Task, TaskReview, TaskReviewAttempt
from .persistence import save_state


def get_review_diff(
    cwd: Path,
    repo: str,
    base_sha: str,
    head_sha: str,
    previous_head_sha: str | None,
    github_client: GitHubClient,
) -> str:
    diff_pairs = [(base_sha, head_sha)]
    if previous_head_sha and previous_head_sha != base_sha:
        diff_pairs.append((previous_head_sha, head_sha))

    full_diff = ""
    for base, head in diff_pairs:
        # Try local git first
        try:
            completed = subprocess.run(
                ["git", "diff", f"{base}...{head}"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                full_diff += completed.stdout
                continue
        except OSError:
            pass

        # Fallback to GitHub
        try:
            compare = github_client.compare_commits(repo, base, head)
            files = compare.get("files", [])
            for f in files:
                patch = f.get("patch")
                if patch:
                    filename = f.get("filename")
                    full_diff += f"diff --git a/{filename} b/{filename}\n"
                    full_diff += patch + "\n"
        except Exception as e:
            print(f"Warning: Failed to fetch diff from GitHub for {base}...{head}: {e}")

    return full_diff


def format_review_sticky_comment(
    task: Task,
    status: str,
    attempt: int,
    head_sha: str,
    summary: str,
    next_steps: str,
    findings: list[dict[str, Any]] | None = None,
) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if status == "in_progress":
        emoji = "⏳"
        status_text = "In Progress"
    elif status == "error":
        emoji = "⚠️"
        status_text = "Error"
    else:
        emoji = "✅" if status == "pass" else "❌"
        status_text = "Passed" if status == "pass" else "Changes Requested"

    lines = [
        f"## Codex Review Results {emoji}",
        f"- **Status**: {status_text}",
        f"- **Attempt**: {attempt} / {task.max_attempts}",
        f"- **Head SHA**: `{head_sha}`",
        f"- **Updated At**: {now}",
        "",
        "### Summary",
        summary,
        "",
    ]

    if findings:
        lines.append("### Findings")
        for f in findings:
            file = f.get("file")
            line = f.get("line")
            msg = f.get("message")
            line_info = f" (line {line})" if line else ""
            lines.append(f"- **{file}**{line_info}: {msg}")
        lines.append("")

    lines.extend([
        "### Next Steps",
        next_steps,
    ])

    return "\n".join(lines)


def update_sticky_comment(
    github_client: GitHubClient,
    repo: str,
    issue_number: int,
    body: str,
    task: Task,
) -> None:
    if task.review and task.review.sticky_comment_id:
        try:
            github_client.update_issue_comment(repo, task.review.sticky_comment_id, body)
            return
        except Exception as e:
            print(f"Warning: Failed to update existing sticky comment: {e}")

    # Create new comment
    try:
        comment = github_client.post_issue_comment(repo, issue_number, body)
        if not task.review:
            task.review = TaskReview()
        task.review.sticky_comment_id = comment.get("id")
        task.review.sticky_comment_url = comment.get("html_url")
    except Exception as e:
        print(f"Warning: Failed to post sticky comment: {e}")
        raise


def apply_review_result(
    task: Task,
    result: dict[str, Any],
    head_sha: str,
    github_client: GitHubClient,
    repo: str,
    issue_number: int,
) -> None:
    status = result["status"]
    summary = result["summary"]
    next_steps = result["next_steps"]

    # Update task status and attempts
    task.attempts += 1

    attempt = TaskReviewAttempt(
        head_sha=head_sha,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        status=status,  # type: ignore
        summary=summary,
        next_steps=next_steps,
    )

    if not task.review:
        task.review = TaskReview()
    task.review.attempts.append(attempt)

    if status == "changes_requested":
        task.status = "needs_fix"
        # Post fix request
        fix_msg = f"@jules please fix the following issues found in the review:\n\n{summary}\n\nNext steps: {next_steps}"
        try:
            github_client.post_issue_comment(repo, issue_number, fix_msg)
        except Exception as e:
            print(f"Warning: Failed to post fix request comment: {e}")
            raise
    else:
        task.status = "waiting_human_review"

    task.updated_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def is_task_eligible_for_review(
    task: Task,
    pull_request_data: dict[str, Any],
) -> tuple[bool, str | None]:
    if pull_request_data.get("state") != "open":
        return False, "Pull request is not open."
    if pull_request_data.get("draft"):
        return False, "Pull request is a draft."

    if task.status in ("codex_reviewing", "jules_fixing"):
        return False, f"Task is already in {task.status} status."

    current_head_sha = pull_request_data.get("head", {}).get("sha")
    if not current_head_sha:
        return False, "Could not determine current head SHA."

    if task.review:
        seen_shas = {a.head_sha for a in task.review.attempts}
        if current_head_sha in seen_shas:
            return False, f"Head SHA {current_head_sha} has already been reviewed."

    if task.attempts >= task.max_attempts:
        return False, f"Task has reached maximum review attempts ({task.max_attempts})."

    return True, None


def codex_review_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pass", "changes_requested"],
            },
            "summary": {"type": "string", "minLength": 1},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "message": {"type": "string"},
                    },
                    "required": ["file", "line","message"],
                },
            },
            "next_steps": {"type": "string"},
        },
        "required": ["status", "summary", "findings", "next_steps"],
    }


def run_codex_review(
    prompt: str,
    *,
    cwd: Path,
    codex_bin: str = "codex",
    runner: CommandRunner = run_command,
) -> dict[str, Any]:
    payload = call_codex(
        prompt,
        codex_review_schema(),
        cwd=cwd,
        codex_bin=codex_bin,
        runner=runner,
    )

    if not isinstance(payload, dict):
        raise PipelineError("Codex review failed: payload is not a dictionary.")

    return payload
