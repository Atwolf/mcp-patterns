from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP

from server.cache import build_cache, refresh_loop
from server.models import CachedData


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    base_url = os.environ.get("DOWNSTREAM_API_URL", "")
    ttl = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

    if not base_url:
        # No downstream API configured — start with an empty cache for demo purposes
        initial_cache = CachedData(ttl_seconds=ttl)
        yield {"cache_holder": [initial_cache], "http_client": None}
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Eager fetch — fail-fast if downstream is unreachable
        initial_cache = await build_cache(client, base_url, ttl)

        # Mutable holder for copy-on-write swap
        cache_holder: list[CachedData] = [initial_cache]

        # Spawn background refresh task
        refresh_task = asyncio.create_task(
            refresh_loop(cache_holder, client, base_url, ttl)
        )

        try:
            yield {"cache_holder": cache_holder, "http_client": client}
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
