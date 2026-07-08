from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...client import JulesClient
from ...models import State, ProjectState
from ...config import Config
from ...codex import resolve_tool_for_phase
from ...pipeline import decompose_task # re-added for tests
from ..io import (
    build_review_prompt,
    prompt_for_clarification_answer,
    prompt_for_review,
    render_clarification_question,
    render_plan,
)
from ...services.run_service import RunService, RunOptions


def run_confirmation_loop(
    task: str,
    *,
    cwd: Path,
    tool_name: str = "codex",
    tool_bin: str | None = None,
    gemini_skip_trust: bool = False,
    runner: Any = None,
    input_func=input,
    output=print,
) -> Any:
    """Backward compatible wrapper."""
    service = RunService(State(project=ProjectState(root=str(cwd), repo="")), JulesClient(api_key=""), cwd, runner=runner)
    options = RunOptions(
        task_description=task,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
        input_func=input_func,
        output_func=output,
        render_plan_func=lambda p: render_plan(p, output=output),
        prompt_for_review_func=lambda: prompt_for_review(input_func=input_func, output=output),
        build_review_prompt_func=build_review_prompt,
    )
    return service.run_confirmation_loop_logic(task, options)

def run_clarification_loop(
    task: str,
    *,
    cwd: Path,
    tool_name: str = "codex",
    tool_bin: str | None = None,
    gemini_skip_trust: bool = False,
    runner: Any = None,
    input_func=input,
    output=print,
    max_rounds: int = 5,
) -> str:
    """Backward compatible wrapper."""
    service = RunService(State(project=ProjectState(root=str(cwd), repo="")), JulesClient(api_key=""), cwd, runner=runner)
    options = RunOptions(
        task_description=task,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
        input_func=input_func,
        output_func=output,
        render_clarification_question_func=lambda q, i, t: render_clarification_question(q, output=output, index=i, total=t),
        prompt_for_clarification_answer_func=lambda q: prompt_for_clarification_answer(q, input_func=input_func, output=output),
    )
    return service.run_clarification_loop_logic(options)


def handle_run(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
    config: Config,
) -> int:
    tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase("plan", config, args)
    auto_plan_approval = args.auto_plan_approval or config.auto_plan_approval
    automation_mode = args.automation_mode or config.automation_mode or "AUTO_CREATE_PR"

    service = RunService(state, client, cwd)
    options = RunOptions(
        task_description=args.task,
        no_confirm=args.no_confirm,
        auto_plan_approval=auto_plan_approval,
        automation_mode=automation_mode,
        tool_name=tool_name,
        tool_bin=tool_bin,
        gemini_skip_trust=gemini_skip_trust,
        input_func=input,
        output_func=print,
        render_plan_func=lambda p: render_plan(p, output=print),
        prompt_for_review_func=lambda: prompt_for_review(input_func=input, output=print),
        render_clarification_question_func=lambda q, i, t: render_clarification_question(q, output=print, index=i, total=t),
        prompt_for_clarification_answer_func=lambda q: prompt_for_clarification_answer(q, input_func=input, output=print),
        build_review_prompt_func=build_review_prompt,
    )

    result = service.execute(options)
    return result.exit_code
