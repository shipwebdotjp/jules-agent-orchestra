# PR Review Loop

## Summary
- Add a standalone `review` command that manually runs Codex review for eligible PR-backed tasks.
- Record review history per task, update a single sticky PR issue comment in place, and drive the local task state through `codex_reviewing`, `needs_fix`, and `waiting_human_review`.
- Keep `sync` focused on state refresh; this iteration does not auto-launch review from `sync`.

## Implementation
- Extend the task model with `task.review` for review history, keeping the requested attempt payload shape and adding a stored sticky comment ID so the same GitHub comment can be edited safely.
- Add the new review statuses to the local task state model, and keep backward-compatible loading for existing `reviewing` values so older `state.json` snapshots do not break.
- Implement review eligibility exactly from the rules you gave: PR open, not draft, local status not `codex_reviewing` or `jules_fixing`, current head SHA unseen in prior attempts, and attempts remaining.
- Build review execution around the PR SHAs from GitHub: first attempt uses `base_sha...current_head_sha`, retries also include `previous_head_sha...current_head_sha` as the incremental diff, with a hybrid diff source that prefers local `git diff` and falls back to GitHub compare data when local SHAs are unavailable.
- Use a strict JSON Schema prompt for Codex review output, then render the result into the sticky comment body with `status`, `attempt`, `head_sha`, timestamps, summary, and next steps.
- On `changes_requested`, append an attempt record, update the sticky comment, post the `@jules` fix request, and move the task to `needs_fix`.
- On pass, append the attempt record, update the sticky comment to the completed form, and move the task to `waiting_human_review`.
- Update the CLI surface and documentation to include the new `review` command and note the GitHub permission requirements already needed for issue comments.

## Test Plan
- Add focused unit tests for eligibility checks, attempt counting, head SHA de-duplication, and state transitions across first review, retry, changes requested, and pass.
- Add tests for sticky comment creation/update behavior and the GitHub client path used for compare-diff fallback and comment editing.
- Add a light CLI parse/smoke test for the new `review` command and a serialization test that confirms existing state files still load with the legacy `reviewing` status.
- Run a syntax/import check plus the narrow review-path tests only; no broad behavior-heavy regression suite.

## Assumptions
- `review` is a manual command in this phase, and `sync` does not auto-trigger it.
- Review history lives on each task, not at run level.
- The sticky comment is updated in place, so the state needs to retain the GitHub comment ID as well as the public URL.
- Codex review output should stay compact and machine-readable, with only locally actionable findings.
