"""Invocation-local diagnostics, never inserted into the parent tool response."""

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class Diagnostics:
    observations: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    cache: list[str] = field(default_factory=list)
    model_usage: list[dict] = field(default_factory=list)
    handoffs: list[dict] = field(default_factory=list)


current: ContextVar[Diagnostics | None] = ContextVar("parts_diagnostics", default=None)
