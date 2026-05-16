from __future__ import annotations

from typing import Any, Iterable

import httpx


class JulesAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        base = super().__str__()
        if self.status_code:
            return f"{base} (Status: {self.status_code}, Body: {self.response_body})"
        return base


class JulesClient:
    def __init__(self, api_key: str, base_url: str = "https://jules.googleapis.com/v1alpha"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "X-Goog-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def list_sources(self) -> Iterable[dict[str, Any]]:
        url = f"{self.base_url}/sources"
        params: dict[str, Any] = {}

        while True:
            with httpx.Client() as client:
                response = client.get(url, headers=self.headers, params=params)

            if response.status_code != 200:
                raise JulesAPIError(
                    f"Failed to list sources: {response.text}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            data = response.json()
            sources = data.get("sources", [])
            yield from sources

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
            params["pageToken"] = next_page_token

    def create_session(
        self,
        prompt: str,
        source_name: str,
        starting_branch: str,
        title: str | None = None,
        automation_mode: str = "AUTOMATION_MODE_UNSPECIFIED",
        require_plan_approval: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/sessions"
        payload = {
            "prompt": prompt,
            "sourceContext": {
                "source": source_name,
                "githubRepoContext": {
                    "startingBranch": starting_branch,
                },
            },
            "automationMode": automation_mode,
        }
        if title:
            payload["title"] = title
        if require_plan_approval:
            payload["requirePlanApproval"] = True

        with httpx.Client() as client:
            response = client.post(url, headers=self.headers, json=payload)

        if response.status_code != 200:
            raise JulesAPIError(
                f"Failed to create session: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )

        return response.json()
