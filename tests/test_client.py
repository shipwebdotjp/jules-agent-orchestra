from __future__ import annotations

import httpx
import pytest
import respx
from jules_agent.client import JulesAPIError, JulesClient


@respx.mock
def test_list_sources_paginated():
    client = JulesClient(api_key="test-key")

    # Mock first page
    respx.get("https://jules.googleapis.com/v1alpha/sources").mock(
        return_value=httpx.Response(
            200,
            json={
                "sources": [{"name": "sources/1", "githubRepo": {"owner": "o1", "repo": "r1"}}],
                "nextPageToken": "token1"
            }
        )
    )
    # Mock second page
    respx.get("https://jules.googleapis.com/v1alpha/sources", params={"pageToken": "token1"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "sources": [{"name": "sources/2", "githubRepo": {"owner": "o2", "repo": "r2"}}]
            }
        )
    )

    sources = list(client.list_sources())
    assert len(sources) == 2
    assert sources[0]["name"] == "sources/1"
    assert sources[1]["name"] == "sources/2"


@respx.mock
def test_create_session_success():
    client = JulesClient(api_key="test-key")
    respx.post("https://jules.googleapis.com/v1alpha/sessions").mock(
        return_value=httpx.Response(200, json={"id": "session-123", "name": "sessions/session-123"})
    )

    session = client.create_session(
        prompt="test prompt",
        source_name="sources/1",
        starting_branch="main"
    )
    assert session["id"] == "session-123"


@respx.mock
def test_api_error_handling():
    client = JulesClient(api_key="test-key")
    respx.post("https://jules.googleapis.com/v1alpha/sessions").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )

    with pytest.raises(JulesAPIError) as excinfo:
        client.create_session("p", "s", "b")

    assert excinfo.value.status_code == 403
    assert "Forbidden" in str(excinfo.value)
