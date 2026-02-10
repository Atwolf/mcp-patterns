from __future__ import annotations

from fastmcp import Context, FastMCP

from server.models import CachedData


def register_resources(mcp: FastMCP) -> None:

    @mcp.resource("cache://entities/summary")
    async def cache_summary(ctx: Context = Context) -> str:  # type: ignore[assignment]
        """Summary of the entity cache: counts, categories, freshness."""
        cache_holder = ctx.lifespan_context["cache_holder"]
        cache: CachedData = cache_holder[0]

        categories = sorted({e.category for e in cache.entities.values()})
        return (
            f"Total entities: {len(cache.entities)}\n"
            f"Categories: {', '.join(categories) if categories else '(none)'}\n"
            f"Last refreshed: {cache.last_refreshed_at.isoformat()}\n"
            f"TTL: {cache.ttl_seconds}s\n"
            f"Stale: {cache.is_stale}"
        )

    @mcp.resource("cache://entities/health")
    async def cache_health(ctx: Context = Context) -> str:  # type: ignore[assignment]
        """Simple health check for the entity cache."""
        cache_holder = ctx.lifespan_context["cache_holder"]
        cache: CachedData = cache_holder[0]

        status = "healthy" if not cache.is_stale else "stale"
        return (
            f"status: {status}\n"
            f"last_refresh: {cache.last_refreshed_at.isoformat()}"
        )
