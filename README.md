# MCP Patterns

Research and documentation of Model Context Protocol (MCP) server patterns for Python. These patterns are focused on the `mcp` Python SDK ([modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)).

This repository documents architectural considerations, SDK mechanisms, design trade-offs, and open questions for each pattern. No implementation code is included — these are research documents intended to inform future implementation.

---

## Patterns

### [Pattern 1: Resource Caching](./pattern-1-resource-caching.md)

An MCP server that acts as an API layer for a downstream service. On connection, the server eagerly fetches all required data in a single operation, stores it as a structured MCP resource / in-memory cache, and makes it programmatically available to tools. Tools inject arguments from the cache without presenting the full resource to the LLM or re-querying the downstream service. Refresh is governed by a static TTL.

**Key topics**: Lifespan hooks, resource registration, tool-internal enrichment, background refresh, copy-on-write concurrency, cache-resource duality.

### [Pattern 2: Dynamic Scopes Based Tool Execution](./pattern-2-dynamic-scopes-tool-execution.md)

An MCP server that enforces zero-trust authorization by verifying a user JWT (relayed from a trusted upstream application) against a userinfo API. User roles and entitlements are resolved at connection time and enforced across four authorization layers: global access, tool visibility, tool invocation, and data-level filtering.

**Key topics**: Token relay, `TokenVerifier`, session-scoped entitlements, component visibility, scope-to-tool mapping, defense in depth, policy engine integration.

### [Pattern 3: FastMCP Logging and Observability](./pattern-3-fastmcp-logging-observability.md)

An MCP server instrumented for structured logging, distributed tracing, and metrics via OpenTelemetry, with Arize Phoenix as the observability backend. Captures tool invocations, cache operations, authorization decisions, and downstream API calls as correlated traces. Supports context propagation across the MCP client-server boundary, evaluation workflows, and configurable verbosity for production vs. development.

**Key topics**: OpenTelemetry tracing, Arize Phoenix, OpenInference semantic conventions, MCP semantic conventions, context propagation via `_meta`, Python logging correlation, cache/auth span instrumentation, metrics histograms, evaluation workflows.

---

## Cross-Cutting Concerns

When combining these patterns, several considerations arise:

| Concern | Pattern 1 Impact | Pattern 2 Impact |
|---|---|---|
| **Cache scope** | Server-scoped (shared across sessions) vs. session-scoped | Per-user entitlements affect what data is visible |
| **Data filtering** | Cache contains all data; tools select relevant subsets | Entitlements constrain which subsets are authorized |
| **Initialization** | Lifespan fetches data from downstream | Token verification resolves user entitlements |
| **Session state** | Optional (cache is server-scoped) | Required (entitlements stored per session) |
| **Refresh** | TTL-based background refresh of cached data | JWT expiration governs entitlement staleness |
| **Observability** | Cache refresh spans and staleness metrics | Auth decision audit spans with user/scope attributes |

**Key question**: If both patterns are combined, should the cache be pre-filtered per user's entitlements (session-scoped cache), or should a server-wide cache be filtered at tool execution time? The latter is more memory-efficient; the former is more secure by default (no risk of leaking unfiltered data through a bug).

**Observability integration**: Pattern 3 provides the tracing and metrics layer for both patterns. Cache operations (Pattern 1) and authorization decisions (Pattern 2) produce OTel spans that are correlated into unified traces viewable in Arize Phoenix.

---

## SDK References

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — `mcp` package on PyPI
- [MCP Python SDK Docs](https://modelcontextprotocol.github.io/python-sdk/)
- [MCP Specification](https://modelcontextprotocol.io/specification/)
- [FastMCP Docs](https://gofastmcp.com/)

## Python Libraries Referenced

| Library | Purpose |
|---|---|
| `mcp` | MCP server/client SDK |
| `httpx` | Async HTTP client |
| `pydantic` | Data validation and serialization |
| `PyJWT` / `python-jose` | JWT handling |
| `authlib` | OAuth 2.0/OIDC support |
| `casbin` / `cedarpy` | Policy engines for authorization |
| `opentelemetry-api` / `opentelemetry-sdk` | Distributed tracing and metrics |
| `arize-phoenix-otel` | Arize Phoenix OTel configuration |
| `openinference-instrumentation-mcp` | MCP client-server context propagation |
