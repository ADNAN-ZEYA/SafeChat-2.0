# backend/models/message.py
"""Direct-message data models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    chat_id: str
    sender_uid: str
    text: str
    image_url: str | None = None
    media_type: Literal["text", "image", "video", "doc", "link"] = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: Literal["approved", "pending_review", "rejected"] = "approved"
    moderation_layer: str | None = None
    moderation_reason: str | None = None
    rejection_reason: str | None = None
    flagged_terms: list[str] = Field(default_factory=list)
    # True when `text` is client-side E2E ciphertext (sent while the chat's
    # encryption_mode was "trusted"). Recorded per-message — not derived from
    # the chat's *current* encryption_mode — so history stays correct even
    # after the chat toggles modes later (see AGENT.md / DATABASE_SCHEMA.md).
    encrypted: bool = False
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    schema_version: int = 1


class Chat(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    participants: list[str]
    last_message_text: str | None = None
    last_message_at: datetime | None = None
    # Per-participant unread badge counters: {uid: count}. Incremented when a
    # message is DELIVERED to that uid; reset via POST /chats/{id}/read.
    unread_counts: dict[str, int] = Field(default_factory=dict)
    # "pending" = SafeChat Mode ON (unencrypted, moderated).
    # "trusted" = SafeChat Mode OFF (E2E encrypted, unmoderated).
    encryption_mode: Literal["pending", "trusted"] = "pending"
    created_at: datetime
    updated_at: datetime
    schema_version: int = 1


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    image_url: str | None = None
    media_type: Literal["text", "image", "video", "doc", "link"] = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)
    # When True, submit flagged content for human verification (pending_review).
    submit_for_review: bool = False


class EncryptionModeRequest(BaseModel):
    mode: Literal["pending", "trusted"]
