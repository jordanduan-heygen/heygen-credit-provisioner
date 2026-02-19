"""
Pydantic models for request/response validation.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9\-]+$", max_length=64)
    name: str = Field(..., max_length=200)
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    grant_days: int = 3
    api_quota: int = 1000
    api_sub: bool = True
    max_claims: int = 200


class EventUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    grant_days: Optional[int] = None
    api_quota: Optional[int] = None
    api_sub: Optional[bool] = None
    max_claims: Optional[int] = None
    qr_token: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    slug: str
    name: str
    active: bool
    starts_at: Optional[str]
    ends_at: Optional[str]
    grant_days: int
    api_quota: int
    api_sub: bool
    max_claims: int
    qr_token: Optional[str]
    created_at: str
    created_by: Optional[str]


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

class ClaimRequest(BaseModel):
    event_slug: str


class ClaimResponse(BaseModel):
    id: int
    event_slug: str
    account_id: str
    email: str
    name: Optional[str]
    status: str
    error_msg: Optional[str]
    attempt: int
    created_at: str
    updated_at: str
    granted_at: Optional[str]
    expires_at: Optional[str]


class ClaimStatusResponse(BaseModel):
    status: str
    expires_at: Optional[str] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class AdminActionResponse(BaseModel):
    id: int
    admin_email: str
    action: str
    target_id: Optional[str]
    detail: Optional[str]
    created_at: str


class EventStatsResponse(BaseModel):
    total: int
    success: int
    errors: int
    pending: int
    rate_limited: int
    active_grants: int
