# backend/moderation/engine.py
"""Unified moderation engine — orchestrates the cascade layers.

Cascade order (text):
  0. Empty / whitespace short-circuit — no work, no API calls.
  1. Lexicon (Layer 1): weighted keyword scorer, in-process. Returns exact
     match spans for client-side highlighting. Reported as ``layer="keyword"``.
  2. TF-IDF model (Layer 2): trained scikit-learn classifier for contextual
     toxicity the lexicon misses. Reported as ``layer="tfidf"``. Fail-open —
     contributes nothing when the artifact is absent.
  3. OpenAI Moderation API (upcoming): runs only if an API key is configured.

First block wins; downstream layers are skipped to save latency and API spend.
Latency is recorded per-layer and overall. The triggering layer's ``matches``
(lexicon spans) and scores ride along on the result so callers can both block
and highlight.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from core.config import get_settings
from models.moderation import ModerationResult
from moderation import lexicon, tfidf_model
from moderation.openai_moderation import check_with_openai
from moderation.vision import check_image_with_vision

logger = logging.getLogger(__name__)

# HC-06: the Layer-2 flag threshold is a tuning parameter, configurable via
# TFIDF_FLAG_THRESHOLD (see core/config.py). Lower it to catch more at the
# cost of false positives; the human-verification flow is the safety valve
# for anything wrongly caught. Grow moderation/data/seed_corpus.csv and
# retrain to sharpen the model itself.


def hash_content(text: str) -> str:
    """SHA-256 hex digest of the original text — used in moderation logs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


async def moderate_text(text: str) -> ModerationResult:
    """Run text through the moderation cascade.

    Returns a :class:`ModerationResult` with ``blocked``, the triggering
    ``layer``, lexicon ``matches`` (character spans for highlighting), layer
    scores, and timing metadata. Empty / whitespace-only text passes without
    invoking any cascade layer.
    """
    start = _now_ms()
    content_hash = hash_content(text)
    layer_latencies: dict[str, float] = {}

    if not text or not text.strip():
        return ModerationResult(
            blocked=False,
            latency_ms=_now_ms() - start,
            layer_latencies=layer_latencies,
            content_hash=content_hash,
        )

    # ---- Layer 1: weighted lexicon (in-process, no I/O) -------------------
    layer_start = _now_ms()
    lex = lexicon.evaluate(text)
    layer_latencies["keyword"] = _now_ms() - layer_start

    if lex.blocked:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category=lex.category,
            reason=f"keyword match: {lex.matched_word}",
            matches=lex.matches,
            lexicon_score=lex.score,
            latency_ms=_now_ms() - start,
            layer_latencies=layer_latencies,
            content_hash=content_hash,
        )

    # ---- Layer 2: TF-IDF classifier (server-side, fail-open) --------------
    layer_start = _now_ms()
    tfidf = tfidf_model.score(text)
    layer_latencies["tfidf"] = _now_ms() - layer_start

    if tfidf is not None and tfidf >= get_settings().tfidf_flag_threshold:
        return ModerationResult(
            blocked=True,
            layer="tfidf",
            category="toxicity",
            reason=f"tfidf score {tfidf:.2f}",
            matches=lex.matches,
            lexicon_score=lex.score,
            tfidf_score=tfidf,
            latency_ms=_now_ms() - start,
            layer_latencies=layer_latencies,
            content_hash=content_hash,
        )

    # ---- Layer 3: OpenAI Moderation API -----------------------------------
    layer_start = _now_ms()
    openai_verdict = await check_with_openai(text)
    layer_latencies["openai"] = _now_ms() - layer_start

    if openai_verdict.blocked:
        score = openai_verdict.score if openai_verdict.score is not None else 0.0
        return ModerationResult(
            blocked=True,
            layer="openai",
            category=openai_verdict.category,
            reason=f"openai score {score:.2f}",
            matches=lex.matches,
            lexicon_score=lex.score,
            tfidf_score=tfidf,
            latency_ms=_now_ms() - start,
            layer_latencies=layer_latencies,
            content_hash=content_hash,
        )

    return ModerationResult(
        blocked=False,
        matches=lex.matches,
        lexicon_score=lex.score,
        tfidf_score=tfidf,
        latency_ms=_now_ms() - start,
        layer_latencies=layer_latencies,
        content_hash=content_hash,
    )


async def moderate_image(image_url: str) -> ModerationResult:
    """Run an image URL through the Vision SafeSearch layer.

    Returns a ``ModerationResult`` with ``layer="vision"`` when blocked.
    Errors from the Vision API are fail-open: ``blocked=False`` is returned so
    a transient failure never prevents content from being stored.

    Args:
        image_url: Publicly reachable URL of the image to screen.
    """
    start = _now_ms()
    content_hash = hash_content(image_url)
    layer_latencies: dict[str, float] = {}

    layer_start = _now_ms()
    vision_verdict = await check_image_with_vision(image_url)
    layer_latencies["vision"] = _now_ms() - layer_start

    if vision_verdict.blocked:
        return ModerationResult(
            blocked=True,
            layer="vision",
            category=vision_verdict.category,
            reason=f"vision safeSearch: {vision_verdict.category}",
            latency_ms=_now_ms() - start,
            layer_latencies=layer_latencies,
            content_hash=content_hash,
        )

    return ModerationResult(
        blocked=False,
        latency_ms=_now_ms() - start,
        layer_latencies=layer_latencies,
        content_hash=content_hash,
    )
