# Pattern 1: Resource Caching

## Overview

This pattern describes an MCP server that acts as an API layer for a downstream (non-MCP) service. On connection, the server eagerly fetches all required data from the downstream service in a single operation, stores it in a structured MCP resource, and makes it programmatically available to tools. Subsequent tool calls inject arguments from this cached resource without requiring the full resource content to be presented to the LLM or the downstream service to be queried again. Refresh is governed by a statically defined TTL.

---

## Problem Statement

When an MCP server wraps a downstream API, naive implementations make redundant API calls:

1. The LLM calls `list_resources()` or `read_resource()` to understand the available data
2. The LLM calls a tool, which internally queries the same downstream API
3. Each tool invocation re-fetches data that could have been cached
4. The LLM may need to relay large resource contents back into tool arguments, wasting context window tokens

The goal is to **decouple data fetching from tool execution** by pre-populating a server-side cache that tools can access programmatically, without the LLM acting as an intermediary data shuttle.

---

## Key SDK Mechanisms

### 1. Server Lifespan for Initialization

The MCP Python SDK provides a `lifespan` async context manager that runs once when the server starts. This is the natural place to perform the initial data fetch from the downstream service.

```python
# SDK reference: mcp.server.fastmcp.FastMCP accepts a lifespan parameter
# SDK reference: mcp.server.mcpserver.MCPServer also accepts lifespan

@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    # Startup: fetch all data from downstream service
    data = await fetch_all_from_downstream()
    yield AppContext(cached_data=data)
    # Shutdown: cleanup
```

**Consideration**: Lifespan context is **server-scoped**, meaning all sessions share it. This is appropriate when the cached data is the same for all connected clients. If per-session caching is needed, the pattern must be adjusted (see Session-Scoped Caching below).

### 2. Resources as Structured Data Stores

MCP resources provide read-only access to data, analogous to GET endpoints. The cached data can be exposed as one or more resources so that:
- Clients (or the LLM) can optionally inspect the data via `read_resource()`
- Tools can programmatically access the same data via `ctx.read_resource(uri)` or by directly referencing the lifespan context

**Resource registration approaches:**

| Approach | Mechanism | When to Use |
|---|---|---|
| Static resource via decorator | `@mcp.resource("cache://data")` | When the resource URI is known at definition time |
| Dynamic resource (template) | `@mcp.resource("cache://data/{category}")` | When cached data has multiple segments |
| Programmatic via `add_resource()` | `mcp.add_resource(TextResource(...))` | When resources are created at runtime during lifespan |

### 3. Tool Access to Cached Data

Tools can access the cached resource data through two pathways:

**Pathway A: Via Lifespan Context (Direct Memory Access)**

```python
@mcp.tool()
async def answer_question(question: str, ctx: Context[ServerSession, AppContext]) -> str:
    cached = ctx.request_context.lifespan_context.cached_data
    # Use cached data to formulate answer
```

This is the most performant path. The tool reads directly from the in-memory cache without going through the MCP resource protocol.

**Pathway B: Via `ctx.read_resource()` (Protocol-Aware Access)**

```python
@mcp.tool()
async def answer_question(question: str, ctx: Context) -> str:
    result = await ctx.read_resource("cache://data")
    # Parse and use the resource content
```

This follows the MCP resource protocol, which means it goes through the resource handler function. This is useful when the resource function itself applies transformations or formatting.

### 4. Background Refresh via `asyncio`

Periodic refresh can be implemented by spawning a background task during the lifespan:

```python
# Background task that refreshes the cache on a fixed interval
async def refresh_loop(context: AppContext, interval_seconds: int):
    while True:
        await asyncio.sleep(interval_seconds)
        new_data = await fetch_all_from_downstream()
        context.update_cache(new_data)
```

**Consideration**: Since the refresh task mutates shared state, thread-safe access patterns are necessary if the server handles concurrent requests (which it does with Streamable HTTP transport).

---

## Architectural Design

