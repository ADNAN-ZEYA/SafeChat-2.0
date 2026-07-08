# backend/services/posts.py
"""Post service layer.

Posts are stored at /posts/{post_id} (uuid4). Counter field author.post_count
is incremented/decremented atomically via Firestore batch writes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter

from core.firebase import db
from core.tasks import fire_and_forget
from models.moderation import Match
from models.post import Post
from moderation.engine import moderate_image, moderate_text
from services import follows as follows_service
from services import moderation_queue
from services import users as users_service

logger = logging.getLogger(__name__)

POSTS_COLLECTION = "posts"


class PostBlocked(Exception):
    """Raised when post text is flagged and the author has not opted into human
    verification. Carries the match spans so the route can return them for
    client-side highlighting in the "this can't be uploaded" popup.
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
        super().__init__(reason or "Post blocked by content moderation.")


class PostNotFound(Exception):
    """Raised when a requested post document does not exist."""


class NotAuthorized(Exception):
    """Raised when the requesting user may not perform the action."""


async def _moderate_post_image(post_id: str, image_url: str) -> None:
    """Background task: run Vision SafeSearch on post image_url.

    If the image is blocked the post status is updated to "rejected".
    Errors are logged and swallowed so the task never crashes the event loop.
    """
    try:
        result = await moderate_image(image_url)
        if result.blocked:
            await asyncio.to_thread(
                _post_ref(post_id).update,
                {"status": "rejected", "updated_at": firestore.SERVER_TIMESTAMP},
            )
            logger.info(
                "Post %s rejected by image moderation (category=%s)",
                post_id,
                result.category,
            )
    except Exception as exc:
        logger.warning("Image moderation background task failed for post %s: %s", post_id, exc)


def _post_ref(post_id: str) -> DocumentReference:
    return db.collection(POSTS_COLLECTION).document(post_id)


def _user_ref(uid: str) -> DocumentReference:
    return db.collection("users").document(uid)


async def create_post(
    author_uid: str,
    text: str,
    media_urls: list[str] | None = None,
    media_type: str = "text",
    submit_for_review: bool = False,
) -> Post:
    """Moderate then persist a new post.

    - Clean text -> status "approved"; appears in the feed; post_count += 1.
    - Flagged + submit_for_review=False -> raises ``PostBlocked`` (the route
      returns a 422 carrying the flagged spans so the client can highlight them
      and offer "submit for human verification").
    - Flagged + submit_for_review=True -> status "pending_review"; a
      moderation_queue record is written in the same batch; hidden from the feed
      until an admin approves. post_count is NOT incremented until approval.

    Raises:
        PostBlocked: flagged text when the author has not opted into review.
    """
    result = await moderate_text(text)

    if result.blocked and not submit_for_review:
        raise PostBlocked(
            layer=result.layer,
            reason=result.reason,
            matches=result.matches,
            categories=[m.category for m in result.matches],
        )

    is_pending = result.blocked  # submit_for_review is necessarily True here
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
        logger.info("Post saved as pending_review (layer=%s) for human verification.", result.layer)

    post_id = str(uuid.uuid4())
    now = firestore.SERVER_TIMESTAMP
    post_data: dict[str, Any] = {
        "id": post_id,
        "author_uid": author_uid,
        "author_username": author_username,
        "author_display_name": user.display_name if user else "Anonymous",
        "author_photo_url": user.photo_url if user and user.photo_url else "",
        "text": text,
        "image_url": media_urls[0] if media_urls else None,
        "media_urls": media_urls or [],
        "media_type": media_type,
        "status": initial_status,
        **moderation_meta,
        "like_count": 0,
        "comment_count": 0,
        "view_count": 0,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }

    queue_item: tuple[str, dict[str, Any]] | None = None
    if is_pending:
        queue_item = moderation_queue.build_item(
            content_type="post",
            content_id=post_id,
            author_uid=author_uid,
            author_username=author_username,
            text=text,
            result=result,
        )

    def _write() -> None:
        batch = db.batch()
        batch.set(_post_ref(post_id), post_data)
        # Counters reflect approved content only; pending posts count on approve.
        if initial_status == "approved":
            batch.update(_user_ref(author_uid), {"post_count": firestore.Increment(1)})
        if queue_item is not None:
            qid, qpayload = queue_item
            batch.set(db.collection(moderation_queue.QUEUE_COLLECTION).document(qid), qpayload)
        batch.commit()

    await asyncio.to_thread(_write)

    # Refetch so the returned model carries resolved server timestamps.
    snap = await asyncio.to_thread(_post_ref(post_id).get)
    post = Post.model_validate(snap.to_dict())

    # Fire-and-forget image moderation. Runs after the post is already stored;
    # quietly rejects the post if the Vision check trips. Tracked via
    # core.tasks so the task cannot be garbage-collected mid-flight (CQ-03).
    if media_urls and media_type == "image":
        for url in media_urls:
            fire_and_forget(
                _moderate_post_image(post.id, url),
                name=f"moderate-post-image:{post.id}",
            )

    return post


async def get_post(post_id: str) -> Post | None:
    """Fetch a single post by ID. Returns None if it doesn't exist."""
    snap = await asyncio.to_thread(_post_ref(post_id).get)
    if not snap.exists:
        return None
    return Post.model_validate(snap.to_dict())


