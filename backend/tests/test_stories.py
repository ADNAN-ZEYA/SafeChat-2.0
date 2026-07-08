# backend/tests/test_stories.py
"""Tests for stories — service layer and API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore import SERVER_TIMESTAMP as _SERVER_TIMESTAMP

from main import app
from middleware.auth import get_current_user_claims
from models.moderation import ModerationResult
from models.story import Story
from services import stories as stories_service


# --------------------------------------------------------------------------
# In-memory Firestore fake with subcollection, query, and batch support
# --------------------------------------------------------------------------

def _apply_transform(current: Any, value: Any) -> Any:
    """Resolve a Firestore SERVER_TIMESTAMP or Increment sentinel to a plain value."""
    if value is _SERVER_TIMESTAMP:
        return datetime.now(timezone.utc)
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
    """Chainable query object — applies sort/limit at stream() time."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self._order_field: str | None = None
        self._order_desc: bool = False
        self._limit_n: int | None = None

    def order_by(self, field: str, direction: Any = None) -> "_FakeQuery":
        self._order_field = field
        if direction is not None:
            self._order_desc = str(direction) == "DESCENDING"
        return self

    def where(self, filter: Any = None, **kwargs: Any) -> "_FakeQuery":  # noqa: A002
        # Filtering not needed for these tests — all seeded data matches.
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self._limit_n = n
        return self

    def stream(self) -> list[_FakeSnapshot]:
        items = list(self._store.items())
        if self._order_field:
            items.sort(
                key=lambda kv: kv[1].get(self._order_field) or "",
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
        db: "_FakeDB",
    ) -> None:
        self._store = store
        self.id = doc_id
        self._path = path
        self._db = db

    def collection(self, name: str) -> "_FakeCollection":
        """Return a subcollection rooted at this document."""
        subcoll_path = f"{self._path}/{self.id}/{name}"
        return self._db.collection(subcoll_path)

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.id] = {
            k: _apply_transform(None, v) for k, v in data.items()
        }

    def update(self, data: dict[str, Any]) -> None:
        row = dict(self._store.get(self.id, {}))
        for k, v in data.items():
            row[k] = _apply_transform(row.get(k), v)
        self._store[self.id] = row

    def delete(self) -> None:
        self._store.pop(self.id, None)


class _FakeCollection:
    def __init__(
        self, store: dict[str, Any], path: str, db: "_FakeDB"
    ) -> None:
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
                ref._store[ref.id] = {
                    k: _apply_transform(None, v) for k, v in data.items()
                }
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
    def stories(self) -> dict[str, Any]:
        return self._stores.get("stories", {})

    def views_for(self, story_id: str) -> dict[str, Any]:
        return self._stores.get(f"stories/{story_id}/views", {})


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(stories_service, "db", db)
    return db


# --------------------------------------------------------------------------
# Service-layer tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_story_returns_story_with_expires_at(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(stories_service, "moderate_text", fake_moderate)

    story = await stories_service.create_story(
        "uid-1", "https://example.com/img.jpg", text="Hello!"
    )

    assert isinstance(story, Story)
    assert story.author_uid == "uid-1"
    assert story.image_url == "https://example.com/img.jpg"
    assert story.text == "Hello!"
    assert story.view_count == 0
    assert story.status == "approved"
    # expires_at must be exactly 24 hours after created_at.
    assert story.expires_at == story.created_at + timedelta(hours=24)
    assert len(fake_db.stories) == 1


@pytest.mark.asyncio
async def test_create_story_with_toxic_text_raises(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="slurs",
            reason="keyword match",
            content_hash="h",
        )

    monkeypatch.setattr(stories_service, "moderate_text", fake_moderate)

    with pytest.raises(stories_service.StoryBlocked) as exc_info:
        await stories_service.create_story(
            "uid-1", "https://example.com/img.jpg", text="toxic text"
        )

    assert exc_info.value.layer == "keyword"
    assert len(fake_db.stories) == 0


