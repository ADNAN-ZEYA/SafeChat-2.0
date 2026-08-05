# backend/tests/test_posts.py
"""Tests for posts — service layer and API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore import SERVER_TIMESTAMP as _SERVER_TIMESTAMP

from main import app
from middleware.auth import get_current_user_claims
from models.moderation import Match, ModerationResult
from models.post import Post
from services import likes as likes_service
from services import posts as posts_service

# --------------------------------------------------------------------------
# In-memory Firestore fake with batch support
# --------------------------------------------------------------------------


def _apply_transform(current: Any, value: Any) -> Any:
    """Resolve a Firestore SERVER_TIMESTAMP or Increment sentinel to a plain value."""
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


class _FakeDocRef:
    def __init__(self, store: dict[str, Any], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

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
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


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

    def collection(self, name: str) -> _FakeCollection:
        if name not in self._stores:
            self._stores[name] = {}
        return _FakeCollection(self._stores[name])

    def batch(self) -> _FakeBatch:
        return _FakeBatch()

    @property
    def posts(self) -> dict[str, Any]:
        return self._stores.get("posts", {})

    @property
    def users(self) -> dict[str, Any]:
        return self._stores.get("users", {})


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(posts_service, "db", db)
    return db


# --------------------------------------------------------------------------
# Service-layer tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_post_returns_post(fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(blocked=False, content_hash="h")

    monkeypatch.setattr(posts_service, "moderate_text", fake_moderate)

    post = await posts_service.create_post("uid-1", "Hello world", media_urls=["https://storage.googleapis.com/bucket/img.jpg"], media_type="image")

    assert isinstance(post, Post)
    assert post.author_uid == "uid-1"
    assert post.text == "Hello world"
    assert post.status == "approved"
    assert post.like_count == 0
    assert post.comment_count == 0
    assert len(fake_db.posts) == 1
    assert fake_db.users["uid-1"]["post_count"] == 1


@pytest.mark.asyncio
async def test_create_post_blocked_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="slurs",
            reason="keyword match",
            content_hash="h",
        )

    monkeypatch.setattr(posts_service, "moderate_text", fake_moderate)

    with pytest.raises(posts_service.PostBlocked) as exc_info:
        await posts_service.create_post("uid-1", "toxic text", media_urls=["https://storage.googleapis.com/bucket/img.jpg"], media_type="image")

    assert exc_info.value.layer == "keyword"


@pytest.mark.asyncio
async def test_create_post_submit_for_review_creates_pending_and_queue(
    fake_db: _FakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_moderate(text: str) -> ModerationResult:
        return ModerationResult(
            blocked=True,
            layer="keyword",
            category="english_slurs",
            reason="keyword match: idiot",
            matches=[Match(term="idiot", category="english_slurs", weight=0.5, start=0, end=5)],
            lexicon_score=0.5,
            content_hash="h",
        )

    async def fake_profile(uid: str) -> None:
        return None

    monkeypatch.setattr(posts_service, "moderate_text", fake_moderate)
    monkeypatch.setattr(posts_service.users_service, "get_user_profile", fake_profile)

    post = await posts_service.create_post("uid-1", "idiot", media_urls=["https://storage.googleapis.com/bucket/img.jpg"], media_type="image", submit_for_review=True)

    assert post.status == "pending_review"
    assert post.flagged_terms == ["idiot"]
    # pending posts are not counted until approved
    assert fake_db.users.get("uid-1", {}).get("post_count", 0) == 0
    # a moderation_queue record was written in the same batch
    queue = fake_db._stores.get("moderation_queue", {})
    assert len(queue) == 1
    item = next(iter(queue.values()))
    assert item["content_type"] == "post"
    assert item["content_id"] == post.id
    assert item["status"] == "pending_review"
    assert item["flagged_terms"] == ["idiot"]


@pytest.mark.asyncio
async def test_get_post_returns_none_when_missing(fake_db: _FakeDB) -> None:
    result = await posts_service.get_post("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_delete_post_raises_not_authorized(fake_db: _FakeDB) -> None:
    # Seed a post owned by alice.
    fake_db.collection("posts").document("post-1").set({"id": "post-1", "author_uid": "alice"})

    with pytest.raises(posts_service.NotAuthorized):
        await posts_service.delete_post("post-1", requesting_uid="bob")

    # Post must still exist — nothing was deleted.
    assert "post-1" in fake_db.posts


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


@pytest.fixture(autouse=True)
def _default_is_liked_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: viewer has not liked any post. Like tests live in test_likes.py."""

    async def fake_is_liked(user_uid: str, post_id: str) -> bool:
        return False

    monkeypatch.setattr(likes_service, "is_liked", fake_is_liked)


