# Pattern 3: FastMCP Logging and Observability with Arize OpenTelemetry

## Overview

This pattern describes how to instrument an MCP server (built with the `mcp` Python SDK's `FastMCP` interface) for structured logging, distributed tracing, metrics collection, and LLM-aware observability. Telemetry is exported via OpenTelemetry to Arize Phoenix (self-hosted or cloud), enabling trace visualization, tool-call debugging, cache-refresh monitoring, and evaluation workflows. The pattern is designed as a composable layer that integrates with Pattern 1 (Resource Caching) and Pattern 2 (Dynamic Scopes).

---

## Problem Statement

MCP servers operate as intermediaries between LLM agents and downstream services. Without observability:

1. Tool call failures are opaque — the LLM agent sees an error string, but the server operator has no trace of what happened internally
2. Cache refresh cycles (Pattern 1) run silently; stale data or fetch failures go undetected until they surface as incorrect tool responses
3. Authorization decisions (Pattern 2) are enforced but not auditable — there is no record of which user attempted which tool with which scopes
4. Latency bottlenecks between the MCP server and downstream APIs are invisible
5. There is no baseline for evaluating whether tool responses are correct, complete, or timely

The goal is to **instrument the MCP server with structured, OpenTelemetry-based telemetry that captures the full lifecycle of every request** — from tool invocation through cache access, downstream API calls, and authorization checks — and export it to Arize Phoenix for visualization, alerting, and evaluation.

---

## Key SDK Mechanisms

### 1. Python `logging` Module Integration

The `mcp` Python SDK uses Python's standard `logging` module internally. FastMCP servers can configure logging at startup to capture SDK-level events (connection lifecycle, transport errors, protocol messages):

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# SDK loggers of interest:
# "mcp.server"       — Server lifecycle events
# "mcp.server.stdio" — stdio transport messages
# "mcp.server.sse"   — SSE transport messages
# "mcp"              — Root MCP logger
```

**Consideration**: Python `logging` provides the foundation for local diagnostics. OpenTelemetry captures the structured trace/metric data for distributed observability. Both should be configured — `logging` for stderr/file output, OpenTelemetry for trace export.

### 2. MCP Context Logging Methods

The MCP SDK provides logging methods on the `Context` object that send log messages back to the connected MCP client:

```python
@mcp.tool()
async def my_tool(query: str, ctx: Context) -> str:
    await ctx.debug("Starting query processing")
    await ctx.info(f"Processing query: {query}")
    await ctx.warning("Cache is stale, results may be outdated")
    await ctx.error("Downstream service returned 500")
```

These methods emit MCP `notifications/message` protocol messages. They are visible to the MCP client (and by extension, the LLM agent) — not to a backend observability system. They complement but do not replace server-side telemetry.

### 3. OpenTelemetry API (Tracing)

OpenTelemetry provides the vendor-neutral instrumentation API. The key primitives:

```python
from opentelemetry import trace

tracer = trace.get_tracer("mcp-patterns.observability")

# Create a span for any operation
with tracer.start_as_current_span("tools/call get_data") as span:
    span.set_attribute("mcp.method.name", "tools/call")
    span.set_attribute("gen_ai.tool.name", "get_data")
    # ... operation ...
```

Without a configured `TracerProvider`, all tracing calls are no-ops with zero overhead.

### 4. OpenTelemetry SDK (Export Pipeline)

The SDK provides the concrete implementation — processors, exporters, and samplers:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)
trace.set_tracer_provider(provider)
```

**Consideration**: The `TracerProvider` must be configured **before** any spans are created. For MCP servers, this means configuring telemetry before constructing the `FastMCP` instance or at the very beginning of the lifespan.

### 5. Arize Phoenix as the Telemetry Backend

Phoenix is an open-source AI observability platform that natively understands OpenTelemetry traces and extends them with LLM-specific semantics (token counts, prompt/completion content, tool call attribution, evaluations).

```python
from phoenix.otel import register

tracer_provider = register(
    project_name="my-mcp-server",
    endpoint="http://localhost:6006/v1/traces",  # Phoenix collector
    batch=True,
)
```

Phoenix can run locally (`phoenix serve`), in a container, or as Arize Cloud (`app.phoenix.arize.com`).

### 6. OpenInference Semantic Conventions

OpenInference extends OpenTelemetry with AI/LLM-specific attribute conventions:

| Attribute | Description |
|---|---|
| `openinference.span.kind` | Span category: `TOOL`, `CHAIN`, `LLM`, `AGENT`, `RETRIEVER` |
| `tool.name` | Tool identifier |
| `tool.description` | Tool description |
| `tool.parameters` | Serialized tool input |
| `input.value` / `input.mime_type` | Span input content |
| `output.value` / `output.mime_type` | Span output content |
| `session.id` | MCP session identifier |
| `user.id` | Authenticated user identity |
| `metadata` | Arbitrary JSON metadata |

### 7. MCP Context Propagation (`openinference-instrumentation-mcp`)

The `openinference-instrumentation-mcp` package propagates W3C Trace Context between MCP clients and servers via the `params._meta` field, enabling unified traces across the client-server boundary:

```python
from openinference.instrumentation.mcp import MCPInstrumentor

MCPInstrumentor().instrument(tracer_provider=tracer_provider)
```

This enables a trace originating in an agent runtime (MCP client) to continue through the MCP server's tool execution, producing a single end-to-end trace.

---

## Architectural Design

### Telemetry Data Flow

```
┌──────────────┐                    ┌──────────────────────────────────────┐
│              │   call_tool()      │          MCP Server                  │
│  MCP Client  │ ────────────────>  │                                      │
│  (Agent      │                    │  ┌──────────┐   ┌────────────────┐  │
│   Runtime)   │                    │  │ OTel     │   │ Arize Phoenix  │  │
│              │                    │  │ Trace    │──>│ (Collector)    │  │
│              │                    │  │ Exporter │   │                │  │
│              │                    │  └──────────┘   └────────────────┘  │
│              │                    │       ↑                              │
│              │                    │  ┌────┴──────────────────────────┐  │
│              │                    │  │ Instrumented Server Logic     │  │
│              │                    │  │                               │  │
│              │                    │  │  ┌─────────────────────────┐  │  │
│              │                    │  │  │ Tool Handler            │  │  │
│              │                    │  │  │  • span: tools/call     │  │  │
│              │                    │  │  │  • attrs: tool.name,    │  │  │
│              │                    │  │  │    input, output        │  │  │
│              │                    │  │  └─────────────────────────┘  │  │
│              │                    │  │                               │  │
│              │                    │  │  ┌─────────────────────────┐  │  │
│              │                    │  │  │ Cache Operations        │  │  │
│              │                    │  │  │  • span: cache.read     │  │  │
│              │                    │  │  │  • span: cache.refresh  │  │  │
│              │                    │  │  │  • attrs: cache.hit,    │  │  │
│              │                    │  │  │    cache.age_seconds    │  │  │
│              │                    │  │  └─────────────────────────┘  │  │
│              │                    │  │                               │  │
│              │                    │  │  ┌─────────────────────────┐  │  │
│              │                    │  │  │ Auth Decisions          │  │  │
│              │                    │  │  │  • span: auth.verify    │  │  │
│              │                    │  │  │  • span: auth.authorize │  │  │
│              │                    │  │  │  • attrs: enduser.id,   │  │  │
│              │                    │  │  │    auth.decision        │  │  │
│              │                    │  │  └─────────────────────────┘  │  │
│              │                    │  │                               │  │
│              │                    │  │  ┌─────────────────────────┐  │  │
│              │                    │  │  │ Downstream API Calls    │  │  │
│              │                    │  │  │  • span: http.request   │  │  │
│              │                    │  │  │  • attrs: http.method,  │  │  │
│              │                    │  │  │    http.url, status     │  │  │
│              │                    │  │  └─────────────────────────┘  │  │
│              │                    │  └───────────────────────────────┘  │
│              │ <────────────────  │                                      │
│              │   result           │                                      │
└──────────────┘                    └──────────────────────────────────────┘
```

### Span Hierarchy for a Typical Tool Call

```
[CLIENT] tools/call get_firewall_rule
  └── [SERVER] tools/call get_firewall_rule
        ├── [INTERNAL] auth.check_scope
        │     └── attributes: enduser.id, auth.scopes, auth.decision=allowed
        ├── [INTERNAL] cache.read
        │     └── attributes: cache.hit=true, cache.key=firewalls, cache.age_seconds=42
        └── [INTERNAL] format_response
              └── attributes: output.value=..., output.mime_type=text/plain
```

### Span Hierarchy for a Cache Refresh

```
[INTERNAL] cache.refresh
  ├── [CLIENT] http.request GET /api/v1/firewalls
  │     └── attributes: http.method=GET, http.url=..., http.status_code=200
  ├── [INTERNAL] cache.build
  │     └── attributes: cache.items_count=347, cache.size_bytes=52400
  └── [INTERNAL] cache.swap
        └── attributes: cache.previous_age_seconds=300, cache.refreshed_at=...
```

### Telemetry Configuration Tiers

The pattern supports three deployment tiers, from minimal to full observability:

| Tier | What | How | Backend |
|---|---|---|---|
| **Local development** | Python `logging` + console span exporter | `logging.basicConfig()` + `ConsoleSpanExporter` | Terminal output |
| **Self-hosted observability** | Full OTel traces + metrics | `BatchSpanProcessor` + `OTLPSpanExporter` | Phoenix self-hosted (`phoenix serve`) |
| **Cloud observability** | Full OTel + evaluations + alerting | `BatchSpanProcessor` + Phoenix/Arize Cloud endpoint | `app.phoenix.arize.com` or Arize Cloud |

---

## Design Considerations

### 1. Instrumentation Scope: What to Trace

Not every function needs a span. Over-instrumentation creates noise and increases export volume. The pattern recommends tracing at these boundaries:

| Boundary | Span Name Convention | Why |
|---|---|---|
| Tool invocation | `tools/call {tool_name}` | Primary unit of work in MCP |
| Resource read | `resources/read {uri}` | Tracks resource access patterns |
| Cache read (hit/miss) | `cache.read` | Identifies cache effectiveness |
| Cache refresh cycle | `cache.refresh` | Monitors background refresh health |
| Downstream HTTP call | `http.request {method} {path}` | Tracks external dependency latency |
| Token verification | `auth.verify_token` | Monitors auth latency and failures |
| Authorization check | `auth.check_scope` | Audit trail for access decisions |
| Server lifespan startup | `server.startup` | Captures initialization time and failures |
| Server lifespan shutdown | `server.shutdown` | Captures graceful shutdown |

**Consideration**: Use `opentelemetry.instrumentation.httpx` to auto-instrument all `httpx.AsyncClient` calls. This covers downstream API calls without manual span creation.

### 2. Attribute Schema: OTel MCP Semantic Conventions

The OpenTelemetry project has published [official semantic conventions for MCP](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/). These should be used as the primary attribute schema:

**Required attributes:**

| Attribute | Description | Example |
|---|---|---|
| `mcp.method.name` | MCP protocol method | `"tools/call"`, `"resources/read"` |

**Conditionally required:**

| Attribute | Condition | Example |
|---|---|---|
| `error.type` | When operation fails | `"ToolError"`, `"TimeoutError"` |
| `gen_ai.tool.name` | When involving a tool | `"get_firewall_rule"` |
| `mcp.resource.uri` | When involving a resource | `"cache://firewalls"` |

**Recommended:**

| Attribute | Description | Example |
|---|---|---|
| `mcp.protocol.version` | MCP version | `"2025-03-26"` |
| `mcp.session.id` | Session identifier | `"sess_abc123"` |
| `rpc.system` | Always `"mcp"` | `"mcp"` |
| `rpc.service` | Server name | `"firewall-mcp-server"` |
| `server.address` / `server.port` | Server endpoint | `"0.0.0.0"` / `8080` |

**OpenInference extensions (for Phoenix):**

| Attribute | Description | Example |
|---|---|---|
| `openinference.span.kind` | AI span type | `"TOOL"`, `"CHAIN"` |
| `tool.name` | Tool identifier | `"get_firewall_rule"` |
| `tool.parameters` | Serialized input | `'{"app_name": "alpha"}'` |
| `input.value` | Raw input | `"alpha"` |
| `output.value` | Raw output | `"Rule: allow 443 inbound"` |
| `session.id` | Maps to MCP session | `"sess_abc123"` |
| `user.id` | Authenticated user | `"user-123"` |

### 3. Metrics: What to Measure

The OTel MCP semantic conventions define four histogram metrics:

| Metric | Description | Unit |
|---|---|---|
| `mcp.server.operation.duration` | Server-side processing time per operation | seconds |
| `mcp.server.session.duration` | Total session lifetime | seconds |
| `mcp.client.operation.duration` | Client-side round-trip time | seconds |
| `mcp.client.session.duration` | Client-side session lifetime | seconds |

**Pattern-specific metrics (custom):**

| Metric | Type | Description |
|---|---|---|
| `mcp.cache.refresh.duration` | Histogram | Time to complete a cache refresh cycle |
| `mcp.cache.refresh.items_count` | Gauge | Number of items in cache after refresh |
| `mcp.cache.age_seconds` | Gauge | Seconds since last successful refresh |
| `mcp.cache.refresh.errors` | Counter | Count of failed refresh attempts |
| `mcp.auth.verify.duration` | Histogram | Time to verify a token |
| `mcp.auth.decisions.total` | Counter | Authorization decisions by outcome (allowed/denied) |
| `mcp.tools.call.total` | Counter | Tool invocations by tool name and status |

### 4. Trace Context Propagation Across the MCP Boundary

MCP uses JSON-RPC over HTTP (Streamable HTTP transport) or stdio. Trace context must be propagated so that a trace originating in the agent runtime continues through the MCP server:

**Mechanism**: W3C Trace Context headers (`traceparent`, `tracestate`) are carried in the `params._meta` field of JSON-RPC messages. The `openinference-instrumentation-mcp` package handles injection (client-side) and extraction (server-side) automatically.

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "get_firewall_rule",
    "arguments": {"app_name": "alpha"},
    "_meta": {
      "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    }
  },
  "id": 1
}
```

**Consideration**: If the MCP client does not inject `traceparent`, the server creates a new root span. The pattern should work gracefully in both cases.

### 5. Logging Correlation: Linking Python Logs to OTel Traces

Python log records can be enriched with trace context so that log lines are correlated with the active span:

```python
import logging
from opentelemetry import trace

