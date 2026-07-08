# backend/services/stories.py
"""Story service layer.

Stories are stored in the flat "stories" collection with a 24-hour TTL
enforced via the expires_at field. Views are stored at
/stories/{story_id}/views/{viewer_uid} (subcollection) and view_count is
kept in sync via batch writes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter

from core.firebase import db
from models.moderation import Match
from models.story import Story
from moderation.engine import moderate_text
from services import follows as follows_service
from services import moderation_queue
from services import users as users_service

STORIES_COLLECTION = "stories"
VIEWS_SUBCOLLECTION = "views"
STORY_TTL_HOURS = 24


class StoryBlocked(Exception):
    """Raised when story text is flagged and the author has not opted into
    human verification. Carries match spans for client-side highlighting
    (API-04 — same contract as PostBlocked / MessageBlocked).
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
        super().__init__(reason or "Story blocked by content moderation.")


class StoryNotFound(Exception):
    """Raised when a requested story document does not exist."""


class NotAuthorized(Exception):
    """Raised when the requesting user may not perform the action."""


def _story_ref(story_id: str) -> DocumentReference:
    return db.collection(STORIES_COLLECTION).document(story_id)


def _view_ref(story_id: str, viewer_uid: str) -> DocumentReference:
    return (
        db.collection(STORIES_COLLECTION)
        .document(story_id)
        .collection(VIEWS_SUBCOLLECTION)
        .document(viewer_uid)
    )


async def create_story(
    author_uid: str,
    image_url: str,
    text: str | None = None,
    submit_for_review: bool = False,
) -> Story:
    """Create a new story. Text (if present) is run through content moderation.

    expires_at is set to exactly STORY_TTL_HOURS after created_at.

    API-04 — same human-verification contract as posts/comments/messages:
    - Clean -> status "approved", visible in followers' story feeds.
    - Flagged + submit_for_review=False -> raises ``StoryBlocked`` (route
      returns 422 MODERATION_FLAGGED with the match spans).
    - Flagged + submit_for_review=True -> status "pending_review" plus a
      moderation_queue record in the same batch; hidden from feeds until an
      admin approves.

    Raises:
        StoryBlocked: flagged text when the author has not opted into review.
    """
    result = None
    if text:
        result = await moderate_text(text)
        if result.blocked and not submit_for_review:
            raise StoryBlocked(
                layer=result.layer,
                reason=result.reason,
                matches=result.matches,
                categories=[m.category for m in result.matches],
            )

    is_pending = bool(result and result.blocked)
    initial_status = "pending_review" if is_pending else "approved"

    story_id = str(uuid.uuid4())
    now_dt = datetime.now(timezone.utc)
    story_data: dict[str, Any] = {
        "id": story_id,
        "author_uid": author_uid,
        "image_url": image_url,
        "text": text,
        "status": initial_status,
        "view_count": 0,
        "created_at": now_dt,
        "expires_at": now_dt + timedelta(hours=STORY_TTL_HOURS),
        "schema_version": 1,
    }

    queue_item: tuple[str, dict[str, Any]] | None = None
    if is_pending and result is not None and text is not None:
        user = await users_service.get_user_profile(author_uid)
        queue_item = moderation_queue.build_item(
            content_type="story",
            content_id=story_id,
            author_uid=author_uid,
            author_username=user.username if user else "unknown",
            text=text,
            result=result,
        )

    def _write() -> None:
        batch = db.batch()
        batch.set(db.collection(STORIES_COLLECTION).document(story_id), story_data)
        if queue_item is not None:
            qid, qpayload = queue_item
            batch.set(db.collection(moderation_queue.QUEUE_COLLECTION).document(qid), qpayload)
        batch.commit()

    await asyncio.to_thread(_write)
    return Story.model_validate(story_data)


