# backend/moderation/openai_moderation.py
"""OpenAI Moderation API — text moderation layer of the cascade.

Uses the omni-moderation-latest model. Direct HTTP via httpx so we don't pull
in the full openai SDK for one endpoint. Fail-open: timeouts, HTTP errors,
and missing API key all return non-blocking verdicts — the cascade has other
layers that compensate.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import get_settings
from models.moderation import OpenAIVerdict

logger = logging.getLogger(__name__)

OPENAI_MODERATION_URL = "https://api.openai.com/v1/moderations"

# Block when category_scores[category] >= threshold. Each value tuned per
# severity — sexual/minors is near-zero by design.
_THRESHOLDS: dict[str, float] = {
    "hate": 0.7,
    "hate/threatening": 0.5,
    "harassment": 0.6,
    "harassment/threatening": 0.5,
    "self-harm": 0.5,
    "self-harm/intent": 0.4,
    "sexual": 0.7,
    "sexual/minors": 0.3,
    "violence": 0.7,
    "violence/graphic": 0.6,
}


async def _post_moderation(
    *, api_key: str, payload: dict[str, Any]
) -> httpx.Response:
    """Thin wrapper around the HTTP POST so tests can patch a single seam."""
    async with httpx.AsyncClient(
        timeout=get_settings().openai_timeout_seconds
    ) as client:
        return await client.post(
            OPENAI_MODERATION_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )


async def check_with_openai(text: str) -> OpenAIVerdict:
    """Run text through the OpenAI Moderation API.

    Returns OpenAIVerdict(skipped=True) when no API key is configured, or
    OpenAIVerdict(error=True) on timeout / HTTP failure / unparseable response.
    In both cases blocked=False — defense in depth lives in the other layers.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return OpenAIVerdict(blocked=False, skipped=True)

    if not text or not text.strip():
        return OpenAIVerdict(blocked=False)

    payload = {"model": settings.openai_moderation_model, "input": text}
    try:
        response = await _post_moderation(
            api_key=settings.openai_api_key, payload=payload
        )
    except httpx.TimeoutException:
        logger.warning(
            "OpenAI moderation timed out (%.1fs)", get_settings().openai_timeout_seconds
        )
        return OpenAIVerdict(blocked=False, error=True)
    except httpx.HTTPError as exc:
        logger.warning("OpenAI moderation HTTP error: %s", exc)
        return OpenAIVerdict(blocked=False, error=True)

    if response.status_code != 200:
        # 429 (rate limit) lands here too — we surface as error and let the
        # caller decide; no in-layer retry.
        logger.warning(
            "OpenAI moderation returned %d: %s",
            response.status_code,
            (response.text or "(no body)")[:200],
        )
        return OpenAIVerdict(blocked=False, error=True)

    try:
        body = response.json()
    except ValueError:
        logger.warning("OpenAI moderation returned non-JSON body")
        return OpenAIVerdict(blocked=False, error=True)

    results = body.get("results") or []
    if not results:
        logger.warning("OpenAI moderation response had no results")
        return OpenAIVerdict(blocked=False, error=True)

    raw_scores = results[0].get("category_scores") or {}
    try:
        all_scores = {k: float(v) for k, v in raw_scores.items()}
    except (TypeError, ValueError):
        logger.warning("OpenAI moderation returned non-numeric scores")
        return OpenAIVerdict(blocked=False, error=True)

    # Highest-scoring violation wins (most signal for logs/admin review).
    triggered_category: str | None = None
    triggered_score: float = -1.0
    for category, threshold in _THRESHOLDS.items():
        score = all_scores.get(category, 0.0)
        if score >= threshold and score > triggered_score:
            triggered_category = category
            triggered_score = score

    if triggered_category is not None:
        return OpenAIVerdict(
            blocked=True,
            category=triggered_category,
            score=triggered_score,
            all_scores=all_scores,
        )

    return OpenAIVerdict(blocked=False, all_scores=all_scores)
