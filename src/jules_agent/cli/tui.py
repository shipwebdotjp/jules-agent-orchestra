from __future__ import annotations

import datetime
import threading
from pathlib import Path
from typing import Any, List, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Static, Label, Input, Button, RadioSet, RadioButton
from textual.containers import Horizontal, Vertical, Container, ScrollableContainer
from textual.screen import ModalScreen, Screen
from textual.binding import Binding

from ..models import State, Run, Task, ExecutionPlan
from ..codex import resolve_tool_for_phase, SelectionCancelled, ClarificationQuestion
from ..persistence import save_state
from ..client import JulesClient
from ..github import GitHubClient
from ..config import Config

from ..services.sync_service import SyncService, SyncOptions
from ..services.approve_service import ApproveService, ApproveOptions
from ..services.feedback_service import FeedbackService, FeedbackOptions
from ..services.review_service import ReviewService, ReviewOptions
from ..services.review_pass_service import ReviewPassService, ReviewPassOptions
from ..services.send_service import SendService, SendOptions
from ..services.merge_service import MergeService, MergeOptions
from ..services.next_service import NextService, NextOptions
from ..services.retry_service import RetryService, RetryOptions
from ..services.delete_service import DeleteService, DeleteOptions
from ..services.run_service import RunService, RunOptions


class TaskItem(ListItem):
    def __init__(self, run: Run, task: Task):
        super().__init__()
        self.run = run
        self.task_data = task

    def compose(self) -> ComposeResult:
        yield Label(f"[{self.run.id}] {self.task_data.id}: {self.task_data.title} ({self.task_data.status})")


class DetailPane(Static):
    def update_detail(self, run: Run | None, task: Task | None):
        if not run or not task:
            self.update("Select a task to see details.")
            return

        detail = f"""[bold]Run ID:[/bold] {run.id} ([italic]{run.status}[/italic])
[bold]Task ID:[/bold] {task.id} ([italic]{task.status}[/italic])
[bold]Title:[/bold] {task.title}

[bold]Description:[/bold]
{task.description or "No description."}

[bold]Prompt:[/bold]
{task.prompt or "No prompt."}
"""
        if task.jules and task.jules.session_url:
            detail += f"\n[bold]Jules URL:[/bold] {task.jules.session_url}"
        if task.pull_request:
            detail += f"\n[bold]PR URL:[/bold] {task.pull_request.url}"

        self.update(detail)


class TextInputModal(ModalScreen[str]):
    def __init__(self, title: str, placeholder: str = ""):
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.title_text),
            Input(placeholder=self.placeholder, id="modal_input"),
            Horizontal(
                Button("Submit", variant="primary", id="submit"),
                Button("Cancel", variant="error", id="cancel"),
            ),
            id="modal_container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.dismiss(self.query_one("#modal_input", Input).value)
        else:
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_mount(self) -> None:
        self.query_one("#modal_input").focus()


class ClarificationModal(ModalScreen[str]):
    def __init__(self, question: ClarificationQuestion, round_idx: int, total_rounds: int):
        super().__init__()
        self.question_data = question
        self.round_idx = round_idx
        self.total_rounds = total_rounds

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Clarification (Round {self.round_idx}/{self.total_rounds})"),
            Label(self.question_data.question),
            RadioSet(*[RadioButton(opt) for opt in self.question_data.options], id="options"),
            Horizontal(
                Button("Submit", variant="primary", id="submit"),
                Button("Cancel", variant="error", id="cancel"),
            ),
            id="modal_container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            rs = self.query_one("#options", RadioSet)
            if rs.pressed_index >= 0:
                self.dismiss(self.question_data.options[rs.pressed_index])
            else:
                self.notify("Please select an option", variant="error")
        else:
            self.dismiss("__CANCEL__")


