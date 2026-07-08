# backend/tests/test_openai_moderation.py
"""Tests for moderation.openai_moderation."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from moderation import openai_moderation
from moderation.openai_moderation import check_with_openai


class _FakeSettings:
    def __init__(self, api_key: str | None) -> None:
        self.openai_api_key = api_key
        # HC-05: model + timeout are now settings-driven.
        self.openai_moderation_model = "omni-moderation-latest"
        self.openai_timeout_seconds = 3.0


def _patch_settings(monkeypatch: pytest.MonkeyPatch, api_key: str | None) -> None:
    monkeypatch.setattr(
        openai_moderation, "get_settings", lambda: _FakeSettings(api_key)
    )


def _mock_response(status_code: int, body: dict[str, Any] | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = "" if body is None else str(body)
    response.json.return_value = body or {}
    return response


@pytest.mark.asyncio
async def test_returns_blocked_when_score_exceeds_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        return _mock_response(
            200,
            {"results": [{"category_scores": {"hate": 0.9, "violence": 0.1}}]},
        )

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("hateful text")

    assert verdict.blocked is True
    assert verdict.category == "hate"
    assert verdict.score == pytest.approx(0.9)
    assert verdict.error is False
    assert verdict.skipped is False


@pytest.mark.asyncio
async def test_returns_clean_when_all_below_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        return _mock_response(
            200,
            {"results": [{"category_scores": {"hate": 0.1, "violence": 0.05}}]},
        )

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("hello world")

    assert verdict.blocked is False
    assert verdict.category is None
    assert verdict.error is False


@pytest.mark.asyncio
async def test_skipped_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, None)
    verdict = await check_with_openai("anything")
    assert verdict.skipped is True
    assert verdict.blocked is False


@pytest.mark.asyncio
async def test_error_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("anything")
    assert verdict.error is True
    assert verdict.blocked is False


@pytest.mark.asyncio
async def test_error_on_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        return _mock_response(500, {})

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("anything")
    assert verdict.error is True
    assert verdict.blocked is False


@pytest.mark.asyncio
async def test_error_on_http_429_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 surfaces as error=True; this layer does not retry."""
    _patch_settings(monkeypatch, "sk-test")
    call_count = {"n": 0}

    async def fake_post(**kwargs: Any) -> MagicMock:
        call_count["n"] += 1
        return _mock_response(429, {})

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("anything")
    assert verdict.error is True
    assert verdict.blocked is False
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_request_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "sk-test-key")
    captured: dict[str, Any] = {}

    async def fake_post(*, api_key: str, payload: dict[str, Any]) -> MagicMock:
        captured["api_key"] = api_key
        captured["payload"] = payload
        return _mock_response(200, {"results": [{"category_scores": {}}]})

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    await check_with_openai("the input text")

    assert captured["api_key"] == "sk-test-key"
    assert captured["payload"] == {
        "model": "omni-moderation-latest",
        "input": "the input text",
    }


@pytest.mark.asyncio
async def test_flagged_field_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """We rely on our own thresholds, not OpenAI's `flagged` boolean."""
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        return _mock_response(
            200,
            {
                "results": [
                    {
                        "flagged": True,                  # would imply blocked
                        "category_scores": {"hate": 0.1}, # but threshold is 0.7
                    }
                ]
            },
        )

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("borderline")
    assert verdict.blocked is False


@pytest.mark.asyncio
async def test_highest_violation_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "sk-test")

    async def fake_post(**kwargs: Any) -> MagicMock:
        return _mock_response(
            200,
            {
                "results": [
                    {
                        "category_scores": {
                            "hate": 0.75,         # exceeds 0.7
                            "harassment": 0.95,   # exceeds 0.6, higher
                            "violence": 0.5,      # below 0.7
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    verdict = await check_with_openai("multi-category bad text")
    assert verdict.blocked is True
    assert verdict.category == "harassment"
    assert verdict.score == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_empty_text_skips_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "sk-test")
    call_count = {"n": 0}

    async def fake_post(**kwargs: Any) -> MagicMock:
        call_count["n"] += 1
        return _mock_response(200, {"results": [{"category_scores": {}}]})

    monkeypatch.setattr(openai_moderation, "_post_moderation", fake_post)

    assert (await check_with_openai("")).blocked is False
    assert (await check_with_openai("   ")).blocked is False
    assert call_count["n"] == 0
