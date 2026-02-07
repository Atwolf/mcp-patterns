# Pattern 2: Dynamic Scopes Based Tool Execution

## Overview

This pattern describes an MCP server that enforces zero-trust authorization by verifying a user JWT against a userinfo API, extracting the user's roles and scopes, and dynamically constraining tool execution based on those scopes. The authorization flow involves a trusted upstream application (App1) that authenticates the user and relays their JWT through an agent runtime to the MCP server. The MCP server must resolve user entitlements at connection time and enforce them on every tool invocation.

---

## Problem Statement

In enterprise environments, MCP servers often expose tools that access sensitive resources (e.g., firewall rules, infrastructure configurations, user data). Unrestricted tool access creates security risks:

1. An LLM agent could query firewall rules for applications the user isn't entitled to
2. Without verification, the MCP server trusts whatever identity the client claims
3. Role-based access control (RBAC) must be enforced at the tool execution layer, not just at the LLM/prompt layer
4. Scopes may be fine-grained: a user may have "developer" entitlements for App A but not App B

The goal is to **verify identity at connection time, resolve entitlements, and enforce them as authorization constraints on every tool call**.

---

## System Architecture

### Token Relay Flow

```
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│              │    │                  │    │                  │    │                  │
│   App1       │    │  Agent Runtime   │    │   MCP Server     │    │  Userinfo API    │
│  (Trusted)   │    │  (MCP Client)    │    │                  │    │  (IdP / AuthZ)   │
│              │    │                  │    │                  │    │                  │
│  User authn  │    │                  │    │                  │    │                  │
│  ──────────> │    │                  │    │                  │    │                  │
│  JWT issued  │    │                  │    │                  │    │                  │
│              │    │                  │    │                  │    │                  │
│  1. Send JWT │    │                  │    │                  │    │                  │
│  ──────────> │ 2. │  Connect +       │    │                  │    │                  │
│              │    │  Bearer JWT      │    │                  │    │                  │
│              │    │  ──────────────> │ 3. │  Verify token    │    │                  │
│              │    │                  │    │  ──────────────> │    │                  │
│              │    │                  │    │ <──────────────  │    │                  │
│              │    │                  │    │  4. User roles   │    │                  │
│              │    │                  │    │     + scopes     │    │                  │
│              │    │                  │    │                  │    │                  │
│              │    │  5. call_tool()  │    │  6. Check scopes │    │                  │
│              │    │  ──────────────> │    │     before exec  │    │                  │
│              │    │ <──────────────  │    │                  │    │                  │
│              │    │  7. Filtered     │    │                  │    │                  │
│              │    │     result       │    │                  │    │                  │
└──────────────┘    └──────────────────┘    └──────────────────┘    └──────────────────┘
```

### Actors

| Actor | Role | MCP Role |
|---|---|---|
| **App1** | Trusted application that authenticates the user and issues/relays a JWT | External (not MCP) |
| **Agent Runtime** | Process executing the LLM agent; creates the MCP client session | MCP Client |
| **MCP Server** | Receives connection, verifies JWT, enforces scopes on tool calls | MCP Server (Resource Server in OAuth terms) |
| **Userinfo API** | Identity Provider or authorization service that resolves JWT to user roles/scopes | External (not MCP) |

---

## Key SDK Mechanisms

### 1. Token Verification with `TokenVerifier`

The MCP Python SDK provides the `TokenVerifier` protocol for server-side token validation. This is the entry point for zero-trust verification.

```python
# SDK reference: mcp.server.auth.provider
class TokenVerifier(Protocol):
    async def verify_token(self, token: str) -> AccessToken: ...

# SDK reference: mcp.server.auth.provider
@dataclass
class AccessToken:
    token: str
    client_id: str | None
    scopes: list[str]
    expires_at: int | None
    # Additional custom fields possible
```

The `TokenVerifier` implementation is responsible for:
1. Extracting the Bearer token from the `Authorization` header
2. Calling the userinfo API to validate the token and retrieve user claims
3. Returning an `AccessToken` with populated `scopes`

