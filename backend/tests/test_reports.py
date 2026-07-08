# backend/tests/test_reports.py
"""Tests for reports — service layer and API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore import SERVER_TIMESTAMP as _SERVER_TIMESTAMP

from main import app
from middleware.auth import get_current_user_claims, require_admin
from models.report import Report
from services import reports as reports_service


# --------------------------------------------------------------------------
# In-memory Firestore fake
# --------------------------------------------------------------------------

def _apply_transform(current: Any, value: Any) -> Any:
    """Resolve SERVER_TIMESTAMP to a real datetime; pass everything else through."""
    if value is _SERVER_TIMESTAMP:
        return datetime.now(timezone.utc)
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
        self._store[self.id] = {
            k: _apply_transform(None, v) for k, v in data.items()
        }


class _FakeQuery:
    """Minimal query fake — does not filter; respects limit only."""

    def __init__(self, store: dict[str, Any], _limit: int | None = None) -> None:
        self._store = store
        self._limit_n = _limit

    def where(self, filter: Any = None, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._store, self._limit_n)

    def order_by(self, field: str, direction: Any = None) -> _FakeQuery:
        return _FakeQuery(self._store, self._limit_n)

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._store, n)

    def stream(self) -> list[_FakeSnapshot]:
        items = list(self._store.items())
        if self._limit_n is not None:
            items = items[: self._limit_n]
        return [_FakeSnapshot(k, v) for k, v in items]


class _FakeCollection:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)

    def where(self, filter: Any = None, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._store)

    def order_by(self, field: str, direction: Any = None) -> _FakeQuery:
        return _FakeQuery(self._store)


class _FakeDB:
    def __init__(self) -> None:
        self._stores: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        if name not in self._stores:
            self._stores[name] = {}
        return _FakeCollection(self._stores[name])

    def seed(self, collection: str, docs: dict[str, dict[str, Any]]) -> None:
        if collection not in self._stores:
            self._stores[collection] = {}
        self._stores[collection].update(docs)

    def reports(self) -> dict[str, Any]:
        return self._stores.get("reports", {})


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    monkeypatch.setattr(reports_service, "db", db)
    return db


# --------------------------------------------------------------------------
# Service-layer tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_report_stores_doc_with_correct_fields(
    fake_db: _FakeDB,
) -> None:
    report = await reports_service.create_report(
        reporter_uid="uid-1",
        target_type="post",
        target_id="post-999",
        reason="This post contains harmful misinformation.",
    )

    assert isinstance(report, Report)
    assert report.reporter_uid == "uid-1"
    assert report.target_type == "post"
    assert report.target_id == "post-999"
    assert report.reason == "This post contains harmful misinformation."
    assert report.status == "pending"
    assert report.schema_version == 1
    assert isinstance(report.created_at, datetime)
    # Exactly one doc persisted.
    assert len(fake_db.reports()) == 1


@pytest.mark.asyncio
async def test_cannot_report_self_raises_cannot_report_self(
    fake_db: _FakeDB,
) -> None:
    """Reporting your own account (target_type="user") raises CannotReportSelf."""
    with pytest.raises(reports_service.CannotReportSelf):
        await reports_service.create_report(
            reporter_uid="uid-1",
            target_type="user",
            target_id="uid-1",
            reason="Testing self-report guard check.",
        )


@pytest.mark.asyncio
async def test_create_report_idempotent_returns_existing_pending(
    fake_db: _FakeDB,
) -> None:
    """A duplicate pending report for the same reporter+target is returned as-is."""
    existing_id = "existing-report-id"
    fake_db.seed(
        "reports",
        {
            existing_id: {
                "id": existing_id,
                "reporter_uid": "uid-1",
                "target_type": "story",
                "target_id": "story-42",
                "reason": "Original report reason text goes here.",
                "status": "pending",
                "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                "schema_version": 1,
            }
        },
    )

    report = await reports_service.create_report(
        reporter_uid="uid-1",
        target_type="story",
        target_id="story-42",
        reason="Duplicate report — should return the original.",
    )

    assert report.id == existing_id
    # No new doc should have been created.
    assert len(fake_db.reports()) == 1


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


def _sample_report() -> Report:
    return Report(
        id="report-1",
        reporter_uid="uid-1",
        target_type="post",
        target_id="post-1",
        reason="This post contains harmful content.",
        status="pending",
        created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        schema_version=1,
    )


def test_post_reports_returns_201(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    async def fake_create(
        reporter_uid: str, target_type: str, target_id: str, reason: str
    ) -> Report:
        return _sample_report()

    monkeypatch.setattr(reports_service, "create_report", fake_create)

    response = client.post(
        "/api/v1/reports",
        json={
            "target_type": "post",
            "target_id": "post-1",
            "reason": "This post contains harmful content.",
        },
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert "report" in data
    assert data["report"]["reporter_uid"] == "uid-1"
    assert data["report"]["status"] == "pending"


def test_post_reports_requires_auth_returns_401(
    client: TestClient,
) -> None:
    """Request with no Authorization header must be rejected with 401."""
    response = client.post(
        "/api/v1/reports",
        json={
            "target_type": "post",
            "target_id": "post-1",
            "reason": "This post contains harmful content.",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_get_reports_requires_admin_returns_403_for_non_admin(
    client: TestClient,
) -> None:
    """Authenticated non-admin must receive 403 on the admin-only list endpoint."""
    _override_claims({"uid": "uid-1", "admin": False})

    response = client.get("/api/v1/reports")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_get_reports_returns_list_for_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_admin_claims() -> dict[str, Any]:
        return {"uid": "admin-1", "admin": True}

    app.dependency_overrides[require_admin] = fake_admin_claims

    async def fake_get_reports(
        status: str | None = None, limit: int = 50
    ) -> list[Report]:
        return [_sample_report()]

    monkeypatch.setattr(reports_service, "get_reports", fake_get_reports)

    response = client.get("/api/v1/reports")

    assert response.status_code == 200
    data = response.json()["data"]
    assert "reports" in data
    assert len(data["reports"]) == 1
    assert data["reports"][0]["id"] == "report-1"


def test_post_reports_reason_too_short_returns_400(
    client: TestClient,
) -> None:
    """A reason shorter than 10 characters must be rejected with 400 INVALID_INPUT."""
    _override_claims({"uid": "uid-1", "admin": False})

    response = client.post(
        "/api/v1/reports",
        json={"target_type": "post", "target_id": "post-1", "reason": "short"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_INPUT"
    assert body["error"]["field"] == "reason"


def test_resolve_report_requires_admin(client: TestClient) -> None:
    _override_claims({"uid": "uid-1", "admin": False})
    response = client.post(
        "/api/v1/reports/report-1/resolve",
        json={"action": "dismiss", "notes": "n/a"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_resolve_report_records_decision_for_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_admin_claims() -> dict[str, Any]:
        return {"uid": "admin-1", "admin": True}

    app.dependency_overrides[require_admin] = fake_admin_claims

    async def fake_resolve(
        report_id: str, admin_uid: str, action: str, notes: str
    ) -> Report:
        return Report(
            id=report_id,
            reporter_uid="uid-1",
            target_type="post",
            target_id="post-1",
            reason="This post contains harmful content.",
            status="dismissed" if action == "dismiss" else "reviewed",
            resolution_action=action,  # type: ignore[arg-type]
            resolution_notes=notes,
            resolved_by=admin_uid,
            created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(reports_service, "resolve_report", fake_resolve)

    response = client.post(
        "/api/v1/reports/report-1/resolve",
        json={"action": "block_content", "notes": "removed"},
    )

    assert response.status_code == 200
    report = response.json()["data"]["report"]
    assert report["status"] == "reviewed"
    assert report["resolution_action"] == "block_content"
    assert report["resolved_by"] == "admin-1"


def test_resolve_report_missing_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_admin_claims() -> dict[str, Any]:
        return {"uid": "admin-1", "admin": True}

    app.dependency_overrides[require_admin] = fake_admin_claims

    async def fake_resolve(*args: Any, **kwargs: Any) -> Report:
        raise reports_service.ReportNotFound("report-x")

    monkeypatch.setattr(reports_service, "resolve_report", fake_resolve)

    response = client.post(
        "/api/v1/reports/report-x/resolve",
        json={"action": "dismiss", "notes": ""},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
