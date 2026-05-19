from __future__ import annotations

import httpx
from typing import Any


class GitHubAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        base = super().__str__()
        if self.status_code:
            return f"{base} (Status: {self.status_code}, Body: {self.response_body})"
        return base


class GitHubClient:
    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        self.token = token
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client() as client:
                response = client.request(method, url, headers=self.headers, **kwargs)
            return response
        except httpx.HTTPError as e:
            raise GitHubAPIError(f"HTTP request failed: {e}") from e

    def post_issue_comment(self, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        path = f"/repos/{repo}/issues/{issue_number}/comments"
        response = self._request("POST", path, json={"body": body})
        if response.status_code != 201:
            raise GitHubAPIError(
                f"Failed to post comment: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        path = f"/repos/{repo}/issues/comments/{comment_id}"
        response = self._request("PATCH", path, json={"body": body})
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to update comment: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    def get_pull_request(self, repo: str, pull_number: int) -> dict[str, Any]:
        path = f"/repos/{repo}/pulls/{pull_number}"
        response = self._request("GET", path)
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to get PR details: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    def is_pull_request_merged(self, repo: str, pull_number: int) -> bool:
        path = f"/repos/{repo}/pulls/{pull_number}/merge"
        response = self._request("GET", path)
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            return False
        raise GitHubAPIError(
            f"Failed to check PR merge status: {response.text}",
            status_code=response.status_code,
            response_body=response.text,
        )

    def compare_commits(self, repo: str, base: str, head: str) -> dict[str, Any]:
        path = f"/repos/{repo}/compare/{base}...{head}"
        response = self._request("GET", path)
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to compare commits: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    def merge_pull_request(
        self,
        repo: str,
        pull_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str | None = None,
    ) -> dict[str, Any]:
        path = f"/repos/{repo}/pulls/{pull_number}/merge"
        payload: dict[str, Any] = {}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message
        if merge_method:
            payload["merge_method"] = merge_method

        response = self._request("PUT", path, json=payload)
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to merge PR: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()
