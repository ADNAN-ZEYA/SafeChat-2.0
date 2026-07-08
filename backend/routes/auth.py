# backend/routes/auth.py
"""Authentication routes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from core.firebase import db
from middleware.auth import get_current_user_claims
from models.auth import CurrentUser
from models.user import OnboardRequest, UpdateProfileRequest
from moderation.engine import moderate_text
from routes.users import apply_profile_update
from services import storage as storage_service
from services import users as users_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _sanitize(obj: Any) -> Any:
    """Recursively convert datetime/Firestore timestamp objects to ISO strings.

    Firestore returns DatetimeWithNanoseconds (a datetime subclass) for
    timestamp fields. JSONResponse cannot serialize these, so we walk the
    structure and convert any datetime instance to an ISO 8601 string.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


@router.get("/me")
async def get_me(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Return the authenticated user, plus their Firestore profile if it exists.

    If no /users/{uid} document exists yet, `needs_onboarding` is true and the
    client should run the onboarding flow (username selection, profile setup).
    """
    user = CurrentUser.from_decoded_token(claims)

    snapshot = await asyncio.to_thread(
        lambda: db.collection("users").document(user.uid).get()
    )

    if snapshot.exists:
        profile_data = _sanitize(snapshot.to_dict())
        if profile_data.get("photo_url"):
            profile_data["photo_url"] = storage_service.sign_media_url(profile_data["photo_url"])
        if profile_data.get("background_url"):
            profile_data["background_url"] = storage_service.sign_media_url(profile_data["background_url"])
        data = {
            "user": user.model_dump(),
            "profile": profile_data,
            "needs_onboarding": False,
        }
    else:
        data = {
            "user": user.model_dump(),
            "profile": None,
            "needs_onboarding": True,
        }

    return JSONResponse(content={"data": data, "meta": _meta()})


@router.post("/onboard", status_code=201)
async def onboard(
    payload: OnboardRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Create the authenticated user's profile and reserve their username.

    The username and user-profile docs are created in a single Firestore
    transaction. `username`, `display_name`, and `bio` run through the
    moderation cascade first (SEC-07) — profile fields are the first content
    other users ever see.
    """
    uid = claims["uid"]
    email = claims.get("email")
    photo_url = claims.get("picture") # Extracted from Google Auth

    for field_name, value in (
        ("username", payload.username),
        ("display_name", payload.display_name),
        ("bio", payload.bio),
    ):
        if value and value.strip():
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
        profile = await users_service.reserve_username(
            uid=uid,
            email=email,
            phone_number=payload.phone_number,
            username=payload.username,
            display_name=payload.display_name,
            dob=payload.dob,
            bio=payload.bio,
            photo_url=photo_url,
        )
    except users_service.UsernameTaken as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "USERNAME_TAKEN",
                "message": f"Username '{payload.username}' is already taken.",
                "field": "username",
            },
        ) from exc
    except users_service.PhoneNumberTaken as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PHONE_TAKEN",
                "message": f"Phone number '{payload.phone_number}' is already registered.",
                "field": "phone_number",
            },
        ) from exc
    except users_service.AlreadyOnboarded as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": "User has already completed onboarding.",
            },
        ) from exc

    return JSONResponse(
        status_code=201,
        content={
            "data": {"profile": profile.model_dump(mode="json")},
            "meta": _meta(),
        },
    )

@router.patch("/profile", status_code=200)
async def update_profile(
    payload: UpdateProfileRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """Update the authenticated user's profile.

    API-02: thin alias for PATCH /users/me, kept because the Flutter client
    calls this path (e.g. encryption_service.dart publishes `public_key`
    here). Both routes share ONE implementation — apply_profile_update in
    routes/users.py — so moderation and URL-signing behavior can never
    drift between them again.
    """
    profile_dict = await apply_profile_update(claims["uid"], payload)
    return JSONResponse(
        status_code=200,
        content={
            "data": {"profile": profile_dict},
            "meta": _meta(),
        },
    )
