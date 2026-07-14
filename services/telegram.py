"""
Telegram alert service for mevion platform.

Sends alerts to a configured Telegram chat via the Bot API.
All functions are fire-and-forget — failures are logged, never raised.
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL   = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


def send_alert(message: str) -> None:
    """
    Send a Telegram message to the configured chat.
    Never raises — logs warning on failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return

    try:
        resp = requests.post(
            TELEGRAM_API_URL,
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Telegram alert sent: %s", message[:60])
        else:
            log.warning("Telegram API returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Telegram alert failed: %s", exc)
