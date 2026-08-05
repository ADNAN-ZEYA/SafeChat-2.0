# backend/core/cache.py
"""Simple in-memory TTL cache utility."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TTLCache:
    """Thread-safe in-memory key-value cache with time-to-live expiration."""

    def __init__(self, default_ttl_seconds: float = 60.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Get cached value if present and not expired."""
        if key not in self._store:
            return None
        expires_at, value = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """Set cached value with an optional TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.time() + ttl
        self._store[key] = (expires_at, value)

    def invalidate(self, key: str) -> None:
        """Remove a key from the cache if it exists."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all keys in cache."""
        self._store.clear()
