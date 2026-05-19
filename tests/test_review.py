import unittest
import json
from unittest.mock import MagicMock, patch
from jules_agent.models import Task, TaskReview, TaskReviewAttempt
from jules_agent.pipeline import is_task_eligible_for_review, update_sticky_comment, format_review_sticky_comment, get_review_diff
from jules_agent.cli import build_parser

class TestReview(unittest.TestCase):
    def test_legacy_reviewing_mapping(self):
        data = {
            "id": "t1",
            "title": "Task 1",
            "status": "reviewing",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }
        task = Task.from_dict(data)
        self.assertEqual(task.status, "codex_reviewing")

    def test_task_serialization_with_review(self):
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
        self.assertEqual(serialized["review"]["sticky_comment_id"], 123)
        self.assertEqual(len(serialized["review"]["attempts"]), 1)

        deserialized = Task.from_dict(serialized)
        self.assertEqual(deserialized.review.sticky_comment_id, 123)
        self.assertEqual(len(deserialized.review.attempts), 1)
        self.assertEqual(deserialized.review.attempts[0].head_sha, "abc")

    def test_eligibility(self):
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
        self.assertTrue(eligible)

        # Not open
        pr_data["state"] = "closed"
        eligible, reason = is_task_eligible_for_review(task, pr_data)
        self.assertFalse(eligible)
        self.assertIn("not open", reason)

        # Draft
        pr_data["state"] = "open"
        pr_data["draft"] = True
        eligible, reason = is_task_eligible_for_review(task, pr_data)
        self.assertFalse(eligible)
        self.assertIn("draft", reason)

        # Already reviewing
        pr_data["draft"] = False
        task.status = "codex_reviewing"
        eligible, reason = is_task_eligible_for_review(task, pr_data)
        self.assertFalse(eligible)
        self.assertIn("already in codex_reviewing", reason)

        # Seen SHA
        task.status = "pr_created"
        task.review = TaskReview(attempts=[
            TaskReviewAttempt(head_sha="sha1", created_at="...", status="pass", summary="...", next_steps="...")
        ])
        eligible, reason = is_task_eligible_for_review(task, pr_data)
        self.assertFalse(eligible)
        self.assertIn("already been reviewed", reason)

        # Attempt limit
        pr_data["head"]["sha"] = "sha2"
        task.attempts = 3
        task.max_attempts = 3
        eligible, reason = is_task_eligible_for_review(task, pr_data)
        self.assertFalse(eligible)
        self.assertIn("maximum review attempts", reason)

    def test_sticky_comment_update(self):
        github_client = MagicMock()
        task = Task(
            id="t1", title="T1", status="pr_created",
            created_at="...", updated_at="...",
            review=TaskReview(sticky_comment_id=123)
        )

        update_sticky_comment(github_client, "owner/repo", 456, "new body", task)
        github_client.update_issue_comment.assert_called_once_with("owner/repo", 123, "new body")

    def test_sticky_comment_creation(self):
        github_client = MagicMock()
        github_client.post_issue_comment.return_value = {"id": 789, "html_url": "http://url"}
        task = Task(
            id="t1", title="T1", status="pr_created",
            created_at="...", updated_at="..."
        )

        update_sticky_comment(github_client, "owner/repo", 456, "new body", task)
        github_client.post_issue_comment.assert_called_once_with("owner/repo", 456, "new body")
        self.assertEqual(task.review.sticky_comment_id, 789)

    @patch("subprocess.run")
    def test_get_review_diff_fallback(self, mock_run):
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

        self.assertIn("file1.py", diff)
        self.assertIn("+new", diff)
        github_client.compare_commits.assert_called_once_with("owner/repo", "base", "head")

    def test_cli_parser_review(self):
        parser = build_parser()
        args = parser.parse_args(["review", "run_20240101_001:t1"])
        self.assertEqual(args.command, "review")
        self.assertEqual(args.task_id, "run_20240101_001:t1")

if __name__ == "__main__":
    unittest.main()