### Data Flow

```
┌──────────────┐                    ┌──────────────────┐                   ┌──────────────────┐
│              │   1. initialize()  │                  │  2. Eager fetch   │                  │
│  MCP Client  │ ────────────────>  │   MCP Server     │ ────────────────> │ Downstream API   │
│  (LLM Host)  │                    │                  │ <──────────────── │ Service          │
│              │                    │  ┌────────────┐  │  3. Response      │                  │
│              │                    │  │ Cache      │  │                   │                  │
│              │   4. call_tool()   │  │ (Resource) │  │                   │                  │
│              │ ────────────────>  │  └────────────┘  │                   │                  │
│              │ <────────────────  │     ↑ read       │                   │                  │
│              │   5. result        │     │ from cache  │                   │                  │
│              │                    │  ┌────────────┐  │                   │                  │
│              │                    │  │ Tool       │  │                   │                  │
│              │                    │  │ Handler    │──┘                   │                  │
│              │                    │  └────────────┘                      │                  │
│              │                    │                  │  6. Periodic      │                  │
│              │                    │  ┌────────────┐  │     refresh       │                  │
│              │                    │  │ Background │ ────────────────>    │                  │
│              │                    │  │ Task       │ <────────────────    │                  │
│              │                    │  └────────────┘  │                   │                  │
└──────────────┘                    └──────────────────┘                   └──────────────────┘
```

### Cache Structure

The cache should be modeled as a typed dataclass or Pydantic model that:
- Captures all the data that tools will need
- Provides field-level access so tools can extract specific values without parsing the full structure
- Includes metadata like `last_refreshed_at` and `ttl_seconds`

```python
# Conceptual structure (not implementation code)
@dataclass
class CachedServiceData:
    entities: dict[str, Entity]       # Keyed for O(1) lookup
    last_refreshed_at: datetime
    ttl_seconds: int
    lock: asyncio.Lock                # For thread-safe updates
```

### Lifecycle Phases

| Phase | Trigger | Action |
|---|---|---|
| **Initialization** | Server startup (lifespan `__aenter__`) | Fetch all data from downstream; populate cache; start refresh task |
| **Serving** | Tool calls during MCP session | Read from cache; never query downstream |
| **Refresh** | Background task fires on TTL interval | Re-fetch from downstream; atomically update cache; optionally send `ResourceUpdatedNotification` |
| **Shutdown** | Server shutdown (lifespan `__aexit__`) | Cancel refresh task; close downstream HTTP client |

---

## Design Considerations

### 1. Server-Scoped vs. Session-Scoped Caching

**Server-scoped** (via lifespan context):
- One cache shared across all connected clients
- Appropriate when the downstream data is the same for all users
- Lifespan runs once at server start, not per connection
- Memory efficient: single copy of the data

**Session-scoped** (via `ctx.set_state()` / `ctx.get_state()`):
- Per-session cache that expires with the session
- Appropriate when the downstream data varies per user (e.g., the downstream API requires user-specific credentials)
- State is automatically keyed by session ID
- State expires after 1 day by default in the SDK
- The session state API stores arbitrary values: `ctx.set_state("cache_key", serialized_data)`

**Hybrid approach**: Server-scoped cache for shared data, session-scoped overlays for user-specific data.

**Question**: If the downstream API requires user-specific credentials (e.g., per-user API keys passed via the MCP auth flow), should the cache be purely session-scoped, or should there be a shared base cache with session-scoped deltas?

### 2. Eager Fetch Strategy: Single Method

The pattern specifies fetching "all required information in a single method." This implies:

- The downstream API must support a bulk/batch endpoint, or
- The server must orchestrate multiple API calls concurrently (e.g., `asyncio.gather(fetch_users(), fetch_config(), fetch_rules())`) and aggregate results
- The fetch method should be idempotent and resilient to partial failures

