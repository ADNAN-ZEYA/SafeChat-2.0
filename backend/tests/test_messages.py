# backend/tests/test_messages.py
"""Tests for the DM system — service layer and API endpoints."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore import SERVER_TIMESTAMP as _SERVER_TIMESTAMP

from main import app
from middleware.auth import get_current_user_claims
from models.message import Chat, Message
from models.moderation import Match, ModerationResult
from services import follows as follows_service
from services import messages as messages_service

# --------------------------------------------------------------------------
# In-memory Firestore fake with subcollection, query, and batch support
# (Same design as test_comments.py — extended with chats/messages helpers.)
# --------------------------------------------------------------------------


def _apply_transform(current: Any, value: Any) -> Any:
    """Resolve SERVER_TIMESTAMP to a real datetime; pass everything else through."""
    if value is _SERVER_TIMESTAMP:
        return datetime.now(UTC)
    if type(value).__name__ == "Increment" and hasattr(value, "value"):
        return max(0, int(current or 0) + int(value.value))
    return value


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeQuery:
    """Chainable query — applies sort and limit at stream() time."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self._order_field: str | None = None
        self._order_desc: bool = False
        self._limit_n: int | None = None

    def order_by(self, field: str, direction: Any = None) -> _FakeQuery:
        self._order_field = field
        if direction is not None:
            self._order_desc = str(direction) == "DESCENDING"
        return self

    def where(self, filter: Any = None, **kwargs: Any) -> _FakeQuery:  # noqa: A002
        # Filtering is not exercised in these unit tests — seeded data is
        # already scoped to the right collection path.
        return self

    def limit(self, n: int) -> _FakeQuery:
        self._limit_n = n
        return self

    def stream(self) -> list[_FakeSnapshot]:
        items = list(self._store.items())
        if self._order_field:
            items.sort(
                key=lambda kv: kv[1].get(self._order_field) or datetime.min.replace(tzinfo=UTC),
                reverse=self._order_desc,
            )
        snaps = [_FakeSnapshot(k, v) for k, v in items]
        if self._limit_n is not None:
            snaps = snaps[: self._limit_n]
        return snaps


class _FakeDocRef:
    def __init__(
        self,
        store: dict[str, Any],
        doc_id: str,
        path: str,
        db: _FakeDB,
    ) -> None:
        self._store = store
        self.id = doc_id
        self._path = path
        self._db = db

    def collection(self, name: str) -> _FakeCollection:
        subcoll_path = f"{self._path}/{self.id}/{name}"
        return self._db.collection(subcoll_path)

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.id] = {k: _apply_transform(None, v) for k, v in data.items()}

    def update(self, data: dict[str, Any]) -> None:
        row = dict(self._store.get(self.id, {}))
        for k, v in data.items():
            row[k] = _apply_transform(row.get(k), v)
        self._store[self.id] = row

    def delete(self) -> None:
        self._store.pop(self.id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, Any], path: str, db: _FakeDB) -> None:
        self._store = store
        self._path = path
        self._db = db

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id, self._path, self._db)

    def order_by(self, field: str, direction: Any = None) -> _FakeQuery:
        return _FakeQuery(self._store).order_by(field, direction)

    def where(self, filter: Any = None, **kwargs: Any) -> _FakeQuery:  # noqa: A002
        return _FakeQuery(self._store).where(filter, **kwargs)

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._store).limit(n)

    def stream(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(k, v) for k, v in self._store.items()]

    def add(self, data: dict[str, Any]) -> tuple[None, _FakeDocRef]:
        doc_id = str(uuid.uuid4())
        ref = self.document(doc_id)
        ref.set(data)
        return (None, ref)


