"""
Authentication helpers.

In production, this uses HeyGen OAuth.
In dev mode (COMMUNITY_DEV_MODE=true), it uses a mock session.
"""

from fastapi import Request, HTTPException
from community.config import DEV_MODE, ADMIN_EMAIL_DOMAIN


# Session key used to store user info after OAuth
SESSION_KEY = "user"


def get_current_user(request: Request) -> dict:
    """Get the authenticated user from the session.

    Returns dict with at least: account_id, email, name
    Raises 401 if not logged in.
    """
    if DEV_MODE:
        # In dev mode, use query params or defaults for testing
        email = request.query_params.get("dev_email", "testuser@example.com")
        return {
            "account_id": request.query_params.get("dev_account_id", "dev-account-001"),
            "email": email,
            "name": request.query_params.get("dev_name", "Test User"),
        }

    user = request.session.get(SESSION_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> dict:
    """Get user and verify they have a @heygen.com email.

    Returns the user dict if authorized.
    Raises 403 if not an admin.
    """
    user = get_current_user(request)
    email = user.get("email", "")
    if not email.endswith(f"@{ADMIN_EMAIL_DOMAIN}"):
        raise HTTPException(status_code=403, detail="Admin access requires @heygen.com email")
    return user
