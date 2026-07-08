# backend/middleware/rate_limit.py
"""In-process sliding-window rate limiting (API-01).

Implements the per-endpoint-group limits documented in
docs/API_CONTRACTS.md §14. Exceeded limits return
``429 RATE_LIMITED`` in the standard error envelope with a ``Retry-After``
header.

Design notes
------------
- **In-process by design.** The Cloud Run deployment runs a single gunicorn
  worker (see backend/Dockerfile), so one process sees all of an instance's
  traffic. With N autoscaled instances the effective limit is up to N x the
  configured value — acceptable for abuse mitigation; a shared store (Redis)
  is the upgrade path if precise global limits are ever required.
- **Keying.** Authenticated requests are keyed by a SHA-256 prefix of the
  bearer token (no signature verification here — auth middleware does that
  work once, later in the stack). Firebase ID tokens rotate roughly hourly,
  which resets the caller's buckets; that is fine for burst protection.
  Unauthenticated requests fall back to the client IP.
- **No locking.** All mutation happens on the event loop; Starlette
  middleware never runs this code from worker threads.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

_MINUTE = 60.0
_HOUR = 3600.0
_DAY = 86400.0


@dataclass(frozen=True)
class Rule:
    """One endpoint-group limit: first matching rule wins."""

    name: str
    methods: frozenset[str]
    pattern: re.Pattern[str]
    limit: int
    window_seconds: float
    per_ip: bool = False  # key by client IP instead of bearer token


# Contract limits (docs/API_CONTRACTS.md §14), most specific first.
DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        name="auth",
        methods=frozenset({"GET", "POST", "PATCH"}),
        pattern=re.compile(r"^/api/v1/auth(/|$)"),
        limit=10,
        window_seconds=_MINUTE,
        per_ip=True,
    ),
    Rule(
        name="messages-send",
        methods=frozenset({"POST"}),
        pattern=re.compile(r"^/api/v1/chats/[^/]+/messages$"),
        limit=30,
        window_seconds=_MINUTE,
    ),
    Rule(
        name="posts-create",
        methods=frozenset({"POST"}),
        pattern=re.compile(r"^/api/v1/posts$"),
        limit=10,
        window_seconds=_HOUR,
    ),
    Rule(
        name="comments-create",
        methods=frozenset({"POST"}),
        pattern=re.compile(r"^/api/v1/posts/[^/]+/comments$"),
        limit=30,
        window_seconds=_HOUR,
    ),
    Rule(
        name="reports-create",
        methods=frozenset({"POST"}),
        pattern=re.compile(r"^/api/v1/reports$"),
        limit=20,
        window_seconds=_DAY,
    ),
    Rule(
        name="default",
        methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"}),
        pattern=re.compile(r"^/api/v1/(?!health$)"),  # never throttle /health
        limit=600,
        window_seconds=_MINUTE,
    ),
)

# Buckets are pruned opportunistically; this cap bounds memory if a bucket's
# window is long (e.g. reports @ 24h) under sustained abuse.
_MAX_EVENTS_PER_BUCKET = 2048


@dataclass
class SlidingWindowLimiter:
    """Pure limiter logic — separated from the middleware for unit testing."""

    rules: tuple[Rule, ...] = DEFAULT_RULES
    _buckets: dict[tuple[str, str], deque[float]] = field(default_factory=dict)

    def match(self, method: str, path: str) -> Rule | None:
        for rule in self.rules:
            if method in rule.methods and rule.pattern.match(path):
                return rule
        return None

    def check(self, rule: Rule, key: str, now: float | None = None) -> float | None:
        """Record one hit. Returns None if allowed, or seconds-until-retry."""
        now = time.monotonic() if now is None else now
        bucket = self._buckets.setdefault((rule.name, key), deque())

        cutoff = now - rule.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= rule.limit:
            return bucket[0] + rule.window_seconds - now

        bucket.append(now)
        if len(bucket) > _MAX_EVENTS_PER_BUCKET:
            bucket.popleft()
        return None


def _caller_key(request: Request, rule: Rule) -> str:
    if not rule.per_ip:
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer ") and len(authorization) > 7:
            digest = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()
            return f"tok:{digest[:16]}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def _rate_limited_response(retry_after_seconds: float) -> JSONResponse:
    retry_after = max(1, math.ceil(retry_after_seconds))
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(retry_after)},
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Too many requests. Slow down and try again shortly.",
            },
            "meta": {
                "request_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
    )


def install_rate_limiter(app: FastAPI, limiter: SlidingWindowLimiter | None = None) -> None:
    """Attach the rate-limit middleware to ``app``."""
    active = limiter or SlidingWindowLimiter()

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        rule = active.match(request.method, request.url.path)
        if rule is None:
            return await call_next(request)

        retry_in = active.check(rule, _caller_key(request, rule))
        if retry_in is not None:
            return _rate_limited_response(retry_in)

        response: Response = await call_next(request)
        return response
