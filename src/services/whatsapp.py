"""
whatsapp.py — send messages via Meta WhatsApp Cloud API.

Java equivalent: a RestTemplate / WebClient wrapper calling the Meta Graph API.
"""
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# Meta Graph API base URL
_BASE_URL = (
    f"https://graph.facebook.com/v19.0"
    f"/{settings.whatsapp_phone_number_id}/messages"
)
_HEADERS = {
    "Authorization": f"Bearer {settings.whatsapp_api_token}",
    "Content-Type": "application/json",
}


async def send_text_message(to: str, text: str) -> None:
    """
    Send a plain text WhatsApp message to a phone number.

    `to` should be the phone number WITHOUT leading + (e.g. "919876543210").
    Meta requires the full international number without the + prefix.
    """
    if not settings.whatsapp_api_token or settings.whatsapp_api_token == "your_permanent_token":
        # Local dev: just log instead of calling Meta
        logger.info(f"[DEV] WhatsApp → {to}: {text[:120]}")
        return

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_BASE_URL, headers=_HEADERS, json=payload)
        if resp.status_code != 200:
            logger.error(
                f"Meta API error {resp.status_code}: {resp.text[:200]}"
            )
        else:
            logger.debug(f"✅ Sent to {to}: {text[:60]}")
