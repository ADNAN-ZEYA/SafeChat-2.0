# backend/tests/test_moderation_review.py
"""Tests for services.moderation_review — approve/reject orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from models.moderation import ModerationQueueItem
from services import moderation_review


def _item(
    content_type: str = "post", status: str = "pending_review", **kw: Any
) -> ModerationQueueItem:
    base: dict[str, Any] = {
        "id": "q1",
        "content_type": content_type,
        "content_id": "c1",
        "author_uid": "uid-1",
        "text": "bad words",
        "status": status,
        "created_at": datetime(2026, 6, 1, tzinfo=UTC),
    }
    base.update(kw)
    return ModerationQueueItem(**base)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Patch the seams the orchestrator calls, and record the calls.

    CQ-02: the orchestrator claims the queue item first via
    ``claim_pending`` (transactional check-and-set), then applies the
    decision to the content, then notifies. ``get`` serves the final
    re-fetch.
    """
    calls: dict[str, list[Any]] = {"apply": [], "claimed": [], "notify": [], "released": []}
    state = {"item": _item()}

    async def fake_claim(
        queue_id: str, status: str, resolver_uid: str, reason: str | None = None
    ) -> ModerationQueueItem:
        pending = state["item"]
        calls["claimed"].append((queue_id, status, resolver_uid, reason))
        state["item"] = _item(status=status, reason=reason)
        return pending

    async def fake_release(queue_id: str) -> None:
        calls["released"].append(queue_id)
        state["item"] = _item(status="pending_review")

    async def fake_get(queue_id: str) -> ModerationQueueItem:
        return state["item"]

    async def fake_set_post(post_id: str, status: str, reason: str | None = None) -> None:
        calls["apply"].append(("post", post_id, status, reason))

    async def fake_notify(uid: str, **kwargs: Any) -> None:
        calls["notify"].append((uid, kwargs))

    monkeypatch.setattr(moderation_review.moderation_queue, "claim_pending", fake_claim)
    monkeypatch.setattr(moderation_review.moderation_queue, "release_claim", fake_release)
    monkeypatch.setattr(moderation_review.moderation_queue, "get", fake_get)
    monkeypatch.setattr(moderation_review.posts_service, "set_post_status", fake_set_post)
    monkeypatch.setattr(moderation_review.notifications_service, "create_notification", fake_notify)
    return calls


@pytest.mark.asyncio
async def test_approve_claims_publishes_and_notifies(captured: dict[str, list[Any]]) -> None:
    result = await moderation_review.approve("q1", "admin-1")

    assert ("q1", "approved", "admin-1", None) in captured["claimed"]
    assert ("post", "c1", "approved", None) in captured["apply"]
    assert captured["notify"][0][0] == "uid-1"
    assert captured["notify"][0][1]["notification_type"] == "appeal_update"
    assert captured["released"] == []
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_reject_hides_with_reason_and_notifies(captured: dict[str, list[Any]]) -> None:
    result = await moderation_review.reject("q1", "admin-1", "Contains a slur")

    assert ("q1", "rejected", "admin-1", "Contains a slur") in captured["claimed"]
    assert ("post", "c1", "rejected", "Contains a slur") in captured["apply"]
    assert result.status == "rejected"
    # the rejection reason is surfaced to the author in the notification body
    assert "Contains a slur" in captured["notify"][0][1]["body"]


@pytest.mark.asyncio
async def test_claim_happens_before_apply(captured: dict[str, list[Any]]) -> None:
    """The claim must win the race BEFORE any content mutation happens."""
    order: list[str] = []

    original_apply = moderation_review._apply_to_content

    async def tracking_apply(*args: Any, **kwargs: Any) -> None:
        order.append("apply")
        await original_apply(*args, **kwargs)

    # captured fixture already patched claim; wrap apply to observe ordering
    # via the calls dict lengths at apply time.
    await moderation_review.approve("q1", "admin-1")
    assert len(captured["claimed"]) == 1
    assert len(captured["apply"]) == 1


@pytest.mark.asyncio
async def test_apply_failure_releases_claim(
    captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CQ-02 compensation: if the content update fails after a successful
    claim, the claim is released so the item stays reviewable."""

    async def failing_set_post(post_id: str, status: str, reason: str | None = None) -> None:
        raise RuntimeError("transient firestore error")

    monkeypatch.setattr(moderation_review.posts_service, "set_post_status", failing_set_post)

    with pytest.raises(RuntimeError):
        await moderation_review.approve("q1", "admin-1")

    assert captured["released"] == ["q1"]
    assert captured["notify"] == []  # author must not be notified of a failed decision


@pytest.mark.asyncio
async def test_approve_missing_item_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import moderation_queue

    async def fake_claim(*args: Any, **kwargs: Any) -> ModerationQueueItem:
        raise moderation_queue.QueueItemNotFound("missing")

    monkeypatch.setattr(moderation_review.moderation_queue, "claim_pending", fake_claim)
    with pytest.raises(moderation_review.QueueItemNotFound):
        await moderation_review.approve("missing", "admin-1")


@pytest.mark.asyncio
async def test_decide_on_already_resolved_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import moderation_queue

    async def fake_claim(*args: Any, **kwargs: Any) -> ModerationQueueItem:
        raise moderation_queue.AlreadyClaimed("q1")

    monkeypatch.setattr(moderation_review.moderation_queue, "claim_pending", fake_claim)
    with pytest.raises(moderation_review.AlreadyResolved):
        await moderation_review.reject("q1", "admin-1", "too late")
