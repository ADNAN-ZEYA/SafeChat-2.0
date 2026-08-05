# backend/routes/posts.py
"""Post routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from middleware.auth import get_current_user_claims
from models.comment import CreateCommentRequest
from models.moderation import Match
from models.post import CreatePostRequest, Post
from services import comments as comments_service
from services import likes as likes_service
from services import posts as posts_service
from services import storage as storage_service

router = APIRouter(prefix="/posts", tags=["posts"])

_FEED_LIMIT_DEFAULT = 20
_FEED_LIMIT_MAX = 20
_COMMENTS_LIMIT_DEFAULT = 20
_COMMENTS_LIMIT_MAX = 50


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _moderation_flagged_response(matches: list[Match], reason: str | None) -> JSONResponse:
    """422 envelope for flagged content the author may submit for human review.

    Carries the flagged spans (``matches``) so the client can highlight the
    offending words in the "this can't be uploaded" popup and offer the
    "submit for human verification" action.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "MODERATION_FLAGGED",
                "message": (
                    "This content can't be uploaded as-is. Edit it, or submit it "
                    "for human verification."
                ),
                "field": "text",
                "matches": [m.model_dump(mode="json") for m in matches],
                "reason": reason,
            },
            "meta": _meta(),
        },
    )


def _serialize_post(post: Post) -> dict[str, Any]:
    """Serialize a post for the client, replacing private-bucket media URLs with
    short-lived signed read URLs so clients can actually load the images.
    """
    data = post.model_dump(mode="json")
    if data.get("image_url"):
        data["image_url"] = storage_service.sign_media_url(data["image_url"])
    if data.get("media_urls"):
        data["media_urls"] = [storage_service.sign_media_url(url) for url in data["media_urls"]]
    if data.get("author_photo_url"):
        data["author_photo_url"] = storage_service.sign_media_url(data["author_photo_url"])
    return data


def _post_not_found(post_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"Post '{post_id}' not found."},
    )


def _comment_not_found(comment_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"Comment '{comment_id}' not found."},
    )


