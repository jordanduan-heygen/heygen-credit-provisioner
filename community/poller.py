"""
Poller — polls the local SQLite DB for PENDING claims and sends
HeyGen bot commands via Playwright (same Slack automation as before).

This replaces the Google Apps Script polling in the original server.py.
The Playwright/Slack logic is identical — only the data source changed.

Usage:
    1. Run login.py first to save your Slack session cookies
    2. Ensure config.json has slack_team_id, slack_channel_id, heygen_bot_user_id
    3. python -m community.poller
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

from community import db
from community.config import load_config

SESSION_PATH = os.environ.get(
    "SLACK_SESSION_PATH",
    os.path.join(os.path.dirname(__file__), "..", "slack_session", "state.json"),
)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))


class SlackAutomation:
    """Drives Playwright to type HeyGen bot commands in Slack.

    This is the same logic from the original slack-bot/server.py,
    kept intentionally identical so the grant mechanism doesn't change.
    """

    def __init__(self):
        self.page = None
        self.browser = None
        self.browser_context = None
        self.playwright_instance = None
        self.config = load_config()

    def start(self):
        session_path = os.path.normpath(SESSION_PATH)
        if not os.path.exists(session_path):
            print(f"ERROR: No session found at {session_path}. Run login.py first.")
            sys.exit(1)

        print("Launching browser...")
        self.playwright_instance = sync_playwright().start()
        self.browser = self.playwright_instance.chromium.launch(headless=True)
        self.browser_context = self.browser.new_context(
            storage_state=session_path,
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self.page = self.browser_context.new_page()

        channel_url = (
            f"https://app.slack.com/client/"
            f"{self.config['slack_team_id']}/{self.config['slack_channel_id']}"
        )
        print(f"Navigating to {channel_url} ...")
        self.page.goto(channel_url, wait_until="domcontentloaded", timeout=30000)

        selectors_to_try = [
            '[data-qa="message_input"]',
            '[contenteditable="true"]',
            '[data-qa="message-pane"]',
            '.p-workspace__primary_view',
        ]
        for sel in selectors_to_try:
            try:
                self.page.wait_for_selector(sel, timeout=20000)
                print(f"Slack channel loaded (matched: {sel})")
                return
            except Exception:
                continue

        print(f"WARNING: Could not confirm Slack loaded. URL: {self.page.url}")

    def send_message(self, email: str, api_sub: str = "True",
                     api_quota: str = "1000", days: str = "3"):
        """Type a HeyGen command in Slack's compose box and send it."""
        bot_user_id = self.config["heygen_bot_user_id"]

        try:
            self.page.click('[data-qa="message_input"]', timeout=5000)
        except Exception:
            self.page.click('[contenteditable="true"]', timeout=5000)

        time.sleep(0.3)

        self.page.keyboard.type("@HeyGen", delay=50)
        time.sleep(1.5)

        autocomplete_clicked = False
        autocomplete_selectors = [
            f'[data-qa="mention_item"][data-user-id="{bot_user_id}"]',
            '[data-qa="mention_item"]:first-child',
            '[data-qa-type="user"]:first-child',
            'li[role="option"]:first-child',
            '[data-qa="virtual-list-item"]:first-child',
        ]

        for selector in autocomplete_selectors:
            try:
                elem = self.page.wait_for_selector(selector, timeout=1000)
                if elem:
                    elem.click()
                    autocomplete_clicked = True
                    print(f"  Autocomplete selected via: {selector}")
                    break
            except Exception:
                continue

        if not autocomplete_clicked:
            print("  Trying keyboard selection for autocomplete...")
            try:
                self.page.keyboard.press("Tab")
                autocomplete_clicked = True
            except Exception:
                try:
                    self.page.keyboard.press("Enter")
                    autocomplete_clicked = True
                except Exception:
                    pass

        if not autocomplete_clicked:
            print("  WARNING: Autocomplete failed, typing raw mention")
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            time.sleep(0.2)
            self.page.keyboard.type("@HeyGen Bot ", delay=30)

        time.sleep(0.3)

        command_suffix = (
            f" enterprise subscription {email}"
            f" --api-sub {api_sub}"
            f" --api-quota {api_quota}"
            f" --days {days}"
        )
        self.page.keyboard.type(command_suffix, delay=20)
        time.sleep(0.3)

        self.page.keyboard.press("Enter")
        time.sleep(1)

        print(f"  Message sent for: {email}")

    def reload_channel(self):
        channel_url = (
            f"https://app.slack.com/client/"
            f"{self.config['slack_team_id']}/{self.config['slack_channel_id']}"
        )
        self.page.goto(channel_url, wait_until="domcontentloaded", timeout=20000)
        self.page.wait_for_selector('[data-qa="message_input"]', timeout=15000)


def process_pending_claims(automation: SlackAutomation):
    """Fetch PENDING claims from DB and process them via Playwright."""
    pending = db.get_pending_claims()

    if pending:
        print(f"Found {len(pending)} pending claim(s)")

    for claim in pending:
        claim_id = claim["id"]
        email = claim["email"]
        grant_days = str(claim["grant_days"])
        api_quota = str(claim["api_quota"])
        api_sub = "True" if claim["api_sub"] else "False"

        print(f"  Processing claim {claim_id}: {email}")
        try:
            automation.send_message(
                email=email,
                api_sub=api_sub,
                api_quota=api_quota,
                days=grant_days,
            )

            now = datetime.now(timezone.utc)
            granted_at = now.strftime("%Y-%m-%d %H:%M:%S")
            expires_at = (now + timedelta(days=int(grant_days))).strftime("%Y-%m-%d %H:%M:%S")

            db.update_claim_status(
                claim_id=claim_id,
                status="SUCCESS",
                granted_at=granted_at,
                expires_at=expires_at,
            )
            print(f"  Claim {claim_id}: SUCCESS (expires {expires_at})")

        except Exception as e:
            print(f"  Claim {claim_id}: ERROR - {e}")
            db.update_claim_status(
                claim_id=claim_id,
                status="ERROR",
                error_msg=str(e),
            )
            try:
                automation.reload_channel()
            except Exception:
                pass

        time.sleep(2)


def main():
    db.init_db()

    automation = SlackAutomation()
    automation.start()

    print(f"\nPolling DB every {POLL_INTERVAL}s for new claims...\n")

    while True:
        try:
            process_pending_claims(automation)
        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
