import httpx
import pytest
import respx
from jules_agent.github import GitHubClient, GitHubAPIError

@respx.mock
def test_post_issue_comment_success():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    issue_number = 123
    body = "Test comment"

    route = respx.post(f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments").mock(
        return_value=httpx.Response(201, json={"id": 1, "body": body})
    )

    result = client.post_issue_comment(repo, issue_number, body)

    assert result["id"] == 1
    assert result["body"] == body
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"

@respx.mock
def test_post_issue_comment_error():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    issue_number = 123

    respx.post(f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments").mock(
        return_value=httpx.Response(404, text="Not Found")
    )

    with pytest.raises(GitHubAPIError) as excinfo:
        client.post_issue_comment(repo, issue_number, "test")

    assert excinfo.value.status_code == 404
    assert "Not Found" in str(excinfo.value)

@respx.mock
def test_is_pull_request_merged_true():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    pull_number = 456

    respx.get(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(204)
    )

    assert client.is_pull_request_merged(repo, pull_number) is True

@respx.mock
def test_is_pull_request_merged_false():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    pull_number = 456

    respx.get(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(404)
    )

    assert client.is_pull_request_merged(repo, pull_number) is False

@respx.mock
def test_merge_pull_request_success():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    pull_number = 456

    route = respx.put(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(200, json={"merged": True, "message": "Pull Request successfully merged"})
    )

    result = client.merge_pull_request(repo, pull_number, commit_title="Merge PR", commit_message="Closing issue")

    assert result["merged"] is True
    assert route.called
    import json
    payload = json.loads(route.calls.last.request.content)
    assert payload["commit_title"] == "Merge PR"
    assert payload["commit_message"] == "Closing issue"

@respx.mock
def test_merge_pull_request_error():
    client = GitHubClient(token="test-token")
    repo = "owner/repo"
    pull_number = 456

    respx.put(f"https://api.github.com/repos/{repo}/pulls/{pull_number}/merge").mock(
        return_value=httpx.Response(405, text="Pull Request is not mergeable")
    )

    with pytest.raises(GitHubAPIError) as excinfo:
        client.merge_pull_request(repo, pull_number)

    assert excinfo.value.status_code == 405
    assert "not mergeable" in str(excinfo.value)
