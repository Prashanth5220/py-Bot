"""
renderers.py — functions that produce WhatsApp reply strings (menus, lists).

Java equivalent: Thymeleaf / String.format() helper methods.
"""
from datetime import datetime, timezone, timedelta


# ── IST date/time helpers ──────────────────────────────────────────────────────

def get_today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%Y-%m-%d")


def get_current_time_ist() -> str:
    """Return current time in IST as HH:MM."""
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%H:%M")


def friendly_date(iso: str) -> str:
    """'2026-03-15' → '15 Mar 2026'"""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d")
        return dt.strftime("%-d %b %Y")  # Linux; use %#d on Windows
    except Exception:
        return iso


# ── Menu renderers ─────────────────────────────────────────────────────────────

def render_main_menu() -> str:
    return (
        "🏥 *Healix Premier Healthcare* 💙\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome! How can we assist you today?\n\n"
        "1️⃣  *New Patient*    — Register profile\n"
        "2️⃣  *Returning*      — Login to account\n"
        "3️⃣  *Management*     — Admin access\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Type a number or just say 'Hi' to start._"
    )


def render_user_menu() -> str:
    return (
        "🏥 *Healix Patient Dashboard* 📋\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣  📅  *Book Appointment*\n"
        "2️⃣  📋  *View or Modify Appointment*\n"
        "3️⃣  🏥  *Our Services*\n"
        "4️⃣  📝  *Patient Feedback*\n"
        "5️⃣  🚨  *Emergency Contact*\n"
        "6️⃣  🤝  *Human Support*\n"
        "7️⃣  ❌  *Cancel Appointment*\n"
        "8️⃣  🗓️  *Reschedule Appt*\n"
        "9️⃣  🔐  *Account Security*\n"
        "0️⃣  🚪  *Logout*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Tip: Type a number corresponding to the menu above!_"
    )


def render_list(items: list[str], title: str) -> str:
    """Numbered list renderer."""
    lines = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
    return f"{title}\n\n{lines}\n\n_Type back to go back._"


def status_label(status: str) -> str:
    labels = {
        "BOOKED": "✅ Booked",
        "CANCELED": "🚫 Cancelled",
        "APPROVED": "✅ Approved",
        "REJECTED": "❌ Rejected",
        "AVAILABLE": "🟢 Available",
    }
    return labels.get(status, status or "N/A")
