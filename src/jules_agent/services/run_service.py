from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..client import JulesClient
from ..models import Run, State, Task, JulesSessionInfo, ExecutionPlan
from ..config import Config
from ..pipeline import (
    build_clarified_task_prompt,
    identify_clarifications,
    decompose_task,
    find_source_name,
    format_subtask_for_jules,
    validate_plan,
)
from ..codex import ClarificationExchange
from ..git import CommandRunner, get_git_branch, run_command
from ..persistence import generate_run_id, save_state
from ..cli.state import get_jules_state_mapping
from .options import Options
from .results import OperationResult

MAX_CLARIFICATION_ROUNDS = 5

@dataclass
class RunOptions(Options):
    task_description: str
    no_confirm: bool = False
    auto_plan_approval: bool = False
    automation_mode: str = "AUTO_CREATE_PR"
    tool_name: str = "codex"
    tool_bin: Optional[str] = None
    gemini_skip_trust: bool = False
    max_rounds: int = MAX_CLARIFICATION_ROUNDS
    input_func: Callable[[str], str] = input
    output_func: Callable[[str], None] = print
    render_plan_func: Optional[Callable[[Any], None]] = None
    prompt_for_review_func: Optional[Callable[[], Optional[str]]] = None
    render_clarification_question_func: Optional[Callable[[Any, int, int], None]] = None
    prompt_for_clarification_answer_func: Optional[Callable[[Any], str]] = None
    build_review_prompt_func: Optional[Callable[[str, list[str]], str]] = None

class RunService:
    def __init__(
        self,
        state: State,
        client: JulesClient,
        cwd: Path,
        runner: CommandRunner = None,
    ):
        self.state = state
        self.client = client
        self.cwd = cwd
        self.runner = runner or run_command

    def execute(self, options: RunOptions) -> OperationResult:
        if options.no_confirm:
            clarified_task = options.task_description
            plan = decompose_task(
                clarified_task,
                cwd=self.cwd,
                tool_name=options.tool_name,
                tool_bin=options.tool_bin,
                gemini_skip_trust=options.gemini_skip_trust,
                runner=self.runner,
            )
            validate_plan(plan)
        else:
            clarified_task = self.run_clarification_loop_logic(options)
            plan = self.run_confirmation_loop_logic(clarified_task, options)

        now_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        run_id = generate_run_id(self.state)
        run = Run(
            id=run_id,
            original_task=options.task_description,
            strategy=plan.strategy,
            status="running",
            created_at=now_iso,
            updated_at=now_iso,
            automation_mode=options.automation_mode,
            require_plan_approval=not options.auto_plan_approval,
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

        self.state.runs.append(run)
        save_state(self.cwd, self.state)
        options.output_func(f"Plan saved. Run ID: {run_id}")

        tasks_to_dispatch = []
        if plan.strategy == "single_session":
            tasks_to_dispatch = run.tasks
        elif plan.strategy == "sequential_subtasks":
            tasks_to_dispatch = [run.tasks[0]]

        source_name = find_source_name(self.client, self.state.project.repo)
        starting_branch = get_git_branch(self.cwd)

        for task in tasks_to_dispatch:
            options.output_func(f"Dispatching task: {task.id} - {task.title}")
            task.status = "dispatching"
            save_state(self.cwd, self.state)
            try:
                session = self.client.create_session(
                    prompt=task.prompt or task.title,
                    source_name=source_name,
                    starting_branch=starting_branch,
                    title=task.title,
                    require_plan_approval=not options.auto_plan_approval,
                    automation_mode=options.automation_mode,
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
                options.output_func(f"  Success: {task.jules.session_url}")
            except Exception as e:
                task.status = "failed"
                options.output_func(f"  Failed: {e}")

            task.updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            save_state(self.cwd, self.state)

        return OperationResult(exit_code=0)

    def run_clarification_loop_logic(self, options: RunOptions) -> str:
        clarification_history: list[ClarificationExchange] = []
        max_rounds = options.max_rounds
        output = options.output_func

        for round_index in range(1, max_rounds + 1):
            clarification = identify_clarifications(
                options.task_description,
                clarification_history,
                cwd=self.cwd,
                tool_name=options.tool_name,
                tool_bin=options.tool_bin,
                gemini_skip_trust=options.gemini_skip_trust,
                runner=self.runner,
            )
            if not clarification.has_questions:
                if clarification_history:
                    output("No further clarification is needed.")
                return build_clarified_task_prompt(options.task_description, clarification_history)

            output(f"Clarification round {round_index}/{max_rounds}:")
            for question_index, question in enumerate(clarification.questions, start=1):
                if options.render_clarification_question_func:
                    options.render_clarification_question_func(
                        question,
                        question_index,
                        len(clarification.questions),
                    )

                if options.prompt_for_clarification_answer_func:
                    answer = options.prompt_for_clarification_answer_func(question)
                else:
                    answer = options.input_func("Answer: ")

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
        return build_clarified_task_prompt(options.task_description, clarification_history)

    def run_confirmation_loop_logic(self, task: str, options: RunOptions) -> ExecutionPlan:
        feedback_history: list[str] = []
        output = options.output_func
        while True:
            if options.build_review_prompt_func:
                review_task = options.build_review_prompt_func(task, feedback_history)
            else:
                review_task = task # Fallback

            plan = decompose_task(
                review_task,
                cwd=self.cwd,
                tool_name=options.tool_name,
                tool_bin=options.tool_bin,
                gemini_skip_trust=options.gemini_skip_trust,
                runner=self.runner,
            )
            validate_plan(plan)

            if options.render_plan_func:
                options.render_plan_func(plan)

            if options.prompt_for_review_func:
                feedback = options.prompt_for_review_func()
            else:
                feedback = None # Fallback

            if feedback is None:
                return plan

            feedback_history.append(feedback)
            output("Revising plan with feedback...")
