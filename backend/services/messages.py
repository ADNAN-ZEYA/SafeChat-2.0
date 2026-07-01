# backend/services/messages.py
"""Direct-messaging service layer.

Chats live at /chats/{chat_id}.
Messages live at /chats/{chat_id}/messages/{message_id}.

Chat IDs are deterministic: "{min(uid_a, uid_b)}_{max(uid_a, uid_b)}", so
there is always at most one chat document between any user-pair regardless of
which side initiates.
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
from models.message import Chat, Message
from models.moderation import Match
from moderation.engine import moderate_text
from services import follows as follows_service
from services import moderation_queue
from services.notifications import send_message_notification

logger = logging.getLogger(__name__)

CHATS_COLLECTION = "chats"
MESSAGES_SUBCOLLECTION = "messages"
USERS_COLLECTION = "users"
ENCRYPTION_AUDIT_COLLECTION = "encryption_mode_audit_logs"

_ENCRYPTED_PLACEHOLDER = "🔒 Encrypted message"


class CannotMessageSelf(Exception):
    """Raised when a user tries to start a chat with themselves."""


class NotMutualFollow(Exception):
    """Raised when a SafeChat Mode change is attempted between non-mutual followers."""


class MessageBlocked(Exception):
    """Raised when message text is flagged and the sender has not opted into
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
        super().__init__(reason or "Message blocked by content moderation.")


class NotAuthorized(Exception):
    """Raised when the requesting user is not a participant in the chat."""


class ChatNotFound(Exception):
    """Raised when the requested chat document does not exist."""


class MessageNotFound(Exception):
    """Raised when the requested message document does not exist."""


def _chat_ref(chat_id: str) -> DocumentReference:
    return db.collection(CHATS_COLLECTION).document(chat_id)


def _message_ref(chat_id: str, message_id: str) -> DocumentReference:
    return (
        db.collection(CHATS_COLLECTION)
        .document(chat_id)
        .collection(MESSAGES_SUBCOLLECTION)
        .document(message_id)
    )


async def get_or_create_chat(uid_a: str, uid_b: str) -> Chat:
    """Return the chat between uid_a and uid_b, creating it if it doesn't exist.

    The chat_id is deterministic so there is always at most one chat document
    per user-pair regardless of call order.

    Raises:
        CannotMessageSelf: if uid_a == uid_b.
    """
    if uid_a == uid_b:
        raise CannotMessageSelf("You cannot start a chat with yourself.")

    chat_id = f"{min(uid_a, uid_b)}_{max(uid_a, uid_b)}"

    snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if snap.exists:
        return Chat.model_validate(snap.to_dict())

    now = firestore.SERVER_TIMESTAMP
    chat_data: dict[str, Any] = {
        "id": chat_id,
        "participants": sorted([uid_a, uid_b]),
        "last_message_text": None,
        "last_message_at": None,
        "encryption_mode": "pending",
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }

    await asyncio.to_thread(_chat_ref(chat_id).set, chat_data)

    # Refetch to resolve SERVER_TIMESTAMP.
    snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    return Chat.model_validate(snap.to_dict())


def _write_encryption_mode_audit(payload: dict[str, Any]) -> None:
    """Sync write into Firestore. Isolated so tests can patch a single seam."""
    db.collection(ENCRYPTION_AUDIT_COLLECTION).add(payload)


