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
import base64
import binascii
import logging
import uuid
from datetime import datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import DocumentReference, FieldFilter

from core.firebase import db
from core.tasks import fire_and_forget
from models.message import Chat, Message
from models.moderation import Match
from moderation.engine import moderate_image, moderate_text
from services import follows as follows_service
from services import moderation_queue
from services.notifications import send_message_notification

logger = logging.getLogger(__name__)

CHATS_COLLECTION = "chats"
MESSAGES_SUBCOLLECTION = "messages"
USERS_COLLECTION = "users"
ENCRYPTION_AUDIT_COLLECTION = "encryption_mode_audit_logs"
# Composite-ID collections owned by blocks.py / follows.py. Read here through
# this module's `db` (rather than through those services) so the relationship
# guard hits the same Firestore client the tests fake out.
BLOCKS_COLLECTION = "blocks"
FOLLOWS_COLLECTION = "follows"

_ENCRYPTED_PLACEHOLDER = "🔒 Encrypted message"

# Minimum decoded size of a valid E2E payload: 12-byte AES-GCM nonce +
# 16-byte MAC tag (an empty plaintext still carries both). See
# frontend/lib/features/chat/data/encryption_service.dart.
_MIN_CIPHERTEXT_BYTES = 28


class CannotMessageSelf(Exception):
    """Raised when a user tries to start a chat with themselves."""


class NotMutualFollow(Exception):
    """Raised when a SafeChat Mode change is attempted between non-mutual followers."""


class MessagingNotAllowed(Exception):
    """Raised when a block (either direction) or the recipient's DM privacy
    setting (`allow_messages_from`) forbids messaging between the two users.
    The message is deliberately generic — it must not reveal to a blocked
    user that they were blocked.
    """


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


class SafeChatProtectionBlocked(Exception):
    """Raised when a recipient has SafeChat Protection enabled and an incoming
    message contains inappropriate or harmful content.
    """

    def __init__(
        self,
        layer: str | None = None,
        reason: str | None = None,
        matches: list[Match] | None = None,
        categories: list[str] | None = None,
    ) -> None:
        self.layer = layer
        self.reason = reason or "This user has SafeChat Protection enabled. Inappropriate messages are not allowed to be sent to this user."
        self.matches = matches or []
        self.categories = categories or []
        super().__init__(self.reason)


class InvalidCiphertext(Exception):
    """Raised when a message sent to a "trusted" (E2E) chat is not structurally
    valid ciphertext. Guards the `encrypted: true` invariant: a client-side
    race (or a hostile client) must never get plaintext stored as encrypted —
    and must never skip moderation for human-readable text.
    """


class NotAuthorized(Exception):
    """Raised when the requesting user is not a participant in the chat."""


class ChatNotFound(Exception):
    """Raised when the requested chat document does not exist."""


class MessageNotFound(Exception):
    """Raised when the requested message document does not exist."""


def _validate_ciphertext(text: str) -> None:
    """Require `text` to look like the client's E2E payload (base64 of
    nonce + ciphertext + MAC). Raises InvalidCiphertext otherwise.
    """
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCiphertext(
            "This chat is end-to-end encrypted; the message body must be ciphertext."
        ) from exc
    if len(raw) < _MIN_CIPHERTEXT_BYTES:
        raise InvalidCiphertext(
            "This chat is end-to-end encrypted; the message body must be ciphertext."
        )


_CANNOT_MESSAGE = "You can't message this user."


async def _ensure_can_message(sender_uid: str, recipient_uid: str) -> None:
    """Relationship guard for DMs (SEC-05).

    Refuses when either user has blocked the other, or when the recipient's
    `allow_messages_from` setting excludes the sender:
      - "everyone" (default, incl. missing profile): allowed
      - "followers": sender must be a follower of the recipient
      - "none":      nobody may message them

    Raises:
        MessagingNotAllowed: with a generic message for every refusal reason.
    """

    def _check() -> None:
        # Blocks are composite-ID docs: {blocker_uid}_{blocked_uid}.
        sender_blocked_recipient = (
            db.collection(BLOCKS_COLLECTION)
            .document(f"{sender_uid}_{recipient_uid}")
            .get()
        )
        recipient_blocked_sender = (
            db.collection(BLOCKS_COLLECTION)
            .document(f"{recipient_uid}_{sender_uid}")
            .get()
        )
        if sender_blocked_recipient.exists or recipient_blocked_sender.exists:
            raise MessagingNotAllowed(_CANNOT_MESSAGE)

        recipient_snap = db.collection(USERS_COLLECTION).document(recipient_uid).get()
        allow = "everyone"
        if recipient_snap.exists:
            allow = (recipient_snap.to_dict() or {}).get("allow_messages_from") or "everyone"

        if allow == "none":
            raise MessagingNotAllowed(_CANNOT_MESSAGE)
        if allow == "followers":
            # Follows are composite-ID docs: {follower_uid}_{followee_uid}.
            follow_snap = (
                db.collection(FOLLOWS_COLLECTION)
                .document(f"{sender_uid}_{recipient_uid}")
                .get()
            )
            if not follow_snap.exists:
                raise MessagingNotAllowed(_CANNOT_MESSAGE)

    await asyncio.to_thread(_check)


