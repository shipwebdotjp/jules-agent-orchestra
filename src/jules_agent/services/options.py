from dataclasses import dataclass
from typing import Any, Callable, Optional
from ..models import Run, Task

@dataclass
class Options:
    """Base class for service options."""
    pass

@dataclass
class RetryOptions(Options):
    run: Run
    task: Task
    args: Any = None
    output_func: Callable[[str], None] = print

@dataclass
class SyncOptions(Options):
    skip_pr_sync: bool = False
    json_output: bool = False
    output_func: Callable[[str], None] = print

@dataclass
class ApproveOptions(Options):
    run: Run
    task: Task
    task_id_for_print: str
    output_func: Callable[[str], None] = print

@dataclass
class MergeOptions(Options):
    run: Run
    task: Task
    task_id_for_print: str
    merge_method: Optional[str] = None
    delete_branch: Optional[bool] = None
    pull: Optional[bool] = None
    output_func: Callable[[str], None] = print

@dataclass
class ReviewOptions(Options):
    task: Task
    tool_name: str = "codex"
    tool_bin: Optional[str] = None
    gemini_skip_trust: bool = False
    output_func: Callable[[str], None] = print

@dataclass
class ReviewPassOptions(Options):
    task: Task
    output_func: Callable[[str], None] = print

@dataclass
class SendOptions(Options):
    run: Run
    task: Task
    message: str
    task_id_for_print: str
    output_func: Callable[[str], None] = print

@dataclass
class NextOptions(Options):
    run: Run
    task: Task
    args: Any = None
    output_func: Callable[[str], None] = print
