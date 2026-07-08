# backend/tests/test_rate_limit.py
"""Tests for middleware/rate_limit.py (API-01)."""

from __future__ import annotations

from middleware.rate_limit import DEFAULT_RULES, SlidingWindowLimiter

# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def test_match_auth_routes_by_ip() -> None:
    limiter = SlidingWindowLimiter()
    rule = limiter.match("POST", "/api/v1/auth/onboard")
    assert rule is not None
    assert rule.name == "auth"
    assert rule.per_ip is True


def test_match_message_send() -> None:
    limiter = SlidingWindowLimiter()
    rule = limiter.match("POST", "/api/v1/chats/uid-1_uid-2/messages")
    assert rule is not None
    assert rule.name == "messages-send"
    assert rule.limit == 30


def test_match_post_create_vs_comments() -> None:
    limiter = SlidingWindowLimiter()
    posts = limiter.match("POST", "/api/v1/posts")
    comments = limiter.match("POST", "/api/v1/posts/abc/comments")
    assert posts is not None and posts.name == "posts-create"
    assert comments is not None and comments.name == "comments-create"


def test_match_reports() -> None:
    limiter = SlidingWindowLimiter()
    rule = limiter.match("POST", "/api/v1/reports")
    assert rule is not None
    assert rule.name == "reports-create"
    assert rule.window_seconds == 86400.0


def test_get_requests_fall_through_to_default() -> None:
    limiter = SlidingWindowLimiter()
    rule = limiter.match("GET", "/api/v1/posts/feed")
    assert rule is not None
    assert rule.name == "default"
    assert rule.limit == 600


def test_health_is_never_throttled() -> None:
    limiter = SlidingWindowLimiter()
    assert limiter.match("GET", "/api/v1/health") is None


def test_non_api_paths_unmatched() -> None:
    limiter = SlidingWindowLimiter()
    assert limiter.match("GET", "/docs") is None


# ---------------------------------------------------------------------------
# Sliding-window behavior
# ---------------------------------------------------------------------------


def test_allows_up_to_limit_then_blocks() -> None:
    limiter = SlidingWindowLimiter()
    rule = next(r for r in DEFAULT_RULES if r.name == "posts-create")  # 10/hour

    now = 1000.0
    for i in range(rule.limit):
        assert limiter.check(rule, "tok:a", now=now + i) is None

    retry_in = limiter.check(rule, "tok:a", now=now + rule.limit)
    assert retry_in is not None
    assert retry_in > 0


def test_window_slides_and_recovers() -> None:
    limiter = SlidingWindowLimiter()
    rule = next(r for r in DEFAULT_RULES if r.name == "messages-send")  # 30/min

    now = 5000.0
    for i in range(rule.limit):
        assert limiter.check(rule, "tok:b", now=now + i * 0.1) is None
    assert limiter.check(rule, "tok:b", now=now + 5) is not None

    # After the window has fully passed, the caller is allowed again.
    assert limiter.check(rule, "tok:b", now=now + rule.window_seconds + 10) is None


def test_keys_are_isolated() -> None:
    limiter = SlidingWindowLimiter()
    rule = next(r for r in DEFAULT_RULES if r.name == "posts-create")

    now = 0.0
    for i in range(rule.limit):
        assert limiter.check(rule, "tok:user1", now=now + i) is None
    # user1 exhausted; user2 unaffected.
    assert limiter.check(rule, "tok:user1", now=now + rule.limit) is not None
    assert limiter.check(rule, "tok:user2", now=now + rule.limit) is None


def test_retry_after_reflects_oldest_event() -> None:
    limiter = SlidingWindowLimiter()
    rule = next(r for r in DEFAULT_RULES if r.name == "auth")  # 10/min

    start = 100.0
    for i in range(rule.limit):
        assert limiter.check(rule, "ip:1.2.3.4", now=start + i) is None

    retry_in = limiter.check(rule, "ip:1.2.3.4", now=start + 30)
    assert retry_in is not None
    # Oldest event at t=100, window 60s -> free at t=160 -> ~30s from t=130.
    assert 29.0 <= retry_in <= 31.0
