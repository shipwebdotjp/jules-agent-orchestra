from __future__ import annotations

import argparse
from ...models import State
from ...pipeline import format_activities


def handle_status(args: argparse.Namespace, state: State) -> int:
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

    return 0
