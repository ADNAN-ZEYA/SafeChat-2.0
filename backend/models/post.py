# backend/models/post.py
"""Post data models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Post(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    author_uid: str
    author_username: str = "unknown"
    author_display_name: str = "Anonymous"
    author_photo_url: str = ""
    text: str
    image_url: str | None = None
    media_urls: list[str] = Field(default_factory=list)
    media_type: str = "text"
    status: Literal["approved", "rejected", "pending_review"] = "approved"
    moderation_layer: str | None = None
    moderation_reason: str | None = None
    rejection_reason: str | None = None  # human-readable reason shown to author
    flagged_terms: list[str] = Field(default_factory=list)
    like_count: int = 0
    comment_count: int = 0
    view_count: int = 0
    created_at: datetime
    updated_at: datetime
    schema_version: int = 1


class CreatePostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    media_urls: list[str] = Field(..., min_length=1, max_length=10)
    media_type: str = "image"
    # When True, knowingly submit flagged content for human verification
    # (stored as pending_review) instead of being rejected with a 422.
    submit_for_review: bool = False
