# backend/services/reports.py
"""Report service layer.

Reports are stored at /reports/{report_id} (uuid4).  No counters are updated
on create/delete — the reports collection is admin-facing only.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from core.firebase import db
from models.report import Report

REPORTS_COLLECTION = "reports"

# Flat collections we can look up by target_id to check authorship.
# "comment" is intentionally absent — it lives in a subcollection and cannot
# be fetched without knowing the parent post_id.
_TARGET_COLLECTIONS: dict[str, str] = {
    "post": "posts",
    "story": "stories",
}


class CannotReportSelf(Exception):
    """Raised when a user attempts to report their own content or account."""


async def _get_content_author_uid(target_type: str, target_id: str) -> str | None:
    """Return the author_uid of the target document, or None if unavailable.

    Returns None when the target type is "user" (handled by the caller) or
    "comment" (subcollection, not resolvable by target_id alone), and when
    the document simply does not exist.
    """
    collection = _TARGET_COLLECTIONS.get(target_type)
    if collection is None:
        return None

    snap = await asyncio.to_thread(
        db.collection(collection).document(target_id).get
    )
    if not snap.exists:
        return None
    return (snap.to_dict() or {}).get("author_uid")


async def create_report(
    reporter_uid: str,
    target_type: str,
    target_id: str,
    reason: str,
) -> Report:
    """File a report. Idempotent: returns any existing pending report silently.

    Args:
        reporter_uid: UID of the user filing the report.
        target_type: One of "post", "comment", "user", "story".
        target_id: The ID of the reported content or user.
        reason: Free-text description (10–500 chars, validated at route level).

    Raises:
        CannotReportSelf: if the reporter is reporting their own content or
            their own account.
    """
    # Self-report guard
    if target_type == "user":
        if reporter_uid == target_id:
            raise CannotReportSelf("You cannot report your own account.")
    else:
        author_uid = await _get_content_author_uid(target_type, target_id)
        if author_uid is not None and author_uid == reporter_uid:
            raise CannotReportSelf("You cannot report your own content.")

    # Idempotency: return an existing pending report for the same reporter+target.
    def _find_existing() -> list[Any]:
        return list(
            db.collection(REPORTS_COLLECTION)
            .where(filter=FieldFilter("reporter_uid", "==", reporter_uid))
            .where(filter=FieldFilter("target_id", "==", target_id))
            .where(filter=FieldFilter("status", "==", "pending"))
            .limit(1)
            .stream()
        )

    existing = await asyncio.to_thread(_find_existing)
    if existing:
        return Report.model_validate(existing[0].to_dict())

    # Persist the new report.
    report_id = str(uuid.uuid4())
    report_data: dict[str, Any] = {
        "id": report_id,
        "reporter_uid": reporter_uid,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "status": "pending",
        "created_at": firestore.SERVER_TIMESTAMP,
        "schema_version": 1,
    }

    await asyncio.to_thread(
        db.collection(REPORTS_COLLECTION).document(report_id).set, report_data
    )

    # Refetch to resolve SERVER_TIMESTAMP into a real datetime.
    snap = await asyncio.to_thread(
        db.collection(REPORTS_COLLECTION).document(report_id).get
    )
    return Report.model_validate(snap.to_dict())


async def get_reports(
    status: str | None = None,
    limit: int = 50,
) -> list[Report]:
    """Return reports ordered by created_at descending.

    Admin-only; caller is responsible for access control.

    Args:
        status: Optional filter — "pending", "reviewed", or "dismissed".
        limit: Maximum number of reports to return; capped at 50.
    """
    cap = min(limit, 50)

    def _query() -> list[Report]:
        q: Any = db.collection(REPORTS_COLLECTION)
        if status is not None:
            q = q.where(filter=FieldFilter("status", "==", status))
        q = q.order_by("created_at", direction=firestore.Query.DESCENDING).limit(cap)
        return [Report.model_validate(snap.to_dict()) for snap in q.stream()]

    return await asyncio.to_thread(_query)


class ReportNotFound(Exception):
    """Raised when a report to resolve does not exist."""


async def resolve_report(
    report_id: str,
    admin_uid: str,
    action: str,
    notes: str,
) -> Report:
    """Record an admin's resolution of a report (API-08, contract §11).

    Sets status to "dismissed" for the dismiss action, otherwise "reviewed",
    and records the action/notes/admin/timestamp. Enforcing the action's side
    effects (blocking content, suspending the user) is deliberately out of
    scope here — those are separate moderation operations; this endpoint
    records the decision so the report leaves the pending queue.

    Raises:
        ReportNotFound: if the report does not exist.
    """
    ref = db.collection(REPORTS_COLLECTION).document(report_id)
    snap = await asyncio.to_thread(ref.get)
    if not snap.exists:
        raise ReportNotFound(report_id)

    new_status = "dismissed" if action == "dismiss" else "reviewed"
    updates: dict[str, Any] = {
        "status": new_status,
        "resolution_action": action,
        "resolution_notes": notes,
        "resolved_by": admin_uid,
        "resolved_at": firestore.SERVER_TIMESTAMP,
    }
    await asyncio.to_thread(ref.update, updates)
    snap2 = await asyncio.to_thread(ref.get)
    return Report.model_validate(snap2.to_dict())