**Question**: What happens if the initial fetch fails? Options:
- **Fail-fast**: Server refuses to start. The MCP client gets an error on `initialize()`.
- **Graceful degradation**: Server starts with empty cache. Tools return "data not available" responses. Refresh task will populate cache when downstream becomes available.
- **Retry with backoff**: Server startup blocks (up to a timeout) while retrying the downstream fetch.

### 3. Cache Invalidation and Refresh

The pattern specifies "refresh and lifecycle statically defined as a set time." This means:

- No event-driven invalidation (e.g., webhooks from downstream)
- No client-triggered cache busting
- A fixed interval (e.g., every 300 seconds) governs when the cache is refreshed

**SDK mechanism for notifying clients of cache updates:**

The MCP spec supports `ResourceUpdatedNotification`. When the background refresh task updates the cache, the server can notify subscribed clients:

```python
# After updating the cache:
await ctx.session.send_resource_updated(uri="cache://data")
```

However, since the primary consumers are tools (not the LLM reading resources directly), notifications may not be necessary unless the LLM is expected to re-read resources.

**Consideration**: The `ResourceManager` automatically sends `ResourceListChangedNotification` when resources are added or removed. If the refresh changes the set of resources (not just their content), this notification fires automatically.

### 4. Tool Argument Injection from Cache

The pattern states: "Subsequent questions and tool calls will inject arguments from this resource without requiring the full resource content to be presented to the LLM."

This is a critical design point. There are several interpretations:

**Interpretation A: Tool-internal enrichment**

The tool accepts minimal arguments from the LLM (e.g., an entity name or ID) and internally enriches the request using the cache:

```
LLM -> call_tool("get_firewall_status", {"app_name": "myapp"})
Tool -> reads cache, finds all firewall rules for "myapp", returns formatted answer
```

The LLM never sees or handles the full cached dataset. This is the most likely intended pattern.

**Interpretation B: Server-side argument resolution**

The server pre-resolves tool arguments before the tool function executes. This would require custom middleware or a tool wrapper that intercepts the call and injects additional arguments.

**Interpretation C: Resource-linked tool parameters**

Tool parameters reference resource URIs, and the server resolves them transparently. The MCP spec doesn't natively support this, but it could be implemented as a convention.

**Recommendation**: Interpretation A is the most natural fit. Tools should accept minimal arguments from the LLM, do a keyed lookup in the cache (O(1) via dict), and use the cached data internally.

### 5. Concurrency and Thread Safety

With Streamable HTTP transport, multiple clients can connect simultaneously, and a single client can make concurrent tool calls. The cache must be safe for concurrent reads during updates.

**Approaches:**

| Strategy | Mechanism | Trade-off |
|---|---|---|
| `asyncio.Lock` | Acquire lock for reads and writes | Safe but serializes all access |
| Copy-on-write | Refresh creates a new cache object and atomically swaps the reference | No locking needed for reads; best for read-heavy workloads |
| `threading.RLock` | For mixed sync/async code | Needed if sync tools access the cache |

**Recommendation**: Copy-on-write is ideal. The refresh task builds a complete new cache object, then replaces the reference in one atomic assignment. Python's GIL ensures reference assignment is atomic for simple attributes.

### 6. Cache Data vs. MCP Resource Duality

There's a tension between storing data as a Python object in memory (for tool performance) and exposing it as an MCP resource (for protocol compliance and LLM visibility):

| Aspect | In-Memory Object | MCP Resource |
|---|---|---|
| Access speed | O(1) attribute access | Requires serialization/deserialization |
| LLM visibility | Not directly visible | Can be read via `read_resource()` |
| Tool access | Direct Python object | Must parse text/JSON content |
| Protocol compliance | Not an MCP primitive | First-class MCP primitive |

**Recommendation**: Maintain the cache as an in-memory Python object (dataclass/Pydantic model). Optionally expose it as an MCP resource for transparency, but tools should always access the in-memory object directly via lifespan context. The resource registration is a "view" over the cache, not the cache itself.

### 7. Downstream Client Lifecycle