class TraceContextFilter(logging.Filter):
    def filter(self, record):
        span = trace.get_current_span()
        ctx = span.get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.trace_id else ""
        record.span_id = format(ctx.span_id, "016x") if ctx.span_id else ""
        return True
```

This enables searching logs by trace ID in any log aggregation system.

### 6. Sensitive Data Handling

MCP tool inputs and outputs may contain sensitive data (PII, credentials, infrastructure details). The pattern must address:

| Strategy | Mechanism | When |
|---|---|---|
| **Redaction** | Strip sensitive fields before setting span attributes | Default for production |
| **Sampling** | Only export a fraction of traces (e.g., 10%) | High-throughput production |
| **Separate storage** | Log full data locally, export only metadata via OTel | Compliance-sensitive environments |
| **Opt-in verbosity** | Environment variable controls whether `input.value`/`output.value` are captured | Development vs. production |

```python
CAPTURE_IO = os.getenv("MCP_OTEL_CAPTURE_IO", "false").lower() == "true"

if CAPTURE_IO:
    span.set_attribute("input.value", str(tool_input))
    span.set_attribute("output.value", str(tool_output))
```

### 7. Integration with Pattern 1 (Resource Caching)

Cache operations should produce spans and metrics:

- **Cache read**: Record hit/miss, key, age of cached data
- **Cache refresh**: Record duration, items fetched, success/failure
- **Cache swap**: Record the atomic reference swap as an event

```python
with tracer.start_as_current_span("cache.refresh") as span:
    span.set_attribute("cache.ttl_seconds", self.ttl)
    new_data = await self.fetch_fn()
    span.set_attribute("cache.items_count", len(new_data))
    # Atomic swap
    self._data = new_data
    span.add_event("cache.swapped", {"cache.previous_age_seconds": age})
