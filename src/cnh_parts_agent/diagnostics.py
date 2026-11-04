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


def record_model_usage(response):
    """Retain reported usage immediately, even if a later graph step fails."""
    if (diagnostics := current.get()) is not None:
        diagnostics.model_usage.extend(
            dict(message.usage_metadata)
            for message in response.result
            if getattr(message, "usage_metadata", None)
        )
    return response
