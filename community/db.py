"""
SQLite database layer for the Community Events portal.

Tables:
  events         – one row per community event (e.g. "miami")
  claims         – one row per user activation (audit log)
  admin_actions  – tracks admin operations (rerun, revoke, etc.)
"""

import sqlite3
import os
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

DB_PATH = os.environ.get("COMMUNITY_DB_PATH", "community.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript(SCHEMA)


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    starts_at   TEXT,
    ends_at     TEXT,
    grant_days  INTEGER NOT NULL DEFAULT 3,
    api_quota   INTEGER NOT NULL DEFAULT 1000,
    api_sub     INTEGER NOT NULL DEFAULT 1,
    max_claims  INTEGER NOT NULL DEFAULT 200,
    qr_token    TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by  TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_slug  TEXT    NOT NULL REFERENCES events(slug),
    account_id  TEXT    NOT NULL,
    email       TEXT    NOT NULL,
    name        TEXT,
    status      TEXT    NOT NULL DEFAULT 'PENDING',
    error_msg   TEXT,
    attempt     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    granted_at  TEXT,
    expires_at  TEXT,
    rerun_by    TEXT,
    UNIQUE(event_slug, account_id)
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_email TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    target_id   TEXT,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_event  ON claims(event_slug);
CREATE INDEX IF NOT EXISTS idx_claims_email  ON claims(email);
"""


# ---------------------------------------------------------------------------
# Events CRUD
# ---------------------------------------------------------------------------

def create_event(slug: str, name: str, created_by: str = None, **kwargs) -> dict:
    with get_db() as conn:
        cols = ["slug", "name", "created_by"]
        vals = [slug, name, created_by]
        for key in ("active", "starts_at", "ends_at", "grant_days",
                     "api_quota", "api_sub", "max_claims", "qr_token"):
            if key in kwargs:
                cols.append(key)
                vals.append(kwargs[key])
        placeholders = ",".join("?" for _ in vals)
        col_names = ",".join(cols)
        conn.execute(f"INSERT INTO events ({col_names}) VALUES ({placeholders})", vals)
        return get_event(slug)


def get_event(slug: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None


def list_events() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_event(slug: str, **kwargs) -> dict | None:
    allowed = {"name", "active", "starts_at", "ends_at", "grant_days",
               "api_quota", "api_sub", "max_claims", "qr_token"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_event(slug)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [slug]
    with get_db() as conn:
        conn.execute(f"UPDATE events SET {set_clause} WHERE slug = ?", vals)
        return get_event(slug)


# ---------------------------------------------------------------------------
# Claims CRUD
# ---------------------------------------------------------------------------

def create_claim(event_slug: str, account_id: str, email: str,
                 name: str = None) -> dict:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO claims (event_slug, account_id, email, name)
               VALUES (?, ?, ?, ?)""",
            (event_slug, account_id, email.lower().strip(), name),
        )
        row = conn.execute(
            "SELECT * FROM claims WHERE event_slug = ? AND account_id = ?",
            (event_slug, account_id),
        ).fetchone()
        return dict(row)


def get_claim(claim_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return dict(row) if row else None


def get_claim_by_account(event_slug: str, account_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM claims WHERE event_slug = ? AND account_id = ?",
            (event_slug, account_id),
        ).fetchone()
        return dict(row) if row else None


def list_claims(event_slug: str = None, status: str = None,
                limit: int = 100, offset: int = 0) -> list[dict]:
    with get_db() as conn:
        query = "SELECT * FROM claims WHERE 1=1"
        params = []
        if event_slug:
            query += " AND event_slug = ?"
            params.append(event_slug)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_claims(event_slug: str, status: str = None) -> int:
    with get_db() as conn:
        query = "SELECT COUNT(*) FROM claims WHERE event_slug = ?"
        params = [event_slug]
        if status:
            query += " AND status = ?"
            params.append(status)
        return conn.execute(query, params).fetchone()[0]


def get_pending_claims() -> list[dict]:
    """Fetch claims with status PENDING — used by the poller."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.*, e.grant_days, e.api_quota, e.api_sub
               FROM claims c
               JOIN events e ON c.event_slug = e.slug
               WHERE c.status = 'PENDING'
               ORDER BY c.created_at ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def update_claim_status(claim_id: int, status: str,
                        error_msg: str = None,
                        granted_at: str = None,
                        expires_at: str = None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """UPDATE claims
               SET status = ?, error_msg = ?, granted_at = ?,
                   expires_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, error_msg, granted_at, expires_at, now, claim_id),
        )


def rerun_claim(claim_id: int, admin_email: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """UPDATE claims
               SET status = 'PENDING', error_msg = NULL,
                   attempt = attempt + 1, rerun_by = ?, updated_at = ?
               WHERE id = ?""",
            (admin_email, now, claim_id),
        )
        log_admin_action(admin_email, "rerun_claim", str(claim_id))


# ---------------------------------------------------------------------------
# Admin actions log
# ---------------------------------------------------------------------------

def log_admin_action(admin_email: str, action: str,
                     target_id: str = None, detail: str = None):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO admin_actions (admin_email, action, target_id, detail)
               VALUES (?, ?, ?, ?)""",
            (admin_email, action, target_id, detail),
        )


def list_admin_actions(limit: int = 100, offset: int = 0) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM admin_actions
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def event_stats(event_slug: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """SELECT
                 COUNT(*)                                    AS total,
                 SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END)  AS success,
                 SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END)    AS errors,
                 SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END)  AS pending,
                 SUM(CASE WHEN status='RATE_LIMITED' THEN 1 ELSE 0 END) AS rate_limited,
                 SUM(CASE WHEN status='SUCCESS'
                      AND expires_at > datetime('now') THEN 1 ELSE 0 END) AS active_grants
               FROM claims WHERE event_slug = ?""",
            (event_slug,),
        ).fetchone()
        return dict(row) if row else {}
