from dataclasses import dataclass

from typing import Any, Callable
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
