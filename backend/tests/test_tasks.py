# backend/tests/test_tasks.py
"""Tests for core/tasks.py — tracked fire-and-forget helper (CQ-03)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from core import tasks


@pytest.mark.asyncio
async def test_fire_and_forget_runs_the_coroutine() -> None:
    ran = asyncio.Event()

    async def work() -> None:
        ran.set()

    tasks.fire_and_forget(work(), name="test-work")
    await asyncio.wait_for(ran.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_registry_holds_then_releases_reference() -> None:
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    before = tasks.pending_count()
    task = tasks.fire_and_forget(waits(), name="held")
    assert tasks.pending_count() == before + 1

    gate.set()
    await task
    # done-callback runs on the loop; yield once so it fires.
    await asyncio.sleep(0)
    assert tasks.pending_count() == before


@pytest.mark.asyncio
async def test_exception_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> None:
        raise ValueError("intentional test failure")

    with caplog.at_level(logging.WARNING, logger="core.tasks"):
        task = tasks.fire_and_forget(boom(), name="boom-task")
        # Await completion without propagating; the helper's done-callback logs.
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert any("boom-task" in rec.message for rec in caplog.records)
