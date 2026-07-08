# backend/routes/health.py
"""Health check endpoint.

Probes critical dependencies (Firestore, Firebase Auth) and reports overall
status per the API envelope contract. Returns 503 if any dependency fails.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core import API_VERSION
from core.config import get_settings
from core.firebase import auth, db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

DependencyStatus = Literal["ok", "error"]
ConfigStatus = Literal["configured", "not_configured", "disabled"]


def _probe_firestore() -> DependencyStatus:
    """Read from a sentinel doc to verify Firestore reachability."""
    try:
        # `.get()` on a non-existent doc still performs a real network round-trip
        # and returns a snapshot with .exists == False. We only care that the
        # call completes, not what it returns.
        db.collection("_health").document("ping").get(timeout=5.0)
        return "ok"
    except Exception:  # noqa: BLE001 — health probe should report, not raise
        logger.exception("Firestore health probe failed")
        return "error"


def _probe_firebase_auth() -> DependencyStatus:
    """List one user to verify Firebase Auth reachability and credentials."""
    try:
        auth.list_users(max_results=1)
        return "ok"
    except Exception:  # noqa: BLE001
        logger.exception("Firebase Auth health probe failed")
        return "error"


@router.get("")
async def get_health() -> JSONResponse:
    settings = get_settings()

    firestore_status, auth_status = await asyncio.gather(
        asyncio.to_thread(_probe_firestore),
        asyncio.to_thread(_probe_firebase_auth),
    )

    overall_ok = firestore_status == "ok" and auth_status == "ok"
    status_code = 200 if overall_ok else 503

    # External moderation services: report configuration (not a live probe, to
    # avoid per-health-check API cost). Overall health depends only on the
    # hard dependencies above — these layers fail open by design.
    openai_status: ConfigStatus = (
        "configured" if settings.openai_api_key else "not_configured"
    )
    vision_enabled = os.getenv("SAFECHAT_VISION_ENABLED", "1") not in (
        "0",
        "false",
        "False",
    )
    vision_status: ConfigStatus = "configured" if vision_enabled else "disabled"

    body = {
        "data": {
            "status": "ok" if overall_ok else "error",
            "version": API_VERSION,
            "environment": settings.environment,
            "dependencies": {
                "firestore": firestore_status,
                "firebase_auth": auth_status,
                "openai": openai_status,
                "vision": vision_status,
            },
        },
        "meta": {
            "request_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    return JSONResponse(content=body, status_code=status_code)
