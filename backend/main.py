# backend/main.py
"""FastAPI application entry point for SafeChat backend."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core import API_VERSION, firebase  # noqa: F401  — firebase import triggers Admin SDK init
from core.config import get_settings
from core.logging import (
    configure_logging,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from middleware.rate_limit import install_rate_limiter
from routes import admin as admin_routes
from routes import auth as auth_routes
from routes import health
from routes import messages as messages_routes
from routes import moderation as moderation_routes
from routes import notifications as notifications_routes
from routes import posts as posts_routes
from routes import reports as reports_routes
from routes import safety as safety_routes
from routes import stories as stories_routes
from routes import uploads as uploads_routes
from routes import users as users_routes

logger = logging.getLogger(__name__)

API_V1_PREFIX = "/api/v1"

_DEFAULT_ERROR_CODES: dict[int, str] = {
    400: "INVALID_INPUT",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "MODERATION_BLOCKED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

# Pydantic loc tuples are prefixed with the source (body / query / path / header / cookie).
# We strip the prefix so error.field is the actual user-facing field name.
_VALIDATION_LOC_PREFIXES = {"body", "query", "path", "header", "cookie"}


def _make_meta() -> dict[str, str]:
    # Reuse the correlated request id (PR-03) so an error envelope and the
    # server log lines for the same request share one id.
    rid = get_request_id()
    return {
        "request_id": rid if rid != "-" else str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _envelope_error(
    *,
    status_code: int,
    code: str,
    message: str,
    field: str | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if field:
        error["field"] = field
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "meta": _make_meta()},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "SafeChat API starting (environment=%s, project=%s)",
        settings.environment,
        settings.gcp_project_id,
    )

    try:
        yield
    finally:
        logger.info("SafeChat API shutting down")


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Reshape HTTPException into the standard error envelope.

    Handlers and dependencies may raise HTTPException with `detail` as either:
      - a dict like {"code": "...", "message": "...", "field": "..."}, or
      - a plain string (falls back to the default code for the status).
    """
    detail: Any = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code") or _DEFAULT_ERROR_CODES.get(exc.status_code, "ERROR")
        message = detail.get("message", "")
        field = detail.get("field")
    else:
        code = _DEFAULT_ERROR_CODES.get(exc.status_code, "ERROR")
        message = str(detail) if detail is not None else ""
        field = None

    return _envelope_error(
        status_code=exc.status_code, code=code, message=message, field=field
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert FastAPI's default 422 validation errors into 400 INVALID_INPUT.

    The contract reserves 422 for MODERATION_BLOCKED, so structural validation
    failures use 400 instead. The first error's field path is surfaced as
    `error.field` for client convenience.
    """
    errors = exc.errors()
    field: str | None = None
    message = "Request validation failed."

    if errors:
        first = errors[0]
        loc = first.get("loc", ())
        # Drop the source prefix ("body", "query", etc.) — clients only care
        # about the user-facing field path.
        loc_parts = [str(p) for p in loc if p not in _VALIDATION_LOC_PREFIXES]
        if loc_parts:
            field = ".".join(loc_parts)
        message = first.get("msg", message)

    return _envelope_error(
        status_code=400, code="INVALID_INPUT", message=message, field=field
    )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="SafeChat API",
        version=API_VERSION,
        lifespan=lifespan,
    )

    # SEC-11: only trust arbitrary localhost origins outside production. In
    # production the explicit cors_origins allowlist is the sole authority —
    # a deployed API has no reason to accept requests from localhost pages.
    localhost_regex = None if settings.is_production else r"http://localhost(:\d+)?"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=localhost_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # PR-03: correlate logs with a per-request id (reused from an inbound
    # X-Request-ID when the client/proxy sets one). Registered before the
    # rate limiter so even rate-limited responses carry the id.
    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming else new_request_id()
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response

    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)

    # API-01: per-endpoint-group rate limits (API_CONTRACTS.md §14). Off in
    # development (and therefore in the test suite) unless forced via
    # RATE_LIMIT_ENABLED — see Settings.rate_limit_effective.
    if settings.rate_limit_effective:
        install_rate_limiter(app)

    app.include_router(health.router, prefix=API_V1_PREFIX)
    app.include_router(auth_routes.router, prefix=API_V1_PREFIX)
    app.include_router(admin_routes.router, prefix=API_V1_PREFIX)
    app.include_router(moderation_routes.router, prefix=API_V1_PREFIX)
    app.include_router(notifications_routes.router, prefix=API_V1_PREFIX)
    app.include_router(safety_routes.router, prefix=API_V1_PREFIX)
    app.include_router(users_routes.router, prefix=API_V1_PREFIX)
    app.include_router(posts_routes.router, prefix=API_V1_PREFIX)
    app.include_router(stories_routes.router, prefix=API_V1_PREFIX)
    app.include_router(uploads_routes.router, prefix=API_V1_PREFIX)
    app.include_router(reports_routes.router, prefix=API_V1_PREFIX)
    app.include_router(messages_routes.router, prefix=API_V1_PREFIX)

    return app


app = create_app()
