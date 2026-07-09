from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, List, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Static, Label, Input, Button
from textual.containers import Horizontal, Vertical, Container
from textual.screen import ModalScreen, Screen
from textual.binding import Binding

from ..models import State, Run, Task
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
        self.task = task

    def compose(self) -> ComposeResult:
        yield Label(f"[{self.run.id}] {self.task.id}: {self.task.title} ({self.task.status})")


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

    ListItem {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "sync", "Sync"),
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
            current_task_id = list_view.highlighted_child.task.id

        list_view.clear()

        # Sort runs by created_at descending
        sorted_runs = sorted(self.state.runs, key=lambda r: r.created_at or "", reverse=True)

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
                detail_pane.update_detail(item.run, item.task)
        else:
            detail_pane.update_detail(None, None)

    def get_selected_task(self) -> tuple[Run, Task] | tuple[None, None]:
        list_view = self.query_one("#task_list", ListView)
        if list_view.highlighted_child:
            item = list_view.highlighted_child
            if isinstance(item, TaskItem):
                return item.run, item.task
        return None, None

    async def action_refresh(self) -> None:
        self.refresh_list()
        self.notify("List refreshed")

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
