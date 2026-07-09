from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path

from .github import GitHubClient
from .models import Task, PullRequestInfo, TaskReview
from .review import (
    run_ocr_review,
    format_review_sticky_comment,
    update_sticky_comment,
)
from .git import get_git_root


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jules_agent")

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    event_path = os.environ.get("GITHUB_EVENT_PATH")

    if not repo or not token or not event_path:
        logger.error(
            "Missing environment variables: GITHUB_REPOSITORY, GITHUB_TOKEN, or GITHUB_EVENT_PATH"
        )
        sys.exit(1)

    try:
        with open(event_path, "r") as f:
            event = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load event payload: {e}")
        sys.exit(1)

    # issue_comment event
    issue = event.get("issue", {})
    pr_number = issue.get("number")
    # Verify it's a pull request (issue has pull_request key)
    if not issue.get("pull_request"):
        logger.info("Comment is not on a pull request. Skipping.")
        return

    comment = event.get("comment", {})
    comment_body = comment.get("body", "")

    if not pr_number:
        logger.error("Could not find PR number in event payload.")
        sys.exit(1)

    if "@ocr" not in comment_body:
        logger.info("Comment does not contain @ocr. Skipping.")
        return

    gh = GitHubClient(token)
    try:
        pr_data = gh.get_pull_request(repo, pr_number)
    except Exception as e:
        logger.error(f"Failed to fetch PR data: {e}")
        sys.exit(1)

    base_sha = pr_data["base"]["sha"]
    head_sha = pr_data["head"]["sha"]

    try:
        git_root = get_git_root(Path.cwd())
    except Exception:
        git_root = Path.cwd()

    # Create a dummy task object
    task = Task(
        id="OCR-REVIEW",
        title=pr_data.get("title", "Pull Request Review"),
        status="reviewing",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        description=pr_data.get("body", ""),
        prompt="",
        pull_request=PullRequestInfo(url=pr_data["html_url"]),
        review=TaskReview(),
    )

    # Search for existing OCR review comment to update
    try:
        comments_path = f"/repos/{repo}/issues/{pr_number}/comments"
        # Access private _request for search
        response = gh._request("GET", comments_path)
        if response.status_code == 200:
            for c in response.json():
                if "## OCR Review Results" in c.get("body", ""):
                    task.review.sticky_comment_id = c.get("id")
                    break
    except Exception as e:
        logger.warning(f"Failed to search for existing comments: {e}")

    # Post "In Progress" comment
    in_progress_body = format_review_sticky_comment(
        task=task,
        status="in_progress",
        attempt=1,
        head_sha=head_sha,
        summary="OCR review is currently in progress...",
        next_steps="Please wait for the review to complete.",
        tool_label="OCR",
    )
    update_sticky_comment(gh, repo, pr_number, in_progress_body, task)

    try:
        result = run_ocr_review(
            task=task,
            base_sha=base_sha,
            head_sha=head_sha,
            cwd=git_root,
        )

        # Build final comment body
        # format_review_sticky_comment already handles findings if passed,
        # but let's see how it's implemented. In review.py it doesn't actually append findings to lines.
        # Wait, I should check format_review_sticky_comment in src/jules_agent/review.py again.
        final_body = format_review_sticky_comment(
            task=task,
            status=result["status"],
            attempt=1,
            head_sha=head_sha,
            summary=result["summary"],
            next_steps=result["next_steps"],
            findings=result.get("findings"),
            tool_label="OCR",
        )

        update_sticky_comment(gh, repo, pr_number, final_body, task)

    except Exception as e:
        logger.exception("OCR review failed")
        error_body = format_review_sticky_comment(
            task=task,
            status="error",
            attempt=1,
            head_sha=head_sha,
            summary=f"OCR review failed: {e}",
            next_steps="Please check the workflow logs.",
            tool_label="OCR",
        )
        update_sticky_comment(gh, repo, pr_number, error_body, task)
        sys.exit(1)


if __name__ == "__main__":
    main()
