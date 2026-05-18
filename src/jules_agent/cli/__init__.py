from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from .io import build_review_prompt, prompt_for_review, render_plan
from .state import (
    extract_pull_request_number,
    get_jules_state_mapping,
    get_run_sync_status,
    resolve_task,
    sync_pr_created_task,
    sync_task,
)
from ..client import JulesClient
from ..config import load_config
from ..github import GitHubClient
from ..models import (
    ExecutionPlan,
    JulesSessionInfo,
    ProjectState,
    Run,
    State,
    Task,
)
from ..pipeline import (
    CommandRunner,
    PipelineError,
    decompose_task,
    find_source_name,
    format_activities,
    format_subtask_for_jules,
    generate_run_id,
    get_git_branch,
    get_git_remote_repo,
    get_git_root,
    load_state,
    run_command,
    save_state,
    suggest_reply,
    validate_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jules-agent",
        description="Analyze a task with Codex and dispatch it to Jules.",
    )
    parser.add_argument(
        "--repo",
        help="Optional Jules repo override, for example owner/name.",
    )
    parser.add_argument(
        "--codex-bin",
        help="Path to the codex executable.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a custom configuration file.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    run_parser = subparsers.add_parser("run", help="Run a new task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
    )

    status_parser = subparsers.add_parser("status", help="Show local state")
    status_parser.add_argument(
        "--show-activities",
        action="store_true",
        help="Show session activities",
    )

    subparsers.add_parser("sync", help="Sync with Jules API")

    approve_parser = subparsers.add_parser("approve", help="Approve a task plan")
    approve_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")

    send_parser = subparsers.add_parser("send", help="Send a message to a task")
    send_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")
    send_parser.add_argument("message", help="Message to send")

    feedback_parser = subparsers.add_parser(
        "feedback", help="Interactive feedback loop for a task"
    )
    feedback_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")

    subparsers.add_parser("next", help="Dispatch next task in sequential run")

    return parser


def run_confirmation_loop(
    task: str,
    *,
    cwd: Path,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
) -> ExecutionPlan:
    feedback_history: list[str] = []
    while True:
        review_task = build_review_prompt(task, feedback_history)
        plan = decompose_task(
            review_task,
            cwd=cwd,
            codex_bin=codex_bin,
            runner=runner,
        )
        validate_plan(plan)
        render_plan(plan, output=output)

        feedback = prompt_for_review(input_func=input_func, output=output)
        if feedback is None:
            return plan

        feedback_history.append(feedback)
        output("Revising plan with feedback...")