def _override_claims(claims: dict[str, Any]) -> None:
    async def fake_claims() -> dict[str, Any]:
        return claims

    app.dependency_overrides[get_current_user_claims] = fake_claims


def _sample_post(**overrides: Any) -> Post:
    now = datetime(2026, 5, 17, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "post-abc",
        "author_uid": "uid-1",
        "text": "Hello world",
        "image_url": None,
        "status": "approved",
        "like_count": 0,
        "comment_count": 0,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }
    base.update(overrides)
    return Post(**base)


def test_post_endpoint_returns_201(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_create(
        author_uid: str,
        text: str,
        media_urls: list[str] | None = None,
        media_type: str = "text",
        submit_for_review: bool = False,
    ) -> Post:
        return _sample_post(author_uid=author_uid, text=text)

    monkeypatch.setattr(posts_service, "create_post", fake_create)

    response = client.post(
        "/api/v1/posts",
        json={"text": "Hello world", "media_urls": ["https://storage.googleapis.com/b/img.jpg"], "media_type": "image"},
    )

    assert response.status_code == 201
    data = response.json()["data"]["post"]
    assert data["author_uid"] == "uid-1"
    assert data["text"] == "Hello world"
    assert data["status"] == "approved"


def test_post_endpoint_toxic_text_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_create(
        author_uid: str,
        text: str,
        media_urls: list[str] | None = None,
        media_type: str = "text",
        submit_for_review: bool = False,
    ) -> Post:
        raise posts_service.PostBlocked(
            layer="keyword",
            reason="keyword match: idiot",
            matches=[Match(term="idiot", category="english_slurs", weight=0.5, start=0, end=5)],
        )

    monkeypatch.setattr(posts_service, "create_post", fake_create)

    response = client.post(
        "/api/v1/posts",
        json={"text": "toxic content", "media_urls": ["https://storage.googleapis.com/b/img.jpg"], "media_type": "image"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "MODERATION_FLAGGED"
    assert body["error"]["field"] == "text"
    assert body["error"]["matches"][0]["term"] == "idiot"
    assert body["error"]["matches"][0]["start"] == 0


def test_get_feed_returns_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_feed(
        viewer_uid: str,
        feed_type: str = "following",
        limit: int = 20,
        before_created_at: str | None = None,
    ) -> list[Post]:
        return [_sample_post(id="p1"), _sample_post(id="p2")]

    monkeypatch.setattr(posts_service, "get_feed", fake_get_feed)

    response = client.get("/api/v1/posts/feed")

    assert response.status_code == 200
    posts = response.json()["data"]["posts"]
    assert len(posts) == 2
    assert posts[0]["id"] == "p1"


def test_get_post_returns_404_when_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_get_post(post_id: str) -> None:
        return None

    monkeypatch.setattr(posts_service, "get_post", fake_get_post)

    response = client.get("/api/v1/posts/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_delete_post_returns_204(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_delete(post_id: str, requesting_uid: str, is_admin: bool = False) -> None:
        return None

    monkeypatch.setattr(posts_service, "delete_post", fake_delete)

    response = client.delete("/api/v1/posts/post-abc")

    assert response.status_code == 204
    assert response.content == b""


def test_delete_post_returns_403_for_non_author(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-other", "admin": False})

    async def fake_delete(post_id: str, requesting_uid: str, is_admin: bool = False) -> None:
        raise posts_service.NotAuthorized(requesting_uid)

    monkeypatch.setattr(posts_service, "delete_post", fake_delete)

    response = client.delete("/api/v1/posts/post-abc")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
