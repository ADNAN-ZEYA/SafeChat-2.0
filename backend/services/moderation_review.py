# backend/services/moderation_review.py
"""Admin moderation decisions — approve or reject pending content.

Each decision:
  1. dispatches to the owning content service to publish (approve) or hide
     (reject) the underlying post / comment / message,
  2. marks the moderation_queue record resolved, and
  3. notifies the author (in-app notification).

Used by the admin review portal (routes/admin.py).
"""

from __future__ import annotations

import logging

from models.moderation import ModerationQueueItem
from services import comments as comments_service
from services import messages as messages_service
from services import moderation_queue
from services import notifications as notifications_service
from services import posts as posts_service
from services import stories as stories_service

logger = logging.getLogger(__name__)


class QueueItemNotFound(Exception):
    """Raised when the queue item to act on does not exist."""


class AlreadyResolved(Exception):
    """Raised when the queue item has already been approved or rejected."""


async def _apply_to_content(item: ModerationQueueItem, status: str, reason: str | None) -> None:
    """Publish (approve) or hide (reject) the underlying content document.

    If the content was deleted before review, the *NotFound error is swallowed
    so the queue record can still be resolved cleanly.
    """
    try:
        if item.content_type == "post":
            await posts_service.set_post_status(item.content_id, status, reason)
        elif item.content_type == "comment" and item.post_id:
            await comments_service.set_comment_status(item.post_id, item.content_id, status, reason)
        elif item.content_type == "message" and item.chat_id:
            await messages_service.set_message_status(item.chat_id, item.content_id, status, reason)
        elif item.content_type == "story":
            await stories_service.set_story_status(item.content_id, status, reason)
    except (
        posts_service.PostNotFound,
        comments_service.CommentNotFound,
        messages_service.MessageNotFound,
        stories_service.StoryNotFound,
    ):
        logger.warning(
            "Content %s/%s gone before review resolved; queue updated anyway.",
            item.content_type,
            item.content_id,
        )


async def _notify_author(item: ModerationQueueItem, status: str, reason: str | None) -> None:
    label = {
        "post": "post",
        "comment": "comment",
        "message": "message",
        "story": "story",
    }.get(item.content_type, "content")
    if status == "approved":
        title = "Your content was approved"
        body = f"Your {label} passed human review and is now visible."
    else:
        title = "Your content was rejected"
        body = f"Your {label} was reviewed and won't be published."
        if reason:
            body += f" Reason: {reason}"

    await notifications_service.create_notification(
        item.author_uid,
        notification_type="appeal_update",
        title=title,
        body=body,
        reference_id=item.content_id,
        target_route="/profile/content-status",
    )


async def _decide(
    queue_id: str, admin_uid: str, status: str, reason: str | None
) -> ModerationQueueItem:
    # CQ-02: claim the queue item FIRST, inside a transaction, so two admins
    # deciding the same item can never both apply the decision (which would
    # e.g. double-increment post_count on approve). Exactly one claim wins.
    try:
        item = await moderation_queue.claim_pending(queue_id, status, admin_uid, reason)
    except moderation_queue.QueueItemNotFound as exc:
        raise QueueItemNotFound(queue_id) from exc
    except moderation_queue.AlreadyClaimed as exc:
        raise AlreadyResolved(queue_id) from exc

    try:
        await _apply_to_content(item, status, reason)
    except Exception:
        # The claim succeeded but the content update failed (transient
        # Firestore error — *NotFound is swallowed inside _apply_to_content).
        # Release the claim so the item stays reviewable, then surface the
        # original error to the admin.
        try:
            await moderation_queue.release_claim(queue_id)
        except Exception:  # pragma: no cover — best-effort compensation
            logger.exception("Failed to release claim on queue item %s", queue_id)
        raise

    await _notify_author(item, status, reason)

    resolved = await moderation_queue.get(queue_id)
    return resolved if resolved is not None else item


async def approve(queue_id: str, admin_uid: str) -> ModerationQueueItem:
    """Approve pending content: publish it, resolve the queue, notify author."""
    return await _decide(queue_id, admin_uid, "approved", None)


async def reject(queue_id: str, admin_uid: str, reason: str | None = None) -> ModerationQueueItem:
    """Reject pending content: hide it, resolve the queue, notify author."""
    return await _decide(queue_id, admin_uid, "rejected", reason)
