"""Request-scoped Web ADK progress and retrieval deadline."""

from collections.abc import Callable
from contextvars import ContextVar


progress_sink: ContextVar[Callable[[str], None] | None] = ContextVar("progress_sink", default=None)
retrieval_deadline: ContextVar[float | None] = ContextVar("retrieval_deadline", default=None)


def report_progress(message: str) -> None:
    sink = progress_sink.get()
    if sink is not None:
        sink(message)
