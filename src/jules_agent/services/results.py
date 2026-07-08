from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    success: bool
    message: str | None = None
    data: T | None = None
    errors: list[str] = field(default_factory=list)