def run_feedback_loop(
    task: Task,
    *,
    cwd: Path,
    client: JulesClient,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
) -> None:
    if not task.jules:
        raise PipelineError("Task has no Jules session info.")

    feedback_history: list[str] = []
    while True:
        if not sync_task(client, task):
            output(
                "Error: Failed to sync task state. Please check your connection and try again."
            )
            return

        output("\nFetching suggestion from Codex...")
        is_awaiting_plan_approval = task.status == "awaiting_plan_approval"
        activities = list(client.list_activities(task.jules.session_name))
        result = suggest_reply(
            task.prompt or task.title,
            activities,
            feedback_history,
            cwd=cwd,
            is_awaiting_plan_approval=is_awaiting_plan_approval,
            codex_bin=codex_bin,
            runner=runner,
        )
        suggestion = result["suggestion"]
        explanation = result["explanation"]
        approval_recommended = result.get("approval_recommended", False)

        output("-" * 40)
        output(f"Explanation: {explanation}")
        if is_awaiting_plan_approval:
            rec_str = "YES" if approval_recommended else "NO"
            output(f"Approval recommended: {rec_str}")
        output("-" * 40)
        output(f"Suggested message:\n{suggestion}")
        output("-" * 40)

        while True:
            try:
                if is_awaiting_plan_approval and approval_recommended:
                    prompt_msg = (
                        "\nApprove the plan as recommended? (y), provide feedback (f), or write manual message (m)? [y/f/m]: "
                    )
                else:
                    prompt_msg = (
                        "\nApprove suggestion (y), provide feedback (f), or write manual message (m)? [y/f/m]: "
                    )
                answer = input_func(prompt_msg).strip().lower()
            except EOFError as exc:
                raise PipelineError("Feedback loop needs interactive input.") from exc

            if answer in {"y", "yes"}:
                if is_awaiting_plan_approval and approval_recommended:
                    output("Approving plan...")
                    client.approve_plan(task.jules.session_name)
                    task.status = "plan_approved"
                else:
                    output("Sending suggestion to Jules...")
                    client.send_message(task.jules.session_name, suggestion)
                return
            elif answer == "f":
                try:
                    feedback = input_func("Feedback for revision: ").strip()
                except EOFError as exc:
                    raise PipelineError("Feedback input was closed.") from exc
                if feedback:
                    feedback_history.append(feedback)
                    output("Revising suggestion...")
                    break
                output("Feedback cannot be empty.")
            elif answer == "m":
                try:
                    output("Enter your message to Jules (Enter a blank line to finish):")
                    lines = []
                    while True:
                        line = input_func("> ")
                        if not line:
                            break
                        lines.append(line)
                    message = "\n".join(lines).strip()
                except EOFError as exc:
                    raise PipelineError("Manual message input was closed.") from exc
                if message:
                    output("Sending manual message to Jules...")
                    client.send_message(task.jules.session_name, message)
                    return
                output("Message cannot be empty.")
            else:
                output("Please answer with y, f, or m.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)

    api_key = os.environ.get("JULES_API_KEY") or config.api_key
    if not api_key:
        parser.exit(
            1,
            "Error: JULES_API_KEY is not set in environment or configuration.\n",
        )

    github_token = os.environ.get("GITHUB_TOKEN") or config.github_token

    repo = args.repo or config.repo
    codex_bin = args.codex_bin or config.codex_bin
    base_url = config.base_url

    client = JulesClient(api_key=api_key, base_url=base_url)
    github_client = GitHubClient(token=github_token) if github_token else None
    cwd = Path.cwd()

    if args.command is None:
        parser.print_help()
        return 0

    try:
        state = load_state(cwd)
        if state is None:
            git_root = get_git_root(cwd)
            if repo is None:
                repo_info = get_git_remote_repo(cwd)
                if repo_info:
                    repo = f"{repo_info[0]}/{repo_info[1]}"
            if repo is None:
                parser.exit(
                    1, "Error: Could not determine repository. Use --repo owner/name.\n"
                )

            state = State(project=ProjectState(root=str(git_root), repo=repo))

        if args.command == "run":
            if args.no_confirm:
                plan = decompose_task(args.task, cwd=cwd, codex_bin=codex_bin)
            else:
                plan = run_confirmation_loop(args.task, cwd=cwd, codex_bin=codex_bin)

            now_iso = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            run_id = generate_run_id(state)
            run = Run(
                id=run_id,
                original_task=args.task,
                strategy=plan.strategy,
                status="running",
                created_at=now_iso,
                updated_at=now_iso,
            )

            for i, subtask in enumerate(plan.tasks, start=1):
                task = Task(
                    id=f"TASK-{i:03d}",
                    title=subtask.title,
                    description=subtask.details,
                    status="planned",
                    created_at=now_iso,
                    updated_at=now_iso,
                    prompt=format_subtask_for_jules(subtask),
                    acceptance_criteria=subtask.acceptance_criteria,
                    out_of_scope=subtask.out_of_scope,
                )
                run.tasks.append(task)

            state.runs.append(run)
            save_state(cwd, state)
            print(f"Plan saved. Run ID: {run_id}")

            tasks_to_dispatch = []
            if plan.strategy in ("single_session", "parallel_subtasks"):
                tasks_to_dispatch = run.tasks
            elif plan.strategy == "sequential_subtasks":
                tasks_to_dispatch = [run.tasks[0]]

            source_name = find_source_name(client, state.project.repo)
            starting_branch = get_git_branch(cwd)

            for task in tasks_to_dispatch:
                print(f"Dispatching task: {task.id} - {task.title}")
                task.status = "dispatching"
                save_state(cwd, state)
                try:
                    session = client.create_session(
                        prompt=task.prompt or task.title,
                        source_name=source_name,
                        starting_branch=starting_branch,
                        title=task.title,
                        require_plan_approval=True,
                        automation_mode="AUTO_CREATE_PR",
                    )
                    task.jules = JulesSessionInfo(
                        session_id=session["id"],
                        session_name=session["name"],
                        state=session.get("state", "QUEUED"),
                        session_url=session.get("url"),
                        create_time=session.get("createTime"),
                        update_time=session.get("updateTime"),
                    )
                    task.status = get_jules_state_mapping(task.jules.state, False)
                    print(f"  Success: {task.jules.session_url}")
                except Exception as e:
                    task.status = "failed"
                    print(f"  Failed: {e}", file=sys.stderr)

                task.updated_at = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                save_state(cwd, state)

        elif args.command == "status":
            if not state.runs:
                print("No runs found.")
                return 0

            for run in reversed(state.runs):
                print(f"Run: {run.id} [{run.status}] - {run.original_task}")
                for task in run.tasks:
                    status_str = f"  {task.id}: [{task.status}] {task.title}"
                    if task.jules and task.jules.session_url:
                        status_str += f" ({task.jules.session_url})"
                    if task.pull_request:
                        status_str += f" -> PR: {task.pull_request.url}"
                    print(status_str)

                    if args.show_activities and task.jules and task.jules.activities:
                        formatted = format_activities(task.jules.activities)
                        for line in formatted.splitlines():
                            print(f"    {line}")
                print()

        elif args.command == "sync":
            updated_count = 0
            if github_client is None and any(
                task.status == "pr_created"
                for run in state.runs
                for task in run.tasks
            ):
                print(
                    "Warning: GITHUB_TOKEN is not set; skipping PR status checks.",
                    file=sys.stderr,
                )

            for run in state.runs:
                has_pr_created_tasks = any(
                    task.status == "pr_created" for task in run.tasks
                )
                should_sync_run = run.status in ("running", "planned", "failed")
                if github_client and has_pr_created_tasks and run.status == "completed":
                    should_sync_run = True

                if should_sync_run:
                    previous_status = run.status
                    reopened_from_completed = (
                        github_client is not None
                        and previous_status == "completed"
                        and has_pr_created_tasks
                    )
                    run_updated = reopened_from_completed

                    for task in run.tasks:
                        if task.status == "pr_created":
                            if github_client and sync_pr_created_task(
                                github_client,
                                state.project.repo,
                                task,
                            ):
                                updated_count += 1
                                run_updated = True
                            continue

                        if task.status not in (
                            "completed",
                            "merged",
                            "failed",
                            "cancelled",
                            "pr_closed",
                        ):
                            if sync_task(client, task):
                                updated_count += 1
                                run_updated = True

                    if run_updated:
                        run.status = get_run_sync_status(
                            run,
                            previous_status=previous_status,
                            reopened_from_completed=reopened_from_completed,
                        )
                        run.updated_at = (
                            datetime.datetime.now(datetime.timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )

            save_state(cwd, state)
            print(f"Synced {updated_count} tasks.")

        elif args.command == "approve":
            _run, task = resolve_task(state, args.task_id)
            if not task.jules:
                parser.exit(
                    1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
                )

            print(f"Approving plan for task {args.task_id}...")
            client.approve_plan(task.jules.session_name)
            task.status = "plan_approved"
            task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(cwd, state)
            print("Done.")

        elif args.command == "feedback":
            _run, task = resolve_task(state, args.task_id)
            if not task.jules:
                parser.exit(
                    1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
                )

            run_feedback_loop(
                task,
                cwd=cwd,
                client=client,
                codex_bin=codex_bin,
            )
            task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(cwd, state)
            print("Done.")

        elif args.command == "send":
            _run, task = resolve_task(state, args.task_id)
            if not task.jules:
                parser.exit(
                    1, f"Error: Task {args.task_id} has not been dispatched yet.\n"
                )

            print(f"Sending message to task {args.task_id}...")
            client.send_message(task.jules.session_name, args.message)
            task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(cwd, state)
            print("Done.")

        elif args.command == "next":
            target_run = None
            for run in reversed(state.runs):
                if run.strategy == "sequential_subtasks" and run.status == "running":
                    target_run = run
                    break

            if not target_run:
                print("No active sequential run found.")
                return 0

            next_task = None
            for task in target_run.tasks:
                if task.status == "planned":
                    next_task = task
                    break

            if not next_task:
                print("No more tasks to dispatch in this run.")
                return 0

            source_name = find_source_name(client, state.project.repo)
            starting_branch = get_git_branch(cwd)

            print(f"Dispatching next task: {next_task.id} - {next_task.title}")
            next_task.status = "dispatching"
            save_state(cwd, state)
            try:
                session = client.create_session(
                    prompt=next_task.prompt or next_task.title,
                    source_name=source_name,
                    starting_branch=starting_branch,
                    title=next_task.title,
                    require_plan_approval=True,
                    automation_mode="AUTO_CREATE_PR",
                )
                next_task.jules = JulesSessionInfo(
                    session_id=session["id"],
                    session_name=session["name"],
                    state=session.get("state", "QUEUED"),
                    session_url=session.get("url"),
                    create_time=session.get("createTime"),
                    update_time=session.get("updateTime"),
                )
                next_task.status = get_jules_state_mapping(next_task.jules.state, False)
                print(f"  Success: {next_task.jules.session_url}")
            except Exception as e:
                next_task.status = "failed"
                print(f"  Failed: {e}", file=sys.stderr)

            next_task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(cwd, state)

    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    return 0