async def delete_post(
    post_id: str,
    requesting_uid: str,
    is_admin: bool = False,
) -> None:
    """Delete a post. Only the author or an admin may delete.

    Atomically decrements author.post_count on success.

    Raises:
        PostNotFound: if the post does not exist.
        NotAuthorized: if requesting_uid is neither the author nor an admin.
    """
    snap = await asyncio.to_thread(_post_ref(post_id).get)
    if not snap.exists:
        raise PostNotFound(post_id)

    data = snap.to_dict() or {}
    author_uid = str(data.get("author_uid", ""))

    if requesting_uid != author_uid and not is_admin:
        raise NotAuthorized(requesting_uid)

    def _delete() -> None:
        batch = db.batch()
        batch.delete(_post_ref(post_id))
        batch.update(_user_ref(author_uid), {"post_count": firestore.Increment(-1)})
        batch.commit()

    await asyncio.to_thread(_delete)


async def get_feed(
    viewer_uid: str,
    feed_type: str = "following",
    limit: int = 20,
    before_created_at: str | None = None,
) -> list[Post]:
    """Pull feed: approved posts based on feed_type ('global' or 'following'), newest first.

    Uses cursor pagination via `before_created_at` (ISO-format datetime string).
    Limit is capped at 20. Firestore `in` queries are chunked at 30 values.
    """
    following = await follows_service.get_following(viewer_uid)
    # Always include the user's own posts in their feed
    following.append(viewer_uid)

    cap = min(limit, 20)

    before_dt: datetime | None = None
    if before_created_at:
        try:
            before_dt = datetime.fromisoformat(before_created_at)
        except ValueError:
            before_dt = None

    def _query() -> list[Post]:
        all_posts: list[Post] = []

        if feed_type == "global":
            q = (
                db.collection(POSTS_COLLECTION)
                .where(filter=FieldFilter("status", "==", "approved"))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
            )
            if before_dt is not None:
                q = q.where(filter=FieldFilter("created_at", "<", before_dt))

            for snap in q.limit(cap).stream():
                d = snap.to_dict() or {}
                all_posts.append(Post.model_validate(d))
        else:
            chunks = [following[i : i + 30] for i in range(0, len(following), 30)]
            for chunk in chunks:
                q = (
                    db.collection(POSTS_COLLECTION)
                    .where(filter=FieldFilter("author_uid", "in", chunk))
                    .where(filter=FieldFilter("status", "==", "approved"))
                    .order_by("created_at", direction=firestore.Query.DESCENDING)
                )
                if before_dt is not None:
                    q = q.where(filter=FieldFilter("created_at", "<", before_dt))

                for snap in q.stream():
                    d = snap.to_dict() or {}
                    all_posts.append(Post.model_validate(d))

            # Merge chunk results, re-sort globally, then cap to limit.
            all_posts.sort(key=lambda p: p.created_at, reverse=True)
            all_posts = all_posts[:cap]

        return all_posts

    return await asyncio.to_thread(_query)


async def get_posts_by_author(
    author_uid: str,
    limit: int = 20,
) -> list[Post]:
    """Fetch posts by a specific user, newest first.

    Capped at 50 to prevent unbounded queries.
    """
    cap = min(limit, 50)

    def _query() -> list[Post]:
        # CQ-06: filter + sort + limit server-side via the composite index
        # (author_uid ASC, status ASC, created_at DESC) declared in
        # firestore.indexes.json — instead of fetching 200 docs and filtering
        # in Python. Reads at most `cap` documents.
        q = (
            db.collection(POSTS_COLLECTION)
            .where(filter=FieldFilter("author_uid", "==", author_uid))
            .where(filter=FieldFilter("status", "==", "approved"))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(cap)
        )
        return [Post.model_validate(snap.to_dict() or {}) for snap in q.stream()]

    return await asyncio.to_thread(_query)


async def record_post_view(post_id: str, viewer_uid: str) -> None:
    """Record a view for a post, incrementing the view_count if the user hasn't viewed it yet."""

    def _record_view() -> None:
        view_ref = _post_ref(post_id).collection("views").document(viewer_uid)

        @firestore.transactional
        def update_in_transaction(
            transaction: firestore.Transaction,
            post_ref: DocumentReference,
            view_ref: DocumentReference,
        ) -> None:
            view_snap = view_ref.get(transaction=transaction)
            if view_snap.exists:
                return  # Already viewed

            post_snap = post_ref.get(transaction=transaction)
            if not post_snap.exists:
                raise PostNotFound(post_id)

            transaction.set(view_ref, {"viewed_at": firestore.SERVER_TIMESTAMP})
            transaction.update(post_ref, {"view_count": firestore.Increment(1)})

        transaction = db.transaction()
        update_in_transaction(transaction, _post_ref(post_id), view_ref)

    await asyncio.to_thread(_record_view)


async def set_post_status(post_id: str, status: str, reason: str | None = None) -> Post:
    """Apply an admin moderation decision to a pending post.

    - "approved": post becomes public and author.post_count is incremented.
    - "rejected": post is hidden from everyone and ``rejection_reason`` is set
      (the author sees it in Profile -> Appeals).

    Raises:
        PostNotFound: if the post does not exist (e.g. deleted before review).
    """
    snap = await asyncio.to_thread(_post_ref(post_id).get)
    if not snap.exists:
        raise PostNotFound(post_id)

    data = snap.to_dict() or {}
    author_uid = str(data.get("author_uid", ""))

    updates: dict[str, Any] = {"status": status, "updated_at": firestore.SERVER_TIMESTAMP}
    if status == "rejected":
        updates["rejection_reason"] = reason

    def _write() -> None:
        batch = db.batch()
        batch.update(_post_ref(post_id), updates)
        if status == "approved" and author_uid:
            batch.update(_user_ref(author_uid), {"post_count": firestore.Increment(1)})
        batch.commit()

    await asyncio.to_thread(_write)
    snap2 = await asyncio.to_thread(_post_ref(post_id).get)
    return Post.model_validate(snap2.to_dict())
