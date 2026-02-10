from __future__ import annotations

import os

from fastmcp import Context, FastMCP

from server.auth import extract_bearer_token, get_entitlements, requires_role
from server.cache import build_cache
from server.models import CachedData


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(tags={"reader", "entities"})
    @requires_role("reader", "admin")
    async def list_entities(
        category: str | None = None,
        ctx: Context = Context,  # type: ignore[assignment]
    ) -> str:
        """List cached entities, filtered by the caller's entitlements and optional category."""
        cache_holder = ctx.lifespan_context["cache_holder"]
        cache: CachedData = cache_holder[0]

        token = await extract_bearer_token(ctx)
        entitlements = await get_entitlements(token)

        # Layer 4: data-level filtering by permitted categories
        entities = [
            e
            for e in cache.entities.values()
            if e.category in entitlements.permitted_categories
            and (category is None or e.category == category)
        ]

        if not entities:
            return "No entities found matching your entitlements and filter."

        lines = [f"- {e.name} (id={e.id}, category={e.category})" for e in entities]
        result = "\n".join(lines)
        if cache.is_stale:
            result += "\n\n[Warning: cached data may be stale]"
        return result

    @mcp.tool(tags={"reader", "entities"})
    @requires_role("reader", "admin")
    async def get_entity(
        entity_id: str,
        ctx: Context = Context,  # type: ignore[assignment]
    ) -> str:
        """Retrieve a single entity by ID, subject to entitlement checks."""
        cache_holder = ctx.lifespan_context["cache_holder"]
        cache: CachedData = cache_holder[0]

        entity = cache.entities.get(entity_id)
        if entity is None:
            return f"Entity '{entity_id}' not found."

        token = await extract_bearer_token(ctx)
        entitlements = await get_entitlements(token)

        # Layer 4: check entitlements for this entity's category
        if entity.category not in entitlements.permitted_categories:
            return (
                f"Access denied: you do not have entitlements "
                f"for category '{entity.category}'."
            )

        return (
            f"Name: {entity.name}\n"
            f"ID: {entity.id}\n"
            f"Category: {entity.category}\n"
            f"Metadata: {entity.metadata}"
        )

    @mcp.tool(tags={"admin", "entities"})
    @requires_role("admin")
    async def refresh_cache(
        ctx: Context = Context,  # type: ignore[assignment]
    ) -> str:
        """Force a cache refresh (admin only)."""
        cache_holder = ctx.lifespan_context["cache_holder"]
        http_client = ctx.lifespan_context["http_client"]

        if http_client is None:
            return "No downstream API configured — cache refresh unavailable."

        base_url = os.environ.get("DOWNSTREAM_API_URL", "")
        ttl = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

        new_cache = await build_cache(http_client, base_url, ttl)
        cache_holder[0] = new_cache
        return f"Cache refreshed. {len(new_cache.entities)} entities loaded."