async def set_story_status(story_id: str, status: str, reason: str | None = None) -> Story:
    """Apply an admin moderation decision to a pending story (API-04).

    - "approved": the story becomes visible in followers' feeds (subject to
      its unchanged 24h expiry — a story approved after expiry simply never
      surfaces).
    - "rejected": stays hidden; ``rejection_reason`` is recorded for the
      author's Appeals view.

    Raises:
        StoryNotFound: if the story does not exist.
    """
    snap = await asyncio.to_thread(_story_ref(story_id).get)
    if not snap.exists:
        raise StoryNotFound(story_id)

    updates: dict[str, Any] = {"status": status}
    if status == "rejected":
        updates["rejection_reason"] = reason

    await asyncio.to_thread(_story_ref(story_id).update, updates)
    snap2 = await asyncio.to_thread(_story_ref(story_id).get)
    return Story.model_validate(snap2.to_dict())


async def get_story(story_id: str) -> Story | None:
    """Fetch a story by ID. Returns None if it does not exist or has expired."""
    snap = await asyncio.to_thread(_story_ref(story_id).get)
    if not snap.exists:
        return None
    story = Story.model_validate(snap.to_dict())
    if story.expires_at <= datetime.now(timezone.utc):
        return None  # Treat expired stories as not found.
    return story


async def get_active_stories(uid: str) -> list[Story]:
    """Return active (non-expired) stories for a specific user, newest first."""

    def _query() -> list[Story]:
        now = datetime.now(timezone.utc)
        q = (
            db.collection(STORIES_COLLECTION)
            .where(filter=FieldFilter("author_uid", "==", uid))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        stories: list[Story] = []
        for snap in q.stream():
            d = snap.to_dict() or {}
            if not d:
                continue
            story = Story.model_validate(d)
            if story.expires_at > now:
                stories.append(story)
        return stories

    return await asyncio.to_thread(_query)


async def get_feed_stories(viewer_uid: str) -> list[Story]:
    """Return active stories from users the viewer follows.

    Returns at most one story per followed user (the most recent).
    Stories are ordered by created_at descending across all followed users.
    Uses the same 30-item `in` chunking pattern as the posts feed.
    """
    following = await follows_service.get_following(viewer_uid)
    if not following:
        return []

    def _query() -> list[Story]:
        now = datetime.now(timezone.utc)
        chunks = [following[i : i + 30] for i in range(0, len(following), 30)]
        all_active: list[Story] = []

        for chunk in chunks:
            q = (
                db.collection(STORIES_COLLECTION)
                .where(filter=FieldFilter("author_uid", "in", chunk))
                .where(filter=FieldFilter("status", "==", "approved"))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
            )
            for snap in q.stream():
                d = snap.to_dict() or {}
                if not d:
                    continue
                story = Story.model_validate(d)
                if story.expires_at > now:
                    all_active.append(story)

        # Re-sort globally then take the most recent story per author.
        all_active.sort(key=lambda s: s.created_at, reverse=True)
        seen: set[str] = set()
        feed: list[Story] = []
        for story in all_active:
            if story.author_uid not in seen:
                seen.add(story.author_uid)
                feed.append(story)

        return feed

    return await asyncio.to_thread(_query)


async def delete_story(
    story_id: str,
    requesting_uid: str,
    is_admin: bool = False,
) -> None:
    """Delete a story. Only the author or an admin may delete.

    Raises:
        StoryNotFound: if the story does not exist.
        NotAuthorized: if requesting_uid is neither the author nor an admin.
    """
    snap = await asyncio.to_thread(_story_ref(story_id).get)
    if not snap.exists:
        raise StoryNotFound(story_id)

    data = snap.to_dict() or {}
    author_uid = str(data.get("author_uid", ""))

    if requesting_uid != author_uid and not is_admin:
        raise NotAuthorized(requesting_uid)

    await asyncio.to_thread(_story_ref(story_id).delete)


async def record_view(story_id: str, viewer_uid: str) -> None:
    """Record a story view. Idempotent — no-op if already viewed.

    Atomically creates the view document and increments story.view_count.
    """

    def _write() -> None:
        view_ref = _view_ref(story_id, viewer_uid)
        if view_ref.get().exists:
            return  # Already viewed — preserve original created_at.
        batch = db.batch()
        batch.set(
            view_ref,
            {
                "viewer_uid": viewer_uid,
                "story_id": story_id,
                "created_at": firestore.SERVER_TIMESTAMP,
            },
        )
        batch.update(_story_ref(story_id), {"view_count": firestore.Increment(1)})
        batch.commit()

    await asyncio.to_thread(_write)
