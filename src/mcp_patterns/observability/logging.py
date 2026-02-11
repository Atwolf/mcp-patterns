from __future__ import annotations

import logging
import sys

from opentelemetry import trace


class TraceContextFilter(logging.Filter):
    """Logging filter that injects OTel trace/span IDs into log records.

    Adds ``trace_id`` and ``span_id`` attributes to every log record so that
    log lines can be correlated with active spans.

    Usage::

        handler = logging.StreamHandler()
        handler.addFilter(TraceContextFilter())
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(trace_id)s/%(span_id)s] %(name)s %(levelname)s %(message)s"
        ))
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.trace_id = format(ctx.trace_id, "032x")  # type: ignore[attr-defined]
            record.span_id = format(ctx.span_id, "016x")  # type: ignore[attr-defined]
        else:
            record.trace_id = ""  # type: ignore[attr-defined]
            record.span_id = ""  # type: ignore[attr-defined]
        return True


def configure_logging(
    level: str = "INFO",
    *,
    include_trace_context: bool = True,
    stream: object | None = None,
) -> None:
    """Configure Python logging with optional OTel trace context injection.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
        include_trace_context: Whether to add trace/span IDs to log records.
        stream: Output stream (defaults to ``sys.stderr``).
    """
    if stream is None:
        stream = sys.stderr

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if include_trace_context:
        fmt = "%(asctime)s [%(trace_id)s/%(span_id)s] %(name)s %(levelname)s %(message)s"
    else:
        fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"

    handler = logging.StreamHandler(stream)  # type: ignore[arg-type]
    handler.setFormatter(logging.Formatter(fmt))

    if include_trace_context:
        handler.addFilter(TraceContextFilter())

    root.handlers.clear()
    root.addHandler(handler)
