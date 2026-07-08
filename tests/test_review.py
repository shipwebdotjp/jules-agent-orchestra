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
