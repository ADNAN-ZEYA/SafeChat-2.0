# backend/routes/messages.py
"""Direct-message routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from middleware.auth import get_current_user_claims
from models.message import EncryptionModeRequest, SendMessageRequest
from models.moderation import Match
from services import messages as messages_service

router = APIRouter(prefix="/chats", tags=["messages"])

_MESSAGES_LIMIT_DEFAULT = 50
_MESSAGES_LIMIT_MAX = 50


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _chat_not_found(chat_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"Chat '{chat_id}' not found."},
    )


def _message_not_found(message_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"Message '{message_id}' not found."},
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": "FORBIDDEN",
            "message": "You are not a participant in this chat.",
        },
    )


def _moderation_flagged_response(matches: list[Match], reason: str | None) -> JSONResponse:
    """422 envelope for a flagged message the sender may submit for review."""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "MODERATION_FLAGGED",
                "message": (
                    "This message can't be sent as-is. Edit it, or submit it for "
                    "human verification."
                ),
                "field": "text",
                "matches": [m.model_dump(mode="json") for m in matches],
                "reason": reason,
            },
            "meta": _meta(),
        },
    )


# NOTE: paths are declared longest-first within each HTTP method so FastAPI
# matches more-specific routes before the single-segment catch-all /{uid}.


@router.patch("/{chat_id}/messages/{message_id}/read", status_code=204)
async def mark_message_read(
    chat_id: str,
    message_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Mark a specific message as read by the requesting user."""
    try:
        await messages_service.mark_read(
            chat_id=chat_id,
            message_id=message_id,
            reader_uid=claims["uid"],
        )
    except messages_service.ChatNotFound as exc:
        raise _chat_not_found(chat_id) from exc
    except messages_service.MessageNotFound as exc:
        raise _message_not_found(message_id) from exc
    except messages_service.NotAuthorized as exc:
        raise _forbidden() from exc

    return Response(status_code=204)


@router.post("/{chat_id}/read", status_code=204)
async def mark_chat_read(
    chat_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Mark all messages in the chat as read for the requesting user.

    Resets the caller's unread counter (chat-list badge) and stamps read
    receipts on the other participant's delivered messages.
    """
    try:
        await messages_service.mark_chat_read(chat_id=chat_id, reader_uid=claims["uid"])
    except messages_service.ChatNotFound as exc:
        raise _chat_not_found(chat_id) from exc
    except messages_service.NotAuthorized as exc:
        raise _forbidden() from exc

    return Response(status_code=204)


@router.patch("/{chat_id}/encryption-mode")
async def update_encryption_mode(
    chat_id: str,
    payload: EncryptionModeRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Toggle SafeChat Mode for a chat. Only allowed between mutual followers."""
    try:
        chat = await messages_service.set_encryption_mode(
            chat_id=chat_id,
            requesting_uid=claims["uid"],
            mode=payload.mode,
        )
    except messages_service.ChatNotFound as exc:
        raise _chat_not_found(chat_id) from exc
    except messages_service.NotAuthorized as exc:
        raise _forbidden() from exc
    except messages_service.NotMutualFollow as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "SafeChat Mode can only be changed between mutual followers.",
            },
        ) from exc

    return JSONResponse(
        content={
            "data": {"encryption_mode": chat.encryption_mode},
            "meta": _meta(),
        }
    )


@router.post("/{chat_id}/messages", status_code=201)
async def send_message(
    chat_id: str,
    payload: SendMessageRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Send a message to a chat. Text is run through content moderation."""
    try:
        message = await messages_service.send_message(
            chat_id=chat_id,
            sender_uid=claims["uid"],
            text=payload.text,
            image_url=payload.image_url,
            submit_for_review=payload.submit_for_review,
        )
    except messages_service.ChatNotFound as exc:
        raise _chat_not_found(chat_id) from exc
    except messages_service.NotAuthorized as exc:
        raise _forbidden() from exc
    except messages_service.MessagingNotAllowed as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        ) from exc
    except messages_service.MessageBlocked as exc:
        return _moderation_flagged_response(exc.matches, exc.reason)
    except messages_service.InvalidCiphertext as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_INPUT",
                "message": str(exc),
                "field": "text",
            },
        ) from exc

    status_code = 202 if message.status == "pending_review" else 201
    return JSONResponse(
        status_code=status_code,
        content={
            "data": {"message": message.model_dump(mode="json")},
            "meta": _meta(),
        },
    )


@router.get("/{chat_id}/messages")
async def get_messages(
    chat_id: str,
    limit: int = _MESSAGES_LIMIT_DEFAULT,
    before: str | None = None,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch messages in a chat, newest first."""
    cap = max(1, min(limit, _MESSAGES_LIMIT_MAX))
    try:
        messages = await messages_service.get_messages(
            chat_id=chat_id,
            requesting_uid=claims["uid"],
            limit=cap,
            before_created_at=before,
        )
    except messages_service.ChatNotFound as exc:
        raise _chat_not_found(chat_id) from exc
    except messages_service.NotAuthorized as exc:
        raise _forbidden() from exc

    return JSONResponse(
        content={
            "data": {"messages": [m.model_dump(mode="json") for m in messages]},
            "meta": _meta(),
        }
    )


@router.get("")
async def list_chats(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return all chats the authenticated user participates in."""
    chats = await messages_service.get_chats(uid=claims["uid"])
    return JSONResponse(
        content={
            "data": {"chats": [c.model_dump(mode="json") for c in chats]},
            "meta": _meta(),
        }
    )


@router.post("/{uid}", status_code=201)
async def get_or_create_chat(
    uid: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Start a direct-message chat with user {uid}, or return the existing one."""
    try:
        chat = await messages_service.get_or_create_chat(
            uid_a=claims["uid"],
            uid_b=uid,
        )
    except messages_service.CannotMessageSelf as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_INPUT",
                "message": "You cannot start a chat with yourself.",
            },
        ) from exc
    except messages_service.MessagingNotAllowed as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        ) from exc

    return JSONResponse(
        status_code=201,
        content={"data": {"chat": chat.model_dump(mode="json")}, "meta": _meta()},
    )
