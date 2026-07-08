# backend/models/moderation.py
"""Moderation-related Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QueueStatus = Literal["pending_review", "approved", "rejected"]
ContentType = Literal["post", "comment", "message", "story"]


class Match(BaseModel):
    """A single lexicon hit with its location in the original text.

    ``start``/``end`` are character offsets into the *original* text so the
    client can highlight the exact offending span. They are best-effort: ``-1``
    means the term was detected but could not be located as a contiguous span
    (e.g. via heavy obfuscation), so it is flagged but not highlightable.
    """

    term: str
    category: str
    weight: float = 0.0
    start: int = -1
    end: int = -1


class LexiconVerdict(BaseModel):
    """Result of running text through the weighted-lexicon layer (Layer 1)."""

    blocked: bool
    score: float = 0.0
    matches: list[Match] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    # Convenience summaries for the cascade reason string / moderation logs.
    category: str | None = None
    matched_word: str | None = None


class OpenAIVerdict(BaseModel):
    """Result of running text through the OpenAI Moderation API."""

    blocked: bool
    category: str | None = None
    score: float | None = None
    skipped: bool = False  # API key not configured
    error: bool = False  # HTTP/timeout/parse failure
    all_scores: dict[str, float] | None = None


class VisionVerdict(BaseModel):
    """Result of running an image through Google Cloud Vision SafeSearch."""

    blocked: bool
    category: str | None = None  # "adult" | "violence" | "racy"
    skipped: bool = False  # Vision not configured / disabled
    error: bool = False  # API call failed (fail-open)


class ModerationResult(BaseModel):
    """Unified moderation result across all cascade layers."""

    blocked: bool
    layer: str | None = None  # "keyword" | "tfidf" | "openai" | "gemini" | "vision"
    category: str | None = None  # e.g. "english_slurs", "toxicity", "harassment"
    reason: str | None = None  # human-readable description for logs

    # Exact spans of lexicon hits in the original text, for client highlighting.
    matches: list[Match] = Field(default_factory=list)
    lexicon_score: float | None = None  # Layer 1 aggregate (tf x severity weight)
    tfidf_score: float | None = None  # Layer 2 toxicity probability (0..1)

    latency_ms: float | None = None
    layer_latencies: dict[str, float] | None = None
    content_hash: str | None = None  # SHA-256 hex digest of the original text


class ModerationQueueItem(BaseModel):
    """A piece of content awaiting (or having undergone) human verification.

    Written when a user submits flagged content "for human verification". Read
    by admins (the review queue) and by the author (Profile -> Appeals). The
    underlying post/comment/message document carries the same status.
    """

    id: str
    content_type: ContentType
    content_id: str
    post_id: str | None = None  # set for comments (parent post)
    chat_id: str | None = None  # set for messages (parent chat)
    author_uid: str
    author_username: str = "unknown"
    text: str
    matches: list[Match] = Field(default_factory=list)
    flagged_terms: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    lexicon_score: float | None = None
    tfidf_score: float | None = None
    layer: str | None = None
    status: QueueStatus = "pending_review"
    reason: str | None = None  # rejection reason, shown to the author
    note: str | None = None  # optional note the author added when submitting
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
