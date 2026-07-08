# backend/routes/uploads.py
"""Upload routes — signed URL generation for GCS direct uploads."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from middleware.auth import get_current_user_claims
from services import storage as storage_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class _SignRequest(BaseModel):
    content_type: str
    purpose: Literal["post", "story", "avatar", "background", "message"]
    # Declared upload size; validated against the contract caps (API-03).
    size_bytes: int | None = Field(default=None, ge=1)


@router.post("/sign")
async def sign_upload(
    payload: _SignRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Generate a signed GCS upload URL.

    The client should PUT the file directly to upload_url with the matching
    Content-Type header. The URL is valid for 15 minutes.
    """
    try:
        result = storage_service.generate_upload_url(
            uid=claims["uid"],
            content_type=payload.content_type,
            purpose=payload.purpose,
            size_bytes=payload.size_bytes,
        )
    except storage_service.InvalidContentType:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_CONTENT_TYPE",
                "message": (
                    f"Content type '{payload.content_type}' is not allowed. "
                    "Accepted: image/jpeg, image/png, image/webp, video/mp4."
                ),
                "field": "content_type",
            },
        )
    except storage_service.FileTooLarge as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": (
                    f"Declared size {exc.size_bytes} bytes exceeds the "
                    f"{exc.cap // (1024 * 1024)} MB limit for this media type."
                ),
                "field": "size_bytes",
            },
        ) from exc

    return JSONResponse(
        content={"data": result.model_dump(mode="json"), "meta": _meta()}
    )
