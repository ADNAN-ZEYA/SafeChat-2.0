# backend/models/report.py
"""Report data models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ReportAction = Literal["block_content", "warn_user", "suspend_user", "dismiss"]


class Report(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    reporter_uid: str
    target_type: Literal["post", "comment", "user", "story"]
    target_id: str
    reason: str
    status: Literal["pending", "reviewed", "dismissed"] = "pending"
    resolution_action: ReportAction | None = None
    resolution_notes: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    schema_version: int = 1


class CreateReportRequest(BaseModel):
    target_type: Literal["post", "comment", "user", "story"]
    target_id: str
    reason: str = Field(..., min_length=10, max_length=500)


class ResolveReportRequest(BaseModel):
    """Body for POST /admin/reports/{reportId}/resolve (contract §11)."""

    action: ReportAction
    notes: str = Field(default="", max_length=1000)
