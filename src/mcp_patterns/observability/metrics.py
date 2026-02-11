from __future__ import annotations

from dataclasses import dataclass, field

from opentelemetry import metrics

_METER_NAME = "mcp-patterns.observability"

# OTel MCP semantic convention bucket boundaries (seconds)
_DURATION_BUCKETS = (
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1,
    2,
    5,
    10,
    30,
    60,
    120,
    300,
)


@dataclass(frozen=True)
class MCPMetrics:
    """Container for MCP server metrics instruments.

    All metrics follow the OTel MCP semantic conventions where applicable,
    extended with pattern-specific custom metrics for cache and auth
    observability.
    """

    # --- Standard MCP metrics (OTel semantic conventions) ---
    server_operation_duration: metrics.Histogram = field(repr=False)
    server_session_duration: metrics.Histogram = field(repr=False)

    # --- Cache metrics (Pattern 1 integration) ---
    cache_refresh_duration: metrics.Histogram = field(repr=False)
    cache_refresh_items: metrics.Gauge = field(repr=False)
    cache_age_seconds: metrics.Gauge = field(repr=False)
    cache_refresh_errors: metrics.Counter = field(repr=False)

    # --- Auth metrics (Pattern 2 integration) ---
    auth_verify_duration: metrics.Histogram = field(repr=False)
    auth_decisions_total: metrics.Counter = field(repr=False)

    # --- Tool metrics ---
    tools_call_total: metrics.Counter = field(repr=False)


def create_mcp_metrics(meter_name: str | None = None) -> MCPMetrics:
    """Create and return all MCP server metric instruments.

    Instruments are created once per meter name and are safe to call
    multiple times (OTel de-duplicates by name).
    """
    meter = metrics.get_meter(meter_name or _METER_NAME)

    return MCPMetrics(
        server_operation_duration=meter.create_histogram(
            name="mcp.server.operation.duration",
            description="Duration of MCP server operations",
            unit="s",
        ),
        server_session_duration=meter.create_histogram(
            name="mcp.server.session.duration",
            description="Duration of MCP server sessions",
            unit="s",
        ),
        cache_refresh_duration=meter.create_histogram(
            name="mcp.cache.refresh.duration",
            description="Duration of cache refresh cycles",
            unit="s",
        ),
        cache_refresh_items=meter.create_gauge(
            name="mcp.cache.refresh.items_count",
            description="Number of items in cache after last refresh",
        ),
        cache_age_seconds=meter.create_gauge(
            name="mcp.cache.age_seconds",
            description="Seconds since last successful cache refresh",
        ),
        cache_refresh_errors=meter.create_counter(
            name="mcp.cache.refresh.errors",
            description="Count of failed cache refresh attempts",
        ),
        auth_verify_duration=meter.create_histogram(
            name="mcp.auth.verify.duration",
            description="Duration of token verification",
            unit="s",
        ),
        auth_decisions_total=meter.create_counter(
            name="mcp.auth.decisions.total",
            description="Authorization decisions by outcome",
        ),
        tools_call_total=meter.create_counter(
            name="mcp.tools.call.total",
            description="Tool invocations by tool name and status",
        ),
    )
