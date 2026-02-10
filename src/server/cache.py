from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from server.models import CachedData, EntityData

logger = logging.getLogger(__name__)


async def fetch_all_entities(
    client: httpx.AsyncClient, base_url: str
) -> dict[str, EntityData]:
    response = await client.get(f"{base_url}/entities")
    response.raise_for_status()
    raw_entities = response.json()
    return {
        entity["id"]: EntityData.model_validate(entity)
        for entity in raw_entities
    }


async def build_cache(
    client: httpx.AsyncClient, base_url: str, ttl: int
) -> CachedData:
    entities = await fetch_all_entities(client, base_url)
    return CachedData(
        entities=entities,
        last_refreshed_at=datetime.now(timezone.utc),
        ttl_seconds=ttl,
    )


async def refresh_loop(
    cache_holder: list[CachedData],
    client: httpx.AsyncClient,
    base_url: str,
    ttl: int,
) -> None:
    while True:
        await asyncio.sleep(ttl)
        try:
            new_cache = await build_cache(client, base_url, ttl)
            # Copy-on-write: atomic reference swap
            cache_holder[0] = new_cache
            logger.info("Cache refreshed at %s", new_cache.last_refreshed_at.isoformat())
        except Exception:
            logger.exception("Cache refresh failed; serving stale data")
