from __future__ import annotations

import json
import time
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.trace import StatusCode

P = ParamSpec("P")
R = TypeVar("R")

_TRACER_NAME = "mcp-patterns.observability"


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Return an OTel tracer scoped to the given name (or the default)."""
    return trace.get_tracer(name or _TRACER_NAME)


# ---------------------------------------------------------------------------
# Tool tracing decorator
# ---------------------------------------------------------------------------


def traced_tool(
    *,
    name: str | None = None,
    capture_io: bool | None = None,
) -> Callable[
    [Callable[P, Coroutine[Any, Any, R]]],
    Callable[P, Coroutine[Any, Any, R]],
]:
    """Decorator that wraps an MCP tool handler with an OTel span.

    Usage::

        @mcp.tool()
        @traced_tool()
        async def get_data(key: str, ctx: Context) -> str:
            ...

    The decorator creates a span named ``tools/call {tool_name}`` with
    standard MCP and OpenInference attributes.

    Args:
        name: Override the tool name (defaults to the function name).
        capture_io: Record input/output values on the span.  When ``None``,
            defers to the ``MCP_OTEL_CAPTURE_IO`` environment variable.
    """

    def decorator(
        fn: Callable[P, Coroutine[Any, Any, R]],
    ) -> Callable[P, Coroutine[Any, Any, R]]:
        tool_name = name or fn.__name__
        tracer = get_tracer()

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            should_capture = _should_capture_io(capture_io)

            with tracer.start_as_current_span(
                f"tools/call {tool_name}"
            ) as span:
                span.set_attribute("mcp.method.name", "tools/call")
                span.set_attribute("rpc.system", "mcp")
                span.set_attribute("gen_ai.tool.name", tool_name)
                span.set_attribute("tool.name", tool_name)
                span.set_attribute("openinference.span.kind", "TOOL")

                if should_capture:
                    _set_input_attrs(span, args, kwargs)

                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

                if should_capture and result is not None:
                    span.set_attribute(
                        "output.value", _safe_serialize(result)
                    )
                    span.set_attribute("output.mime_type", "text/plain")

                return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Resource tracing decorator
# ---------------------------------------------------------------------------


def traced_resource(
    *,
    uri: str | None = None,
) -> Callable[
    [Callable[P, Coroutine[Any, Any, R]]],
    Callable[P, Coroutine[Any, Any, R]],
]:
    """Decorator that wraps an MCP resource handler with an OTel span.

    Usage::

        @mcp.resource("cache://firewalls")
        @traced_resource(uri="cache://firewalls")
        async def get_firewalls() -> str:
            ...
    """

    def decorator(
        fn: Callable[P, Coroutine[Any, Any, R]],
    ) -> Callable[P, Coroutine[Any, Any, R]]:
        resource_uri = uri or fn.__name__
        tracer = get_tracer()

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with tracer.start_as_current_span(
                f"resources/read {resource_uri}"
            ) as span:
                span.set_attribute("mcp.method.name", "resources/read")
                span.set_attribute("rpc.system", "mcp")
                span.set_attribute("mcp.resource.uri", resource_uri)

                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

                return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Cache operation tracing (context manager)
# ---------------------------------------------------------------------------


class traced_cache_operation:
    """Context manager that creates an OTel span for cache operations.

    Usage::

        async with traced_cache_operation("read", key="firewalls") as span:
            data = cache.get("firewalls")
            span.set_attribute("cache.hit", data is not None)

        async with traced_cache_operation("refresh", ttl=300) as span:
            new_data = await fetch()
            span.set_attribute("cache.items_count", len(new_data))
    """

    def __init__(
        self,
        operation: str,
        *,
        key: str | None = None,
        ttl: int | None = None,
    ) -> None:
        self._operation = operation
        self._key = key
        self._ttl = ttl
        self._tracer = get_tracer()
        self._span: trace.Span | None = None
        self._token: Any = None
        self._start: float = 0.0

    async def __aenter__(self) -> trace.Span:
        self._start = time.monotonic()
        self._span = self._tracer.start_span(f"cache.{self._operation}")
        self._token = trace.use_span(self._span, end_on_exit=False).__enter__()

        self._span.set_attribute("cache.operation", self._operation)
        if self._key is not None:
            self._span.set_attribute("cache.key", self._key)
        if self._ttl is not None:
            self._span.set_attribute("cache.ttl_seconds", self._ttl)

        return self._span

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        assert self._span is not None

        elapsed = time.monotonic() - self._start
        self._span.set_attribute("cache.duration_ms", round(elapsed * 1000, 2))

        if exc_val is not None:
            self._span.set_status(StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)

        self._span.end()
        if self._token is not None:
            trace.use_span(self._span, end_on_exit=False).__exit__(
                exc_type, exc_val, exc_tb
            )


# ---------------------------------------------------------------------------
# Auth decision tracing (context manager)
# ---------------------------------------------------------------------------


class traced_auth_check:
    """Context manager that creates an OTel span for authorization decisions.

    Usage::

        async with traced_auth_check(
            tool_name="get_firewall_rule",
            user_id="user-123",
            user_scopes=["infra:read"],
            required_scopes=["infra:read"],
        ) as span:
            authorized = check_scopes(...)
            span.set_attribute("auth.decision", "allowed" if authorized else "denied")
    """

    def __init__(
        self,
        *,
        tool_name: str,
        user_id: str | None = None,
        user_scopes: list[str] | None = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        self._tool_name = tool_name
        self._user_id = user_id
        self._user_scopes = user_scopes
        self._required_scopes = required_scopes
        self._tracer = get_tracer()
        self._span: trace.Span | None = None
        self._token: Any = None

    async def __aenter__(self) -> trace.Span:
        self._span = self._tracer.start_span("auth.check_scope")
        self._token = trace.use_span(self._span, end_on_exit=False).__enter__()

        self._span.set_attribute("gen_ai.tool.name", self._tool_name)
        if self._user_id is not None:
            self._span.set_attribute("enduser.id", self._user_id)
        if self._user_scopes is not None:
            self._span.set_attribute(
                "enduser.scope", " ".join(self._user_scopes)
            )
        if self._required_scopes is not None:
            self._span.set_attribute(
                "auth.required_scopes", " ".join(self._required_scopes)
            )

        return self._span

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        assert self._span is not None

        if exc_val is not None:
            self._span.set_status(StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)

        self._span.end()
        if self._token is not None:
            trace.use_span(self._span, end_on_exit=False).__exit__(
                exc_type, exc_val, exc_tb
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_capture_io(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    import os

    return os.getenv("MCP_OTEL_CAPTURE_IO", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _safe_serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _set_input_attrs(
    span: trace.Span,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    serializable_kwargs = {
        k: v
        for k, v in kwargs.items()
        if not k.startswith("ctx") and k != "context"
    }
    if serializable_kwargs:
        span.set_attribute(
            "tool.parameters", json.dumps(serializable_kwargs, default=str)
        )
        first_val = next(iter(serializable_kwargs.values()), None)
        if first_val is not None:
            span.set_attribute("input.value", _safe_serialize(first_val))
            span.set_attribute("input.mime_type", "text/plain")
