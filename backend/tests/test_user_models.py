# backend/tests/test_user_models.py
"""Validation tests for user request models (CQ-05)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.user import OnboardRequest, UpdateProfileRequest


def test_update_profile_username_normalises_case() -> None:
    req = UpdateProfileRequest(username="  Adnan_Z ")
    assert req.username == "adnan_z"


@pytest.mark.parametrize(
    "bad",
    [
        "has space",
        "UPPER-CASE!",
        "dot.name",
        "emoji😀name",
        "ab",  # too short after the pattern check
        "x" * 31,
    ],
)
def test_update_profile_rejects_invalid_usernames(bad: str) -> None:
    with pytest.raises(ValidationError):
        UpdateProfileRequest(username=bad)


def test_update_profile_username_optional() -> None:
    req = UpdateProfileRequest(bio="hello")
    assert req.username is None


def test_onboard_and_update_share_the_same_rule() -> None:
    """Both entry points must accept/reject identically."""
    ok = "valid_name_123"
    assert OnboardRequest(
        username=ok, display_name="X", dob="2008-01-01"
    ).username == ok
    assert UpdateProfileRequest(username=ok).username == ok
