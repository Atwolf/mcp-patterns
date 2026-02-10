from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel


# --- Pydantic models (external boundaries) ---


class EntityData(BaseModel):
    id: str
    name: str
    category: str
    metadata: dict[str, str] = {}


class UserInfo(BaseModel):
    sub: str
    name: str
    email: str
    roles: list[str] = []
    entitlements: dict[str, list[str]] = {}


# --- Dataclasses (internal state) ---


@dataclass
class CachedData:
    entities: dict[str, EntityData] = field(default_factory=dict)
    last_refreshed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = 300

    @property
    def is_stale(self) -> bool:
        elapsed = (datetime.now(timezone.utc) - self.last_refreshed_at).total_seconds()
        return elapsed > self.ttl_seconds


@dataclass
class UserEntitlements:
    user_id: str
    global_roles: set[str] = field(default_factory=set)
    permitted_categories: set[str] = field(default_factory=set)