# NOTE: /feed is declared before /{post_id} so the literal path wins.
@router.get("/feed")
async def get_feed(
    type: str = "following",
    limit: int = _FEED_LIMIT_DEFAULT,
    before: str | None = None,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Paginated feed of approved posts, newest first."""
    cap = max(1, min(limit, _FEED_LIMIT_MAX))
    posts = await posts_service.get_feed(
        viewer_uid=claims["uid"],
        feed_type=type,
        limit=cap,
        before_created_at=before,
    )
    return JSONResponse(
        content={
            "data": {"posts": [_serialize_post(p) for p in posts]},
            "meta": _meta(),
        }
    )


@router.post("", status_code=201)
async def create_post(
    payload: CreatePostRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Create a new post.

    Text + images run through the moderation cascade:
    - Clean content  → 201 Created, status=approved, appears in feed immediately.
    - Flagged content → 202 Accepted, status=pending_review, saved but hidden
      from the public feed. Visible only under the author's own profile.
      A human moderator later approves (post becomes public) or rejects
      (post becomes a draft the author can edit).
    """
    if not payload.media_urls:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": "Posts cannot be text-only. An image or video is required."},
        )

    try:
        post = await posts_service.create_post(
            author_uid=claims["uid"],
            text=payload.text,
            media_urls=payload.media_urls,
            media_type=payload.media_type,
            submit_for_review=payload.submit_for_review,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from exc
    except posts_service.PostBlocked as exc:
        return _moderation_flagged_response(exc.matches, exc.reason)

    status_code = 202 if post.status == "pending_review" else 201
    return JSONResponse(
        status_code=status_code,
        content={"data": {"post": _serialize_post(post)}, "meta": _meta()},
    )


# NOTE: /{post_id}/like and /{post_id}/comments/* are declared before /{post_id}
# so multi-segment paths are matched before the single-segment catch-all.
@router.post("/{post_id}/like", status_code=204)
async def like_post(
    post_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Like a post. Idempotent."""
    if await posts_service.get_post(post_id) is None:
        raise _post_not_found(post_id)
    await likes_service.like_post(claims["uid"], post_id)
    return Response(status_code=204)


@router.delete("/{post_id}/like", status_code=204)
async def unlike_post(
    post_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Unlike a post. Idempotent."""
    if await posts_service.get_post(post_id) is None:
        raise _post_not_found(post_id)
    await likes_service.unlike_post(claims["uid"], post_id)
    return Response(status_code=204)


@router.post("/{post_id}/view", status_code=204)
async def view_post(
    post_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Record a view on a post. Idempotent."""
    try:
        await posts_service.record_post_view(post_id, claims["uid"])
    except posts_service.PostNotFound as exc:
        raise _post_not_found(post_id) from exc
    return Response(status_code=204)


# NOTE: three-segment path declared before two-segment /comments.
@router.delete("/{post_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    post_id: str,
    comment_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Delete a comment. Only the author or an admin may delete."""
    try:
        await comments_service.delete_comment(
            post_id=post_id,
            comment_id=comment_id,
            requesting_uid=claims["uid"],
            is_admin=bool(claims.get("admin", False)),
        )
    except comments_service.CommentNotFound as exc:
        raise _comment_not_found(comment_id) from exc
    except comments_service.NotAuthorized as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "You are not allowed to delete this comment.",
            },
        ) from exc

    return Response(status_code=204)


@router.post("/{post_id}/comments/{comment_id}/like", status_code=204)
async def like_comment(
    post_id: str,
    comment_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Like a comment."""
    if await comments_service.get_comment(post_id, comment_id) is None:
        raise _comment_not_found(comment_id)
    await comments_service.like_comment(claims["uid"], post_id, comment_id)
    return Response(status_code=204)


@router.delete("/{post_id}/comments/{comment_id}/like", status_code=204)
async def unlike_comment(
    post_id: str,
    comment_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Unlike a comment."""
    if await comments_service.get_comment(post_id, comment_id) is None:
        raise _comment_not_found(comment_id)
    await comments_service.unlike_comment(claims["uid"], post_id, comment_id)
    return Response(status_code=204)


@router.post("/{post_id}/comments", status_code=201)
async def create_comment(
    post_id: str,
    payload: CreateCommentRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Create a comment on a post."""
    try:
        comment = await comments_service.create_comment(
            post_id=post_id,
            author_uid=claims["uid"],
            text=payload.text,
            parent_comment_id=payload.parent_comment_id,
            submit_for_review=payload.submit_for_review,
        )
    except comments_service.PostNotFound as exc:
        raise _post_not_found(post_id) from exc
    except comments_service.CommentBlocked as exc:
        return _moderation_flagged_response(exc.matches, exc.reason)

    status_code = 202 if comment.status == "pending_review" else 201
    return JSONResponse(
        status_code=status_code,
        content={"data": {"comment": comment.model_dump(mode="json")}, "meta": _meta()},
    )


@router.get("/{post_id}/comments")
async def get_comments(
    post_id: str,
    limit: int = _COMMENTS_LIMIT_DEFAULT,
    before: str | None = None,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch comments for a post, oldest first."""
    cap = max(1, min(limit, _COMMENTS_LIMIT_MAX))
    comment_list = await comments_service.get_comments(
        post_id=post_id,
        limit=cap,
        before_created_at=before,
    )

    viewer_uid = claims["uid"]

    import asyncio

    # We need to map `is_liked` for each comment
    async def _resolve_is_liked(c):
        liked = await comments_service.is_comment_liked(viewer_uid, post_id, c.id)
        data = c.model_dump(mode="json")
        data["is_liked"] = liked
        return data

    resolved_comments = await asyncio.gather(*[_resolve_is_liked(c) for c in comment_list])

    return JSONResponse(
        content={
            "data": {"comments": resolved_comments},
            "meta": _meta(),
        }
    )


@router.get("/{post_id}")
async def get_post(
    post_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch a single post by ID. Includes is_liked for the requesting user."""
    post = await posts_service.get_post(post_id)
    if post is None:
        raise _post_not_found(post_id)

    viewer_uid = claims["uid"]
    liked = await likes_service.is_liked(viewer_uid, post_id)

    return JSONResponse(
        content={
            "data": {"post": {**_serialize_post(post), "is_liked": liked}},
            "meta": _meta(),
        }
    )


@router.delete("/{post_id}", status_code=204)
async def delete_post(
    post_id: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Delete a post. Only the author or an admin may delete."""
    try:
        await posts_service.delete_post(
            post_id=post_id,
            requesting_uid=claims["uid"],
            is_admin=bool(claims.get("admin", False)),
        )
    except posts_service.PostNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Post '{post_id}' not found."},
        ) from exc
    except posts_service.NotAuthorized as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "You are not allowed to delete this post.",
            },
        ) from exc

    return Response(status_code=204)