class PlanReviewModal(ModalScreen[Optional[str]]):
    def __init__(self, plan: ExecutionPlan):
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        plan_text = f"Strategy: {self.plan.strategy}\n\nTasks:\n"
        for i, t in enumerate(self.plan.tasks, 1):
            plan_text += f"{i}. {t.title}\n"
            if t.details:
                plan_text += f"   Details: {t.details}\n"
            if t.acceptance_criteria:
                plan_text += "   Acceptance Criteria:\n"
                for ac in t.acceptance_criteria:
                    plan_text += f"     - {ac}\n"
            if t.out_of_scope:
                plan_text += "   Out of Scope:\n"
                for oos in t.out_of_scope:
                    plan_text += f"     - {oos}\n"
            plan_text += "\n"

        yield Vertical(
            Label("Plan Review"),
            ScrollableContainer(Static(plan_text), id="plan_scroll"),
            Label("Feedback (leave empty to approve):"),
            Input(id="feedback_input"),
            Horizontal(
                Button("Confirm", variant="primary", id="confirm"),
                Button("Cancel", variant="error", id="cancel"),
            ),
            id="modal_container_large",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            feedback = self.query_one("#feedback_input", Input).value.strip()
            self.dismiss(feedback if feedback else None)
        else:
            self.dismiss("__CANCEL__")

    def on_mount(self) -> None:
        self.query_one("#feedback_input").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        feedback = event.value.strip()
        self.dismiss(feedback if feedback else None)


class JulesTUI(App):
    CSS = """
    #task_list {
        width: 40%;
        border-right: tall $primary;
    }

    #right_pane {
        width: 60%;
        padding: 1;
    }

    #modal_container {
        width: 60%;
        height: auto;
        border: thick $primary;
        padding: 1;
        background: $panel;
        align: center middle;
    }

    #modal_container_large {
        width: 80%;
        height: 80%;
        border: thick $primary;
        padding: 1;
        background: $panel;
        align: center middle;
    }

    #plan_scroll {
        height: 1fr;
        border: solid $accent;
        margin-bottom: 1;
        padding: 1;
    }

    ListItem {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "sync", "Sync"),
        Binding("p", "run_task", "Run Task"),
        Binding("l", "toggle_filter", "Toggle Filter"),
        Binding("a", "approve", "Approve"),
        Binding("f", "feedback", "Feedback"),
        Binding("v", "review", "Review"),
        Binding("P", "review_pass", "Review Pass"),
        Binding("m", "merge", "Merge"),
        Binding("n", "next", "Next"),
        Binding("t", "retry", "Retry"),
        Binding("d", "delete", "Delete"),
        Binding("e", "send_msg", "Send Message"),
    ]

    def __init__(self, state: State, client: JulesClient, github_client: GitHubClient | None, cwd: Path, config: Config):
        super().__init__()
        self.state = state
        self.client = client
        self.github_client = github_client
        self.cwd = cwd
        self.config = config
        self.show_all = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main_container"):
            yield ListView(id="task_list")
            yield DetailPane(id="right_pane")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        list_view = self.query_one("#task_list", ListView)

        # Capture current selection
        current_run_id = None
        current_task_id = None
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, TaskItem):
            current_run_id = list_view.highlighted_child.run.id
            current_task_id = list_view.highlighted_child.task_data.id

        list_view.clear()

        # Sort runs by created_at descending
        sorted_runs = sorted(self.state.runs, key=lambda r: r.created_at or "", reverse=True)

        if not self.show_all:
            sorted_runs = [r for r in sorted_runs if r.status in ("planned", "running")]

        new_index = 0
        found_index = None
        for run in sorted_runs:
            for task in run.tasks:
                if run.id == current_run_id and task.id == current_task_id:
                    found_index = new_index
                list_view.append(TaskItem(run, task))
                new_index += 1

        if found_index is not None:
            list_view.index = found_index

        self.update_detail()
        self.update_title()

    def update_title(self) -> None:
        mode = "All" if self.show_all else "In-progress"
        self.title = f"Jules Agent - {mode} Sessions"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.update_detail()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.update_detail()

    def update_detail(self) -> None:
        list_view = self.query_one("#task_list", ListView)
        detail_pane = self.query_one("#right_pane", DetailPane)

        if list_view.highlighted_child:
            item = list_view.highlighted_child
            if isinstance(item, TaskItem):
                detail_pane.update_detail(item.run, item.task_data)
        else:
            detail_pane.update_detail(None, None)

    def get_selected_task(self) -> tuple[Run, Task] | tuple[None, None]:
        list_view = self.query_one("#task_list", ListView)
        if list_view.highlighted_child:
            item = list_view.highlighted_child
            if isinstance(item, TaskItem):
                return item.run, item.task_data
        return None, None

    async def action_refresh(self) -> None:
        self.refresh_list()
        self.notify("List refreshed")

    def action_toggle_filter(self) -> None:
        self.show_all = not self.show_all
        self.refresh_list()
        mode = "all" if self.show_all else "in-progress only"
        self.notify(f"Filter toggled: showing {mode}")

    def action_run_task(self) -> None:
        def start_run(description: str):
            if not description:
                return

            def do_run():
                service = RunService(self.state, self.client, self.cwd)

                # Callbacks for interactive flow
                current_q_indices = [0, 0]

                def render_clarification_question(question: ClarificationQuestion, idx: int, total: int):
                    current_q_indices[0] = idx
                    current_q_indices[1] = total

                def prompt_for_clarification_answer(question: ClarificationQuestion) -> str:
                    event = threading.Event()
                    answer = "__CANCEL__"

                    def on_answer(val: str):
                        nonlocal answer
                        answer = val
                        event.set()

                    self.call_from_thread(
                        self.push_screen,
                        ClarificationModal(question, current_q_indices[0], current_q_indices[1]),
                        on_answer
                    )
                    event.wait()
                    if answer == "__CANCEL__":
                        raise SelectionCancelled()
                    return answer

                def render_plan(plan: ExecutionPlan):
                    # Handled by prompt_for_review_func
                    pass

                def prompt_for_review() -> Optional[str]:
                    # RunService calls decompose_task then this.
                    # It needs to get ExecutionPlan. Unfortunately RunService doesn't pass it to this callback.
                    # Wait, RunService.run_confirmation_loop_logic calls render_plan(plan) THEN prompt_for_review().
                    # So we need to capture the plan in render_plan.

                    event = threading.Event()
                    feedback = "__CANCEL__"

                    def on_review(val: Optional[str]):
                        nonlocal feedback
                        feedback = val
                        event.set()

                    # We need the plan here. Let's modify do_run to capture it.
                    self.call_from_thread(self.push_screen, PlanReviewModal(current_plan[0]), on_review)
                    event.wait()
                    if feedback == "__CANCEL__":
                        raise SelectionCancelled()
                    return feedback

                current_plan = [None]
                def capture_plan(plan: ExecutionPlan):
                    current_plan[0] = plan

                tool_name, tool_bin, gemini_skip_trust = resolve_tool_for_phase("plan", self.config)
                automation_mode = self.config.automation_mode or "AUTO_CREATE_PR"

                options = RunOptions(
                    task_description=description,
                    no_confirm=False,
                    auto_plan_approval=self.config.auto_plan_approval,
                    automation_mode=automation_mode,
                    tool_name=tool_name,
                    tool_bin=tool_bin,
                    gemini_skip_trust=gemini_skip_trust,
                    output_func=self.notify,
                    render_clarification_question_func=render_clarification_question,
                    prompt_for_clarification_answer_func=prompt_for_clarification_answer,
                    render_plan_func=capture_plan,
                    prompt_for_review_func=prompt_for_review,
                )

                try:
                    service.execute(options)
                    self.notify("Plan created and dispatched")
                except SelectionCancelled:
                    self.notify("Plan creation cancelled")
                except Exception as e:
                    self.notify(f"Error creating plan: {e}", variant="error")

                self.call_from_thread(self.refresh_list)

            self.run_worker(do_run, thread=True)

        self.push_screen(TextInputModal("Enter task description:"), start_run)

    def action_sync(self) -> None:
        def do_sync():
            service = SyncService(self.state, self.client, self.github_client, self.cwd)
            options = SyncOptions(output_func=self.notify)
            service.execute(options)
            self.call_from_thread(self.refresh_list)
            self.notify("Synced with Jules/GitHub")

        self.run_worker(do_sync, thread=True)

    def action_approve(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def do_approve():
            service = ApproveService(self.state, self.client, self.cwd)
            options = ApproveOptions(run=run, task=task, task_id_for_print=f"{run.id}:{task.id}")
            result = service.execute(options)
            self.notify(result.message or "Action completed")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_approve, thread=True)

    def action_feedback(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        if not task.jules:
            self.notify("Error: Task has not been dispatched yet.", variant="error")
            return

        def get_feedback(feedback: str):
            if feedback:
                def do_feedback():
                    # We use SendService to handle the direct message part of feedback
                    # because FeedbackService is designed for the interactive CLI loop.
                    service = SendService(self.state, self.client, self.cwd)
                    options = SendOptions(
                        run=run,
                        task=task,
                        message=feedback,
                        task_id_for_print=f"{run.id}:{task.id}"
                    )
                    result = service.execute(options)
                    if result.success:
                        self.notify("Feedback sent")
                    else:
                        self.notify(result.message or "Failed to send feedback", variant="error")
                    self.call_from_thread(self.refresh_list)

                self.run_worker(do_feedback, thread=True)

        self.push_screen(TextInputModal("Enter feedback for Jules:", ""), get_feedback)

    def action_send_msg(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def send_msg(msg: str):
            if msg:
                def do_send():
                    service = SendService(self.state, self.client, self.cwd)
                    options = SendOptions(
                        run=run,
                        task=task,
                        message=msg,
                        task_id_for_print=f"{run.id}:{task.id}"
                    )
                    result = service.execute(options)
                    if result.success:
                        self.notify("Message sent")
                    else:
                        self.notify(result.message or "Failed to send message", variant="error")
                    self.call_from_thread(self.refresh_list)

                self.run_worker(do_send, thread=True)

        self.push_screen(TextInputModal("Enter message for Jules:", ""), send_msg)

    def action_review(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def do_review():
            service = ReviewService(self.state, self.client, self.github_client, self.cwd)
            options = ReviewOptions(task=task)
            result = service.execute(options)
            if result.success:
                self.notify("Review completed")
            else:
                self.notify(result.message or "Review failed", variant="error")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_review, thread=True)

    def action_review_pass(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def do_review_pass():
            service = ReviewPassService(self.state, self.client, self.github_client, self.cwd)
            options = ReviewPassOptions(task=task)
            result = service.execute(options)
            if result.success:
                self.notify("Review-pass completed")
            else:
                self.notify(result.message or "Review-pass failed", variant="error")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_review_pass, thread=True)

    def action_merge(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def do_merge():
            service = MergeService(self.state, self.client, self.github_client, self.cwd, self.config)
            options = MergeOptions(
                run=run,
                task=task,
                task_id_for_print=f"{run.id}:{task.id}",
                delete_branch=self.config.merge_delete_branch,
                pull=self.config.merge_pull,
                merge_method=self.config.merge_method,
            )
            result = service.execute(options)
            if result.success:
                self.notify("Merge completed")
            else:
                self.notify(result.message or "Merge failed", variant="error")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_merge, thread=True)

    def action_next(self) -> None:
        run, task = self.get_selected_task()
        if not run:
            return

        # NextService needs a planned task to dispatch
        planned_tasks = [t for t in run.tasks if t.status == "planned"]
        if not planned_tasks:
            self.notify("No planned tasks to dispatch in this run.", variant="error")
            return

        task_to_dispatch = planned_tasks[0]

        def do_next():
            service = NextService(self.state, self.client, self.cwd, self.config)
            # automation_mode is passed via args in NextOptions
            from argparse import Namespace
            args = Namespace(automation_mode=self.config.automation_mode)
            options = NextOptions(run=run, task=task_to_dispatch, args=args)
            result = service.execute(options)
            if result.success:
                self.notify(f"Dispatched {task_to_dispatch.id}")
            else:
                self.notify(result.message or "Next dispatch failed", variant="error")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_next, thread=True)

    def action_retry(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def do_retry():
            service = RetryService(self.state, self.client, self.cwd, self.config)
            # RetryOptions accepts output_func
            options = RetryOptions(run=run, task=task, output_func=self.notify)
            result = service.execute(options)
            if result.success:
                self.notify("Retry initiated")
            else:
                self.notify(result.message or "Retry failed", variant="error")
            self.call_from_thread(self.refresh_list)

        self.run_worker(do_retry, thread=True)

    def action_delete(self) -> None:
        run, task = self.get_selected_task()
        if not run or not task:
            return

        def on_confirm(confirm: str):
            if confirm.lower() in ("y", "yes"):
                def do_delete():
                    service = DeleteService(self.state, self.cwd)
                    # Use DeleteOptions for single task deletion
                    options = DeleteOptions(target_run=run, target_task=task, yes=True, output_func=self.notify)
                    result = service.delete_task(options)
                    self.notify(result.message or "Deleted")
                    self.call_from_thread(self.refresh_list)

                self.run_worker(do_delete, thread=True)

        self.push_screen(TextInputModal(f"Delete task {task.id}? Type 'y' to confirm:", ""), on_confirm)

def start_tui(state: State, client: JulesClient, github_client: GitHubClient | None, cwd: Path, config: Config) -> int:
    app = JulesTUI(state, client, github_client, cwd, config)
    app.run()
    return 0
