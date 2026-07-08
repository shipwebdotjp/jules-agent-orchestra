from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class OperationResult:
    """Standard result for service operations."""
    exit_code: int
    message: Optional[str] = None
    data: Any = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0