class _FakeBatch:
    """Mirrors the WriteBatch interface used by db.batch()."""

    def __init__(self) -> None:
        self._pending: list[tuple[str, _FakeDocRef, dict[str, Any]]] = []

    def set(self, ref: _FakeDocRef, data: dict[str, Any]) -> None:
        self._pending.append(("set", ref, dict(data)))

    def update(self, ref: _FakeDocRef, data: dict[str, Any]) -> None:
        self._pending.append(("update", ref, dict(data)))

    def delete(self, ref: _FakeDocRef) -> None:
        self._pending.append(("delete", ref, {}))

    def commit(self) -> None:
        for op, ref, data in self._pending:
            if op == "set":
                ref._store[ref.id] = {k: _apply_transform(None, v) for k, v in data.items()}
            elif op == "update":
                row = dict(ref._store.get(ref.id, {}))
                for k, v in data.items():
                    row[k] = _apply_transform(row.get(k), v)
                ref._store[ref.id] = row
            elif op == "delete":
                ref._store.pop(ref.id, None)
        self._pending.clear()


class _FakeDB:
    def __init__(self) -> None:
        self._stores: dict[str, dict[str, Any]] = {}

    def collection(self, path: str) -> _FakeCollection:
        if path not in self._stores:
            self._stores[path] = {}
        return _FakeCollection(self._stores[path], path, self)

    def batch(self) -> _FakeBatch:
        return _FakeBatch()

    @property
    def chats(self) -> dict[str, Any]:
        return self._stores.get("chats", {})

    def messages_for(self, chat_id: str) -> dict[str, Any]:
        return self._stores.get(f"chats/{chat_id}/messages", {})


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(messages_service, "db", db)
    return db


# --------------------------------------------------------------------------
# Seed helpers
# --------------------------------------------------------------------------


def _seed_chat(
    fake_db: _FakeDB,
    chat_id: str = "uid-1_uid-2",
    participants: list[str] | None = None,
    encryption_mode: str = "pending",
) -> None:
    now = datetime(2026, 5, 18, tzinfo=UTC)
    fake_db.collection("chats").document(chat_id).set(
        {
            "id": chat_id,
            "participants": participants or ["uid-1", "uid-2"],
            "last_message_text": None,
            "last_message_at": None,
            "encryption_mode": encryption_mode,
            "created_at": now,
            "updated_at": now,
            "schema_version": 1,
        }
    )


def _seed_message(
    fake_db: _FakeDB,
    chat_id: str,
    message_id: str,
    created_at: datetime,
    text: str = "Hello!",
    sender_uid: str = "uid-1",
) -> None:
    fake_db.collection(f"chats/{chat_id}/messages").document(message_id).set(
        {
            "id": message_id,
            "chat_id": chat_id,
            "sender_uid": sender_uid,
            "text": text,
            "image_url": None,
            "read_at": None,
            "created_at": created_at,
            "updated_at": created_at,
            "schema_version": 1,
        }
    )


# --------------------------------------------------------------------------
# Service-layer tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_chat_creates_chat_with_deterministic_id(
    fake_db: _FakeDB,
) -> None:
    # Passing uid-b first ensures alphabetic sorting produces the correct id.
    chat = await messages_service.get_or_create_chat("uid-b", "uid-a")

    assert isinstance(chat, Chat)
    assert chat.id == "uid-a_uid-b"
    assert sorted(chat.participants) == ["uid-a", "uid-b"]
    assert len(fake_db.chats) == 1


@pytest.mark.asyncio
async def test_get_or_create_chat_is_idempotent(fake_db: _FakeDB) -> None:
    chat_a = await messages_service.get_or_create_chat("uid-1", "uid-2")
    chat_b = await messages_service.get_or_create_chat("uid-2", "uid-1")

    assert chat_a.id == chat_b.id
    # Still exactly one document in the store.
    assert len(fake_db.chats) == 1


@pytest.mark.asyncio
async def test_cannot_message_self_raises(fake_db: _FakeDB) -> None:
    with pytest.raises(messages_service.CannotMessageSelf):
        await messages_service.get_or_create_chat("uid-1", "uid-1")


