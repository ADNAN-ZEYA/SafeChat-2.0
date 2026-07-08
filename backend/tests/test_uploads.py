# backend/tests/test_uploads.py
"""Tests for upload signed-URL generation — service layer and API endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from main import app
from middleware.auth import get_current_user_claims
from models.storage import UploadUrlResponse
from services import storage as storage_service


# --------------------------------------------------------------------------
# GCS fake — replaces core.storage.bucket in the service module
# --------------------------------------------------------------------------

class _FakeBlob:
    """Minimal Blob fake that returns a predictable signed URL."""

    def __init__(self, path: str) -> None:
        self.path = path

    def generate_signed_url(self, **kwargs: Any) -> str:
        return (
            f"https://storage.googleapis.com/test-bucket/{self.path}"
            "?X-Goog-Signature=fake"
        )


class _FakeBucket:
    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(path)


class _FakeSettings:
    def __init__(self, bucket_name: str) -> None:
        self.storage_bucket_name = bucket_name


# --------------------------------------------------------------------------
# Service-layer tests  (sync — generate_upload_url is not async)
# --------------------------------------------------------------------------

def test_generate_upload_url_correct_path_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    result = storage_service.generate_upload_url("uid-1", "image/jpeg", "post")

    assert result.object_path.startswith("uploads/post/uid-1/")
    assert result.object_path.endswith(".jpg")
    assert result.upload_url.startswith("https://")


def test_generate_upload_url_rejects_invalid_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    with pytest.raises(storage_service.InvalidContentType):
        storage_service.generate_upload_url("uid-1", "image/gif", "post")


@pytest.mark.parametrize(
    "content_type,expected_ext",
    [
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
    ],
)
def test_object_path_contains_uid_and_correct_extension(
    content_type: str,
    expected_ext: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    result = storage_service.generate_upload_url("uid-42", content_type, "avatar")

    assert "uid-42" in result.object_path
    assert result.object_path.endswith(f".{expected_ext}")


def test_generate_upload_url_supports_mp4_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    result = storage_service.generate_upload_url("uid-1", "video/mp4", "post")

    assert result.object_path.endswith(".mp4")


def test_size_cap_allows_image_under_10mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    result = storage_service.generate_upload_url(
        "uid-1", "image/jpeg", "post", size_bytes=9 * 1024 * 1024
    )
    assert result.object_path.endswith(".jpg")


def test_size_cap_rejects_image_over_10mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    with pytest.raises(storage_service.FileTooLarge):
        storage_service.generate_upload_url(
            "uid-1", "image/jpeg", "post", size_bytes=11 * 1024 * 1024
        )


def test_size_cap_allows_video_up_to_100mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    result = storage_service.generate_upload_url(
        "uid-1", "video/mp4", "post", size_bytes=99 * 1024 * 1024
    )
    assert result.object_path.endswith(".mp4")

    with pytest.raises(storage_service.FileTooLarge):
        storage_service.generate_upload_url(
            "uid-1", "video/mp4", "post", size_bytes=101 * 1024 * 1024
        )


def test_expires_at_is_15_minutes_from_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())

    before = datetime.now(timezone.utc)
    result = storage_service.generate_upload_url("uid-1", "image/png", "story")

    # expires_at must be within 2 seconds of exactly 15 minutes from now.
    diff_seconds = (result.expires_at - before).total_seconds()
    assert abs(diff_seconds - 900) < 2


# --------------------------------------------------------------------------
# Download / read-URL signing tests
# --------------------------------------------------------------------------

def test_object_path_from_url_extracts_path_for_our_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        storage_service, "get_settings", lambda: _FakeSettings("test-bucket")
    )

    url = "https://storage.googleapis.com/test-bucket/uploads/post/uid-1/abc.jpg"
    assert storage_service.object_path_from_url(url) == "uploads/post/uid-1/abc.jpg"


@pytest.mark.parametrize(
    "url",
    [
        "https://picsum.photos/seed/x/300/400",
        "https://i.pravatar.cc/150?img=3",
        "https://storage.googleapis.com/other-bucket/uploads/x.jpg",
        "not-a-url",
    ],
)
def test_object_path_from_url_returns_none_for_foreign_urls(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        storage_service, "get_settings", lambda: _FakeSettings("test-bucket")
    )
    assert storage_service.object_path_from_url(url) is None


def test_sign_media_url_signs_our_bucket_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())
    monkeypatch.setattr(
        storage_service, "get_settings", lambda: _FakeSettings("test-bucket")
    )

    url = "https://storage.googleapis.com/test-bucket/uploads/post/uid-1/abc.jpg"
    signed = storage_service.sign_media_url(url)

    assert "X-Goog-Signature=fake" in signed
    assert "uploads/post/uid-1/abc.jpg" in signed


def test_sign_media_url_leaves_foreign_urls_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service, "bucket", _FakeBucket())
    monkeypatch.setattr(
        storage_service, "get_settings", lambda: _FakeSettings("test-bucket")
    )

    url = "https://picsum.photos/seed/x/300/400"
    assert storage_service.sign_media_url(url) == url


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


def _sample_response() -> UploadUrlResponse:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    return UploadUrlResponse(
        upload_url=(
            "https://storage.googleapis.com/test-bucket"
            "/uploads/post/uid-1/abc.jpg?signed=1"
        ),
        object_path="uploads/post/uid-1/abc.jpg",
        expires_at=now + timedelta(minutes=15),
    )


def test_post_sign_returns_200_with_upload_url_and_object_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    def fake_generate(
        uid: str, content_type: str, purpose: str, size_bytes: int | None = None
    ) -> UploadUrlResponse:
        return _sample_response()

    monkeypatch.setattr(storage_service, "generate_upload_url", fake_generate)

    response = client.post(
        "/api/v1/uploads/sign",
        json={"content_type": "image/jpeg", "purpose": "post"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "upload_url" in data
    assert "object_path" in data
    assert data["object_path"] == "uploads/post/uid-1/abc.jpg"
    assert data["upload_url"].startswith("https://")


def test_post_sign_invalid_content_type_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_claims({"uid": "uid-1", "admin": False})

    def fake_generate(
        uid: str, content_type: str, purpose: str, size_bytes: int | None = None
    ) -> UploadUrlResponse:
        raise storage_service.InvalidContentType(content_type)

    monkeypatch.setattr(storage_service, "generate_upload_url", fake_generate)

    response = client.post(
        "/api/v1/uploads/sign",
        json={"content_type": "image/gif", "purpose": "post"},
    )

    assert response.status_code == 400
    body = response.json()
    # API-03: contract-specified error code (docs/API_CONTRACTS.md §8).
    assert body["error"]["code"] == "INVALID_CONTENT_TYPE"
    assert body["error"]["field"] == "content_type"
