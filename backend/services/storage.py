# backend/services/storage.py
"""Image upload service — signed URL generation and public URL resolution.

generate_upload_url() is synchronous: signing is pure CPU work (no network I/O)
and fast enough to call directly from an async route handler.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.config import get_settings
from core.storage import bucket
from models.storage import UploadUrlResponse

SIGNED_URL_TTL_MINUTES = 15
# Read URLs live longer than upload URLs: a feed stays open and CachedNetworkImage
# keeps the bytes after first load, so 12h avoids broken images mid-session.
SIGNED_DOWNLOAD_TTL_HOURS = 12

_PUBLIC_URL_PREFIX = "https://storage.googleapis.com/"

_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
}

# Contract size caps (docs/API_CONTRACTS.md §8): 10 MB images, 100 MB videos.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024


class InvalidContentType(Exception):
    """Raised when an unsupported content type is requested."""


class FileTooLarge(Exception):
    """Raised when the declared size_bytes exceeds the cap for its media type."""

    def __init__(self, size_bytes: int, cap: int) -> None:
        self.size_bytes = size_bytes
        self.cap = cap
        super().__init__(f"{size_bytes} bytes exceeds the {cap}-byte limit.")


def generate_upload_url(
    uid: str,
    content_type: str,
    purpose: str,
    size_bytes: int | None = None,
) -> UploadUrlResponse:
    """Generate a signed GCS upload URL for the given user, content type, and purpose.

    Object path format: uploads/{purpose}/{uid}/{uuid4()}.{ext}

    The returned URL is valid for SIGNED_URL_TTL_MINUTES minutes and accepts
    a single HTTP PUT of the specified content type.

    Args:
        uid: The authenticated user's UID.
        content_type: MIME type — image/jpeg, image/png, image/webp, or video/mp4.
        purpose: Intended use — "post", "story", "avatar", "background", or "message".
        size_bytes: Declared upload size; validated against the contract caps
            (10 MB images / 100 MB videos) when provided.

    Raises:
        InvalidContentType: if content_type is not one of the allowed values.
        FileTooLarge: if size_bytes exceeds the cap for the media type.
    """
    ext = _CONTENT_TYPE_TO_EXT.get(content_type)
    if ext is None:
        raise InvalidContentType(content_type)

    if size_bytes is not None:
        cap = MAX_VIDEO_BYTES if content_type.startswith("video/") else MAX_IMAGE_BYTES
        if size_bytes > cap:
            raise FileTooLarge(size_bytes, cap)

    object_path = f"uploads/{purpose}/{uid}/{uuid.uuid4()}.{ext}"
    expiration = timedelta(minutes=SIGNED_URL_TTL_MINUTES)
    expires_at = datetime.now(timezone.utc) + expiration

    blob = bucket.blob(object_path)
    upload_url: str = blob.generate_signed_url(
        expiration=expiration,
        method="PUT",
        content_type=content_type,
        version="v4",
    )

    return UploadUrlResponse(
        upload_url=upload_url,
        object_path=object_path,
        expires_at=expires_at,
    )


def get_public_url(object_path: str) -> str:
    """Return the public GCS URL for the given object path.

    Format: https://storage.googleapis.com/{bucket}/{object_path}
    """
    bucket_name = get_settings().storage_bucket_name
    return f"https://storage.googleapis.com/{bucket_name}/{object_path}"


def object_path_from_url(url: str) -> str | None:
    """Extract the GCS object path from a stored media URL.

    Stored media URLs are public-style URLs
    (https://storage.googleapis.com/{bucket}/{object_path}). Returns the
    object path for our bucket, or None for anything that isn't a GCS URL
    pointing at our bucket (e.g. external avatars, picsum fallbacks).
    """
    if not url.startswith(_PUBLIC_URL_PREFIX):
        return None
    remainder = url[len(_PUBLIC_URL_PREFIX) :]
    bucket_name = get_settings().storage_bucket_name
    prefix = f"{bucket_name}/"
    if not remainder.startswith(prefix):
        return None
    object_path = remainder[len(prefix) :]
    return object_path or None


def generate_download_url(object_path: str) -> str:
    """Generate a signed GCS read (GET) URL for a private object.

    The bucket is private, so clients cannot fetch objects by their public URL.
    This produces a short-lived signed URL valid for SIGNED_DOWNLOAD_TTL_HOURS.
    """
    blob = bucket.blob(object_path)
    url: str = blob.generate_signed_url(
        expiration=timedelta(hours=SIGNED_DOWNLOAD_TTL_HOURS),
        method="GET",
        version="v4",
    )
    return url


def sign_media_url(url: str) -> str:
    """Convert a stored media URL into a signed read URL when it points at our
    private bucket; otherwise return it unchanged.
    """
    object_path = object_path_from_url(url)
    if object_path is None:
        return url
    return generate_download_url(object_path)
