# backend/core/tasks.py
"""Tracked fire-and-forget background tasks (CQ-03).

A bare ``asyncio.create_task(...)`` whose result is not stored can be
garbage-collected before (or while) it runs — the event loop holds only a
weak reference to tasks. The test suite demonstrated this with
"Task was destroyed but it is pending!" warnings from the FCM send path.

``fire_and_forget`` keeps a strong reference in a module-level registry until
the task completes, and logs (never raises) any exception, so background work
(push notifications, image moderation) reliably runs without ever breaking
the request that scheduled it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(
    coro: Coroutine[Any, Any, Any], *, name: str | None = None
) -> asyncio.Task[Any]:
    """Schedule ``coro`` as a background task that cannot be GC'd mid-flight."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(finished: asyncio.Task[Any]) -> None:
        _background_tasks.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.warning(
                "Background task %r failed: %s", finished.get_name(), exc, exc_info=exc
            )

    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    """Number of in-flight background tasks (introspection for tests)."""
    return len(_background_tasks)
