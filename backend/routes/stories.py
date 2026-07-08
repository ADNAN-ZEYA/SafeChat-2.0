# backend/routes/stories.py
"""Story routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from middleware.auth import get_current_user_claims
from models.story import CreateStoryRequest
from services import stories as stories_service

router = APIRouter(prefix="/stories", tags=["stories"])


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _story_not_found(story_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"Story '{story_id}' not found."},
    )


# NOTE: /feed is declared before /{story_id} so the literal path wins.
@router.get("/feed")
async def get_feed_stories(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Active stories from followed users — one per user, newest first."""
    stories = await stories_service.get_feed_stories(viewer_uid=claims["uid"])
    return JSONResponse(
        content={
            "data": {"stories": [s.model_dump(mode="json") for s in stories]},
            "meta": _meta(),
        }
    )


@router.post("", status_code=201)
async def create_story(
    payload: CreateStoryRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Create a new story. Text (if present) is run through content moderation.

    Responds 201 (approved), 202 (flagged + submitted for human review), or
    422 MODERATION_FLAGGED with match spans — the same contract as posts,
    comments, and messages (API-04).
    """
    try:
        story = await stories_service.create_story(
            author_uid=claims["uid"],
            image_url=payload.image_url,
            text=payload.text,
            submit_for_review=payload.submit_for_review,
        )
    except stories_service.StoryBlocked as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "MODERATION_FLAGGED",
                    "message": (
                        "This story can't be uploaded as-is. Edit it, or submit "
                        "it for human verification."
                    ),
                    "field": "text",
                    "matches": [m.model_dump(mode="json") for m in exc.matches],
                    "reason": exc.reason,
                },
                "meta": _meta(),
            },
        )

    status_code = 202 if story.status == "pending_review" else 201
    return JSONResponse(
        status_code=status_code,
        content={"data": {"story": story.model_dump(mode="json")}, "meta": _meta()},
    )


# NOTE: /{story_id}/view is declared before /{story_id} so the two-segment
# path is matched before the single-segment catch-all.
@router.post("/{story_id}/view", status_code=204)
async def record_view(
    story_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Record a story view. Idempotent."""
    if await stories_service.get_story(story_id) is None:
        raise _story_not_found(story_id)
    await stories_service.record_view(story_id, claims["uid"])
    return Response(status_code=204)


@router.get("/{story_id}")
async def get_story(
    story_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch a single story by ID. Returns 404 if not found or expired."""
    story = await stories_service.get_story(story_id)
    if story is None:
        raise _story_not_found(story_id)

    return JSONResponse(
        content={
            "data": {"story": story.model_dump(mode="json")},
            "meta": _meta(),
        }
    )


@router.delete("/{story_id}", status_code=204)
async def delete_story(
    story_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Delete a story. Only the author or an admin may delete."""
    try:
        await stories_service.delete_story(
            story_id=story_id,
            requesting_uid=claims["uid"],
            is_admin=bool(claims.get("admin", False)),
        )
    except stories_service.StoryNotFound as exc:
        raise _story_not_found(story_id) from exc
    except stories_service.NotAuthorized as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "You are not allowed to delete this story.",
            },
        ) from exc

    return Response(status_code=204)
