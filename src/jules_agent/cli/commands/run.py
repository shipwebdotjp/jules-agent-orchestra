from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import ExecutionPlan, JulesSessionInfo, Run, State, Task
from ...pipeline import (
    CommandRunner,
    decompose_task,
    find_source_name,
    format_subtask_for_jules,
    generate_run_id,
    get_git_branch,
    run_command,
    save_state,
    validate_plan,
)
from ..io import build_review_prompt, prompt_for_review, render_plan
from ..state import get_jules_state_mapping


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


def handle_run(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    codex_bin: str,
) -> int:
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

    return 0
