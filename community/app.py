"""
Community Events Portal — FastAPI application.

Run locally:
    COMMUNITY_DEV_MODE=true uvicorn community.app:app --reload --port 8000

Endpoints:
    /community/{slug}           — Event check-in page (HTML)
    /community/{slug}/qr        — QR code image
    /community/admin/            — Admin dashboard (HTML)
    /api/community/claim         — Create a claim (POST)
    /api/community/claim/status  — Check claim status (GET)
    /api/community/admin/*       — Admin API endpoints
"""

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware
import os

from community import db
from community.models import (
    EventCreate, EventUpdate, ClaimRequest, ClaimResponse,
    ClaimStatusResponse, EventStatsResponse,
)
from community.auth import get_current_user, require_admin
from community.qr import generate_qr_png

app = FastAPI(title="HeyGen Community Events Portal")

# Session middleware (use a real secret in production)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-change-me"),
)

# Templates and static files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.on_event("startup")
def startup():
    db.init_db()


# ---------------------------------------------------------------------------
# HTML Pages
# ---------------------------------------------------------------------------

@app.get("/community/{slug}")
def event_page(slug: str, request: Request):
    """Render the event check-in page."""
    event = db.get_event(slug)
    if not event:
        raise HTTPException(404, "Event not found")
    try:
        user = get_current_user(request)
    except HTTPException:
        # Not logged in — in production, redirect to OAuth
        # In dev mode this won't happen (dev user always exists)
        raise HTTPException(401, "Please log in to HeyGen first")
    return templates.TemplateResponse("event.html", {
        "request": request,
        "event": event,
        "user": user,
    })


@app.get("/community/{slug}/qr")
def event_qr(slug: str, request: Request):
    """Generate a QR code PNG pointing to the event check-in page."""
    event = db.get_event(slug)
    if not event:
        raise HTTPException(404, "Event not found")
    base_url = str(request.base_url).rstrip("/")
    event_url = f"{base_url}/community/{slug}"
    if event.get("qr_token"):
        event_url += f"?t={event['qr_token']}"
    png_bytes = generate_qr_png(event_url)
    return Response(content=png_bytes, media_type="image/png")


# ---------------------------------------------------------------------------
# User-facing API
# ---------------------------------------------------------------------------

@app.post("/api/community/claim")
def create_claim(body: ClaimRequest, request: Request):
    """User clicks 'Activate' — creates a PENDING claim."""
    user = get_current_user(request)
    event = db.get_event(body.event_slug)

    if not event:
        raise HTTPException(404, "Event not found")
    if not event["active"]:
        raise HTTPException(400, "Event is not active")

    # Check event time window
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if event["starts_at"] and now < event["starts_at"]:
        raise HTTPException(400, "Event has not started yet")
    if event["ends_at"] and now > event["ends_at"]:
        raise HTTPException(400, "Event has ended")

    # Check max claims
    current_count = db.count_claims(body.event_slug, status="SUCCESS")
    if current_count >= event["max_claims"]:
        raise HTTPException(400, "Event has reached maximum activations")

    # Check if already claimed
    existing = db.get_claim_by_account(body.event_slug, user["account_id"])
    if existing:
        return JSONResponse(
            content={"status": existing["status"], "message": "Already activated",
                     "claim_id": existing["id"], "expires_at": existing["expires_at"]},
            status_code=200,
        )

    # Create claim
    claim = db.create_claim(
        event_slug=body.event_slug,
        account_id=user["account_id"],
        email=user["email"],
        name=user.get("name"),
    )
    return {"status": "PENDING", "message": "Activation in progress", "claim_id": claim["id"]}


@app.get("/api/community/claim/status")
def claim_status(event_slug: str, request: Request):
    """Poll for claim status after clicking Activate."""
    user = get_current_user(request)
    claim = db.get_claim_by_account(event_slug, user["account_id"])
    if not claim:
        return ClaimStatusResponse(status="NOT_FOUND", message="No activation found")
    return ClaimStatusResponse(
        status=claim["status"],
        expires_at=claim["expires_at"],
        message="Creator access granted!" if claim["status"] == "SUCCESS" else
                "Processing..." if claim["status"] == "PENDING" else
                f"Error: {claim['error_msg']}" if claim["status"] == "ERROR" else
                claim["status"],
    )


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

@app.get("/api/community/admin/events")
def admin_list_events(request: Request):
    require_admin(request)
    events = db.list_events()
    for e in events:
        e["stats"] = db.event_stats(e["slug"])
    return events


@app.post("/api/community/admin/events")
def admin_create_event(body: EventCreate, request: Request):
    admin = require_admin(request)
    existing = db.get_event(body.slug)
    if existing:
        raise HTTPException(400, f"Event '{body.slug}' already exists")
    event = db.create_event(
        slug=body.slug,
        name=body.name,
        created_by=admin["email"],
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        grant_days=body.grant_days,
        api_quota=body.api_quota,
        api_sub=int(body.api_sub),
        max_claims=body.max_claims,
    )
    db.log_admin_action(admin["email"], "create_event", body.slug)
    return event


@app.patch("/api/community/admin/events/{slug}")
def admin_update_event(slug: str, body: EventUpdate, request: Request):
    admin = require_admin(request)
    event = db.get_event(slug)
    if not event:
        raise HTTPException(404, "Event not found")
    updates = body.model_dump(exclude_unset=True)
    if "api_sub" in updates:
        updates["api_sub"] = int(updates["api_sub"])
    if "active" in updates:
        updates["active"] = int(updates["active"])
    updated = db.update_event(slug, **updates)
    db.log_admin_action(admin["email"], "update_event", slug, str(updates))
    return updated


@app.get("/api/community/admin/events/{slug}")
def admin_get_event(slug: str, request: Request):
    require_admin(request)
    event = db.get_event(slug)
    if not event:
        raise HTTPException(404, "Event not found")
    event["stats"] = db.event_stats(slug)
    return event


@app.get("/api/community/admin/events/{slug}/claims")
def admin_event_claims(slug: str, request: Request,
                       status: str = None, limit: int = 100, offset: int = 0):
    require_admin(request)
    return db.list_claims(event_slug=slug, status=status, limit=limit, offset=offset)


@app.get("/api/community/admin/claims")
def admin_list_claims(request: Request, event_slug: str = None,
                      status: str = None, limit: int = 100, offset: int = 0):
    require_admin(request)
    return db.list_claims(event_slug=event_slug, status=status, limit=limit, offset=offset)


@app.post("/api/community/admin/claims/{claim_id}/rerun")
def admin_rerun_claim(claim_id: int, request: Request):
    admin = require_admin(request)
    claim = db.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    if claim["status"] == "PENDING":
        raise HTTPException(400, "Claim is already pending")
    db.rerun_claim(claim_id, admin["email"])
    return {"status": "ok", "message": f"Claim {claim_id} requeued for processing"}


@app.get("/api/community/admin/actions")
def admin_list_actions(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    return db.list_admin_actions(limit=limit, offset=offset)
