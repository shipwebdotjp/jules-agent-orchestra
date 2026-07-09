#!/usr/bin/env python3
import os
import sys
import json
import urllib.request
import subprocess
import datetime


def github_request(method, url, token, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OCR-Review-Action",
    }
    if data:
        data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    event_path = os.environ.get("GITHUB_EVENT_PATH")

    if not repo or not token or not event_path:
        print("Missing required environment variables.")
        sys.exit(1)

    with open(event_path, "r") as f:
        event = json.load(f)

    issue = event.get("issue", {})
    pr_number = issue.get("number")
    if not issue.get("pull_request"):
        print("Not a pull request. Skipping.")
        return

    gh_api_base = "https://api.github.com"
    pr_url = f"{gh_api_base}/repos/{repo}/pulls/{pr_number}"
    pr_data = github_request("GET", pr_url, token)

    base_sha = pr_data["base"]["sha"]
    head_sha = pr_data["head"]["sha"]

    # Search for existing OCR review comment
    comments_url = f"{gh_api_base}/repos/{repo}/issues/{pr_number}/comments"
    comments = github_request("GET", comments_url, token)
    sticky_comment_id = None
    for c in comments:
        if "## OCR Review Results" in c.get("body", ""):
            sticky_comment_id = c.get("id")
            break

    def post_or_update_comment(body):
        nonlocal sticky_comment_id
        if sticky_comment_id:
            url = f"{gh_api_base}/repos/{repo}/issues/comments/{sticky_comment_id}"
            github_request("PATCH", url, token, data={"body": body})
        else:
            url = f"{gh_api_base}/repos/{repo}/issues/{pr_number}/comments"
            resp = github_request("POST", url, token, data={"body": body})
            sticky_comment_id = resp.get("id")

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    in_progress_body = f"""## OCR Review Results ⏳
- **Status**: In Progress
- **Head SHA**: `{head_sha}`
- **Updated At**: {now}

### Summary
OCR review is currently in progress...
"""
    post_or_update_comment(in_progress_body)

    # Run OCR review
    # Construct background text from PR title and body
    background_text = f"Title: {pr_data.get('title', '')}\n\nDescription: {pr_data.get('body', '')}"

    ocr_args = [
        "ocr", "review",
        "--from", base_sha,
        "--to", head_sha,
        "--format", "json",
        "--audience", "agent",
        "--background", background_text
    ]

    try:
        completed = subprocess.run(ocr_args, capture_output=True, text=True, check=True)
        output_text = completed.stdout.strip()
        # Find JSON block in output
        start_idx = output_text.find("{")
        end_idx = output_text.rfind("}")
        if start_idx != -1 and end_idx != -1:
            payload = json.loads(output_text[start_idx:end_idx+1])
        else:
            payload = {"findings": []}

        findings = payload.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        # Normalize findings
        normalized_findings = []
        for f in findings:
            path = f.get("path") or f.get("file") or ""
            line = f.get("start_line") or f.get("line") or 0
            content = f.get("content") or f.get("message") or ""
            normalized_findings.append({"file": path, "line": line, "message": content})

        if normalized_findings:
            status = "❌ Changes Requested"
            summary = payload.get("summary") or "OCR review found issues that need attention."
        else:
            status = "✅ Passed"
            summary = payload.get("summary") or "OCR review passed with no findings."

        final_body = f"""## OCR Review Results {status[0]}
- **Status**: {status[2:]}
- **Head SHA**: `{head_sha}`
- **Updated At**: {now}

### Summary
{summary}
"""
        if normalized_findings:
            final_body += "\n### Findings\n"
            for f in normalized_findings:
                final_body += f"- **{f['file']}** (line {f['line']}): {f['message']}\n"
            final_body += "\n### Next Steps\nPlease fix the findings listed above.\n"
        else:
            final_body += "\n### Next Steps\nNo action needed.\n"

        post_or_update_comment(final_body)

    except Exception as e:
        error_body = f"""## OCR Review Results ⚠️
- **Status**: Error
- **Head SHA**: `{head_sha}`
- **Updated At**: {now}

### Summary
OCR review failed: {e}
"""
        post_or_update_comment(error_body)
        sys.exit(1)


if __name__ == "__main__":
    main()
