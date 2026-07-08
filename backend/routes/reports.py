# backend/routes/reports.py
"""Report routes — file and list user-submitted reports."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from middleware.auth import get_current_user_claims, require_admin
from models.report import CreateReportRequest, ResolveReportRequest
from services import reports as reports_service

router = APIRouter(prefix="/reports", tags=["reports"])


def _meta() -> dict[str, str]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("", status_code=201)
async def create_report(
    payload: CreateReportRequest,
    claims: dict[str, Any] = Depends(get_current_user_claims),
) -> JSONResponse:
    """File a report against a post, comment, story, or user.

    Idempotent: filing a duplicate pending report returns the original silently.
    """
    try:
        report = await reports_service.create_report(
            reporter_uid=claims["uid"],
            target_type=payload.target_type,
            target_id=payload.target_id,
            reason=payload.reason,
        )
    except reports_service.CannotReportSelf as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_INPUT",
                "message": "You cannot report your own content.",
            },
        ) from exc

    return JSONResponse(
        status_code=201,
        content={"data": {"report": report.model_dump(mode="json")}, "meta": _meta()},
    )


@router.get("")
async def list_reports(
    status: str | None = None,
    limit: int = 50,
    admin_claims: dict[str, Any] = Depends(require_admin),
) -> JSONResponse:
    """List reports, newest first. Admin only.

    Optionally filter by status: "pending", "reviewed", or "dismissed".
    """
    reports = await reports_service.get_reports(status=status, limit=limit)
    return JSONResponse(
        content={
            "data": {"reports": [r.model_dump(mode="json") for r in reports]},
            "meta": _meta(),
        }
    )


@router.post("/{report_id}/resolve")
async def resolve_report(
    report_id: str,
    payload: ResolveReportRequest,
    admin_claims: dict[str, Any] = Depends(require_admin),
) -> JSONResponse:
    """Resolve a report (admin only). API-08 / contract §11."""
    try:
        report = await reports_service.resolve_report(
            report_id=report_id,
            admin_uid=admin_claims["uid"],
            action=payload.action,
            notes=payload.notes,
        )
    except reports_service.ReportNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Report '{report_id}' not found."},
        ) from exc

    return JSONResponse(
        content={"data": {"report": report.model_dump(mode="json")}, "meta": _meta()}
    )
