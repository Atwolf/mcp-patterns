from __future__ import annotations

import functools
import hashlib
import logging
import os
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import httpx
from fastmcp import Context

from server.models import UserEntitlements, UserInfo

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

# Server-side entitlement cache keyed by token hash.
# This avoids per-session issues when the MCP client creates stateless sessions.
_entitlement_cache: dict[str, UserEntitlements] = {}


async def verify_token_and_resolve_entitlements(token: str) -> UserEntitlements:
    userinfo_url = os.environ.get(
        "OAUTH_GENERIC_USER_INFO_URL", "https://oauth.com/userinfo"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()

    user_info = UserInfo.model_validate(response.json())

    return UserEntitlements(
        user_id=user_info.sub,
        global_roles=set(user_info.roles),
        permitted_categories=set(user_info.entitlements.get("categories", [])),
    )


async def get_entitlements(token: str) -> UserEntitlements:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if token_hash in _entitlement_cache:
        return _entitlement_cache[token_hash]

    entitlements = await verify_token_and_resolve_entitlements(token)
    _entitlement_cache[token_hash] = entitlements
    return entitlements


async def extract_bearer_token(ctx: Context) -> str:
    # Try to get the token from the HTTP request headers.
    # FastMCP's get_http_request() provides access to the underlying Starlette request.
    try:
        request = ctx.get_http_request()
        if request is not None:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                return auth_header[7:]
    except Exception:
        pass

    raise PermissionError("No bearer token found in request")


def requires_role(*roles: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator enforcing role-based authorization at tool invocation time (Layer 3).

    Tool visibility (Layer 2) is a UX feature, not a security boundary.
    This decorator IS the security boundary.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Find the Context argument
            ctx: Context | None = None
            for arg in args:
                if isinstance(arg, Context):
                    ctx = arg
                    break
            if ctx is None:
                for v in kwargs.values():
                    if isinstance(v, Context):
                        ctx = v
                        break

            if ctx is None:
                raise PermissionError("No context available for authorization check")

            token = await extract_bearer_token(ctx)
            entitlements = await get_entitlements(token)

            if not entitlements.global_roles.intersection(roles):
                raise PermissionError(
                    f"Insufficient permissions. Required one of: {roles}, "
                    f"user has: {entitlements.global_roles}"
                )
            return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