**Consideration**: The `AccessToken.scopes` field holds a flat list of scope strings. The mapping from userinfo response (which may contain nested roles, group memberships, or application entitlements) to flat scopes must be defined by the implementation.

### 2. Auth Settings

```python
# SDK reference: mcp.server.auth.settings
AuthSettings(
    issuer_url="https://auth.example.com",
    resource_server_url="https://mcp.example.com",
    required_scopes=["read"]  # Minimum scopes for any request
)
```

`required_scopes` defines a baseline set of scopes that every request must have. This acts as a global gate but is insufficient for per-tool authorization, which requires additional logic.

### 3. Session State for Storing Resolved Entitlements

Once the userinfo API returns the user's roles and entitlements, they should be stored in session state for access during tool execution:

```python
# SDK reference: ctx.set_state(key, value) / ctx.get_state(key)
# Session state is automatically keyed by session ID and persists across tool calls
```

**Consideration**: Session state in the SDK has a default TTL of 1 day. For long-running sessions, the stored entitlements may need refresh. This aligns with JWT expiration — when the JWT expires, the session should be re-authenticated.

### 4. Component Visibility for Tool Filtering

The SDK provides per-session component visibility controls:

```python
# SDK reference: ctx.enable_components(tags=set) / ctx.disable_components(keys=set)
```

This allows the server to dynamically show or hide tools based on user scopes. For example, if a user lacks admin scopes, admin tools can be hidden from `list_tools()` entirely.

### 5. Context Access in Tools

Tools can access the session and its stored state through the `Context` object:

```python
@mcp.tool()
async def get_firewall_rule(app_name: str, ctx: Context) -> str:
    # Access stored entitlements
    user_scopes = ctx.get_state("user_scopes")
    # Enforce authorization
    ...
```

---

## Authorization Model

### Scope Taxonomy

The pattern requires defining a mapping between userinfo-returned roles and tool-level authorization decisions. This mapping has several layers:

```
Layer 1: Global access     — Can the user use this MCP server at all?
Layer 2: Tool visibility   — Can the user see this tool in list_tools()?
Layer 3: Tool invocation   — Can the user call this tool?
Layer 4: Data filtering    — What subset of results can the user see?
```

### Layer 1: Global Access

Enforced by `AuthSettings.required_scopes`. If the JWT doesn't contain the baseline scopes, the connection is rejected at the protocol level.

**Example**: `required_scopes=["mcp:access"]` — every user must have this scope to connect.

### Layer 2: Tool Visibility

Enforced by component visibility. After resolving user scopes, the server can hide tools the user isn't entitled to use:

```python
# During session initialization or in a middleware:
if "admin" not in user_roles:
    ctx.disable_components(keys={"tool:admin_tool_1", "tool:admin_tool_2"})
```

When tools are disabled, they don't appear in `list_tools()` responses. The LLM never knows they exist.

**Consideration**: This uses the `tags` and `keys` system. Tools must be registered with appropriate tags:

```python
@mcp.tool(tags={"admin", "infrastructure"})
async def reset_firewall(ctx: Context) -> str: ...

@mcp.tool(tags={"developer", "infrastructure"})
async def get_firewall_rule(app_name: str, ctx: Context) -> str: ...
```

Then visibility can be controlled by tag:

```python
# Show only tools matching user's role tags
ctx.enable_components(tags=user_role_tags)
```

### Layer 3: Tool Invocation Authorization

Even if a tool is visible, the server should verify authorization at invocation time (defense in depth). A tool could be visible because the user has the right role, but invocation might require additional checks (e.g., rate limits, specific conditions).

**Pattern**: A decorator or wrapper that checks scopes before executing the tool function.

**Consideration**: The MCP SDK does not provide a built-in per-tool authorization hook. This must be implemented as:
- An authorization check at the beginning of each tool function
- A decorator that wraps tool functions with scope checks
- A custom `ToolManager` subclass that intercepts `call_tool` (more invasive)

### Layer 4: Data-Level Filtering