```

### 8. Integration with Pattern 2 (Dynamic Scopes)

Authorization decisions should produce spans with audit-relevant attributes:

```python
with tracer.start_as_current_span("auth.check_scope") as span:
    span.set_attribute("enduser.id", user_id)
    span.set_attribute("enduser.scope", " ".join(user_scopes))
    span.set_attribute("auth.required_scopes", " ".join(required))
    span.set_attribute("auth.decision", "allowed" if authorized else "denied")
    span.set_attribute("gen_ai.tool.name", tool_name)
```

This creates an auditable trail of every authorization decision, queryable in Phoenix by user, tool, or decision outcome.

### 9. Evaluation Workflows with Phoenix

Phoenix supports evaluation of traced spans using LLM-as-judge or custom evaluators. For MCP servers, evaluation can assess:

| Evaluation Target | What to Evaluate | Method |
|---|---|---|
| Tool response quality | Is the tool response complete and accurate? | LLM-as-judge on `output.value` |
| Tool response latency | Are responses within SLA? | Threshold check on span duration |
| Cache freshness | Is cached data acceptably recent? | Check `cache.age_seconds` vs. TTL |
| Auth correctness | Are authorization decisions consistent with policy? | Rule-based evaluator |

Phoenix evaluations run asynchronously over exported traces. They do not affect server runtime.

### 10. Graceful Degradation

The telemetry layer must never break the MCP server:

- If the OTel collector (Phoenix) is unavailable, the `BatchSpanProcessor` queues spans in memory and drops them if the queue fills. The server continues serving requests.
- If `register()` fails, the server should catch the exception and fall back to no-op tracing.
- All span creation should be wrapped defensively — a tracing error should never propagate to the tool caller.

---

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PHOENIX_COLLECTOR_ENDPOINT` | Phoenix collector URL | `http://localhost:6006` |
| `PHOENIX_PROJECT_NAME` | Project name for trace grouping | `"default"` |
| `PHOENIX_API_KEY` | API key for Phoenix Cloud / Arize Cloud | None |
| `MCP_OTEL_CAPTURE_IO` | Capture tool input/output in spans | `"false"` |
| `MCP_OTEL_SERVICE_NAME` | Service name for OTel resource | Server name |
| `MCP_OTEL_ENABLED` | Master switch for OTel instrumentation | `"true"` |
| `MCP_LOG_LEVEL` | Python logging level | `"INFO"` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Standard OTel endpoint (alternative to Phoenix) | None |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `"grpc"` or `"http/protobuf"` | `"grpc"` |

