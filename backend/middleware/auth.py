# backend/middleware/auth.py
"""Firebase Auth middleware.

Exposes `get_current_user_claims` — a FastAPI dependency that extracts a Bearer
token from the Authorization header, verifies it via the Firebase Admin SDK,
and returns the decoded claims dictionary. All auth failures raise
HTTPException(401) with a structured detail; main.py reshapes it into the
standard error envelope.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import hashlib
from core.cache import TTLCache
from core.firebase import auth

bearer_scheme = HTTPBearer(auto_error=False)
_token_claims_cache = TTLCache(default_ttl_seconds=300.0)


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "UNAUTHENTICATED", "message": message},
    )


async def get_current_user_claims(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict[str, Any]:
    """Verify the Authorization Bearer token and return the decoded claims.

    Raises:
        HTTPException(401): if the header is missing/malformed or the token is
        invalid, expired, revoked, or belongs to a disabled user.
    """
    if credentials is None:
        raise _unauthenticated(
            "Missing or malformed Authorization header. Expected: Bearer <token>."
        )

    token_str = credentials.credentials
    token_key = hashlib.sha256(token_str.encode("utf-8")).hexdigest()
    cached_claims = _token_claims_cache.get(token_key)
    if cached_claims is not None:
        return cached_claims

    try:
        decoded: dict[str, Any] = await asyncio.to_thread(
            auth.verify_id_token, token_str, check_revoked=True
        )
        _token_claims_cache.set(token_key, decoded)
    except auth.ExpiredIdTokenError as exc:
        raise _unauthenticated("ID token has expired.") from exc
    except auth.RevokedIdTokenError as exc:
        raise _unauthenticated("ID token has been revoked.") from exc
    except auth.UserDisabledError as exc:
        raise _unauthenticated("User account has been disabled.") from exc
    except (auth.InvalidIdTokenError, auth.CertificateFetchError, ValueError) as exc:
        raise _unauthenticated("Invalid ID token.") from exc

    return decoded


async def require_admin(
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> dict[str, Any]:
    """FastAPI dependency that requires the `admin` custom claim.

    Builds on top of `get_current_user_claims` so token verification happens
    exactly once per request. Raises 403 FORBIDDEN if the verified user is
    authenticated but not an admin.
    """
    if not claims.get("admin"):
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Admin access required."},
        )
    return claims