@pytest.mark.asyncio
async def test_send_message_stores_message_and_updates_chat(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    msg = await messages_service.send_message("uid-1_uid-2", "uid-1", "Hey there!")

    assert isinstance(msg, Message)
    assert msg.chat_id == "uid-1_uid-2"
    assert msg.sender_uid == "uid-1"
    assert msg.text == "Hey there!"
    assert msg.read_at is None
    assert msg.schema_version == 1
    # Message persisted in the messages subcollection.
    assert len(fake_db.messages_for("uid-1_uid-2")) == 1
    # Chat metadata updated atomically via batch.
    assert fake_db.chats["uid-1_uid-2"]["last_message_text"] == "Hey there!"
    assert fake_db.chats["uid-1_uid-2"]["last_message_at"] is not None


@pytest.mark.asyncio
async def test_send_message_blocked_raises_message_blocked(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="slurs",
            reason="keyword match",
            content_hash="h",
        )

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    with pytest.raises(messages_service.MessageBlocked) as exc_info:
        await messages_service.send_message("uid-1_uid-2", "uid-1", "toxic text")

    assert exc_info.value.layer == "keyword"
    # No message stored.
    assert len(fake_db.messages_for("uid-1_uid-2")) == 0


@pytest.mark.asyncio
async def test_send_message_submit_for_review_pending_and_queue(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="english_slurs",
            reason="keyword match: idiot",
            matches=[Match(term="idiot", category="english_slurs", weight=0.5, start=0, end=5)],
            content_hash="h",
        )

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    msg = await messages_service.send_message(
        "uid-1_uid-2", "uid-1", "idiot", submit_for_review=True
    )

    assert msg.status == "pending_review"
    # Stored, but not delivered: chat preview stays untouched.
    assert len(fake_db.messages_for("uid-1_uid-2")) == 1
    assert fake_db.chats["uid-1_uid-2"]["last_message_text"] is None
    queue = fake_db._stores.get("moderation_queue", {})
    assert len(queue) == 1
    item = next(iter(queue.values()))
    assert item["content_type"] == "message"
    assert item["chat_id"] == "uid-1_uid-2"
    assert item["status"] == "pending_review"


@pytest.mark.asyncio
async def test_send_message_non_participant_raises_not_authorized(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db, participants=["uid-1", "uid-2"])

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    with pytest.raises(messages_service.NotAuthorized):
        await messages_service.send_message("uid-1_uid-2", "uid-outsider", "Hello!")


# Structurally valid E2E payload: base64 of >= 28 bytes (12-byte nonce +
# 16-byte MAC) — mirrors what the Flutter client's encryptText() produces.
_VALID_CIPHERTEXT = base64.b64encode(b"\x01" * 40).decode("ascii")


@pytest.mark.asyncio
async def test_send_message_trusted_mode_skips_moderation(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db, encryption_mode="trusted")

    async def fake_moderate(text: str) -> ModerationResult:
        raise AssertionError("moderation must be skipped for trusted chats")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    msg = await messages_service.send_message("uid-1_uid-2", "uid-1", _VALID_CIPHERTEXT)

    assert msg.status == "approved"
    assert msg.encrypted is True
    assert msg.text == _VALID_CIPHERTEXT
    # Chat preview shows a placeholder, never the raw ciphertext.
    assert fake_db.chats["uid-1_uid-2"]["last_message_text"] != _VALID_CIPHERTEXT


@pytest.mark.asyncio
async def test_send_message_trusted_mode_rejects_plaintext(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SM-01: readable plaintext must never be stored as `encrypted: true`."""
    _seed_chat(fake_db, encryption_mode="trusted")

    async def fake_moderate(text: str) -> ModerationResult:
        raise AssertionError("moderation must be skipped for trusted chats")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    with pytest.raises(messages_service.InvalidCiphertext):
        await messages_service.send_message(
            "uid-1_uid-2", "uid-1", "hey, this is plaintext!"
        )

    # Nothing was written: no message stored, preview untouched.
    assert fake_db.messages_for("uid-1_uid-2") == {}
    assert fake_db.chats["uid-1_uid-2"].get("last_message_text") is None


@pytest.mark.asyncio
async def test_send_message_trusted_mode_rejects_short_base64(
    fake_db: _FakeDB,
) -> None:
    """Base64 that decodes to fewer bytes than nonce+MAC is not ciphertext."""
    _seed_chat(fake_db, encryption_mode="trusted")

    too_short = base64.b64encode(b"\x01" * 8).decode("ascii")
    with pytest.raises(messages_service.InvalidCiphertext):
        await messages_service.send_message("uid-1_uid-2", "uid-1", too_short)


# --------------------------------------------------------------------------
# SEC-05: relationship guard (blocks + allow_messages_from)
# --------------------------------------------------------------------------


def _seed_block(fake_db: _FakeDB, blocker: str, blocked: str) -> None:
    fake_db.collection("blocks").document(f"{blocker}_{blocked}").set(
        {"blocker_uid": blocker, "blocked_uid": blocked}
    )


def _seed_user(fake_db: _FakeDB, uid: str, **fields: Any) -> None:
    fake_db.collection("users").document(uid).set({"uid": uid, **fields})


@pytest.mark.asyncio
async def test_send_message_refused_when_recipient_blocked_sender(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db)
    _seed_block(fake_db, blocker="uid-2", blocked="uid-1")

    with pytest.raises(messages_service.MessagingNotAllowed):
        await messages_service.send_message("uid-1_uid-2", "uid-1", "hi")

    assert fake_db.messages_for("uid-1_uid-2") == {}


@pytest.mark.asyncio
async def test_send_message_refused_when_sender_blocked_recipient(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db)
    _seed_block(fake_db, blocker="uid-1", blocked="uid-2")

    with pytest.raises(messages_service.MessagingNotAllowed):
        await messages_service.send_message("uid-1_uid-2", "uid-1", "hi")


@pytest.mark.asyncio
async def test_send_message_refused_when_recipient_allows_none(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db)
    _seed_user(fake_db, "uid-2", allow_messages_from="none")

    with pytest.raises(messages_service.MessagingNotAllowed):
        await messages_service.send_message("uid-1_uid-2", "uid-1", "hi")


@pytest.mark.asyncio
async def test_send_message_followers_mode_requires_follow(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)
    _seed_user(fake_db, "uid-2", allow_messages_from="followers")

    # Not following -> refused.
    with pytest.raises(messages_service.MessagingNotAllowed):
        await messages_service.send_message("uid-1_uid-2", "uid-1", "hi")

    # Following -> allowed (moderation stubbed clean).
    fake_db.collection("follows").document("uid-1_uid-2").set(
        {"follower_uid": "uid-1", "followee_uid": "uid-2"}
    )

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    msg = await messages_service.send_message("uid-1_uid-2", "uid-1", "hi")
    assert msg.status == "approved"


@pytest.mark.asyncio
async def test_get_or_create_chat_refused_when_blocked(fake_db: _FakeDB) -> None:
    _seed_block(fake_db, blocker="uid-2", blocked="uid-1")

    with pytest.raises(messages_service.MessagingNotAllowed):
        await messages_service.get_or_create_chat("uid-1", "uid-2")

    assert fake_db.chats == {}


# --------------------------------------------------------------------------
# CQ-01: unread_counts lifecycle
# --------------------------------------------------------------------------
# NOTE: the fake's update() stores dotted field paths literally (e.g.
# "unread_counts.uid-2"), while real Firestore nests them into the map.
# Assertions below use the literal dotted key on purpose.


@pytest.mark.asyncio
async def test_new_chat_seeds_zero_unread_counts(fake_db: _FakeDB) -> None:
    chat = await messages_service.get_or_create_chat("uid-1", "uid-2")
    assert chat.unread_counts == {"uid-1": 0, "uid-2": 0}


@pytest.mark.asyncio
async def test_delivered_message_increments_recipient_unread(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    await messages_service.send_message("uid-1_uid-2", "uid-1", "hello")
    await messages_service.send_message("uid-1_uid-2", "uid-1", "again")

    chat_doc = fake_db.chats["uid-1_uid-2"]
    assert chat_doc["unread_counts.uid-2"] == 2
    assert "unread_counts.uid-1" not in chat_doc  # sender's counter untouched


@pytest.mark.asyncio
async def test_pending_review_message_does_not_increment_unread(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            reason="keyword match: idiot",
            matches=[Match(term="idiot", category="english_slurs", weight=0.5, start=0, end=5)],
            content_hash="h",
        )

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    await messages_service.send_message(
        "uid-1_uid-2", "uid-1", "idiot", submit_for_review=True
    )

    assert "unread_counts.uid-2" not in fake_db.chats["uid-1_uid-2"]


@pytest.mark.asyncio
async def test_mark_chat_read_resets_counter_and_stamps_read_at(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db)
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    _seed_message(fake_db, "uid-1_uid-2", "m1", now, sender_uid="uid-2")
    _seed_message(fake_db, "uid-1_uid-2", "m2", now, sender_uid="uid-1")

    await messages_service.mark_chat_read("uid-1_uid-2", "uid-1")

    assert fake_db.chats["uid-1_uid-2"]["unread_counts.uid-1"] == 0
    msgs = fake_db.messages_for("uid-1_uid-2")
    assert msgs["m1"]["read_at"] is not None  # peer's message: read receipt
    assert msgs["m2"]["read_at"] is None  # own message untouched


@pytest.mark.asyncio
async def test_mark_chat_read_requires_participant(fake_db: _FakeDB) -> None:
    _seed_chat(fake_db)
    with pytest.raises(messages_service.NotAuthorized):
        await messages_service.mark_chat_read("uid-1_uid-2", "uid-outsider")


# --------------------------------------------------------------------------
# SEC-08: DM image moderation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_image_rejected_when_vision_blocks(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    _seed_message(fake_db, "uid-1_uid-2", "m1", now, sender_uid="uid-1")

    async def fake_moderate_image(url: str) -> ModerationResult:
        return ModerationResult(blocked=True, layer="vision", category="adult", content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_image", fake_moderate_image)

    await messages_service._moderate_message_image(
        "uid-1_uid-2", "m1", "https://storage.example/img.jpg"
    )

    stored = fake_db.messages_for("uid-1_uid-2")["m1"]
    assert stored["status"] == "rejected"
    assert "safety" in stored["rejection_reason"]


@pytest.mark.asyncio
async def test_message_image_untouched_when_vision_clean(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    _seed_message(fake_db, "uid-1_uid-2", "m1", now, sender_uid="uid-1")

    async def fake_moderate_image(url: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_image", fake_moderate_image)

    await messages_service._moderate_message_image(
        "uid-1_uid-2", "m1", "https://storage.example/img.jpg"
    )

    assert "status" not in fake_db.messages_for("uid-1_uid-2")["m1"]


@pytest.mark.asyncio
async def test_send_message_pending_mode_sets_encrypted_false(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db, encryption_mode="pending")

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(messages_service, "moderate_text", fake_moderate)

    msg = await messages_service.send_message("uid-1_uid-2", "uid-1", "Hey there!")

    assert msg.encrypted is False


@pytest.mark.asyncio
async def test_set_encryption_mode_requires_mutual_follow(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_mutual(uid1: str, uid2: str) -> bool:
        return False

    monkeypatch.setattr(follows_service, "is_mutual_follow", fake_mutual)

    with pytest.raises(messages_service.NotMutualFollow):
        await messages_service.set_encryption_mode("uid-1_uid-2", "uid-1", "trusted")

    # Mode must be untouched.
    assert fake_db.chats["uid-1_uid-2"]["encryption_mode"] == "pending"


@pytest.mark.asyncio
async def test_set_encryption_mode_toggles_when_mutual(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_chat(fake_db)

    async def fake_mutual(uid1: str, uid2: str) -> bool:
        return True

    monkeypatch.setattr(follows_service, "is_mutual_follow", fake_mutual)

    chat = await messages_service.set_encryption_mode("uid-1_uid-2", "uid-1", "trusted")

    assert chat.encryption_mode == "trusted"
    assert fake_db.chats["uid-1_uid-2"]["encryption_mode"] == "trusted"
    audit = fake_db._stores.get("encryption_mode_audit_logs", {})
    assert len(audit) == 1
    entry = next(iter(audit.values()))
    assert entry["reason"] == "toggle"
    assert entry["previous_mode"] == "pending"
    assert entry["new_mode"] == "trusted"


@pytest.mark.asyncio
async def test_set_encryption_mode_non_participant_raises(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db)

    with pytest.raises(messages_service.NotAuthorized):
        await messages_service.set_encryption_mode("uid-1_uid-2", "uid-outsider", "trusted")


@pytest.mark.asyncio
async def test_revert_chat_to_pending_if_trusted_reverts(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db, encryption_mode="trusted")

    await messages_service.revert_chat_to_pending_if_trusted("uid-1", "uid-2")

    assert fake_db.chats["uid-1_uid-2"]["encryption_mode"] == "pending"
    audit = fake_db._stores.get("encryption_mode_audit_logs", {})
    assert len(audit) == 1
    entry = next(iter(audit.values()))
    assert entry["reason"] == "unfollow"
    assert entry["actor_uid"] is None


@pytest.mark.asyncio
async def test_revert_chat_to_pending_if_trusted_noop_when_already_pending(
    fake_db: _FakeDB,
) -> None:
    _seed_chat(fake_db, encryption_mode="pending")

    await messages_service.revert_chat_to_pending_if_trusted("uid-1", "uid-2")

    assert fake_db.chats["uid-1_uid-2"]["encryption_mode"] == "pending"
    assert fake_db._stores.get("encryption_mode_audit_logs", {}) == {}


@pytest.mark.asyncio
async def test_revert_chat_to_pending_if_trusted_noop_when_no_chat(
    fake_db: _FakeDB,
) -> None:
    # No chat seeded — must not raise.
    await messages_service.revert_chat_to_pending_if_trusted("uid-a", "uid-b")
    assert fake_db.chats == {}


@pytest.mark.asyncio
async def test_get_messages_returns_list_ordered_newest_first(
    fake_db: _FakeDB,
) -> None:
    chat_id = "uid-1_uid-2"
    _seed_chat(fake_db, chat_id=chat_id)

    t1 = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 18, 11, 0, tzinfo=UTC)
    t3 = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)

    # Seed in scrambled insertion order to confirm sort is applied.
    for mid, t in [("m2", t2), ("m3", t3), ("m1", t1)]:
        _seed_message(fake_db, chat_id, mid, t)

    messages = await messages_service.get_messages(chat_id, "uid-1")

    assert len(messages) == 3
    assert messages[0].id == "m3"  # newest
    assert messages[1].id == "m2"
    assert messages[2].id == "m1"  # oldest


@pytest.mark.asyncio
async def test_mark_read_sets_read_at(fake_db: _FakeDB) -> None:
    chat_id = "uid-1_uid-2"
    _seed_chat(fake_db, chat_id=chat_id)
    _seed_message(fake_db, chat_id, "msg-1", datetime(2026, 5, 18, tzinfo=UTC))

    assert fake_db.messages_for(chat_id)["msg-1"]["read_at"] is None

    await messages_service.mark_read(chat_id, "msg-1", reader_uid="uid-2")

    assert fake_db.messages_for(chat_id)["msg-1"]["read_at"] is not None


# --------------------------------------------------------------------------
# API tests
# --------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override_claims(claims: dict[str, Any]) -> None:
    async def fake_claims() -> dict[str, Any]:
        return claims

    app.dependency_overrides[get_current_user_claims] = fake_claims


def _sample_chat(**overrides: Any) -> Chat:
    now = datetime(2026, 5, 18, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "uid-1_uid-2",
        "participants": ["uid-1", "uid-2"],
        "last_message_text": None,
        "last_message_at": None,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }
    base.update(overrides)
    return Chat(**base)


def _sample_message(**overrides: Any) -> Message:
    now = datetime(2026, 5, 18, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "msg-1",
        "chat_id": "uid-1_uid-2",
        "sender_uid": "uid-1",
        "text": "Hello there!",
        "image_url": None,
        "read_at": None,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }
    base.update(overrides)
    return Message(**base)


def test_post_chat_uid_returns_201_with_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_or_create(uid_a: str, uid_b: str) -> Chat:
        return _sample_chat()

    monkeypatch.setattr(messages_service, "get_or_create_chat", fake_get_or_create)

    response = client.post("/api/v1/chats/uid-2")

    assert response.status_code == 201
    data = response.json()["data"]
    assert "chat" in data
    assert data["chat"]["id"] == "uid-1_uid-2"
    assert "uid-1" in data["chat"]["participants"]


def test_post_message_returns_201(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_send(
        chat_id: str,
        sender_uid: str,
        text: str,
        image_url: str | None = None,
        submit_for_review: bool = False,
        **kwargs: Any,
    ) -> Message:
        return _sample_message(chat_id=chat_id, sender_uid=sender_uid, text=text)

    monkeypatch.setattr(messages_service, "send_message", fake_send)

    response = client.post(
        "/api/v1/chats/uid-1_uid-2/messages",
        json={"text": "Hello there!"},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert "message" in data
    assert data["message"]["text"] == "Hello there!"
    assert data["message"]["sender_uid"] == "uid-1"


def test_post_message_toxic_text_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_send(
        chat_id: str,
        sender_uid: str,
        text: str,
        image_url: str | None = None,
        submit_for_review: bool = False,
        **kwargs: Any,
    ) -> Message:
        raise messages_service.MessageBlocked(
            layer="keyword",
            reason="keyword match: idiot",
            matches=[Match(term="idiot", category="english_slurs", weight=0.5, start=0, end=5)],
        )

    monkeypatch.setattr(messages_service, "send_message", fake_send)

    response = client.post(
        "/api/v1/chats/uid-1_uid-2/messages",
        json={"text": "toxic content"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "MODERATION_FLAGGED"
    assert body["error"]["field"] == "text"
    assert body["error"]["matches"][0]["term"] == "idiot"


def test_get_chats_returns_200_with_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_chats(uid: str) -> list[Chat]:
        return [
            _sample_chat(),
            _sample_chat(id="uid-1_uid-3", participants=["uid-1", "uid-3"]),
        ]

    monkeypatch.setattr(messages_service, "get_chats", fake_get_chats)

    response = client.get("/api/v1/chats")

    assert response.status_code == 200
    data = response.json()["data"]
    assert "chats" in data
    assert len(data["chats"]) == 2


def test_get_chat_messages_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_messages(
        chat_id: str,
        requesting_uid: str,
        limit: int = 50,
        before_created_at: str | None = None,
    ) -> list[Message]:
        return [_sample_message(id="m1"), _sample_message(id="m2")]

    monkeypatch.setattr(messages_service, "get_messages", fake_get_messages)

    response = client.get("/api/v1/chats/uid-1_uid-2/messages")

    assert response.status_code == 200
    data = response.json()["data"]
    assert "messages" in data
    assert len(data["messages"]) == 2
    assert data["messages"][0]["id"] == "m1"


def test_patch_read_returns_204(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-2", "admin": False})

    async def fake_mark_read(chat_id: str, message_id: str, reader_uid: str) -> None:
        return None

    monkeypatch.setattr(messages_service, "mark_read", fake_mark_read)

    response = client.patch("/api/v1/chats/uid-1_uid-2/messages/msg-1/read")

    assert response.status_code == 204
    assert response.content == b""


def test_patch_encryption_mode_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_set_mode(chat_id: str, requesting_uid: str, mode: str) -> Chat:
        return _sample_chat(encryption_mode=mode)

    monkeypatch.setattr(messages_service, "set_encryption_mode", fake_set_mode)

    response = client.patch(
        "/api/v1/chats/uid-1_uid-2/encryption-mode", json={"mode": "trusted"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["encryption_mode"] == "trusted"


def test_patch_encryption_mode_non_mutual_follow_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_set_mode(chat_id: str, requesting_uid: str, mode: str) -> Chat:
        raise messages_service.NotMutualFollow(chat_id)

    monkeypatch.setattr(messages_service, "set_encryption_mode", fake_set_mode)

    response = client.patch(
        "/api/v1/chats/uid-1_uid-2/encryption-mode", json={"mode": "trusted"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_patch_encryption_mode_non_participant_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-outsider", "admin": False})

    async def fake_set_mode(chat_id: str, requesting_uid: str, mode: str) -> Chat:
        raise messages_service.NotAuthorized(requesting_uid)

    monkeypatch.setattr(messages_service, "set_encryption_mode", fake_set_mode)

    response = client.patch(
        "/api/v1/chats/uid-1_uid-2/encryption-mode", json={"mode": "trusted"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