### Programmatic Configuration

```python
from mcp_patterns.observability import configure_telemetry, TelemetryConfig

config = TelemetryConfig(
    service_name="firewall-mcp-server",
    phoenix_endpoint="http://localhost:6006/v1/traces",
    phoenix_project_name="firewall-server",
    capture_tool_io=False,
    log_level="INFO",
    batch=True,
)

tracer_provider = configure_telemetry(config)
```

---

## SDK Version Considerations

| Feature | Package | Version | Notes |
|---|---|---|---|
| `FastMCP` server | `mcp` | v1.0+ | High-level server API |
| Context logging (`ctx.info()`) | `mcp` | v1.0+ | Client-facing log messages |
| OTel API (no-op tracing) | `opentelemetry-api` | 1.20+ | Zero-overhead when unconfigured |
| OTel SDK (TracerProvider) | `opentelemetry-sdk` | 1.20+ | Required for actual trace export |
| OTLP gRPC exporter | `opentelemetry-exporter-otlp-proto-grpc` | 1.20+ | For Phoenix/any OTLP backend |
| OTLP HTTP exporter | `opentelemetry-exporter-otlp-proto-http` | 1.20+ | Alternative to gRPC |
| Phoenix OTel helper | `arize-phoenix-otel` | 0.14+ | `register()` convenience function |
| OpenInference conventions | `openinference-semantic-conventions` | 0.1+ | AI/LLM attribute constants |
| MCP context propagation | `openinference-instrumentation-mcp` | 1.0+ | `traceparent` via `_meta` |
| httpx auto-instrumentation | `opentelemetry-instrumentation-httpx` | 0.44+ | Auto-instrument downstream calls |