@pytest.mark.asyncio
async def test_create_story_flagged_submit_for_review_enqueues(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-04: flagged + submit_for_review -> pending_review + queue record."""

    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="english_slurs",
            reason="keyword match: idiot",
            content_hash="h",
        )

    async def fake_profile(uid: str) -> None:
        return None  # username falls back to "unknown"

    monkeypatch.setattr(stories_service, "moderate_text", fake_moderate)
    monkeypatch.setattr(stories_service.users_service, "get_user_profile", fake_profile)

    story = await stories_service.create_story(
        "uid-1", "https://example.com/img.jpg", text="idiot", submit_for_review=True
    )

    assert story.status == "pending_review"
    queue = fake_db._stores.get("moderation_queue", {})
    assert len(queue) == 1
    item = next(iter(queue.values()))
    assert item["content_type"] == "story"
    assert item["content_id"] == story.id
    assert item["author_uid"] == "uid-1"
    assert item["status"] == "pending_review"


@pytest.mark.asyncio
async def test_set_story_status_approves_pending_story(fake_db: _FakeDB) -> None:
    now = datetime.now(timezone.utc)
    fake_db.collection("stories").document("s1").set(
        {
            "id": "s1",
            "author_uid": "uid-1",
            "image_url": "https://example.com/img.jpg",
            "text": "hello",
            "status": "pending_review",
            "view_count": 0,
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
            "schema_version": 1,
        }
    )

    story = await stories_service.set_story_status("s1", "approved")
    assert story.status == "approved"

    rejected = await stories_service.set_story_status("s1", "rejected", "not ok")
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "not ok"


@pytest.mark.asyncio
async def test_set_story_status_missing_story_raises(fake_db: _FakeDB) -> None:
    with pytest.raises(stories_service.StoryNotFound):
        await stories_service.set_story_status("nope", "approved")


@pytest.mark.asyncio
async def test_create_story_without_text_skips_moderation(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_moderate(text: str) -> ModerationResult:
        raise RuntimeError("moderation must not be called when text is None")

    monkeypatch.setattr(stories_service, "moderate_text", failing_moderate)

    # text=None — moderation guard must be skipped entirely.
    story = await stories_service.create_story("uid-1", "https://example.com/img.jpg")

    assert isinstance(story, Story)
    assert story.text is None


@pytest.mark.asyncio
async def test_get_story_returns_none_when_missing(fake_db: _FakeDB) -> None:
    result = await stories_service.get_story("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_delete_story_raises_not_authorized(fake_db: _FakeDB) -> None:
    now = datetime.now(timezone.utc)
    fake_db.collection("stories").document("story-1").set(
        {
            "id": "story-1",
            "author_uid": "alice",
            "image_url": "https://example.com/img.jpg",
            "text": None,
            "status": "approved",
            "view_count": 0,
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
            "schema_version": 1,
        }
    )

    with pytest.raises(stories_service.NotAuthorized):
        await stories_service.delete_story("story-1", requesting_uid="bob")

    # Story must still exist — nothing was deleted.
    assert "story-1" in fake_db.stories


@pytest.mark.asyncio
async def test_record_view_is_idempotent(fake_db: _FakeDB) -> None:
    now = datetime.now(timezone.utc)
    fake_db.collection("stories").document("story-1").set(
        {
            "id": "story-1",
            "author_uid": "uid-1",
            "image_url": "https://example.com/img.jpg",
            "text": None,
            "status": "approved",
            "view_count": 0,
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
            "schema_version": 1,
        }
    )

    await stories_service.record_view("story-1", "viewer-1")
    await stories_service.record_view("story-1", "viewer-1")  # second — no-op

    assert len(fake_db.views_for("story-1")) == 1
    assert fake_db.stories["story-1"]["view_count"] == 1


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


def _sample_story(**overrides: Any) -> Story:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    base: dict[str, Any] = {
        "id": "story-abc",
        "author_uid": "uid-1",
        "image_url": "https://example.com/img.jpg",
        "text": None,
        "status": "approved",
        "view_count": 0,
        "created_at": now,
        "expires_at": now + timedelta(hours=24),
        "schema_version": 1,
    }
    base.update(overrides)
    return Story(**base)


def test_post_story_returns_201(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_create(
        author_uid: str,
        image_url: str,
        text: str | None = None,
        submit_for_review: bool = False,
    ) -> Story:
        return _sample_story(author_uid=author_uid, image_url=image_url, text=text)

    monkeypatch.setattr(stories_service, "create_story", fake_create)

    response = client.post(
        "/api/v1/stories",
        json={"image_url": "https://example.com/img.jpg"},
    )

    assert response.status_code == 201
    data = response.json()["data"]["story"]
    assert data["author_uid"] == "uid-1"
    assert data["image_url"] == "https://example.com/img.jpg"
    assert data["status"] == "approved"


def test_post_story_toxic_text_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_create(
        author_uid: str,
        image_url: str,
        text: str | None = None,
        submit_for_review: bool = False,
    ) -> Story:
        raise stories_service.StoryBlocked(layer="keyword", reason="blocked")

    monkeypatch.setattr(stories_service, "create_story", fake_create)

    response = client.post(
        "/api/v1/stories",
        json={"image_url": "https://example.com/img.jpg", "text": "toxic content"},
    )

    assert response.status_code == 422
    body = response.json()
    # API-04: flagged stories use the same code as posts/comments/messages.
    assert body["error"]["code"] == "MODERATION_FLAGGED"
    assert body["error"]["field"] == "text"
    assert body["error"]["matches"] == []


def test_get_feed_stories_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_feed(viewer_uid: str) -> list[Story]:
        return [_sample_story(id="s1"), _sample_story(id="s2")]

    monkeypatch.setattr(stories_service, "get_feed_stories", fake_feed)

    response = client.get("/api/v1/stories/feed")

    assert response.status_code == 200
    stories = response.json()["data"]["stories"]
    assert len(stories) == 2
    assert stories[0]["id"] == "s1"


def test_get_story_returns_404_when_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_story(story_id: str) -> None:
        return None

    monkeypatch.setattr(stories_service, "get_story", fake_get_story)

    response = client.get("/api/v1/stories/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_delete_story_returns_204(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_delete(
        story_id: str, requesting_uid: str, is_admin: bool = False
    ) -> None:
        return None

    monkeypatch.setattr(stories_service, "delete_story", fake_delete)

    response = client.delete("/api/v1/stories/story-abc")

    assert response.status_code == 204
    assert response.content == b""


def test_record_view_returns_204(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_story(story_id: str) -> Story:
        return _sample_story(id=story_id)

    monkeypatch.setattr(stories_service, "get_story", fake_get_story)

    calls: dict[str, int] = {"view": 0}

    async def fake_record_view(story_id: str, viewer_uid: str) -> None:
        calls["view"] += 1

    monkeypatch.setattr(stories_service, "record_view", fake_record_view)

    response = client.post("/api/v1/stories/story-abc/view")

    assert response.status_code == 204
    assert response.content == b""
    assert calls["view"] == 1
