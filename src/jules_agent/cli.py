from __future__ import annotations

import argparse
import os
import sys
import datetime
from pathlib import Path
from typing import Sequence

from .client import JulesClient
from .config import load_config
from .models import ExecutionPlan, State, ProjectState, Run, Task, JulesSessionInfo, PullRequestInfo
from .pipeline import (
    CommandRunner,
    PipelineError,
    decompose_task,
    run_command,
    validate_plan,
    get_git_remote_repo,
    get_git_root,
    load_state,
    save_state,
    generate_run_id,
    find_source_name,
    get_git_branch,
    format_subtask_for_jules,
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

    # run
    run_parser = subparsers.add_parser("run", help="Run a new task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip the confirmation loop and dispatch immediately.",
    )

    # status
    subparsers.add_parser("status", help="Show local state")

    # sync
    subparsers.add_parser("sync", help="Sync with Jules API")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a task plan")
    approve_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")

    # send
    send_parser = subparsers.add_parser("send", help="Send a message to a task")
    send_parser.add_argument("task_id", help="Task ID (RUN_ID:TASK_ID or TASK_ID)")
    send_parser.add_argument("message", help="Message to send")

    # next
    subparsers.add_parser("next", help="Dispatch next task in sequential run")

    return parser


def build_review_prompt(task: str, feedback_history: list[str]) -> str:
    prompt = task.strip()
    if not feedback_history:
        return prompt

    feedback_lines = "\n".join(
        f"{index}. {feedback}" for index, feedback in enumerate(feedback_history, start=1)
    )
    return (
        f"{prompt}\n\n"
        "User feedback from the previous plan:\n"
        f"{feedback_lines}\n\n"
        "Revise the plan to address the feedback."
    )


def render_plan(plan: ExecutionPlan, *, output=print) -> None:
    output(f"Proposed strategy: {plan.strategy}")
    output("Proposed tasks:")
    for index, task in enumerate(plan.tasks, start=1):
        output(f"{index}. {task.title}")
        if task.details:
            output(f"   Details: {task.details}")


def prompt_for_review(
    *,
    input_func=input,
    output=print,
) -> str | None:
    while True:
        try:
            answer = input_func("Approve this plan? [y/n]: ").strip().lower()
        except EOFError as exc:
            raise PipelineError(
                "Confirmation mode needs interactive input. Re-run with --no-confirm to skip it."
            ) from exc

        if answer in {"", "y", "yes"}:
            return None
        if answer in {"n", "no"}:
            try:
                feedback = input_func("Feedback for revision: ").strip()
            except EOFError as exc:
                raise PipelineError(
                    "Feedback input was closed. Re-run with --no-confirm to skip confirmation mode."
                ) from exc
            if feedback:
                return feedback
            output("Feedback cannot be empty.")
            continue
        output("Please answer with y or n.")


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


def resolve_task(state: State, task_id_arg: str) -> tuple[Run, Task]:
    if ":" in task_id_arg:
        run_id, task_id = task_id_arg.split(":", 1)
        for run in state.runs:
            if run.id == run_id:
                for task in run.tasks:
                    if task.id == task_id:
                        return run, task
        raise PipelineError(f"Task {task_id_arg} not found.")
    else:
        # Search for task_id across all runs
        candidates: list[tuple[Run, Task]] = []
        for run in state.runs:
            for task in run.tasks:
                if task.id == task_id_arg:
                    candidates.append((run, task))

        if not candidates:
            raise PipelineError(f"Task {task_id_arg} not found.")
        if len(candidates) > 1:
            run_ids = ", ".join(r.id for r, t in candidates)
            raise PipelineError(
                f"Task {task_id_arg} is ambiguous. Found in runs: {run_ids}. "
                "Please use RUN_ID:TASK_ID format."
            )
        return candidates[0]


def get_jules_state_mapping(jules_state: str, has_pr: bool) -> str:
    mapping = {
        "QUEUED": "dispatched",
        "PLANNING": "planning",
        "AWAITING_PLAN_APPROVAL": "awaiting_plan_approval",
        "AWAITING_USER_FEEDBACK": "awaiting_user_feedback",
        "IN_PROGRESS": "in_progress",
        "PAUSED": "paused",
        "FAILED": "failed",
    }
    if jules_state == "COMPLETED":
        return "pr_created" if has_pr else "completed"
    return mapping.get(jules_state, "dispatched")


def sync_task(client: JulesClient, task: Task) -> bool:
    if not task.jules:
        return False

    try:
        session = client.get_session(task.jules.session_name)
        task.jules.state = session.get("state", task.jules.state)
        task.jules.update_time = session.get("updateTime", task.jules.update_time)
        task.jules.session_url = session.get("url", task.jules.session_url)

        has_pr = False
        outputs = session.get("outputs", [])
        for output in outputs:
            pr = output.get("pullRequest")
            if pr:
                task.pull_request = PullRequestInfo(
                    url=pr.get("url"),
                    title=pr.get("title"),
                    description=pr.get("description"),
                )
                has_pr = True

        task.status = get_jules_state_mapping(task.jules.state, has_pr) # type: ignore
        task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return True
    except Exception as e:
        print(f"Failed to sync task {task.id}: {e}", file=sys.stderr)
        return False


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

    repo = args.repo or config.repo
    codex_bin = args.codex_bin or config.codex_bin
    base_url = config.base_url

    client = JulesClient(api_key=api_key, base_url=base_url)
    cwd = Path.cwd()

    if args.command is None:
        # Support legacy usage if possible, but let's encourage subcommands
        # Actually, let's just show help if no command
        parser.print_help()
        return 0

    try:
        state = load_state(cwd)
        if state is None:
            # Initialize state if not exists
            git_root = get_git_root(cwd)
            if repo is None:
                repo_info = get_git_remote_repo(cwd)
                if repo_info:
                    repo = f"{repo_info[0]}/{repo_info[1]}"
            if repo is None:
                parser.exit(1, "Error: Could not determine repository. Use --repo owner/name.\n")

            state = State(project=ProjectState(root=str(git_root), repo=repo))

        if args.command == "run":
            if args.no_confirm:
                plan = decompose_task(args.task, cwd=cwd, codex_bin=codex_bin)
                # validate_plan(plan) # Removed because we want to support sequential_subtasks now
            else:
                plan = run_confirmation_loop(args.task, cwd=cwd, codex_bin=codex_bin)

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
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

            # Initial dispatch
            tasks_to_dispatch = []
            if plan.strategy == "single_session" or plan.strategy == "parallel_subtasks":
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
                        state=session["state"],
                        session_url=session.get("url"),
                        create_time=session.get("createTime"),
                        update_time=session.get("updateTime"),
                    )
                    task.status = get_jules_state_mapping(task.jules.state, False) # type: ignore
                    print(f"  Success: {task.jules.session_url}")
                except Exception as e:
                    task.status = "failed"
                    print(f"  Failed: {e}", file=sys.stderr)

                task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
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
                print()

        elif args.command == "sync":
            updated_count = 0
            for run in state.runs:
                if run.status in ("running", "planned"):
                    run_updated = False
                    for task in run.tasks:
                        if task.status not in ("completed", "pr_created", "merged", "failed", "cancelled"):
                            if sync_task(client, task):
                                updated_count += 1
                                run_updated = True

                    if run_updated:
                        # Update run status if all tasks are done
                        if all(t.status in ("completed", "pr_created", "merged") for t in run.tasks):
                            run.status = "completed"
                        elif any(t.status == "failed" for t in run.tasks):
                            # This is a bit simple, might need better logic
                            pass
                        run.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

            save_state(cwd, state)
            print(f"Synced {updated_count} tasks.")

        elif args.command == "approve":
            run, task = resolve_task(state, args.task_id)
            if not task.jules:
                parser.exit(1, f"Error: Task {args.task_id} has not been dispatched yet.\n")

            print(f"Approving plan for task {args.task_id}...")
            client.approve_plan(task.jules.session_name)
            task.status = "plan_approved"
            task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            save_state(cwd, state)
            print("Done.")

        elif args.command == "send":
            run, task = resolve_task(state, args.task_id)
            if not task.jules:
                parser.exit(1, f"Error: Task {args.task_id} has not been dispatched yet.\n")

            print(f"Sending message to task {args.task_id}...")
            client.send_message(task.jules.session_name, args.message)
            task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            save_state(cwd, state)
            print("Done.")

        elif args.command == "next":
            # Find the most recent sequential run that has pending tasks
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
                    # Check dependencies (simple: previous task must be pr_created, completed or merged)
                    # For now, let's just take the first planned task
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
                    state=session["state"],
                    session_url=session.get("url"),
                    create_time=session.get("createTime"),
                    update_time=session.get("updateTime"),
                )
                next_task.status = get_jules_state_mapping(next_task.jules.state, False) # type: ignore
                print(f"  Success: {next_task.jules.session_url}")
            except Exception as e:
                next_task.status = "failed"
                print(f"  Failed: {e}", file=sys.stderr)

            next_task.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            save_state(cwd, state)

    except PipelineError as exc:
        parser.exit(1, f"{exc}\n")

    return 0