This is the most nuanced layer and the one highlighted by the firewall rule example. The user can call `get_firewall_rule`, but the results must be filtered to only include applications the user has developer entitlements for.

**Example flow:**

1. User has developer entitlements for: `["app-alpha", "app-beta"]`
2. User calls: `get_firewall_rule(app_name="app-gamma")`
3. Server checks: `"app-gamma"` not in user's entitled applications
4. Server returns: authorization error or empty result

**Alternative**: If the tool returns multiple rules, filter the result set:

1. User calls: `list_firewall_rules()`
2. Server fetches all rules from downstream
3. Server filters to only rules for `["app-alpha", "app-beta"]`
4. Server returns filtered results

---

## Entitlement Resolution

### Userinfo API Response Mapping

The userinfo API will return user claims in a provider-specific format. The MCP server must map these claims to an internal authorization model.

**Example userinfo response:**

```json
{
  "sub": "user-123",
  "name": "Jane Developer",
  "roles": ["developer", "viewer"],
  "entitlements": {
    "applications": {
      "app-alpha": ["read", "write"],
      "app-beta": ["read"]
    },
    "infrastructure": {
      "firewalls": ["read"],
      "networks": []
    }
  }
}
```

**Mapping to internal model:**

```python
# Conceptual structure
@dataclass
class UserEntitlements:
    user_id: str
    global_roles: set[str]            # {"developer", "viewer"}
    app_permissions: dict[str, set[str]]  # {"app-alpha": {"read", "write"}}
    infra_permissions: dict[str, set[str]]  # {"firewalls": {"read"}}
```

**Consideration**: The mapping logic is specific to the organization's identity provider and authorization model. The MCP server must define:

1. How to parse the userinfo response
2. How to normalize different claim formats
3. How to handle missing or unexpected claims
4. How to handle hierarchical roles (e.g., "admin" implies "developer")

### Where to Perform Entitlement Resolution

| Timing | Mechanism | Trade-off |
|---|---|---|
| **At connection time** | In `TokenVerifier.verify_token()` or in an `on_initialize` hook | Single API call; entitlements cached for session duration. Won't reflect real-time revocations. |
| **At each tool call** | Tool function calls userinfo API before executing | Fresh entitlements every time; higher latency; many API calls. |
| **Hybrid** | Resolve at connection time; re-verify on a timer or when the token is close to expiration | Balanced approach. |

**Recommendation**: Resolve at connection time and store in session state. The JWT's `exp` claim provides a natural staleness boundary. If the JWT expires mid-session, the session should be re-authenticated.

---

## Design Considerations

### 1. JWT Relay vs. MCP OAuth Flow

The pattern describes a **token relay** model where App1's JWT is forwarded through the agent runtime to the MCP server. This differs from the standard MCP OAuth flow:

**Standard MCP OAuth flow:**
- MCP client discovers the authorization server via Protected Resource Metadata
- MCP client performs OAuth 2.1 Authorization Code + PKCE flow directly
- Token is issued specifically for the MCP server (audience-restricted)
- Token is obtained through user interaction (consent screen)

**Token relay (this pattern):**
- App1 has already authenticated the user
- JWT is issued by App1's identity provider
- Agent runtime passes the JWT to the MCP client
- MCP client sends it as a Bearer token
- MCP server verifies it against a userinfo API (not the standard MCP auth discovery flow)

**Question**: Should the MCP server implement the full MCP OAuth spec (Protected Resource Metadata, etc.) even though the token comes from App1? Or should it use a simpler Bearer token verification that bypasses the MCP auth discovery flow?

**Consideration**: The MCP spec states that authorization is optional, and HTTP transports "conform to OAuth 2.1." The `TokenVerifier` approach allows custom verification logic without implementing the full OAuth discovery flow, which may be more appropriate for this relay pattern.

### 2. Userinfo Endpoint vs. JWT Introspection vs. Local Validation

Three approaches for verifying the JWT and extracting claims:

| Approach | Description | When to Use |
|---|---|---|
| **Userinfo endpoint** (OIDC) | Call `/userinfo` with the access token; receive user claims | When the token is opaque or when you need authoritative claims from the IdP |
| **Token introspection** (RFC 7662) | Call `/introspect` with the token; receive active/inactive + claims | When you need to check token revocation status |
| **Local JWT validation** | Decode and verify the JWT signature locally using the IdP's public keys (JWKS) | When the JWT is self-contained (contains all needed claims) and you trust the signing keys |

**Trade-offs:**

- Userinfo/introspection requires a network call on every connection (or session), adding latency and a dependency on the IdP
- Local validation is faster but doesn't detect token revocation
- If the JWT contains all required claims (roles, entitlements), local validation + periodic JWKS refresh is most performant
- If entitlements are stored in an external authorization service (not in the JWT), a userinfo call is necessary

**Python libraries for JWT handling:**
- `PyJWT` — Decode and verify JWTs locally
- `python-jose` — JOSE implementation (JWT, JWS, JWE)
- `authlib` — Full OAuth/OIDC client and resource server support

### 3. Scope-to-Tool Mapping Schema

The relationship between scopes/roles and tools needs a formal definition. Options:

**Option A: Inline per-tool declarations**

Each tool declares what scopes it requires:

```python
# Conceptual -- not SDK syntax
@mcp.tool(required_scopes=["infrastructure:read"])
async def get_firewall_rule(app_name: str, ctx: Context) -> str: ...
```

The SDK does not natively support `required_scopes` on the `@mcp.tool()` decorator. This would need to be implemented as a custom decorator or convention.

**Option B: Centralized policy mapping**

A configuration file or dictionary maps tool names to required scopes:

```python
# Conceptual
TOOL_SCOPES = {
    "get_firewall_rule": {
        "required_roles": ["developer"],
        "data_filter": "filter_by_app_entitlements",
    },
    "reset_firewall": {
        "required_roles": ["admin"],
        "data_filter": None,  # admin sees everything
    },
}
```

**Option C: Attribute-based access control (ABAC)**

More flexible than role-based. Authorization decisions consider attributes of the user, the resource, and the environment:

```python
# Conceptual
def authorize(user: UserEntitlements, tool: str, args: dict) -> bool:
    if tool == "get_firewall_rule":
        return args["app_name"] in user.app_permissions
    ...
```

**Question**: Should the scope-to-tool mapping be static (defined at server startup) or dynamic (fetched from an external policy engine like OPA, Cedar, or Casbin)?

### 4. Defense in Depth

Multiple authorization layers should be enforced simultaneously:

1. **Transport layer**: TLS for all connections
2. **Token verification**: JWT signature validation + expiration check
3. **Audience validation**: Token was issued for this MCP server
4. **Global scope check**: `required_scopes` in `AuthSettings`
5. **Tool visibility**: Hide unauthorized tools via component visibility
6. **Tool-level authorization**: Check scopes at invocation time
7. **Data-level filtering**: Filter results within the tool

**Principle**: Never rely on a single layer. Tool visibility hiding is a usability enhancement, not a security boundary. A malicious client could still attempt to call a hidden tool by name. The invocation-time check is the security boundary.

### 5. Error Handling for Authorization Failures

When a user attempts an unauthorized action, the server should:

| Scenario | Response |
|---|---|
| Invalid/expired JWT | Reject connection; return 401 |
| Missing required global scopes | Reject connection; return 403 with `insufficient_scope` |
| Call to hidden tool | Return tool-not-found error (don't reveal the tool exists) |
| Call to visible tool without required scope | Return a clear error: "Insufficient permissions to execute {tool}" |
| Tool call with unauthorized data parameter | Return: "You do not have entitlements for application {app_name}" |

**Consideration**: The MCP spec supports a scope step-up flow where the server returns 403 with `insufficient_scope`, and the client can re-authenticate with expanded scopes. However, in the token relay model, scope step-up requires coordination with App1 (which issued the original JWT), so this may not be feasible without additional protocol support.

### 6. Stateless HTTP Mode Implications

With `stateless_http=True`, the server doesn't maintain session state across requests. Every request must be independently authenticated and authorized:

- Token verification occurs on every request (not just at connection time)
- Session state (`ctx.set_state()`) may not persist between requests in truly stateless mode
- Entitlements must either be encoded in the JWT or re-fetched on every request

**Question**: Is stateless HTTP mode compatible with this pattern? The overhead of calling the userinfo API on every tool call may be prohibitive. Alternatives:
- Encode all entitlements in the JWT claims (avoids userinfo call)
- Use a short-lived cache (e.g., TTL cache keyed by token hash) for userinfo results
- Use stateful HTTP mode (default) which maintains sessions

### 7. Agent Runtime as MCP Client

The agent runtime sits between App1 and the MCP server. It must:

1. Accept the JWT from App1
2. Create an `httpx.AsyncClient` or MCP `ClientSession` with the JWT in the `Authorization` header
3. Initialize the MCP session
4. Execute tool calls as directed by the LLM agent
5. Not modify, forge, or extend the JWT

**Consideration**: The MCP Python SDK's `OAuthClientProvider` handles the full OAuth flow (discovery, PKCE, etc.). For the token relay pattern, a simpler approach is needed — passing a pre-obtained Bearer token in the connection headers. This can be done with:

- Custom headers on the `streamablehttp_client`:
  ```python
  # The streamablehttp_client accepts headers parameter
  async with streamablehttp_client(url, headers={"Authorization": f"Bearer {jwt}"}) as (r, w):
      async with ClientSession(r, w) as session:
          await session.initialize()
  ```

- Or by implementing a minimal `httpx.Auth` subclass that injects the Bearer token.

### 8. Multi-Tenancy Considerations

If the MCP server serves multiple organizations or tenants:

- The JWT's `iss` (issuer) claim identifies the tenant's identity provider
- The `aud` (audience) claim must match the MCP server's expected audience
- Entitlement structures may differ across tenants
- The userinfo endpoint may differ per tenant

**Consideration**: The `TokenVerifier` implementation must handle multi-tenant scenarios by dispatching to the correct JWKS endpoint or userinfo endpoint based on the JWT's issuer.

---

## Scope Enforcement Patterns

### Pattern A: Decorator-Based Authorization

A custom decorator wraps tool functions to enforce scope checks before execution:

```python
# Conceptual pattern (not implementation)
def requires_scope(*scopes):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = find_context_in_args(kwargs)
            user_scopes = ctx.get_state("user_scopes")
            if not all(s in user_scopes for s in scopes):
                raise PermissionError(f"Missing required scopes: {scopes}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator
```

**Trade-off**: Simple and explicit, but every tool must be decorated. If the decorator interacts with the `@mcp.tool()` decorator, ordering matters.

### Pattern B: Centralized Authorization Middleware

A single authorization check intercepts all tool calls:

**Consideration**: The MCP Python SDK does not expose a tool-level middleware hook. The low-level `Server` API has `@server.call_tool()` which is a single handler for all tool calls, providing a natural interception point. With `FastMCP`, one would need to subclass or wrap the tool manager.

### Pattern C: Data-Level Filtering in Tool Logic

For the firewall rule example, the tool itself contains the filtering logic:

```python
# Conceptual
@mcp.tool()
async def get_firewall_rule(app_name: str, ctx: Context) -> str:
    user_entitlements = ctx.get_state("user_entitlements")
    entitled_apps = user_entitlements.app_permissions.keys()

    if app_name not in entitled_apps:
        return f"Access denied: you do not have entitlements for {app_name}"

    # Proceed with fetching the rule from downstream
    rule = await fetch_firewall_rule(app_name)
    return format_rule(rule)
```

**Trade-off**: Authorization logic is co-located with business logic. This is natural for data-level filtering but can become repetitive across many tools.

### Pattern D: Policy Engine Integration

Delegate authorization decisions to an external policy engine:

| Engine | Language | Integration |
|---|---|---|
| **OPA (Open Policy Agent)** | Rego | HTTP API or Python library (`opa-python-client`) |
| **Cedar (AWS)** | Cedar | Python bindings (`cedarpy`) |
| **Casbin** | PERM model | Python library (`casbin`) |

**Consideration**: An external policy engine decouples authorization logic from the MCP server code. Policies can be updated without redeploying the server. However, it adds another network dependency and operational complexity.

---

## SDK Version Considerations

| Feature | SDK Version | Notes |
|---|---|---|
| `TokenVerifier` protocol | v1.x+ | Server-side token verification |
| `AuthSettings` | v1.x+ | Global scope requirements |
| `ctx.set_state()` / `ctx.get_state()` | v2.x | Session-scoped state storage |
| Component visibility (`enable/disable_components`) | v2.x | Per-session tool filtering by tags/keys |
| Tool `tags` parameter | v2.x | Tag-based tool categorization |
| Streamable HTTP transport | v1.8+ | Required for Bearer token relay |
| `AccessToken` dataclass | v1.x+ | Token representation with scopes |

---

## Open Questions

1. **Token format**: Is the JWT from App1 an access token or an ID token? If it's an ID token, the MCP server should not use it for API authorization (per OIDC best practices). The agent runtime may need to exchange it for an access token first.

2. **Scope granularity**: How granular should scopes be? Options range from coarse ("read", "write") to fine-grained ("firewall:read:app-alpha"). Fine-grained scopes reduce server-side filtering logic but increase token size and management complexity.

3. **Entitlement propagation**: If the MCP server also calls a downstream API (as in Pattern 1), should it forward the user's JWT to the downstream service (token relay) or use its own service-to-service credentials (token exchange)? This affects whether the downstream service enforces its own authorization.

4. **Revocation handling**: How should the MCP server handle JWT revocation mid-session? Options:
   - Ignore (rely on short token lifetimes)
   - Periodic re-validation against the introspection endpoint
   - Webhook-based revocation notifications

5. **Audit logging**: Should tool invocations be logged with the user identity and authorization decision for compliance? The MCP SDK supports logging via `ctx.info()` / `ctx.warning()`, but a formal audit trail may require integration with an external logging system.

6. **Scope refresh**: If the user's roles change during a long-running session (e.g., an admin grants new entitlements), how does the MCP server learn about this? Options:
   - Session refresh (disconnect and reconnect)
   - Periodic userinfo re-fetch
   - Push-based notification from the IdP

7. **Testing authorization**: How should authorization logic be tested? Considerations:
   - Mock userinfo API responses
   - Test JWTs with various scope combinations
   - Integration tests with a real IdP (e.g., Keycloak in a container)

8. **Interaction with Pattern 1**: If Pattern 1 (Resource Caching) is combined with Pattern 2, should the cached data be pre-filtered per user's entitlements? Or should the full dataset be cached server-wide and filtered at tool execution time?

---

## Related Python Libraries

| Library | Role |
|---|---|
| `mcp` (PyPI) | MCP Python SDK — server framework, auth provider, session management |
| `PyJWT` | Local JWT decoding and signature verification |
| `python-jose` | JOSE (JWT/JWS/JWE) implementation |
| `authlib` | Full OAuth 2.0/OIDC client and resource server |
| `httpx` | Async HTTP client for userinfo API calls |
| `pydantic` | Data validation for entitlement models |
| `casbin` | RBAC/ABAC policy engine (if using external policy) |
| `cedarpy` | Cedar policy language bindings |

---

## Summary

The Dynamic Scopes pattern centers on:

1. **Token relay** from a trusted upstream app through the agent runtime to the MCP server
2. **Zero-trust verification** using the SDK's `TokenVerifier` to validate the JWT against a userinfo API
3. **Entitlement resolution** at connection time, mapping user claims to an internal authorization model
4. **Multi-layer authorization** enforced at the tool visibility, invocation, and data-filtering levels
5. **Session-scoped state** for storing resolved entitlements across tool calls
6. **Defense in depth** — tool hiding is a UX feature, not a security boundary; invocation-time checks are mandatory
7. **Scope-to-tool mapping** via decorators, centralized policy, or external policy engines
