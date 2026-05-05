"""
Simple in-process TTL cache.

Used to avoid redundant yfinance network calls during a single session.
Cache is per-process (not persisted to disk) with configurable TTL.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._store: dict[str, _Entry] = {}
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry and time.time() < entry.expires_at:
            self.hits += 1
            return entry.value
        self.misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = _Entry(value=value, expires_at=time.time() + self.ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# Module-level singletons
market_data_cache = TTLCache(ttl_seconds=3600)   # yfinance: 1 hour
ticker_cache = TTLCache(ttl_seconds=86400)        # ticker resolution: 24 hours
peer_universe_cache = TTLCache(ttl_seconds=86400) # peer universe snapshots: 24 hours