async def _moderate_message_image(chat_id: str, message_id: str, image_url: str) -> None:
    """Background task (SEC-08): Vision SafeSearch on a DM image.

    Mirrors services/posts.py::_moderate_post_image — runs after the message
    is stored, flips it to "rejected" if the image trips SafeSearch. Only
    scheduled on the moderated ("pending" encryption_mode) path; trusted
    chats intentionally bypass all moderation per the SafeChat Mode spec.

    Known accepted edge: a delivered message rejected here does not decrement
    the recipient's unread counter (badge may briefly overcount by one).
    """
    try:
        result = await moderate_image(image_url)
        if result.blocked:
            await asyncio.to_thread(
                _message_ref(chat_id, message_id).update,
                {
                    "status": "rejected",
                    "rejection_reason": "Image failed automated safety checks.",
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            logger.info(
                "Message %s in chat %s rejected by image moderation (category=%s)",
                message_id,
                chat_id,
                result.category,
            )
    except Exception as exc:  # never crash the event loop from a bg task
        logger.warning(
            "Image moderation background task failed for message %s: %s", message_id, exc
        )


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
        MessagingNotAllowed: if a block or the recipient's DM privacy setting
            forbids messaging (SEC-05) — applies to existing chats too, since
            "block" means no interaction at all.
    """
    if uid_a == uid_b:
        raise CannotMessageSelf("You cannot start a chat with yourself.")

    await _ensure_can_message(uid_a, uid_b)

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
        "unread_counts": {uid_a: 0, uid_b: 0},
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
    media_type: str = "text",
    metadata: dict[str, Any] | None = None,
    submit_for_review: bool = False,
) -> Message:
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    if sender_uid not in chat_data.get("participants", []):
        raise NotAuthorized(sender_uid)

    recipient_for_guard = next(
        (uid for uid in chat_data.get("participants", []) if uid != sender_uid), None
    )
    if recipient_for_guard is not None:
        await _ensure_can_message(sender_uid, recipient_for_guard)

        # Check if recipient has SafeChat Protection enabled
        recipient_snap = await asyncio.to_thread(
            db.collection(USERS_COLLECTION).document(recipient_for_guard).get
        )
        if recipient_snap.exists:
            rec_dict = recipient_snap.to_dict() or {}
            safechat_protection = rec_dict.get("safechat_protection", True)
            if safechat_protection and media_type in ("text", "link"):
                text_res = await moderate_text(text)
                if text_res.blocked:
                    raise SafeChatProtectionBlocked(
                        layer=text_res.layer,
                        reason="This user has SafeChat Protection enabled. Inappropriate messages are not allowed to be sent to this user.",
                        matches=text_res.matches,
                        categories=[m.category for m in text_res.matches],
                    )

    is_trusted = chat_data.get("encryption_mode", "pending") == "trusted"

    message_id = str(uuid.uuid4())
    now = firestore.SERVER_TIMESTAMP

    if is_trusted:
        # SafeChat Mode is OFF for this chat: `text` is already client-side E2E
        # ciphertext. The moderation pipeline never sees plaintext here — skip
        # it entirely, per the SafeChat Mode spec.
        #
        # SM-01 guard: reject anything that is not structurally ciphertext, so
        # a client that raced the mode toggle (or a hostile client) can never
        # store readable plaintext stamped `encrypted: true` with moderation
        # skipped.
        _validate_ciphertext(text)
        message_data: dict[str, Any] = {
            "id": message_id,
            "chat_id": chat_id,
            "sender_uid": sender_uid,
            "text": text,
            "image_url": image_url,
            "media_type": media_type,
            "metadata": metadata or {},
            "status": "approved",
            "encrypted": True,
            "read_at": None,
            "created_at": now,
            "updated_at": now,
            "schema_version": 1,
        }
        preview_text = _ENCRYPTED_PLACEHOLDER
        notification_text = _ENCRYPTED_PLACEHOLDER
        recipient_uid = recipient_for_guard

        def _write() -> None:
            batch = db.batch()
            batch.set(_message_ref(chat_id, message_id), message_data)
            chat_updates: dict[str, Any] = {
                "last_message_text": preview_text,
                "last_message_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if recipient_uid:
                chat_updates[f"unread_counts.{recipient_uid}"] = firestore.Increment(1)
            batch.update(_chat_ref(chat_id), chat_updates)
            batch.commit()

        await asyncio.to_thread(_write)
        snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
        message = Message.model_validate(snap.to_dict())

        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name: str = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            fire_and_forget(
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
        "media_type": media_type,
        "metadata": metadata or {},
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
        # Only delivered (approved) messages update the chat preview and the
        # recipient's unread counter.
        if initial_status == "approved":
            chat_updates: dict[str, Any] = {
                "last_message_text": text,
                "last_message_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if recipient_for_guard:
                chat_updates[f"unread_counts.{recipient_for_guard}"] = firestore.Increment(1)
            batch.update(_chat_ref(chat_id), chat_updates)
        if queue_item is not None:
            qid, qpayload = queue_item
            batch.set(db.collection(moderation_queue.QUEUE_COLLECTION).document(qid), qpayload)
        batch.commit()

    await asyncio.to_thread(_write)

    # Refetch to resolve SERVER_TIMESTAMP.
    snap = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    message = Message.model_validate(snap.to_dict())

    # SEC-08: screen DM images with Vision SafeSearch, exactly like posts.
    if image_url:
        fire_and_forget(
            _moderate_message_image(chat_id, message_id, image_url),
            name=f"moderate-message-image:{message_id}",
        )

    # Push to the recipient only when the message was actually delivered.
    if initial_status == "approved":
        recipient_uid = recipient_for_guard
        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name: str = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            fire_and_forget(
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

    # Visibility is a disjunction — status == "approved" OR sender == me —
    # which Firestore can't express as one indexed filter, so we filter in
    # Python. CQ-07: over-fetch so that dropping the peer's hidden
    # (pending/rejected) messages doesn't return a short page and strand
    # older approved messages behind a broken has_more heuristic. Capped so a
    # chat full of one side's hidden messages can't force an unbounded read.
    over_fetch = min(cap * 3, 150)

    def _query() -> list[Message]:
        q = (
            db.collection(CHATS_COLLECTION)
            .document(chat_id)
            .collection(MESSAGES_SUBCOLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        if before_dt is not None:
            q = q.where(filter=FieldFilter("created_at", "<", before_dt))
        q = q.limit(over_fetch)
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
                if len(results) >= cap:
                    break
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


_MARK_READ_BATCH_CAP = 500  # Firestore batch limit safety


async def mark_chat_read(chat_id: str, reader_uid: str) -> None:
    """Mark the whole chat read for reader_uid (POST /chats/{id}/read).

    Resets the reader's unread counter and stamps read_at on every delivered
    message from the other participant that hasn't been read yet.

    Raises:
        ChatNotFound: if the chat document does not exist.
        NotAuthorized: if reader_uid is not a participant.
    """
    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    if not chat_snap.exists:
        raise ChatNotFound(chat_id)

    chat_data = chat_snap.to_dict() or {}
    if reader_uid not in chat_data.get("participants", []):
        raise NotAuthorized(reader_uid)

    def _write() -> None:
        batch = db.batch()
        batch.update(_chat_ref(chat_id), {f"unread_counts.{reader_uid}": 0})
        count = 1

        messages = (
            db.collection(CHATS_COLLECTION)
            .document(chat_id)
            .collection(MESSAGES_SUBCOLLECTION)
            .stream()
        )
        for msg_snap in messages:
            msg = msg_snap.to_dict() or {}
            if (
                msg.get("sender_uid") != reader_uid
                and msg.get("read_at") is None
                and msg.get("status", "approved") == "approved"
            ):
                batch.update(
                    _message_ref(chat_id, str(msg.get("id", msg_snap.id))),
                    {"read_at": firestore.SERVER_TIMESTAMP},
                )
                count += 1
                if count >= _MARK_READ_BATCH_CAP:
                    batch.commit()
                    batch = db.batch()
                    count = 0

        if count > 0:
            batch.commit()

    await asyncio.to_thread(_write)


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

    chat_snap = await asyncio.to_thread(_chat_ref(chat_id).get)
    participants = (chat_snap.to_dict() or {}).get("participants", [])
    recipient_uid = next((uid for uid in participants if uid != sender_uid), None)

    updates: dict[str, Any] = {"status": status, "updated_at": firestore.SERVER_TIMESTAMP}
    if status == "rejected":
        updates["rejection_reason"] = reason

    def _write() -> None:
        batch = db.batch()
        batch.update(_message_ref(chat_id, message_id), updates)
        if status == "approved":
            chat_updates: dict[str, Any] = {
                "last_message_text": text,
                "last_message_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            # Admin approval is the delivery moment for a pending message.
            if recipient_uid:
                chat_updates[f"unread_counts.{recipient_uid}"] = firestore.Increment(1)
            batch.update(_chat_ref(chat_id), chat_updates)
        batch.commit()

    await asyncio.to_thread(_write)

    if status == "approved":
        if recipient_uid:
            sender_snap = await asyncio.to_thread(
                db.collection(USERS_COLLECTION).document(sender_uid).get
            )
            display_name = (sender_snap.to_dict() or {}).get("display_name") or "Someone"
            fire_and_forget(
                send_message_notification(recipient_uid, display_name, text, chat_id)
            )

    snap2 = await asyncio.to_thread(_message_ref(chat_id, message_id).get)
    return Message.model_validate(snap2.to_dict())


async def update_presence(
    chat_id: str,
    uid: str,
    is_typing: bool = False,
    is_viewing: bool = False,
) -> None:
    """Update participant presence state in Firestore under /chats/{chat_id}/presence/{uid}."""
    ref = db.collection(CHATS_COLLECTION).document(chat_id).collection("presence").document(uid)
    await asyncio.to_thread(
        ref.set,
        {
            "uid": uid,
            "is_typing": is_typing,
            "is_viewing": is_viewing,
            "last_active": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
