# CLAUDE.md

## Project Overview

**mcp-patterns** is a Python library that provides reusable, importable patterns for building MCP (Model Context Protocol) servers. It is designed to be imported by downstream MCP server applications — not used standalone. The goal is to offer simple, flexible base classes and utilities that enforce established architectural patterns so that each new MCP server doesn't reinvent common concerns.

This library targets the `mcp` Python SDK ([modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)).

## Patterns

The library implements two core patterns:

### Pattern 1: Resource Caching (`pattern-1-resource-caching.md`)

An MCP server that wraps a downstream (non-MCP) API service. On server startup (via lifespan), it eagerly fetches all required data from the downstream service in a single operation and stores it in a typed, in-memory cache. Tools read from this cache directly via the lifespan context — the LLM never handles or relays the full cached dataset. A background `asyncio` task refreshes the cache on a statically defined TTL interval using a copy-on-write strategy. The cache is optionally exposed as an MCP resource for transparency.

Key implementation points:
- Lifespan hook performs the initial eager fetch
- Cache is a Pydantic model or dataclass with O(1) keyed lookups
- Tools access cache via `ctx.request_context.lifespan_context`
- Background refresh task with copy-on-write (atomic reference swap)
- `httpx.AsyncClient` for downstream communication (created in lifespan, shared across requests)

### Pattern 2: Dynamic Scopes Based Tool Execution (`pattern-2-dynamic-scopes-tool-execution.md`)

An MCP server that enforces zero-trust authorization using JWT token relay. A trusted upstream app (App1) authenticates the user and passes the JWT through an agent runtime (MCP client) to the MCP server. The server verifies the JWT against a userinfo API using the SDK's `TokenVerifier` protocol, resolves user roles/entitlements, and enforces authorization at four layers: global access, tool visibility, tool invocation, and data-level filtering.

Key implementation points:
- `TokenVerifier` implementation calls userinfo API and returns `AccessToken` with scopes
- Entitlements resolved at connection time and stored in session state (`ctx.set_state()`)
- Tool visibility controlled via `ctx.enable_components()` / `ctx.disable_components()` with tags
- Invocation-time scope checks (decorator pattern) as the security boundary
- Data-level filtering within tool logic based on stored entitlements

## Tech Stack and Conventions

### Package Management
- **uv** for all package management, dependency resolution, and virtual environments
- `pyproject.toml` for project metadata and dependencies
- No `requirements.txt` or `setup.py` — use `uv` exclusively

### Data Modeling
- **Pydantic models** for external-facing data (API responses, cache structures, entitlements, configuration)
- **dataclasses** for internal state objects (lifespan context, lightweight containers)
- Prefer Pydantic when validation or serialization is needed; prefer dataclass for simple typed containers
- All models should be type-annotated — no `Any` types unless unavoidable

### Python
- Python 3.11+ (for modern typing features: `X | Y` unions, `Self`, `TypeVar` defaults)
- Async-first: all MCP handlers, tools, and resource functions should be `async def`
- `httpx.AsyncClient` for HTTP communication (not `requests`, not `aiohttp`)
- `asyncio` for concurrency primitives (locks, tasks, events)

### Dependencies
Core dependencies:
- `mcp` — MCP Python SDK (server framework, resource/tool registration, auth)
- `httpx` — Async HTTP client
- `pydantic` — Data validation and serialization

Auth-related (Pattern 2):
- `PyJWT` or `python-jose` — JWT decoding and verification
- `authlib` — OAuth 2.0/OIDC support (if full OIDC flows are needed)

Optional (Pattern 2, policy engines):
- `casbin` — RBAC/ABAC policy engine
- `cedarpy` — Cedar policy language bindings

### Code Style
- No docstrings on obvious methods; add comments only where logic isn't self-evident
- Prefer explicit imports over star imports
- Use `from __future__ import annotations` for forward references
- Type aliases for complex types (e.g., `EntitlementMap = dict[str, set[str]]`)

## Project Structure

```
mcp-patterns/
├── pyproject.toml
├── README.md
├── CLAUDE.md
├── pattern-1-resource-caching.md        # Research/design doc
├── pattern-2-dynamic-scopes-tool-execution.md  # Research/design doc
└── src/
    └── mcp_patterns/
        ├── __init__.py
        ├── caching/                     # Pattern 1
        │   ├── __init__.py
        │   ├── cache.py                 # Cache model, refresh logic
        │   ├── lifespan.py              # Lifespan context manager factory
        │   └── resource.py              # MCP resource view over cache
        └── auth/                        # Pattern 2
            ├── __init__.py
            ├── verifier.py              # TokenVerifier implementation
            ├── entitlements.py           # Entitlement models and resolution
            ├── scopes.py                # Scope-to-tool mapping, decorators
            └── middleware.py            # Authorization enforcement
```

## Design Principles

1. **Import and extend** — Downstream MCP servers import base classes/utilities from this library and configure them for their specific downstream API and auth provider. The library should not impose opinions about the downstream service's API shape.

2. **Simple defaults, flexible overrides** — Common cases should require minimal configuration. For example, a caching server should only need to provide a fetch function and a TTL. Authorization should only need a `TokenVerifier` and a scope mapping.

3. **Pydantic for boundaries, dataclass for internals** — Use Pydantic models at serialization boundaries (cache content exposed as resources, entitlement models parsed from userinfo responses). Use dataclasses for internal state that never leaves the process.

4. **Copy-on-write for shared state** — Cache refresh builds a new object and swaps the reference atomically. No locking needed for reads.

5. **Defense in depth for auth** — Tool visibility is a UX feature, not a security boundary. Invocation-time checks are mandatory. Data-level filtering is the final enforcement layer.

6. **Async-first** — All public APIs should be async. Sync wrappers are acceptable for simple utilities but should not be the primary interface.

## Development Commands

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run type checking
uv run mypy src/

# Run linting
uv run ruff check src/

# Format code
uv run ruff format src/
```

## Key MCP SDK References

- Server: `mcp.server.fastmcp.FastMCP` (high-level), `mcp.server.mcpserver.MCPServer` (typed lifespan)
- Context: `mcp.server.mcpserver.Context[ServerSession, AppContext]`
- Auth: `mcp.server.auth.provider.TokenVerifier`, `mcp.server.auth.provider.AccessToken`
- Auth settings: `mcp.server.auth.settings.AuthSettings`
- Resources: `@mcp.resource()` decorator, `mcp.add_resource()`
- Tools: `@mcp.tool()` decorator with `tags` parameter
- Session state: `ctx.set_state(key, value)`, `ctx.get_state(key)`
- Component visibility: `ctx.enable_components(tags=...)`, `ctx.disable_components(keys=...)`
- Transport: Streamable HTTP for production (`mcp.run(transport="streamable-http")`)

## Open Design Decisions

These are documented in the pattern files and should be resolved before implementation:

- Cache granularity: monolithic vs. segmented resources
- Server-scoped vs. session-scoped cache (or hybrid)
- Scope-to-tool mapping: inline decorators vs. centralized policy vs. external engine
- JWT validation strategy: userinfo call vs. local JWKS validation
- Error behavior on failed initial fetch: fail-fast vs. graceful degradation
- Interaction between Pattern 1 and Pattern 2 when combined (pre-filtered cache vs. runtime filtering)