---

## Open Questions

1. **Span granularity for cache reads**: Should every cache dictionary lookup produce a span, or only cache reads triggered by tool calls? Per-lookup spans could be noisy in a high-throughput server.

2. **Metrics export**: Should metrics be exported via OTel Metrics SDK (histograms, counters) to Phoenix, or is trace-based analysis sufficient? Phoenix supports trace-derived metrics but also accepts native OTel metrics.

3. **Log export via OTel**: The OpenTelemetry Logs SDK can bridge Python `logging` records into OTel log records for export. Should this pattern adopt OTel logs export, or keep logs and traces as separate pipelines?

4. **Sampling strategy**: For high-throughput MCP servers, should the pattern use head-based sampling (e.g., `TraceIdRatioBased(0.1)`) or tail-based sampling (export all, sample at the collector)? Tail-based preserves error traces but requires more collector resources.

5. **Multi-server trace stitching**: When an MCP server delegates to another MCP server (mounted sub-servers or proxy pattern), how should trace context be propagated through the delegation chain? The `openinference-instrumentation-mcp` package handles single-hop propagation, but multi-hop may require additional configuration.

6. **Evaluation feedback loops**: Can Phoenix evaluation results (e.g., "this tool response was low quality") be fed back into the MCP server to trigger re-computation or cache invalidation? This would close the observability loop but adds complexity.

