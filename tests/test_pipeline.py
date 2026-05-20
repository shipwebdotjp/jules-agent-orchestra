from __future__ import annotations

import subprocess
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from jules_agent.pipeline import (
    ClarificationExchange,
    build_clarified_task_prompt,
    clarification_schema,
    codex_schema,
    decompose_task,
    dispatch_subtasks,
    PipelineError,
    normalize_plan,
    normalize_subtasks,
    parse_json_document,
    run_pipeline,
)
from jules_agent.models import Subtask

@pytest.fixture
def git_repo_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/o1/r1.git"],
            cwd=cwd,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"], cwd=cwd, check=True
        )
        yield cwd

def test_parse_json_document_accepts_code_fences() -> None:
    payload = parse_json_document(
        """```json
        {"strategy":"single_session","tasks":[{"title":"First"}]}
        ```"""
    )
    assert payload["tasks"][0]["title"] == "First"

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

def test_dispatch_subtasks_invokes_api(git_repo_path: Path) -> None:
    client = MagicMock()
    client.list_sources.return_value = [
        {"name": "sources/1", "githubRepo": {"owner": "o1", "repo": "r1"}}
    ]
    client.create_session.side_effect = [
        {"id": "s1", "url": "u1"},
        {"id": "s2", "url": "u2"},
    ]

    subtasks = [Subtask(title="One"), Subtask(title="Two")]
    results = dispatch_subtasks(
        subtasks,
        cwd=git_repo_path,
        client=client,
        strategy="sequential_subtasks",
    )

    assert len(results) == 1
    assert results[0].session_id == "s1"
    assert client.create_session.call_count == 1
    assert client.create_session.call_args_list[0].kwargs["require_plan_approval"]

def test_run_pipeline_uses_api(git_repo_path: Path) -> None:
    client = MagicMock()
    client.list_sources.return_value = [
        {"name": "sources/1", "githubRepo": {"owner": "o1", "repo": "r1"}}
    ]
    client.create_session.return_value = {"id": "s1"}

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
            Path(args[args.index("--output-last-message") + 1]).write_text(
                '{"strategy":"single_session","tasks":[{"title":"One"}]}',
                encoding="utf-8",
            )
        return responses.pop(0)

    outcome = run_pipeline(
        "task",
        cwd=git_repo_path,
        client=client,
        runner=runner,
    )

    assert len(outcome.dispatches) == 1
    assert outcome.plan.strategy == "single_session"
    assert [task.title for task in outcome.subtasks] == ["One"]
    assert outcome.dispatches[0].session_id == "s1"

def test_validate_plan_rejects_parallel_subtasks() -> None:
    from jules_agent.models import ExecutionPlan
    from jules_agent.pipeline import validate_plan

    plan = ExecutionPlan(strategy="parallel_subtasks", tasks=[Subtask(title="One")])  # type: ignore
    with pytest.raises(PipelineError) as cm:
        validate_plan(plan)
    assert "parallel_subtasks" in str(cm.value)
