from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch
from jules_agent.models import Task, TaskReview, TaskReviewAttempt
from jules_agent.pipeline import is_task_eligible_for_review, update_sticky_comment, format_review_sticky_comment, get_review_diff
from jules_agent.cli import build_parser

def test_review_passed_serialization():
    attempt = TaskReviewAttempt(
        head_sha="abc",
        created_at="2024-01-01T00:00:00Z",
        status="pass",
        summary="Looks good",
        next_steps="Merge it"
    )
    review = TaskReview(
        sticky_comment_id=123,
        sticky_comment_url="http://gh.com/123",
        passed_head_sha="abc",
        attempts=[attempt]
    )
    task = Task(
        id="t1",
        title="Task 1",
        status="review_passed",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        review=review
    )

    serialized = task.to_dict()
    assert serialized["review"]["passed_head_sha"] == "abc"

    deserialized = Task.from_dict(serialized)
    assert deserialized.review.passed_head_sha == "abc"
    assert deserialized.status == "review_passed"

def test_task_serialization_with_review():
    attempt = TaskReviewAttempt(
        head_sha="abc",
        created_at="2024-01-01T00:00:00Z",
        status="pass",
        summary="Looks good",
        next_steps="Merge it"
    )
    review = TaskReview(
        sticky_comment_id=123,
        sticky_comment_url="http://gh.com/123",
        attempts=[attempt]
    )
    task = Task(
        id="t1",
        title="Task 1",
        status="waiting_human_review",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        review=review
    )

    serialized = task.to_dict()
    assert serialized["review"]["sticky_comment_id"] == 123
    assert len(serialized["review"]["attempts"]) == 1

    deserialized = Task.from_dict(serialized)
    assert deserialized.review.sticky_comment_id == 123
    assert len(deserialized.review.attempts) == 1
    assert deserialized.review.attempts[0].head_sha == "abc"

def test_eligibility():
    task = Task(
        id="t1",
        title="Task 1",
        status="pr_created",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z"
    )

    # Eligible
    pr_data = {"state": "open", "draft": False, "head": {"sha": "sha1"}}
    eligible, _ = is_task_eligible_for_review(task, pr_data)
    assert eligible

    # Not open
    pr_data["state"] = "closed"
    eligible, reason = is_task_eligible_for_review(task, pr_data)
    assert not eligible
    assert "not open" in reason

    # Draft
    pr_data["state"] = "open"
    pr_data["draft"] = True
    eligible, reason = is_task_eligible_for_review(task, pr_data)
    assert not eligible
    assert "draft" in reason

    # Already reviewing (status check removed from eligible as AdvanceEngine handles it)
    pr_data["draft"] = False

    # Seen SHA
    task.status = "pr_created"
    task.review = TaskReview(attempts=[
        TaskReviewAttempt(head_sha="sha1", created_at="...", status="pass", summary="...", next_steps="...")
    ])
    eligible, reason = is_task_eligible_for_review(task, pr_data)
    assert not eligible
    assert "already been reviewed" in reason

    # Attempt limit
    pr_data["head"]["sha"] = "sha2"
    task.attempts = 3
    task.max_attempts = 3
    eligible, reason = is_task_eligible_for_review(task, pr_data)
    assert not eligible
    assert "maximum review attempts" in reason

    # Already passed
    pr_data["head"]["sha"] = "sha3"
    task.review.passed_head_sha = "sha3"
    eligible, reason = is_task_eligible_for_review(task, pr_data)
    assert not eligible
    assert "already passed review" in reason

def test_sticky_comment_update():
    github_client = MagicMock()
    task = Task(
        id="t1", title="T1", status="pr_created",
        created_at="...", updated_at="...",
        review=TaskReview(sticky_comment_id=123)
    )

    update_sticky_comment(github_client, "owner/repo", 456, "new body", task)
    github_client.update_issue_comment.assert_called_once_with("owner/repo", 123, "new body")

def test_sticky_comment_creation():
    github_client = MagicMock()
    github_client.post_issue_comment.return_value = {"id": 789, "html_url": "http://url"}
    task = Task(
        id="t1", title="T1", status="pr_created",
        created_at="...", updated_at="..."
    )

    update_sticky_comment(github_client, "owner/repo", 456, "new body", task)
    github_client.post_issue_comment.assert_called_once_with("owner/repo", 456, "new body")
    assert task.review.sticky_comment_id == 789

@patch("subprocess.run")
def test_get_review_diff_fallback(mock_run):
    # Local git fails
    mock_run.return_value = MagicMock(returncode=1)

    github_client = MagicMock()
    github_client.compare_commits.return_value = {
        "files": [
            {"filename": "file1.py", "patch": "@@ -1 +1 @@\n-old\n+new"}
        ]
    }

    diff = get_review_diff(
        cwd=MagicMock(),
        repo="owner/repo",
        base_sha="base",
        head_sha="head",
        previous_head_sha=None,
        github_client=github_client
    )

    assert "file1.py" in diff
    assert "+new" in diff
    github_client.compare_commits.assert_called_once_with("owner/repo", "base", "head")

