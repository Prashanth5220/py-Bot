"""
webhook.py — POST /webhook  (Telegram sends updates here)
             GET  /health-tg (optional: check webhook status)
             POST /setup-webhook (register your URL with Telegram, run once after deploy)

How Telegram webhooks work:
  1. You call setWebhook(url) once after your server is live.
  2. Telegram pushes every incoming message as a POST to that URL.
  3. You respond 200 OK quickly (< 5s) — Telegram retries if you don't.

Java equivalent: @RestController with POST /webhook.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel

from src.config import settings
from src.handlers.user_handler import handle_user_message
from src.handlers.admin_handler import handle_admin_message
from src.services.dynamodb import get_session
from src.services.telegram import send_message, set_webhook, delete_webhook

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Telegram Webhook"])

# Admin steps set — sessions in these steps go to the admin handler
ADMIN_STEPS = {
    "ADMIN_ID", "ADMIN_PASSWORD", "ADMIN_MENU",
    "ADMIN_APPOINTMENTS", "ADMIN_USERS",
    "ADMIN_MANAGE_APPT_ID", "ADMIN_MANAGE_APPT_ACTION",
    "DOCTOR_MENU",
    "ADMIN_ADD_SLOT_DOCTOR", "ADMIN_ADD_SLOT_DATE", "ADMIN_ADD_SLOT_TIME",
}


# ── Telegram Update model — just enough fields we need ────────────────────────
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict | None = None
    callback_query: dict | None = None  # for inline button taps (future use)

    class Config:
        extra = "allow"  # ignore extra fields Telegram may send


# ── POST /webhook ─────────────────────────────────────────────────────────────
@router.post("/webhook")
async def telegram_webhook(update: TelegramUpdate, background_tasks: BackgroundTasks):
    """
    Telegram pushes every update here.
    We MUST return 200 immediately — actual processing happens in background.
    """
    background_tasks.add_task(_process_update, update)
    return {"ok": True}


async def _process_update(update: TelegramUpdate) -> None:
    """Process the Telegram update asynchronously (runs after 200 is returned)."""
    try:
        # ── Extract message info ──────────────────────────────────────────────
        msg = update.message
        if not msg:
            return  # ignore non-message updates (channel posts, etc.)

        chat_id: int = msg.get("chat", {}).get("id")
        text: str = msg.get("text", "").strip()

        if not chat_id or not text:
            return

        # Telegram chat_id is our session identifier (unique per user)
        session_id = str(chat_id)

        logger.info(f"📩 [{session_id}] {text[:60]}")

        # ── Load session ──────────────────────────────────────────────────────
        session = await get_session(session_id)
        role = session.get("role", "GUEST")
        current_step = session.get("currentStep", "START")

        normalized = text.strip().lower()
        admin_intent = current_step in ("START", "MAIN_MENU") and \
            any(k in normalized for k in ("3", "admin", "management"))

        # ── Route to correct handler ──────────────────────────────────────────
        if role == "ADMIN" or current_step in ADMIN_STEPS or admin_intent:
            reply = await handle_admin_message(session_id, text, session)
        else:
            reply = await handle_user_message(session_id, text, session)

        # ── Send reply ────────────────────────────────────────────────────────
        await send_message(chat_id, reply)

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"❌ Error in _process_update: {exc}\n{tb}")
        # Also write to file for debugging
        try:
            with open("debug.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== ERROR ===\n{tb}\n")
        except Exception:
            pass
        try:
            await send_message(chat_id, "⚠️ Something went wrong. Please type /start to begin again.")
        except Exception:
            pass


# ── POST /setup-webhook — run this ONCE after deploying to ECS ────────────────
@router.post("/setup-webhook")
async def register_webhook():
    """
    Registers your bot's webhook URL with Telegram.
    Call this once after your ECS service is live:
        curl -X POST https://your-domain.com/setup-webhook

    The webhook URL will be: https://your-domain.com/webhook
    """
    base = settings.booking_calendar_url
    # Strip trailing /book or /book/ to get the root URL
    for suffix in ("/book/", "/book"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")
    webhook_url = f"{base}/webhook"
    result = await set_webhook(webhook_url)
    return result


# ── DELETE /setup-webhook — switch to polling (local dev) ─────────────────────
@router.delete("/setup-webhook")
async def remove_webhook():
    """Remove the webhook so you can use polling locally."""
    return await delete_webhook()
