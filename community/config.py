"""
Configuration for the community portal.
Reads from config.json (same format as existing slack-bot config).
"""

import json
import os

CONFIG_PATH = os.environ.get("COMMUNITY_CONFIG_PATH", "config.json")

_config = None


def load_config() -> dict:
    global _config
    if _config is None:
        with open(CONFIG_PATH) as f:
            _config = json.load(f)
    return _config


def get_slack_team_id() -> str:
    return load_config()["slack_team_id"]


def get_slack_channel_id() -> str:
    return load_config()["slack_channel_id"]


def get_heygen_bot_user_id() -> str:
    return load_config()["heygen_bot_user_id"]


# OAuth placeholder — replace with real HeyGen OAuth config
HEYGEN_OAUTH_CLIENT_ID = os.environ.get("HEYGEN_OAUTH_CLIENT_ID", "")
HEYGEN_OAUTH_CLIENT_SECRET = os.environ.get("HEYGEN_OAUTH_CLIENT_SECRET", "")
HEYGEN_OAUTH_REDIRECT_URI = os.environ.get(
    "HEYGEN_OAUTH_REDIRECT_URI",
    "http://localhost:8000/auth/callback",
)

# For local testing: set to "true" to skip OAuth and use a mock user
DEV_MODE = os.environ.get("COMMUNITY_DEV_MODE", "false").lower() == "true"

# Admin email domain
ADMIN_EMAIL_DOMAIN = "heygen.com"
