# backend/tests/test_engine.py
"""Tests for moderation.engine — the hybrid cascade orchestrator."""

from __future__ import annotations

import hashlib

import pytest

from models.moderation import LexiconVerdict, Match, OpenAIVerdict
from moderation import engine
from moderation.engine import moderate_text


@pytest.fixture(autouse=True)
def _default_layers_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default all three layers to clean; individual tests override as needed."""
    monkeypatch.setattr(engine.lexicon, "evaluate", lambda text: LexiconVerdict(blocked=False))
    monkeypatch.setattr(engine.tfidf_model, "score", lambda text: None)

    async def fake_openai(text: str) -> OpenAIVerdict:
        return OpenAIVerdict(blocked=False)

    monkeypatch.setattr(engine, "check_with_openai", fake_openai)


@pytest.mark.asyncio
async def test_lexicon_block_short_circuits_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = [Match(term="idiot", category="english_slurs", weight=0.5, start=8, end=13)]
    monkeypatch.setattr(
        engine.lexicon,
        "evaluate",
        lambda text: LexiconVerdict(
            blocked=True,
            score=0.5,
            matches=matches,
            categories=["english_slurs"],
            category="english_slurs",
            matched_word="idiot",
        ),
    )

    calls = {"tfidf": 0, "openai": 0}

    def fake_tfidf(text: str) -> float:
        calls["tfidf"] += 1
        return 0.99

    async def fake_openai(text: str) -> OpenAIVerdict:
        calls["openai"] += 1
        return OpenAIVerdict(blocked=True, category="hate", score=0.9)

    monkeypatch.setattr(engine.tfidf_model, "score", fake_tfidf)
    monkeypatch.setattr(engine, "check_with_openai", fake_openai)

    result = await moderate_text("you are idiot")

    assert result.blocked is True
    assert result.layer == "keyword"
    assert result.category == "english_slurs"
    assert "idiot" in (result.reason or "")
    assert result.matches == matches
    assert result.lexicon_score == 0.5
    assert calls == {"tfidf": 0, "openai": 0}
    assert "tfidf" not in (result.layer_latencies or {})
    assert "openai" not in (result.layer_latencies or {})


@pytest.mark.asyncio
async def test_tfidf_block_when_lexicon_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine.tfidf_model, "score", lambda text: 0.95)

    openai_calls = {"n": 0}

    async def fake_openai(text: str) -> OpenAIVerdict:
        openai_calls["n"] += 1
        return OpenAIVerdict(blocked=True)

    monkeypatch.setattr(engine, "check_with_openai", fake_openai)

    result = await moderate_text("subtle but mean")

    assert result.blocked is True
    assert result.layer == "tfidf"
    assert result.category == "toxicity"
    assert result.tfidf_score == 0.95
    assert "0.95" in (result.reason or "")
    assert openai_calls["n"] == 0


@pytest.mark.asyncio
async def test_tfidf_below_threshold_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    below = engine.get_settings().tfidf_flag_threshold - 0.01
    monkeypatch.setattr(engine.tfidf_model, "score", lambda text: below)

    result = await moderate_text("mildly spicy but fine")

    assert result.blocked is False
    assert result.tfidf_score == below


@pytest.mark.asyncio
async def test_openai_block_when_others_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_openai(text: str) -> OpenAIVerdict:
        return OpenAIVerdict(blocked=True, category="hate", score=0.92)

    monkeypatch.setattr(engine, "check_with_openai", fake_openai)

    result = await moderate_text("hateful but not keyworded")

    assert result.blocked is True
    assert result.layer == "openai"
    assert result.category == "hate"
    assert "0.92" in (result.reason or "")


@pytest.mark.asyncio
async def test_all_layers_clean() -> None:
    result = await moderate_text("hello world")

    assert result.blocked is False
    assert result.layer is None
    assert result.category is None
    assert result.reason is None
    assert result.matches == []


@pytest.mark.asyncio
async def test_empty_text_skips_all_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"lex": 0, "tfidf": 0, "openai": 0}

    def fake_lex(text: str) -> LexiconVerdict:
        calls["lex"] += 1
        return LexiconVerdict(blocked=False)

    def fake_tfidf(text: str) -> None:
        calls["tfidf"] += 1
        return None

    async def fake_openai(text: str) -> OpenAIVerdict:
        calls["openai"] += 1
        return OpenAIVerdict(blocked=False)

    monkeypatch.setattr(engine.lexicon, "evaluate", fake_lex)
    monkeypatch.setattr(engine.tfidf_model, "score", fake_tfidf)
    monkeypatch.setattr(engine, "check_with_openai", fake_openai)

    for text in ["", "   ", "\n\t"]:
        result = await moderate_text(text)
        assert result.blocked is False

    assert calls == {"lex": 0, "tfidf": 0, "openai": 0}


@pytest.mark.asyncio
async def test_latency_ms_populated() -> None:
    result = await moderate_text("any text")
    assert result.latency_ms is not None
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_content_hash_matches_sha256() -> None:
    text = "hash me please"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result = await moderate_text(text)
    assert result.content_hash == expected


@pytest.mark.asyncio
async def test_content_hash_populated_for_empty_text() -> None:
    expected = hashlib.sha256(b"").hexdigest()
    result = await moderate_text("")
    assert result.content_hash == expected


@pytest.mark.asyncio
async def test_layer_latencies_recorded_when_all_run() -> None:
    result = await moderate_text("clean text")
    latencies = result.layer_latencies or {}
    assert "keyword" in latencies
    assert "tfidf" in latencies
    assert "openai" in latencies


@pytest.mark.asyncio
async def test_layer_latencies_stop_at_keyword_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine.lexicon,
        "evaluate",
        lambda text: LexiconVerdict(
            blocked=True,
            category="threats",
            matched_word="kill you",
            matches=[Match(term="kill you", category="threats", weight=1.0, start=0, end=8)],
        ),
    )

    result = await moderate_text("kill you")

    latencies = result.layer_latencies or {}
    assert "keyword" in latencies
    assert "tfidf" not in latencies
    assert "openai" not in latencies
