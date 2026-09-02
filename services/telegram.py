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


def send_alert(message: str, chat_id: str | None = None) -> None:
    """
    Send a Telegram message to a chat.

    chat_id: optional override — defaults to TELEGRAM_CHAT_ID when omitted,
    so every pre-existing caller (gateway offline, overcurrent) is unaffected.
    Pass an explicit chat_id to send to a different destination with the
    same bot token (e.g. machine stop/start alerts going to their own chat).

    Never raises — logs warning on failure.
    """
    target_chat_id = chat_id or TELEGRAM_CHAT_ID

    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        log.warning("Telegram not configured — TELEGRAM_BOT_TOKEN or chat_id missing")
        return

    try:
        resp = requests.post(
            TELEGRAM_API_URL,
            json={
                "chat_id":    target_chat_id,
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
