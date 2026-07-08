# backend/tests/test_request_id.py
"""Tests for request-id log correlation (PR-03)."""

from __future__ import annotations

import logging

from core.logging import (
    RequestIdFilter,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)


def test_default_request_id_is_dash() -> None:
    assert get_request_id() == "-"


def test_set_and_reset_request_id() -> None:
    token = set_request_id("abc123")
    try:
        assert get_request_id() == "abc123"
    finally:
        reset_request_id(token)
    assert get_request_id() == "-"


def test_new_request_id_is_unique_hex() -> None:
    a, b = new_request_id(), new_request_id()
    assert a != b
    assert all(c in "0123456789abcdef" for c in a)


def test_filter_injects_request_id_onto_record() -> None:
    token = set_request_id("req-42")
    try:
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "req-42"  # type: ignore[attr-defined]
    finally:
        reset_request_id(token)