async def _log_encryption_mode_change(
    *,
    chat_id: str,
    previous_mode: str,
    new_mode: str,
    reason: str,
    actor_uid: str | None,
) -> None:
    """Best-effort audit trail for SafeChat Mode transitions. Fail-open."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "previous_mode": previous_mode,
        "new_mode": new_mode,
        "reason": reason,  # "toggle" (user action) | "unfollow" (system-forced)
        "actor_uid": actor_uid,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    try:
        await asyncio.to_thread(_write_encryption_mode_audit, payload)
    except Exception:
        logger.warning(
            "Failed to write encryption-mode audit log (chat_id=%s)", chat_id, exc_info=True
        )


async def set_encryption_mode(chat_id: str, requesting_uid: str, mode: str) -> Chat:
    """Toggle a chat's SafeChat Mode (`encryption_mode`) between two states.

    Gate: only participants may change the mode, and only between mutual
    followers — non-mutual chats are always forced into "pending" (moderated).

    Raises:
        ChatNotFound: if the chat document does not exist.
        NotAuthorized: if requesting_uid is not a participant in the chat.
        NotMutualFollow: if the two participants do not mutually follow each other.
    """
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    participants = chat_data.get("participants", [])
    if requesting_uid not in participants:
        raise NotAuthorized(requesting_uid)

    other_uid = next((uid for uid in participants if uid != requesting_uid), None)
    if other_uid is None or not await follows_service.is_mutual_follow(
        requesting_uid, other_uid
    ):
        raise NotMutualFollow(chat_id)

    previous_mode = chat_data.get("encryption_mode", "pending")
    await asyncio.to_thread(
        _chat_ref(chat_id).update,
        {"encryption_mode": mode, "updated_at": firestore.SERVER_TIMESTAMP},
    )

    if previous_mode != mode:
        await _log_encryption_mode_change(
            chat_id=chat_id,
            previous_mode=previous_mode,
            new_mode=mode,
            reason="toggle",
            actor_uid=requesting_uid,
        )

    snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    return Chat.model_validate(snap.to_dict())


async def revert_chat_to_pending_if_trusted(uid_a: str, uid_b: str) -> None:
    """After an unfollow, force a "trusted" chat between uid_a/uid_b back to "pending".

    No-op if the chat doesn't exist or is already "pending". Called from the
    unfollow route — breaking mutual-follow status must immediately restore
    moderation, since SafeChat Mode is only available between mutual followers.
    """
    if uid_a == uid_b:
        return

    chat_id = f"{min(uid_a, uid_b)}_{max(uid_a, uid_b)}"
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        return

    chat_data = chat_snap.to_dict() or {}
    if chat_data.get("encryption_mode") != "trusted":
        return

    await asyncio.to_thread(
        _chat_ref(chat_id).update,
        {"encryption_mode": "pending", "updated_at": firestore.SERVER_TIMESTAMP},
    )
    logger.info("Chat %s forced trusted -> pending after unfollow.", chat_id)
    await _log_encryption_mode_change(
        chat_id=chat_id,
        previous_mode="trusted",
        new_mode="pending",
        reason="unfollow",
        actor_uid=None,
    )


async def send_message(
    chat_id: str,
    sender_uid: str,
    text: str,
    image_url: str | None = None,
    submit_for_review: bool = False,
) -> Message:
    """Moderate then persist a message.

    - Clean -> status "approved"; chat preview updated; FCM sent to recipient.
    - Flagged + submit_for_review=False -> raises ``MessageBlocked`` (route 422
      with the flagged spans).
    - Flagged + submit_for_review=True -> status "pending_review"; queued for
      review; NOT delivered (chat preview untouched, no FCM) until an admin
      approves.

    Raises:
        ChatNotFound: if the chat document does not exist.
        NotAuthorized: if sender_uid is not a participant in the chat.
        MessageBlocked: flagged text when the sender has not opted into review.
    """
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    if sender_uid not in chat_data.get("participants", []):
        raise NotAuthorized(sender_uid)

    is_trusted = chat_data.get("encryption_mode", "pending") == "trusted"

    message_id = str(uuid.uuid4())
    now = firestore.SERVER_TIMESTAMP

    if is_trusted:
        # SafeChat Mode is OFF for this chat: `text` is already client-side E2E
        # ciphertext. The moderation pipeline never sees plaintext here — skip
        # it entirely, per the SafeChat Mode spec.
        message_data: dict[str, Any] = {
            "id": message_id,
            "chat_id": chat_id,
            "sender_uid": sender_uid,
            "text": text,
            "image_url": image_url,
            "status": "approved",
            "encrypted": True,
            "read_at": None,
            "created_at": now,
            "updated_at": now,
            "schema_version": 1,
        }
        preview_text = _ENCRYPTED_PLACEHOLDER
        notification_text = _ENCRYPTED_PLACEHOLDER

        def _write() -> None:
            batch = db.batch()
            batch.set(_message_ref(chat_id, message_id), message_data)
            batch.update(
                _chat_ref(chat_id),
                {
                    "last_message_text": preview_text,
                    "last_message_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            batch.commit()

        await asyncio.to_thread(_write)
        snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
        message = Message.model_validate(snap.to_dict())

        participants = chat_data.get("participants", [])
        recipient_uid = next((uid for uid in participants if uid != sender_uid), None)
        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name: str = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            asyncio.create_task(
                send_message_notification(
                    recipient_uid, display_name, notification_text, chat_id
                )
            )

        return message

    result = await moderate_text(text)

    if result.blocked and not submit_for_review:
        raise MessageBlocked(
            layer=result.layer,
            reason=result.reason,
            matches=result.matches,
            categories=[m.category for m in result.matches],
        )

    is_pending = result.blocked
    initial_status = "pending_review" if is_pending else "approved"

    moderation_meta: dict[str, Any] = {}
    author_username = "unknown"
    if is_pending:
        moderation_meta = {
            "moderation_layer": result.layer,
            "moderation_reason": result.reason,
            "flagged_terms": list(dict.fromkeys(m.term for m in result.matches)),
        }
        sender_snap = await asyncio.to_thread(
            db.collection(USERS_COLLECTION).document(sender_uid).get
        )
        author_username = (sender_snap.to_dict() or {}).get("username") or "unknown"
        logger.info("Message saved as pending_review (layer=%s); not delivered.", result.layer)

    message_data = {
        "id": message_id,
        "chat_id": chat_id,
        "sender_uid": sender_uid,
        "text": text,
        "image_url": image_url,
        "status": initial_status,
        **moderation_meta,
        "read_at": None,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }

    queue_item: tuple[str, dict[str, Any]] | None = None
    if is_pending:
        queue_item = moderation_queue.build_item(
            content_type="message",
            content_id=message_id,
            author_uid=sender_uid,
            author_username=author_username,
            text=text,
            result=result,
            chat_id=chat_id,
        )

    def _write() -> None:
        batch = db.batch()
        batch.set(_message_ref(chat_id, message_id), message_data)
        # Only delivered (approved) messages update the chat preview.
        if initial_status == "approved":
            batch.update(
                _chat_ref(chat_id),
                {
                    "last_message_text": text,
                    "last_message_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        if queue_item is not None:
            qid, qpayload = queue_item
            batch.set(db.collection(moderation_queue.QUEUE_COLLECTION).document(qid), qpayload)
        batch.commit()

    await asyncio.to_thread(_write)

    # Refetch to resolve SERVER_TIMESTAMP.
    snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    message = Message.model_validate(snap.to_dict())

    # Push to the recipient only when the message was actually delivered.
    if initial_status == "approved":
        participants = chat_data.get("participants", [])
        recipient_uid = next((uid for uid in participants if uid != sender_uid), None)
        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name: str = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            asyncio.create_task(
                send_message_notification(recipient_uid, display_name, text, chat_id)
            )

    return message


async def get_messages(
    chat_id: str,
    requesting_uid: str,
    limit: int = 50,
    before_created_at: str | None = None,
) -> list[Message]:
    """Return messages in a chat, newest first.

    Cursor pagination via `before_created_at` (ISO-format datetime string).
    Limit is capped at 50.

    Raises:
        ChatNotFound: if the chat document does not exist.
        NotAuthorized: if requesting_uid is not a participant.
    """
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    if requesting_uid not in chat_data.get("participants", []):
        raise NotAuthorized(requesting_uid)

    cap = min(limit, 50)

    before_dt: datetime | None = None
    if before_created_at:
        try:
            before_dt = datetime.fromisoformat(before_created_at)
        except ValueError:
            before_dt = None

    def _query() -> list[Message]:
        q = (
            db.collection(CHATS_COLLECTION)
            .document(chat_id)
            .collection(MESSAGES_SUBCOLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        if before_dt is not None:
            q = q.where(filter=FieldFilter("created_at", "<", before_dt))
        q = q.limit(cap)
        # The recipient only sees approved messages; the sender additionally
        # sees their own pending_review / rejected messages (with status shown).
        results: list[Message] = []
        for snap in q.stream():
            data = snap.to_dict()
            if not data:
                continue
            message = Message.model_validate(data)
            if message.status == "approved" or message.sender_uid == requesting_uid:
                results.append(message)
        return results

    return await asyncio.to_thread(_query)


async def get_chats(uid: str) -> list[Chat]:
    """Return all chats the user participates in, ordered by last_message_at desc.

    Args:
        uid: The requesting user's UID.
    """

    def _query() -> list[Chat]:
        q = (
            db.collection(CHATS_COLLECTION)
            .where(filter=FieldFilter("participants", "array_contains", uid))
            .order_by("last_message_at", direction=firestore.Query.DESCENDING)
        )
        return [Chat.model_validate(snap.to_dict()) for snap in q.stream()]

    return await asyncio.to_thread(_query)


async def mark_read(
    chat_id: str,
    message_id: str,
    reader_uid: str,
) -> None:
    """Mark a message as read by setting its read_at to SERVER_TIMESTAMP.

    Raises:
        ChatNotFound: if the chat document does not exist.
        NotAuthorized: if reader_uid is not a participant.
        MessageNotFound: if the message document does not exist.
    """
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    if reader_uid not in chat_data.get("participants", []):
        raise NotAuthorized(reader_uid)

    msg_snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    if not msg_snap.exists:
        raise MessageNotFound(message_id)

    await asyncio.to_thread(
        _message_ref(chat_id, message_id).update,
        {"read_at": firestore.SERVER_TIMESTAMP},
    )


async def set_message_status(
    chat_id: str, message_id: str, status: str, reason: str | None = None
) -> Message:
    """Apply an admin moderation decision to a pending message.

    - "approved": message is delivered — the chat preview is updated and a push
      notification is sent to the recipient.
    - "rejected": message stays hidden and ``rejection_reason`` is recorded.

    Raises:
        MessageNotFound: if the message does not exist.
    """
    snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    if not snap.exists:
        raise MessageNotFound(message_id)

    data = snap.to_dict() or {}
    text = str(data.get("text", ""))
    sender_uid = str(data.get("sender_uid", ""))

    updates: dict[str, Any] = {"status": status, "updated_at": firestore.SERVER_TIMESTAMP}
    if status == "rejected":
        updates["rejection_reason"] = reason

    def _write() -> None:
        batch = db.batch()
        batch.update(_message_ref(chat_id, message_id), updates)
        if status == "approved":
            batch.update(
                _chat_ref(chat_id),
                {
                    "last_message_text": text,
                    "last_message_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        batch.commit()

    await asyncio.to_thread(_write)

    if status == "approved":
        chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
        participants = (chat_snap.to_dict() or {}).get("participants", [])
        recipient_uid = next((uid for uid in participants if uid != sender_uid), None)
        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            asyncio.create_task(
                send_message_notification(recipient_uid, display_name, text, chat_id)
            )

    snap2 = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    return Message.model_validate(snap2.to_dict())
