# backend/services/moderation_queue.py
"""Moderation review queue — the single home for content under human review.

When a user opts to *submit flagged content for human verification*, the
content document (post/comment/message) is stored with ``status =
pending_review`` AND a lightweight record is written to ``moderation_queue``.

  * Admins read this collection to review pending content (see routes/admin.py).
  * The author reads their own records in Profile -> Appeals.
  * Approve/reject updates BOTH this record and the underlying content document.

All writes go through the backend (Admin SDK); clients never write here.
``build_item`` is pure so callers can persist the queue record inside the same
Firestore batch that stores the content — keeping the two consistent.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter

from core.firebase import db
from models.moderation import ModerationQueueItem, ModerationResult, QueueStatus

QUEUE_COLLECTION = "moderation_queue"


class QueueItemNotFound(Exception):
    """Raised when a queue document does not exist."""


def queue_ref(queue_id: str) -> DocumentReference:
    return db.collection(QUEUE_COLLECTION).document(queue_id)


def build_item(
    *,
    content_type: str,
    content_id: str,
    author_uid: str,
    author_username: str,
    text: str,
    result: ModerationResult,
    post_id: str | None = None,
    chat_id: str | None = None,
    note: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build ``(queue_id, payload)`` for a new pending_review queue record.

    Pure — no I/O. The caller writes ``payload`` to ``queue_ref(queue_id)``,
    typically inside the same batch that stores the content document.
    """
    queue_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "id": queue_id,
        "content_type": content_type,
        "content_id": content_id,
        "post_id": post_id,
        "chat_id": chat_id,
        "author_uid": author_uid,
        "author_username": author_username,
        "text": text,
        "matches": [m.model_dump() for m in result.matches],
        "flagged_terms": list(dict.fromkeys(m.term for m in result.matches)),
        "categories": list(dict.fromkeys(m.category for m in result.matches)),
        "lexicon_score": result.lexicon_score,
        "tfidf_score": result.tfidf_score,
        "layer": result.layer,
        "status": "pending_review",
        "reason": None,
        "note": note,
        "created_at": firestore.SERVER_TIMESTAMP,
        "resolved_at": None,
        "resolved_by": None,
        "schema_version": 1,
    }
    return queue_id, payload


async def list_pending(limit: int = 50) -> list[ModerationQueueItem]:
    """Return pending_review items, oldest first (FIFO for fair review)."""
    cap = min(max(1, limit), 100)

    def _query() -> list[ModerationQueueItem]:
        q = (
            db.collection(QUEUE_COLLECTION)
            .where(filter=FieldFilter("status", "==", "pending_review"))
            .order_by("created_at", direction=firestore.Query.ASCENDING)
            .limit(cap)
        )
        return [ModerationQueueItem.model_validate(s.to_dict()) for s in q.stream() if s.to_dict()]

    return await asyncio.to_thread(_query)


async def list_for_user(uid: str, limit: int = 50) -> list[ModerationQueueItem]:
    """Return the user's own queue records, newest first (Profile -> Appeals)."""
    cap = min(max(1, limit), 100)

    def _query() -> list[ModerationQueueItem]:
        q = (
            db.collection(QUEUE_COLLECTION)
            .where(filter=FieldFilter("author_uid", "==", uid))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(cap)
        )
        return [ModerationQueueItem.model_validate(s.to_dict()) for s in q.stream() if s.to_dict()]

    return await asyncio.to_thread(_query)


async def get(queue_id: str) -> ModerationQueueItem | None:
    """Fetch a single queue item, or None if it doesn't exist."""
    snap = await asyncio.to_thread(queue_ref(queue_id).get)
    if not snap.exists:
        return None
    return ModerationQueueItem.model_validate(snap.to_dict())


async def mark_resolved(
    queue_id: str, status: QueueStatus, resolver_uid: str, reason: str | None = None
) -> None:
    """Set the queue record's terminal status. Caller updates the content doc."""
    updates: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "resolved_at": firestore.SERVER_TIMESTAMP,
        "resolved_by": resolver_uid,
    }
    await asyncio.to_thread(queue_ref(queue_id).update, updates)


class AlreadyClaimed(Exception):
    """Raised when a reviewer tries to claim a queue item that is no longer
    pending_review — i.e. another admin resolved it first (CQ-02)."""


async def claim_pending(
    queue_id: str, status: QueueStatus, resolver_uid: str, reason: str | None = None
) -> ModerationQueueItem:
    """Atomically transition a queue item pending_review -> terminal status.

    Runs check-and-set inside a Firestore transaction so two admins acting on
    the same item can never both win: exactly one claim succeeds, the other
    raises AlreadyClaimed.

    Returns the item as it was BEFORE the claim (the caller needs its
    content_type/content_id to apply the decision to the content doc).

    Raises:
        QueueItemNotFound: if the queue document does not exist.
        AlreadyClaimed: if the item is not in pending_review status.
    """
    ref = queue_ref(queue_id)

    def _txn_claim() -> ModerationQueueItem:
        @firestore.transactional
        def _claim(transaction: firestore.Transaction) -> ModerationQueueItem:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                raise QueueItemNotFound(queue_id)
            item = ModerationQueueItem.model_validate(snap.to_dict())
            if item.status != "pending_review":
                raise AlreadyClaimed(queue_id)
            transaction.update(
                ref,
                {
                    "status": status,
                    "reason": reason,
                    "resolved_at": firestore.SERVER_TIMESTAMP,
                    "resolved_by": resolver_uid,
                },
            )
            return item

        return _claim(db.transaction())

    return await asyncio.to_thread(_txn_claim)


async def release_claim(queue_id: str) -> None:
    """Return a claimed item to pending_review (compensation for a failed
    content update after a successful claim — see moderation_review._decide).
    """
    updates: dict[str, Any] = {
        "status": "pending_review",
        "reason": None,
        "resolved_at": None,
        "resolved_by": None,
    }
    await asyncio.to_thread(queue_ref(queue_id).update, updates)
