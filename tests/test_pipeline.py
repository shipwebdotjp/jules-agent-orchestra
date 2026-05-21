from __future__ import annotations

import subprocess
import tempfile
import pytest
from pathlib import Path

from jules_agent.pipeline import (
    ClarificationExchange,
    build_clarified_task_prompt,
    clarification_schema,
    codex_schema,
    decompose_task,
    PipelineError,
    normalize_plan,
    normalize_subtasks,
)

def test_normalize_plan_accepts_objects_and_strings() -> None:
    plan = normalize_plan(
        {
            "strategy": "single_session",
            "tasks": [
                {
                    "title": "Alpha",
                    "details": "Do the first thing.",
                    "description": "Legacy description should be ignored.",
                    "body": "Legacy body should be ignored.",
                }
            ],
        }
    )
    assert plan.strategy == "single_session"
    assert plan.tasks[0].title == "Alpha"
    assert plan.tasks[0].details == "Do the first thing."

def test_normalize_plan_ignores_legacy_text_aliases() -> None:
    plan = normalize_plan(
        {
            "strategy": "single_session",
            "tasks": [
                {
                    "title": "Alpha",
                    "description": "Legacy description should be ignored.",
                    "body": "Legacy body should be ignored.",
                }
            ],
        }
    )
    assert plan.strategy == "single_session"
    assert plan.tasks[0].details is None

def test_normalize_subtasks_accepts_legacy_payloads() -> None:
    subtasks = normalize_subtasks(
        {
            "subtasks": [
                {"title": "Alpha", "details": "Do the first thing."},
                "Beta",
            ]
        }
    )
    assert subtasks[0].title == "Alpha"
    assert subtasks[0].details == "Do the first thing."
    assert subtasks[1].title == "Beta"

def test_codex_schema_closes_subtask_objects() -> None:
    schema = codex_schema()
    assert not schema["additionalProperties"]
    assert set(schema["properties"]["strategy"]["enum"]) == {"single_session", "sequential_subtasks"}
    assert schema["properties"]["tasks"]["items"]["required"] == [
        "title",
        "details",
        "acceptance_criteria",
        "out_of_scope",
    ]
    assert not schema["properties"]["tasks"]["items"]["additionalProperties"]
    assert set(schema["properties"]["tasks"]["items"]["properties"].keys()) == {
        "title",
        "details",
        "acceptance_criteria",
        "out_of_scope",
    }

def test_clarification_schema_closes_question_objects() -> None:
    schema = clarification_schema()
    assert not schema["additionalProperties"]
    assert schema["properties"]["questions"]["items"]["required"] == ["question", "options"]
    assert not schema["properties"]["questions"]["items"]["additionalProperties"]
    assert set(schema["properties"]["questions"]["items"]["properties"].keys()) == {"question", "options"}

def test_build_clarified_task_prompt_includes_answers() -> None:
    prompt = build_clarified_task_prompt(
        "Build a CLI",
        [
            ClarificationExchange(
                question="Which platform should it target?",
                options=["macOS", "Linux"],
                answer="macOS",
            )
        ],
    )

    assert "Build a CLI" in prompt
    assert "Clarifications gathered:" in prompt
    assert "Which platform should it target?" in prompt
    assert "macOS" in prompt

def test_decompose_task_invokes_codex() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        responses = [
            subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )
        ]

        def runner(args, *, cwd=None, input_text=None):
            if "--output-last-message" in args:
                last_message_path = Path(
                    args[args.index("--output-last-message") + 1]
                )
                last_message_path.write_text(
                    '{"strategy":"sequential_subtasks","tasks":[{"title":"Plan"},{"title":"Implement"}]}',
                    encoding="utf-8",
                )
            return responses.pop(0)

        plan = decompose_task(
            "build a cli",
            cwd=cwd,
            runner=runner,
        )

    assert plan.strategy == "sequential_subtasks"
    assert [task.title for task in plan.tasks] == ["Plan", "Implement"]

def test_validate_plan_rejects_parallel_subtasks() -> None:
    from jules_agent.models import ExecutionPlan, Subtask
    from jules_agent.pipeline import validate_plan

    plan = ExecutionPlan(strategy="parallel_subtasks", tasks=[Subtask(title="One")])  # type: ignore
    with pytest.raises(PipelineError) as cm:
        validate_plan(plan)
    assert "parallel_subtasks" in str(cm.value)