The HTTP client used to communicate with the downstream API should be:
- Created during lifespan startup (reuse connection pool)
- Shared across all requests
- Closed during lifespan shutdown

Libraries to consider:
- `httpx.AsyncClient` - Modern async HTTP client with connection pooling
- `aiohttp.ClientSession` - Alternative async HTTP client

The lifespan pattern naturally accommodates this:

```python
@asynccontextmanager
async def app_lifespan(server):
    async with httpx.AsyncClient(base_url="https://api.downstream.com") as client:
        data = await client.get("/bulk-data")
        cache = build_cache(data.json())
        yield AppContext(cache=cache, http_client=client)
```

### 8. Error Handling During Refresh

The background refresh task must handle errors gracefully:

- **Downstream unavailable**: Keep serving stale cache. Log warning. Retry on next interval.
- **Partial data**: Decide whether to merge partial results into existing cache or discard.
- **Cache corruption**: If the new data fails validation, keep the old cache.

**Consideration**: The cache should have a `is_stale` property based on `last_refreshed_at + ttl_seconds < now`. Tools can optionally include a "data may be stale" warning in their responses.

---

## SDK Version Considerations

| Feature | SDK Version | Notes |
|---|---|---|
| `FastMCP` server | v1.0+ | High-level server API |
| Lifespan hooks | v1.0+ | Available on both `FastMCP` and `MCPServer` |
| `ctx.read_resource()` | v2.2.5+ | Context access in tools |
| `ctx.set_state()` / `ctx.get_state()` | v2.x | Session-scoped state |
| `ResourceUpdatedNotification` | v1.0+ (spec) | Notify clients of resource changes |
| Streamable HTTP transport | v1.8+ | Production deployment transport |
| `cache_tools_list` | v1.x+ | Caching for tool/resource list responses |

---

## Open Questions

1. **Cache granularity**: Should the cache be a single monolithic resource (e.g., `cache://all-data`) or broken into multiple resources (e.g., `cache://users`, `cache://rules`, `cache://config`)? Multiple resources give finer-grained access but require more coordination.

2. **Cache warming on reconnection**: If a client disconnects and reconnects, should the cache be refreshed immediately, or should it continue using the existing server-scoped cache? The lifespan is server-scoped, so reconnection doesn't trigger it. Only a new client session is created.

3. **Backpressure**: If the downstream service is slow, should the initial fetch have a timeout that causes the server to start without data? How does this affect tool behavior?

4. **Memory limits**: For large downstream datasets, should the cache implement size limits or eviction policies? The pattern assumes the entire dataset fits comfortably in memory.

5. **Observability**: Should the cache expose metadata resources (e.g., `cache://meta/last-refresh`, `cache://meta/health`) for monitoring and debugging?

6. **Transport choice**: For a production deployment where multiple LLM agents might connect to this MCP server, Streamable HTTP is the appropriate transport. Does the caching pattern need any adjustments for stateless HTTP mode (`stateless_http=True`)?

7. **Testing strategy**: How should the cache be tested in isolation? Should there be a mock downstream service, or should tests use dependency injection to replace the fetch function?

---

## Related Python Libraries

| Library | Role |
|---|---|
| `mcp` (PyPI) | MCP Python SDK - server framework, resource/tool registration |
| `httpx` | Async HTTP client for downstream API communication |
| `pydantic` | Data validation and serialization for cache structure |
| `asyncio` | Background refresh task, concurrency primitives |

---

## Summary

The Resource Caching pattern centers on:

1. **Lifespan-based eager fetch** at server startup to populate an in-memory cache
2. **Typed cache structure** (dataclass/Pydantic) for O(1) field-level access
3. **Tools read from cache directly** via lifespan context, not through the LLM
4. **Optional MCP resource exposure** as a transparent view over the cache
5. **Background refresh** on a static TTL interval with copy-on-write updates
6. **Graceful staleness handling** when the downstream service is unavailable
