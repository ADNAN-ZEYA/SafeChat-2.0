# backend/routes/users.py
"""User profile routes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from middleware.auth import get_current_user_claims
from models.user import DeviceTokenRequest, UpdateProfileRequest
from moderation.engine import moderate_text
from services import blocks as blocks_service
from services import follows as follows_service
from services import messages as messages_service
from services import posts as posts_service
from services import storage as storage_service
from services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])

_SEARCH_LIMIT_DEFAULT = 10
_SEARCH_LIMIT_MAX = 20


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _user_not_found(identifier: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": f"User '{identifier}' not found."},
    )


def _self_action_error(action: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "INVALID_INPUT", "message": f"You cannot {action} yourself."},
    )


# NOTE: /search is declared before /{username} so the literal path wins over
# the path parameter (both are GET).
@router.get("/search")
async def search_users(
    q: str = "",
    limit: int = _SEARCH_LIMIT_DEFAULT,
    _claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Prefix-search users by username. Empty query returns an empty list."""
    capped_limit = max(1, min(limit, _SEARCH_LIMIT_MAX))
    results = await users_service.search_users(q, capped_limit)
    return JSONResponse(content={"data": {"results": results}, "meta": _meta()})


@router.get("/suggested")
async def get_suggested_users(
    limit: int = 10,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return a list of suggested users to follow."""
    results = await users_service.get_suggested_users(claims["uid"], limit)
    return JSONResponse(content={"data": results, "meta": _meta()})


@router.get("/me/blocked")
async def get_blocked_users(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return a list of blocked users."""
    results = await users_service.get_blocked_users_profiles(claims["uid"])
    return JSONResponse(content={"data": results, "meta": _meta()})


@router.patch("/me")
async def update_me(
    payload: UpdateProfileRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Update the authenticated user's own profile.

    `display_name` and `bio`, when present, are run through the moderation
    cascade before persisting. A blocked value yields 422 MODERATION_BLOCKED.
    """
    uid = claims["uid"]
    fields = payload.model_dump(exclude_unset=True)

    for field_name in ("display_name", "bio"):
        value = fields.get(field_name)
        if isinstance(value, str) and value.strip():
            result = await moderate_text(value)
            if result.blocked:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "MODERATION_BLOCKED",
                        "message": (
                            f"The submitted {field_name.replace('_', ' ')} "
                            "was blocked by content moderation."
                        ),
                        "field": field_name,
                    },
                )

    try:
        profile = await users_service.update_profile(uid, fields)
    except users_service.ProfileNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "Profile not found. Complete onboarding first.",
            },
        ) from exc

    return JSONResponse(
        content={
            "data": {"profile": profile.model_dump(mode="json")},
            "meta": _meta(),
        }
    )


@router.post("/{uid}/block", status_code=204)
async def block_user(
    uid: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Block another user. Idempotent."""
    blocker_uid = claims["uid"]
    if blocker_uid == uid:
        raise _self_action_error("block")

    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    await blocks_service.block_user(blocker_uid, uid)
    return Response(status_code=204)


@router.delete("/{uid}/block", status_code=204)
async def unblock_user(
    uid: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Unblock a user. Idempotent."""
    blocker_uid = claims["uid"]
    if blocker_uid == uid:
        raise _self_action_error("unblock")

    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    await blocks_service.unblock_user(blocker_uid, uid)
    return Response(status_code=204)


@router.post("/{uid}/follow", status_code=204)
async def follow_user(
    uid: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Follow another user. Idempotent."""
    follower_uid = claims["uid"]
    if follower_uid == uid:
        raise _self_action_error("follow")

    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    await follows_service.follow_user(follower_uid, uid)
    return Response(status_code=204)


@router.delete("/{uid}/follow", status_code=204)
async def unfollow_user(
    uid: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Unfollow a user. Idempotent."""
    follower_uid = claims["uid"]
    if follower_uid == uid:
        raise _self_action_error("unfollow")

    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    await follows_service.unfollow_user(follower_uid, uid)

    # Breaking mutual-follow status revokes SafeChat Mode (E2E encryption) —
    # a chat between these two users can only stay "trusted" while they
    # mutually follow each other.
    await messages_service.revert_chat_to_pending_if_trusted(follower_uid, uid)

    return Response(status_code=204)


@router.get("/{uid}/followers")
async def list_followers(
    uid: str,
    _claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return the list of uids that follow the given user."""
    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    followers = await follows_service.get_followers(uid)
    return JSONResponse(content={"data": {"followers": followers}, "meta": _meta()})


@router.get("/{uid}/following")
async def list_following(
    uid: str,
    _claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return the list of uids that the given user follows."""
    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)

    following = await follows_service.get_following(uid)
    return JSONResponse(content={"data": {"following": following}, "meta": _meta()})


@router.get("/{username}")
async def get_user(
    username: str,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch a public profile by username."""
    profile = await users_service.get_profile_by_username(username)
    if profile is None:
        raise _user_not_found(username)

    viewer_uid = claims["uid"]
    blocked, is_following, is_followed_by = await asyncio.gather(
        blocks_service.is_blocked(viewer_uid, profile.uid),
        follows_service.is_following(viewer_uid, profile.uid),
        follows_service.is_following(profile.uid, viewer_uid),
    )

    data = {
        **profile.model_dump(mode="json"),
        "is_following": is_following,
        "is_followed_by": is_followed_by,
        "is_blocked": blocked,
    }
    return JSONResponse(content={"data": data, "meta": _meta()})


@router.post("/device-token", status_code=204)
async def register_device_token(
    payload: DeviceTokenRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> Response:
    """Register an FCM device token for push notifications."""
    await users_service.register_device_token(claims["uid"], payload.token)
    return Response(status_code=204)


@router.get("/{uid}/posts")
async def list_user_posts(
    uid: str,
    limit: int = 20,
    _claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Fetch recent posts by a specific user."""
    # Ensure user exists
    if await users_service.get_user_profile(uid) is None:
        raise _user_not_found(uid)
        
    posts = await posts_service.get_posts_by_author(uid, limit)
    data = []
    for post in posts:
        post_dict = post.model_dump(mode="json")
        if post_dict.get("image_url"):
            post_dict["image_url"] = storage_service.sign_media_url(post_dict["image_url"])
        if post_dict.get("media_urls"):
            post_dict["media_urls"] = [
                storage_service.sign_media_url(url) for url in post_dict["media_urls"]
            ]
        data.append(post_dict)

    return JSONResponse(content={"data": data, "meta": _meta()})