def test_cli_parser_review():
    parser = build_parser()
    args = parser.parse_args(["review", "run_20240101_001:t1"])
    assert args.command == "review"
    assert args.task_id == "run_20240101_001:t1"

def test_resolve_tool_for_phase_ocr_guard():
    from jules_agent.codex import resolve_tool_for_phase, PipelineError

    # Resolve ocr tool for review phase - should succeed
    class MockConfigReview:
        review_tool = "ocr"

    tool_name, tool_bin, _ = resolve_tool_for_phase("review", MockConfigReview())
    assert tool_name == "ocr"

    # Resolve ocr tool for plan phase - should raise PipelineError
    class MockConfigPlan:
        plan_tool = "ocr"

    with pytest.raises(PipelineError) as excinfo:
        resolve_tool_for_phase("plan", MockConfigPlan())
    assert "The 'ocr' tool can only be used during the 'review' phase" in str(excinfo.value)

def test_build_ocr_background_text():
    from jules_agent.review import build_ocr_background_text

    task = Task(
        id="t1",
        title="My Title",
        description="My Desc",
        prompt="My Prompt",
        acceptance_criteria=["Criteria 1", "Criteria 2"],
        out_of_scope=["OOS 1"],
        created_at="...",
        updated_at="...",
        status="planned"
    )

    bg_text = build_ocr_background_text(task)
    assert "Title: My Title" in bg_text
    assert "Description: My Desc" in bg_text
    assert "Prompt: My Prompt" in bg_text
    assert "Acceptance Criteria:" in bg_text
    assert "- Criteria 1" in bg_text
    assert "- Criteria 2" in bg_text
    assert "Out of Scope:" in bg_text
    assert "- OOS 1" in bg_text

def test_run_ocr_review_success_dictionary():
    from jules_agent.review import run_ocr_review
    from pathlib import Path

    task = Task(
        id="t1",
        title="OCR Title",
        description="OCR Desc",
        prompt="OCR Prompt",
        created_at="...",
        updated_at="...",
        status="planned"
    )

    ocr_output_json = {
        "summary": "This is a summary",
        "findings": [
            {
                "path": "src/main.py",
                "start_line": 10,
                "content": "Fix this bug",
                "severity": "high",
                "category": "bug"
            },
            {
                "file": "tests/test_main.py",
                "line": "25",
                "message": "Unused import",
                "severity": "low"
            }
        ]
    }

    mock_runner = MagicMock()
    mock_runner.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(ocr_output_json),
        stderr=""
    )

    result = run_ocr_review(
        task=task,
        base_sha="base123",
        head_sha="head123",
        cwd=Path("/app"),
        tool_bin="custom-ocr-bin",
        runner=mock_runner
    )

    # Verify command execution args
    mock_runner.assert_called_once()
    args, kwargs = mock_runner.call_args
    called_cmd = args[0]
    assert called_cmd[0] == "custom-ocr-bin"
    assert "review" in called_cmd
    assert "--from" in called_cmd
    assert "base123" in called_cmd
    assert "--to" in called_cmd
    assert "head123" in called_cmd

    # Verify result mapping/normalization
    assert result["status"] == "changes_requested"
    assert result["summary"] == "This is a summary"
    assert result["next_steps"] == "Please fix the findings listed above."

    findings = result["findings"]
    assert len(findings) == 2
    assert findings[0]["file"] == "src/main.py"
    assert findings[0]["line"] == 10
    assert findings[0]["message"] == "[high][bug] Fix this bug"

    assert findings[1]["file"] == "tests/test_main.py"
    assert findings[1]["line"] == 25
    assert findings[1]["message"] == "[low] Unused import"

def test_run_ocr_review_success_list_no_findings():
    from jules_agent.review import run_ocr_review
    from pathlib import Path

    task = Task(
        id="t1",
        title="OCR Title",
        created_at="...",
        updated_at="...",
        status="planned"
    )

    mock_runner = MagicMock()
    mock_runner.return_value = MagicMock(
        returncode=0,
        stdout="[]",
        stderr=""
    )

    result = run_ocr_review(
        task=task,
        base_sha="base123",
        head_sha="head123",
        cwd=Path("/app"),
        runner=mock_runner
    )

    assert result["status"] == "pass"
    assert result["summary"] == "OCR review passed with no findings."
    assert result["findings"] == []
    assert result["next_steps"] == "No action needed."

def test_run_ocr_review_failure():
    from jules_agent.review import run_ocr_review
    from jules_agent.codex import PipelineError
    from pathlib import Path

    task = Task(
        id="t1",
        title="OCR Title",
        created_at="...",
        updated_at="...",
        status="planned"
    )

    mock_runner = MagicMock()
    mock_runner.return_value = MagicMock(
        returncode=1,
        stdout="Error occurred",
        stderr="Unable to parse repo"
    )

    with pytest.raises(PipelineError) as excinfo:
        run_ocr_review(
            task=task,
            base_sha="base123",
            head_sha="head123",
            cwd=Path("/app"),
            runner=mock_runner
        )
    assert "OCR review failed with exit code 1" in str(excinfo.value)
