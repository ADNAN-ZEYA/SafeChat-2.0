# backend/services/notifications.py
"""Push notification service — FCM via Firebase Admin SDK.

All sends are fire-and-forget: failures are logged but never raised so that a
missing token or transient FCM error never blocks the calling coroutine.
"""

from __future__ import annotations

import asyncio
import logging

from firebase_admin import messaging
from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from core.firebase import db
from models.notification import NotificationResponse

logger = logging.getLogger(__name__)

FCM_TOKENS_COLLECTION = "fcm_tokens"
NOTIFICATION_BODY_MAX = 100


def _send_fcm(message: messaging.Message) -> None:
    """Thin wrapper around messaging.send — monkeypatch seam in tests."""
    messaging.send(message)


async def send_message_notification(
    recipient_uid: str,
    sender_display_name: str,
    message_text: str,
    chat_id: str,
) -> None:
    """Send a new-message push notification to recipient_uid via FCM.

    Fetches the FCM token from /fcm_tokens/{recipient_uid}. If the document
    doesn't exist or carries no token, the function returns silently — the
    user simply hasn't registered for push notifications.

    Both the token-fetch and the FCM send are fail-open: any exception is
    logged at WARNING level and swallowed so that a transient error never
    prevents a message from being delivered in-app.

    Args:
        recipient_uid: UID of the user to notify.
        sender_display_name: Shown as the notification title.
        message_text: Notification body — truncated to 100 chars.
        chat_id: Passed in the data payload for client-side deep-linking.
    """
    # ---- 1. Fetch FCM token -------------------------------------------------
    try:
        snap = await asyncio.to_thread(
            db.collection(FCM_TOKENS_COLLECTION).document(recipient_uid).get
        )
    except Exception as exc:
        logger.warning("Failed to fetch FCM token for uid=%s: %s", recipient_uid, exc)
        return

    if not snap.exists:
        logger.info("No FCM token for uid=%s — skipping push notification", recipient_uid)
        return

    token: str | None = (snap.to_dict() or {}).get("token")
    if not token:
        logger.info("Empty FCM token for uid=%s — skipping push notification", recipient_uid)
        return

    # ---- 2. Build and send FCM message --------------------------------------
    body = message_text[:NOTIFICATION_BODY_MAX]
    fcm_message = messaging.Message(
        notification=messaging.Notification(
            title=sender_display_name,
            body=body,
        ),
        data={"chat_id": chat_id, "type": "new_message"},
        token=token,
    )

    try:
        await asyncio.to_thread(_send_fcm, fcm_message)
    except Exception as exc:
        logger.warning("FCM send failed for uid=%s chat=%s: %s", recipient_uid, chat_id, exc)


def _user_notifications_ref(uid: str) -> firestore.CollectionReference:
    return db.collection("users").document(uid).collection("notifications")


async def create_notification(
    uid: str,
    *,
    notification_type: str,
    title: str,
    body: str,
    reference_id: str | None = None,
    target_route: str | None = None,
) -> None:
    """Write an in-app notification to users/{uid}/notifications.

    Fail-open: a logging/write failure is swallowed so it never breaks the
    caller (e.g. an admin approve/reject must still succeed).
    """

    def _write() -> None:
        doc_ref = _user_notifications_ref(uid).document()
        doc_ref.set(
            {
                "id": doc_ref.id,
                "type": notification_type,
                "title": title,
                "body": body[:NOTIFICATION_BODY_MAX],
                "is_read": False,
                "reference_id": reference_id,
                "target_route": target_route,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    try:
        await asyncio.to_thread(_write)
    except Exception:
        logger.warning("Failed to write notification for uid=%s", uid, exc_info=True)


async def get_notifications(uid: str, limit: int = 20) -> list[NotificationResponse]:
    """Fetch notifications for a user, newest first."""
    cap = min(max(1, limit), 50)

    def _query() -> list[NotificationResponse]:
        q = (
            _user_notifications_ref(uid)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(cap)
        )

        results: list[NotificationResponse] = []
        for snap in q.stream():
            d = snap.to_dict() or {}
            d["id"] = snap.id
            results.append(NotificationResponse.model_validate(d))
        return results

    return await asyncio.to_thread(_query)


async def mark_as_read(uid: str, notification_id: str) -> None:
    """Mark a single notification as read.

    Fail-open (CQ-08): a missing/already-deleted notification must not 500 the
    request — marking a nonexistent notification read is a harmless no-op.
    """
    from google.api_core.exceptions import NotFound

    def _update() -> None:
        try:
            _user_notifications_ref(uid).document(notification_id).update(
                {"is_read": True}
            )
        except NotFound:
            logger.info(
                "Notification %s for uid=%s not found — mark-read no-op",
                notification_id,
                uid,
            )

    await asyncio.to_thread(_update)


async def mark_many_as_read(uid: str, notification_ids: list[str]) -> None:
    """Mark a specific set of notifications read in one batch (API-07).

    Missing ids are skipped (fail-open) so a stale client id never 500s the
    request. Chunked at the Firestore 500-write batch limit.
    """
    if not notification_ids:
        return

    def _update() -> None:
        ref = _user_notifications_ref(uid)
        batch = db.batch()
        count = 0
        for nid in notification_ids:
            batch.update(ref.document(nid), {"is_read": True})
            count += 1
            if count == 400:
                batch.commit()
                batch = db.batch()
                count = 0
        if count > 0:
            batch.commit()

    try:
        await asyncio.to_thread(_update)
    except Exception:
        # A batch update fails wholesale if any id is missing; fall back to
        # per-id best-effort so valid ids still get marked.
        for nid in notification_ids:
            await mark_as_read(uid, nid)


async def mark_all_as_read(uid: str) -> None:
    """Mark all unread notifications for a user as read."""

    def _update() -> None:
        q = _user_notifications_ref(uid).where(filter=FieldFilter("is_read", "==", False))

        batch = db.batch()
        count = 0
        for snap in q.stream():
            batch.update(snap.reference, {"is_read": True})
            count += 1
            if count == 400:  # Firestore batch limit safety
                batch.commit()
                batch = db.batch()
                count = 0

        if count > 0:
            batch.commit()

    await asyncio.to_thread(_update)
