from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import ExecutionPlan, JulesSessionInfo, Run, State, Task
from ...config import Config
from ...pipeline import (
    build_clarified_task_prompt,
    identify_clarifications,
    decompose_task,
    find_source_name,
    format_subtask_for_jules,
    validate_plan,
)
from ...codex import ClarificationExchange
from ...git import CommandRunner, get_git_branch, run_command
from ...persistence import generate_run_id, save_state
from ..io import (
    build_review_prompt,
    prompt_for_clarification_answer,
    prompt_for_review,
    render_clarification_question,
    render_plan,
)
from ..state import get_jules_state_mapping


MAX_CLARIFICATION_ROUNDS = 5


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


def run_clarification_loop(
    task: str,
    *,
    cwd: Path,
    codex_bin: str,
    runner: CommandRunner = run_command,
    input_func=input,
    output=print,
    max_rounds: int = MAX_CLARIFICATION_ROUNDS,
) -> str:
    clarification_history: list[ClarificationExchange] = []
    for round_index in range(1, max_rounds + 1):
        clarification = identify_clarifications(
            task,
            clarification_history,
            cwd=cwd,
            codex_bin=codex_bin,
            runner=runner,
        )
        if not clarification.has_questions:
            if clarification_history:
                output("No further clarification is needed.")
            return build_clarified_task_prompt(task, clarification_history)

        output(f"Clarification round {round_index}/{max_rounds}:")
        for question_index, question in enumerate(clarification.questions, start=1):
            render_clarification_question(
                question,
                output=output,
                index=question_index,
                total=len(clarification.questions),
            )
            answer = prompt_for_clarification_answer(
                question,
                input_func=input_func,
                output=output,
            )
            clarification_history.append(
                ClarificationExchange(
                    question=question.question,
                    options=question.options,
                    answer=answer,
                )
            )

        if round_index < max_rounds:
            output("Re-checking for remaining clarification gaps...")

    output("Reached the clarification round limit; proceeding with collected answers.")
    return build_clarified_task_prompt(task, clarification_history)


def handle_run(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> int:
    codex_bin = args.codex_bin or config.codex_bin
    auto_plan_approval = args.auto_plan_approval or config.auto_plan_approval

    if args.no_confirm:
        clarified_task = args.task
        plan = decompose_task(clarified_task, cwd=cwd, codex_bin=codex_bin)
        validate_plan(plan)
    else:
        clarified_task = run_clarification_loop(
            args.task,
            cwd=cwd,
            codex_bin=codex_bin,
        )
        plan = run_confirmation_loop(
            clarified_task,
            cwd=cwd,
            codex_bin=codex_bin,
        )

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
    if plan.strategy == "single_session":
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
                require_plan_approval=not auto_plan_approval,
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
