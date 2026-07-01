# backend/tests/test_follows.py
"""Tests for follow/unfollow — service layer and API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from main import app
from middleware.auth import get_current_user_claims
from models.user import UserProfile
from services import follows as follows_service
from services import users as users_service


# --------------------------------------------------------------------------
# In-memory Firestore fake with transaction support
# --------------------------------------------------------------------------

def _apply_transform(current: Any, value: Any) -> Any:
    """Apply a value or a Firestore Increment sentinel to the stored field."""
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

    def get(self, transaction: Any = None) -> _FakeSnapshot:
        return _FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.id] = dict(data)

    def delete(self) -> None:
        self._store.pop(self.id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


class _FakeTransaction:
    """Minimal transaction fake compatible with @firestore.transactional."""

    # The SDK decorator reads _max_attempts, _read_only, _id, and calls
    # _begin / _commit / _rollback / _clean_up — all private names.
    _max_attempts = 1
    _read_only = False
    _id = b"fake-txn-id"

    def __init__(self) -> None:
        self._pending: list[tuple[str, _FakeDocRef, dict[str, Any]]] = []

    def _begin(self, retry_id: Any = None) -> None:
        pass

    def _clean_up(self) -> None:
        self._pending.clear()

    def _rollback(self) -> None:
        self._pending.clear()

    def set(self, ref: _FakeDocRef, data: dict[str, Any]) -> None:
        self._pending.append(("set", ref, dict(data)))

    def update(self, ref: _FakeDocRef, data: dict[str, Any]) -> None:
        self._pending.append(("update", ref, dict(data)))

    def delete(self, ref: _FakeDocRef) -> None:
        self._pending.append(("delete", ref, {}))

    def _commit(self) -> list[Any]:
        for op, ref, data in self._pending:
            if op == "set":
                ref._store[ref.id] = data
            elif op == "update":
                row = dict(ref._store.get(ref.id, {}))
                for k, v in data.items():
                    row[k] = _apply_transform(row.get(k, 0), v)
                ref._store[ref.id] = row
            elif op == "delete":
                ref._store.pop(ref.id, None)
        self._pending.clear()
        return []


class _FakeDB:
    def __init__(self) -> None:
        self._stores: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        if name not in self._stores:
            self._stores[name] = {}
        return _FakeCollection(self._stores[name])

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    @property
    def follows(self) -> dict[str, Any]:
        return self._stores.get("follows", {})

    @property
    def users(self) -> dict[str, Any]:
        return self._stores.get("users", {})


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(follows_service, "db", db)
    return db


def _seed_user(fake_db: _FakeDB, uid: str, **counters: int) -> None:
    """Pre-populate a user doc so counter updates have something to update."""
    fake_db.collection("users").document(uid).set(
        {"uid": uid, "follower_count": 0, "following_count": 0, **counters}
    )


# --------------------------------------------------------------------------
# Service-layer tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_follow_creates_doc_and_increments(fake_db: _FakeDB) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")

    await follows_service.follow_user("alice", "bob")

    assert "alice_bob" in fake_db.follows
    doc = fake_db.follows["alice_bob"]
    assert doc["follower_uid"] == "alice"
    assert doc["followee_uid"] == "bob"

    assert fake_db.users["alice"]["following_count"] == 1
    assert fake_db.users["bob"]["follower_count"] == 1


@pytest.mark.asyncio
async def test_follow_is_idempotent(fake_db: _FakeDB) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")

    await follows_service.follow_user("alice", "bob")
    await follows_service.follow_user("alice", "bob")

    # Second call is a no-op — counters must not double-increment.
    assert len(fake_db.follows) == 1
    assert fake_db.users["alice"]["following_count"] == 1
    assert fake_db.users["bob"]["follower_count"] == 1


@pytest.mark.asyncio
async def test_unfollow_removes_doc_and_decrements(fake_db: _FakeDB) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")
    await follows_service.follow_user("alice", "bob")   # counters → 1

    await follows_service.unfollow_user("alice", "bob")  # counters → 0

    assert fake_db.follows == {}
    assert fake_db.users["alice"]["following_count"] == 0
    assert fake_db.users["bob"]["follower_count"] == 0


@pytest.mark.asyncio
async def test_unfollow_is_idempotent(fake_db: _FakeDB) -> None:
    # No prior follow — must not raise.
    await follows_service.unfollow_user("alice", "bob")
    assert fake_db.follows == {}


@pytest.mark.asyncio
async def test_counter_floor_at_zero(fake_db: _FakeDB) -> None:
    # Counters already at 0 — decrement should stay at 0, not go negative.
    _seed_user(fake_db, "alice", following_count=0)
    _seed_user(fake_db, "bob", follower_count=0)
    await follows_service.follow_user("alice", "bob")
    await follows_service.unfollow_user("alice", "bob")

    assert fake_db.users["alice"]["following_count"] == 0
    assert fake_db.users["bob"]["follower_count"] == 0


@pytest.mark.asyncio
async def test_is_following_true_and_false(fake_db: _FakeDB) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")
    await follows_service.follow_user("alice", "bob")

    assert await follows_service.is_following("alice", "bob") is True
    # Unidirectional: bob has not followed alice back.
    assert await follows_service.is_following("bob", "alice") is False
    assert await follows_service.is_following("alice", "carol") is False


@pytest.mark.asyncio
async def test_follow_self_raises(fake_db: _FakeDB) -> None:
    with pytest.raises(follows_service.CannotFollowSelf):
        await follows_service.follow_user("alice", "alice")


@pytest.mark.asyncio
async def test_is_mutual_follow_true_when_both_directions_exist(
    fake_db: _FakeDB,
) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")
    await follows_service.follow_user("alice", "bob")
    await follows_service.follow_user("bob", "alice")

    assert await follows_service.is_mutual_follow("alice", "bob") is True
    assert await follows_service.is_mutual_follow("bob", "alice") is True


@pytest.mark.asyncio
async def test_is_mutual_follow_false_when_only_one_direction(
    fake_db: _FakeDB,
) -> None:
    _seed_user(fake_db, "alice")
    _seed_user(fake_db, "bob")
    await follows_service.follow_user("alice", "bob")

    assert await follows_service.is_mutual_follow("alice", "bob") is False
    assert await follows_service.is_mutual_follow("bob", "alice") is False


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


def _sample_profile(**overrides: Any) -> UserProfile:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    base: dict[str, Any] = {
        "uid": "target-uid",
        "email": "t@example.com",
        "username": "target",
        "display_name": "Target",
        "bio": "",
        "photo_url": None,
        "dob": "1990-01-01",
        "follower_count": 0,
        "following_count": 0,
        "post_count": 0,
        "is_verified": False,
        "is_suspended": False,
        "created_at": now,
        "updated_at": now,
        "last_active_at": now,
        "private_account": False,
        "allow_messages_from": "everyone",
        "schema_version": 1,
    }
    base.update(overrides)
    return UserProfile(**base)


def test_cannot_follow_self_via_api(client: TestClient) -> None:
    _override_claims({"uid": "me", "admin": False})

    response = client.post("/api/v1/users/me/follow")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_follow_returns_204(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "follower", "admin": False})

    async def fake_get_profile(uid: str) -> UserProfile:
        return _sample_profile(uid=uid)

    monkeypatch.setattr(users_service, "get_user_profile", fake_get_profile)

    calls: dict[str, int] = {"follow": 0}

    async def fake_follow(follower_uid: str, followee_uid: str) -> None:
        calls["follow"] += 1

    monkeypatch.setattr(follows_service, "follow_user", fake_follow)

    response = client.post("/api/v1/users/target-uid/follow")

    assert response.status_code == 204
    assert response.content == b""
    assert calls["follow"] == 1


def test_follow_target_not_found_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "follower", "admin": False})

    async def fake_get_profile(uid: str) -> None:
        return None

    monkeypatch.setattr(users_service, "get_user_profile", fake_get_profile)

    response = client.post("/api/v1/users/ghost/follow")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_unfollow_returns_204(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "follower", "admin": False})

    async def fake_get_profile(uid: str) -> UserProfile:
        return _sample_profile(uid=uid)

    monkeypatch.setattr(users_service, "get_user_profile", fake_get_profile)

    async def fake_unfollow(follower_uid: str, followee_uid: str) -> None:
        return None

    monkeypatch.setattr(follows_service, "unfollow_user", fake_unfollow)

    from services import messages as messages_service

    revert_calls: list[tuple[str, str]] = []

    async def fake_revert(uid_a: str, uid_b: str) -> None:
        revert_calls.append((uid_a, uid_b))

    monkeypatch.setattr(messages_service, "revert_chat_to_pending_if_trusted", fake_revert)

    response = client.delete("/api/v1/users/target-uid/follow")

    assert response.status_code == 204
    # Unfollowing must check whether it broke a "trusted" chat's mutual-follow gate.
    assert revert_calls == [("follower", "target-uid")]


def test_get_followers_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "viewer", "admin": False})

    async def fake_get_profile(uid: str) -> UserProfile:
        return _sample_profile(uid=uid)

    monkeypatch.setattr(users_service, "get_user_profile", fake_get_profile)

    async def fake_get_followers(uid: str) -> list[str]:
        return ["uid-a", "uid-b"]

    monkeypatch.setattr(follows_service, "get_followers", fake_get_followers)

    response = client.get("/api/v1/users/target-uid/followers")

    assert response.status_code == 200
    assert response.json()["data"]["followers"] == ["uid-a", "uid-b"]


def test_get_following_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "viewer", "admin": False})

    async def fake_get_profile(uid: str) -> UserProfile:
        return _sample_profile(uid=uid)

    monkeypatch.setattr(users_service, "get_user_profile", fake_get_profile)

    async def fake_get_following(uid: str) -> list[str]:
        return ["uid-c"]

    monkeypatch.setattr(follows_service, "get_following", fake_get_following)

    response = client.get("/api/v1/users/target-uid/following")

    assert response.status_code == 200
    assert response.json()["data"]["following"] == ["uid-c"]


def test_get_user_shows_is_following_and_is_followed_by(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "viewer", "admin": False})

    async def fake_get_by_username(username: str) -> UserProfile:
        return _sample_profile(uid="target-uid", username=username)

    monkeypatch.setattr(
        users_service, "get_profile_by_username", fake_get_by_username
    )

    from services import blocks as blocks_service

    async def fake_is_blocked(blocker_uid: str, blocked_uid: str) -> bool:
        return False

    monkeypatch.setattr(blocks_service, "is_blocked", fake_is_blocked)

    async def fake_is_following(follower_uid: str, followee_uid: str) -> bool:
        # viewer follows target, but target does not follow viewer back
        return follower_uid == "viewer" and followee_uid == "target-uid"

    monkeypatch.setattr(follows_service, "is_following", fake_is_following)

    response = client.get("/api/v1/users/target")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_following"] is True
    assert data["is_followed_by"] is False
    assert data["is_blocked"] is False
