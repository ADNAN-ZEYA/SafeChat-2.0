# backend/routes/moderation.py
"""Client-facing moderation routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from middleware.auth import get_current_user_claims
from moderation.engine import moderate_text
from services import moderation_queue
from services.moderation_log import log_moderation_decision

router = APIRouter(prefix="/moderation", tags=["moderation"])

_MAX_TEXT_LENGTH = 10_000


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


class ModerationAnalyzeRequest(BaseModel):
    """Input for POST /moderation/analyze."""

    content_type: str = Field(default="text")
    text: str = Field(max_length=_MAX_TEXT_LENGTH)


@router.post("/analyze")
async def analyze_content(
    payload: ModerationAnalyzeRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Analyze text for violations before submission.

    Returns SAFE, WARNING, or BLOCKED status.
    """
    result = await moderate_text(payload.text)

    # Convert internal 'blocked' boolean to SAFE/BLOCKED/WARNING statuses
    # For MVP, warning is not natively produced by moderate_text unless we configure it,
    # but the frontend expects status string.
    status = "BLOCKED" if result.blocked else "SAFE"

    # Log the decision
    await log_moderation_decision(
        result=result,
        content_type="pre_flight",
        content_id=None,
        author_uid=claims["uid"],
    )

    return JSONResponse(
        content={
            "data": {
                "status": status,
                "reason": result.reason,
                "category": result.category,
                # Exact spans of any flagged terms, for client-side highlighting.
                "matches": [m.model_dump(mode="json") for m in result.matches],
            },
            "meta": _meta(),
        }
    )


# NOTE (SEC-01): the former POST /moderation/appeals/{content_id} endpoint was
# removed. It let any authenticated user flip any post to pending_review (an
# IDOR that unpublished other users' content) and wrote to an orphan `appeals`
# collection that no admin surface reads. Human verification is served entirely
# by the submit_for_review flow on POST /posts, /posts/{id}/comments, and
# /chats/{id}/messages, which enqueues into `moderation_queue` — the single
# source of truth the admin portal and GET /moderation/appeals read.


@router.get("/appeals")
async def list_my_appeals(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """List the current user's content under / after human verification.

    Backs the Profile -> Appeals (Content Status) screen: each item carries its
    status (pending_review / approved / rejected) and, when rejected, the
    reason — so the author sees what happened to flagged content they submitted.
    """
    items = await moderation_queue.list_for_user(claims["uid"])
    return JSONResponse(
        content={
            "data": {"items": [item.model_dump(mode="json") for item in items]},
            "meta": _meta(),
        }
    )
