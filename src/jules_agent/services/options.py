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
    automation_mode: Optional[str] = None
    output_func: Callable[[str], None] = print

@dataclass
class SyncOptions(Options):
    skip_pr_sync: bool = False
    json_output: bool = False
    output_func: Callable[[str], None] = print
    error_func: Callable[[str], None] = lambda x: None