7. **Cost attribution**: For servers that call LLM APIs internally (e.g., RAG tools), should the pattern capture token counts and cost attributes (`llm.token_count.*`, `llm.cost.*`) from OpenInference conventions?

8. **Interaction with MCP client telemetry**: If the MCP client (agent runtime) also exports traces, how are duplicate spans (client-side `tools/call` + server-side `tools/call`) handled in Phoenix? The client span has kind `CLIENT` and the server span has kind `SERVER` — Phoenix should correlate them via `traceparent`, but this needs verification.

---

## Related Python Libraries

| Library | Role |
|---|---|
| `mcp` (PyPI) | MCP Python SDK — server framework, resource/tool registration |
| `opentelemetry-api` | OTel tracing API (no-op without SDK) |
| `opentelemetry-sdk` | OTel SDK — TracerProvider, processors, samplers |
| `opentelemetry-exporter-otlp-proto-grpc` | OTLP gRPC exporter for traces |
| `opentelemetry-exporter-otlp-proto-http` | OTLP HTTP exporter (alternative) |
| `opentelemetry-instrumentation-httpx` | Auto-instrument httpx HTTP calls |
| `arize-phoenix` | Phoenix observability platform (self-hosted) |
| `arize-phoenix-otel` | Phoenix-aware OTel configuration helper |
| `openinference-semantic-conventions` | AI/LLM attribute constants |
| `openinference-instrumentation-mcp` | MCP client-server context propagation |
| `pydantic` | Configuration model validation |
| `httpx` | Async HTTP client (instrumented by OTel) |

---

## Summary

The FastMCP Logging and Observability pattern centers on:

1. **Dual-layer logging** — Python `logging` for local diagnostics, MCP `ctx.info()`/`ctx.error()` for client-facing messages
2. **OpenTelemetry instrumentation** at key boundaries: tool calls, resource reads, cache operations, auth decisions, and downstream HTTP calls
3. **OTel MCP semantic conventions** as the primary attribute schema, extended with OpenInference attributes for AI-specific observability in Phoenix
4. **Arize Phoenix** as the telemetry backend for trace visualization, latency analysis, and evaluation workflows
5. **Context propagation** via `openinference-instrumentation-mcp` to unify traces across the MCP client-server boundary
6. **Composable integration** with Pattern 1 (cache spans and metrics) and Pattern 2 (auth decision audit trail)
7. **Configurable verbosity** — tool I/O capture controlled by environment variable, sampling for high-throughput deployments
8. **Graceful degradation** — telemetry failures never break the server; absent collector means no-op tracing
