# backend/routes/notifications.py
"""Notifications API routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from middleware.auth import get_current_user_claims
from services import notifications as notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


class MarkReadRequest(BaseModel):
    """Body for POST /notifications/read (API_CONTRACTS.md §9)."""

    ids: list[str] = Field(default_factory=list, max_length=500)


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("")
async def list_notifications(
    limit: int = 20,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch notifications for the current user."""
    notifications = await notifications_service.get_notifications(claims["uid"], limit)
    data = [n.model_dump(mode="json") for n in notifications]
    return JSONResponse(content={"data": data, "meta": _meta()})


@router.put("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Mark a specific notification as read."""
    await notifications_service.mark_as_read(claims["uid"], notification_id)
    return Response(status_code=204)


@router.post("/read", status_code=204)
async def mark_read_batch(
    payload: MarkReadRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Mark the given notification ids as read (API-07, contract §9)."""
    await notifications_service.mark_many_as_read(claims["uid"], payload.ids)
    return Response(status_code=204)


@router.put("/read-all", status_code=204)
async def mark_all_read(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Mark all unread notifications as read."""
    await notifications_service.mark_all_as_read(claims["uid"])
    return Response(status_code=204)
