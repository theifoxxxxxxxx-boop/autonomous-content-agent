from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


PlatformType = Literal["douyin", "xhs"]


class JobCreateResponse(BaseModel):
    job_id: str
    status: str = "accepted"


class EventPayload(BaseModel):
    type: str
    job_id: str
    message: str
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    ok: bool = True
    app: str = "autonomous-content-agent-backend"


class JobStateResponse(BaseModel):
    job_id: str
    status: str
    state: dict[str, Any]


class JobEventsResponse(BaseModel):
    job_id: str
    status: str
    total: int
    events: list[EventPayload]
