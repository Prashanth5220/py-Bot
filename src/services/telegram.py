"""
telegram.py — send messages via Telegram Bot API.

Telegram Bot API docs: https://core.telegram.org/bots/api
We use raw httpx calls — no heavy library needed.

Java equivalent: a RestTemplate / WebClient wrapper.
"""
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(chat_id: int | str, text: str) -> None:
    """
    Send a text message to a Telegram chat.

    `chat_id`  — the user's Telegram chat ID (from incoming update)
    `text`     — message text; supports Telegram Markdown (*bold*, _italic_, `code`)

    In local dev (no token set) it just logs instead of calling Telegram.
    """
    if not settings.telegram_bot_token:
        logger.info(f"[DEV] Telegram → {chat_id}: {text[:120]}")
        return

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",      # lets you use *bold* and _italic_
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{_BASE_URL}/sendMessage", json=payload)
            if resp.status_code != 200:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.exception(f"Failed to send Telegram message: {exc}")


async def set_webhook(webhook_url: str) -> dict:
    """
    Register your server's URL with Telegram so it sends updates to you.
    Call this once after deploy:  POST /setup-webhook in main.py
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE_URL}/setWebhook",
            json={"url": webhook_url, "drop_pending_updates": True},
        )
        return resp.json()


async def delete_webhook() -> dict:
    """Remove the webhook (switch back to polling mode for local dev)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{_BASE_URL}/deleteWebhook", json={"drop_pending_updates": True})
        return resp.json()
