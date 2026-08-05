# backend/services/comments.py
"""Comment service layer.

Comments are stored at /posts/{post_id}/comments/{comment_id} (subcollection).
post.comment_count is kept in sync via batch writes alongside the comment document.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter

from core.firebase import db
from models.comment import Comment
from models.moderation import Match
from moderation.engine import moderate_text
from services import moderation_queue
from services import users as users_service

POSTS_COLLECTION = "posts"
COMMENTS_SUBCOLLECTION = "comments"


class CommentBlocked(Exception):
    """Raised when comment text is flagged and the author has not opted into
    human verification. Carries match spans for client-side highlighting.
    """

    def __init__(
        self,
        layer: str | None = None,
        reason: str | None = None,
        matches: list[Match] | None = None,
        categories: list[str] | None = None,
    ) -> None:
        self.layer = layer
        self.reason = reason
        self.matches = matches or []
        self.categories = categories or []
        super().__init__(reason or "Comment blocked by content moderation.")


class CommentNotFound(Exception):
    """Raised when a requested comment document does not exist."""


class PostNotFound(Exception):
    """Raised when the parent post does not exist."""


class NotAuthorized(Exception):
    """Raised when the requesting user may not perform the action."""


def _post_ref(post_id: str) -> DocumentReference:
    return db.collection(POSTS_COLLECTION).document(post_id)


def _comment_ref(post_id: str, comment_id: str) -> DocumentReference:
    return (
        db.collection(POSTS_COLLECTION)
        .document(post_id)
        .collection(COMMENTS_SUBCOLLECTION)
        .document(comment_id)
    )


async def create_comment(
    post_id: str,
    author_uid: str,
    text: str,
    parent_comment_id: str | None = None,
    submit_for_review: bool = False,
) -> Comment:
    """Moderate then persist a new comment.

    - Clean -> status "approved"; visible on the post; comment_count += 1.
    - Flagged + submit_for_review=False -> raises ``CommentBlocked`` (route
      returns 422 with the flagged spans).
    - Flagged + submit_for_review=True -> status "pending_review"; a
      moderation_queue record is written; hidden from the thread and not counted
      until an admin approves.
    """
    post_snap = await asyncio.to_thread(_post_ref(post_id).get)
    if not post_snap.exists:
        raise PostNotFound(post_id)

    result = await moderate_text(text)

    if result.blocked and not submit_for_review:
        raise CommentBlocked(
            layer=result.layer,
            reason=result.reason,
            matches=result.matches,
            categories=[m.category for m in result.matches],
        )

    is_pending = result.blocked
    initial_status = "pending_review" if is_pending else "approved"

    user = await users_service.get_user_profile(author_uid)
    author_username = user.username if user else "unknown"

    moderation_meta: dict[str, Any] = {}
    if is_pending:
        moderation_meta = {
            "moderation_layer": result.layer,
            "moderation_reason": result.reason,
            "flagged_terms": list(dict.fromkeys(m.term for m in result.matches)),
        }

    comment_id = str(uuid.uuid4())
    now = firestore.SERVER_TIMESTAMP
    comment_data: dict[str, Any] = {
        "id": comment_id,
        "post_id": post_id,
        "author_uid": author_uid,
        "author_display_name": user.display_name if user else "Anonymous",
        "author_photo_url": user.photo_url if user and user.photo_url else "",
        "author_username": author_username,
        "text": text,
        "parent_comment_id": parent_comment_id,
        "status": initial_status,
        **moderation_meta,
        "like_count": 0,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }

    queue_item: tuple[str, dict[str, Any]] | None = None
    if is_pending:
        queue_item = moderation_queue.build_item(
            content_type="comment",
            content_id=comment_id,
            author_uid=author_uid,
            author_username=author_username,
            text=text,
            result=result,
            post_id=post_id,
        )

    def _write() -> None:
        batch = db.batch()
        batch.set(_comment_ref(post_id, comment_id), comment_data)
        # comment_count reflects approved comments only.
        if initial_status == "approved":
            batch.update(_post_ref(post_id), {"comment_count": firestore.Increment(1)})
        if queue_item is not None:
            qid, qpayload = queue_item
            batch.set(db.collection(moderation_queue.QUEUE_COLLECTION).document(qid), qpayload)
        batch.commit()

    await asyncio.to_thread(_write)

    # Refetch so the returned model carries resolved server timestamps.
    snap = await asyncio.to_thread(_comment_ref(post_id, comment_id).get)
    return Comment.model_validate(snap.to_dict())


async def get_comments(
    post_id: str,
    limit: int = 20,
    before_created_at: str | None = None,
) -> list[Comment]:
    """Fetch comments for a post, ordered by created_at ascending (oldest first).

    Cursor pagination via `before_created_at` (ISO-format datetime string).
    Limit is capped at 50.
    """
    cap = min(limit, 50)

    before_dt: datetime | None = None
    if before_created_at:
        try:
            before_dt = datetime.fromisoformat(before_created_at)
        except ValueError:
            before_dt = None

    def _query() -> list[Comment]:
        q = (
            db.collection(POSTS_COLLECTION)
            .document(post_id)
            .collection(COMMENTS_SUBCOLLECTION)
            .where(filter=FieldFilter("status", "==", "approved"))
            .order_by("created_at", direction=firestore.Query.ASCENDING)
        )
        if before_dt is not None:
            q = q.where(filter=FieldFilter("created_at", "<", before_dt))
        q = q.limit(cap)

        # Only approved comments are public; pending_review / rejected are
        # hidden from the thread (the author sees them in Profile -> Appeals).
        results: list[Comment] = []
        for snap in q.stream():
            data = snap.to_dict()
            if not data:
                continue
            comment = Comment.model_validate(data)
            if comment.status == "approved":
                results.append(comment)
        return results

    return await asyncio.to_thread(_query)


async def delete_comment(
    post_id: str,
    comment_id: str,
    requesting_uid: str,
    is_admin: bool = False,
) -> None:
    """Delete a comment. Only the author or an admin may delete.

    Atomically decrements post.comment_count (floor at 0) via batch write.

    Raises:
        CommentNotFound: if the comment does not exist.
        NotAuthorized: if requesting_uid is neither the author nor an admin.
    """
    snap = await asyncio.to_thread(_comment_ref(post_id, comment_id).get)
    if not snap.exists:
        raise CommentNotFound(comment_id)

    data = snap.to_dict() or {}
    author_uid = str(data.get("author_uid", ""))

    if requesting_uid != author_uid and not is_admin:
        raise NotAuthorized(requesting_uid)

    def _delete() -> None:
        batch = db.batch()
        batch.delete(_comment_ref(post_id, comment_id))
        batch.update(_post_ref(post_id), {"comment_count": firestore.Increment(-1)})
        batch.commit()

    await asyncio.to_thread(_delete)


async def get_comment(post_id: str, comment_id: str) -> Comment | None:
    """Fetch a single comment by ID."""
    snap = await asyncio.to_thread(_comment_ref(post_id, comment_id).get)
    if not snap.exists:
        return None
    return Comment.model_validate(snap.to_dict())


async def like_comment(uid: str, post_id: str, comment_id: str) -> None:
    """Like a comment. Idempotent."""
    likes_ref = _comment_ref(post_id, comment_id).collection("likes").document(uid)

    def _like() -> None:
        if likes_ref.get().exists:
            return
        batch = db.batch()
        batch.set(likes_ref, {"uid": uid, "created_at": firestore.SERVER_TIMESTAMP})
        batch.update(_comment_ref(post_id, comment_id), {"like_count": firestore.Increment(1)})
        batch.commit()

    await asyncio.to_thread(_like)


async def unlike_comment(uid: str, post_id: str, comment_id: str) -> None:
    """Unlike a comment. Idempotent."""
    likes_ref = _comment_ref(post_id, comment_id).collection("likes").document(uid)

    def _unlike() -> None:
        if not likes_ref.get().exists:
            return
        batch = db.batch()
        batch.delete(likes_ref)
        batch.update(_comment_ref(post_id, comment_id), {"like_count": firestore.Increment(-1)})
        batch.commit()

    await asyncio.to_thread(_unlike)


async def is_comment_liked(uid: str, post_id: str, comment_id: str) -> bool:
    """Check if a user liked a comment."""
    snap = await asyncio.to_thread(
        _comment_ref(post_id, comment_id).collection("likes").document(uid).get
    )
    return snap.exists


async def set_comment_status(
    post_id: str, comment_id: str, status: str, reason: str | None = None
) -> Comment:
    """Apply an admin moderation decision to a pending comment.

    - "approved": comment becomes visible and post.comment_count is incremented.
    - "rejected": comment stays hidden and ``rejection_reason`` is recorded.

    Raises:
        CommentNotFound: if the comment does not exist.
    """
    snap = await asyncio.to_thread(_comment_ref(post_id, comment_id).get)
    if not snap.exists:
        raise CommentNotFound(comment_id)

    updates: dict[str, Any] = {"status": status, "updated_at": firestore.SERVER_TIMESTAMP}
    if status == "rejected":
        updates["rejection_reason"] = reason

    def _write() -> None:
        batch = db.batch()
        batch.update(_comment_ref(post_id, comment_id), updates)
        if status == "approved":
            batch.update(_post_ref(post_id), {"comment_count": firestore.Increment(1)})
        batch.commit()

    await asyncio.to_thread(_write)
    snap2 = await asyncio.to_thread(_comment_ref(post_id, comment_id).get)
    return Comment.model_validate(snap2.to_dict())
