"""
admin_handler.py — Admin state machine (Python port of admin_lambda.mjs).

Flow:
  Login: ADMIN_ID → ADMIN_PASSWORD → ADMIN_MENU
  ADMIN_MENU:
    1 → Appointments (ADMIN_APPOINTMENTS: filter/paginate/manage)
    2 → Doctors (DOCTOR_MENU: departments / doctors / slots)
    3 → Users (ADMIN_USERS)
    4 → Logout
  ADMIN_APPOINTMENTS:
    1=Today 2=All 3=Booked 4=Approved 5=Rejected 6=Manage 7=Back
    next/prev = pagination
  ADMIN_MANAGE_APPT_ID → ADMIN_MANAGE_APPT_ACTION (Approve/Reject)
  DOCTOR_MENU: 1=Departments 2=Doctors 3=Slots 4=Back
"""
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

import src.services.dynamodb as db
from src.services.dynamodb import (
    scan_all, get_admin, get_appointment,
    _appointments_table, _users_table,
    _departments_table, _doctors_table, _timeslots_table,
    free_slot, update_step, bulk_session_update, full_reset, save_temp,
    reschedule_appointment, create_slot,
)
from src.utils.validators import normalize, sanitize

logger = logging.getLogger(__name__)

IST             = timezone(timedelta(hours=5, minutes=30))
LINE            = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
THIN            = "──────────────────────────"
PAGE_SZ         = 10
ADMIN_TTL_SECS  = 3600  # 1-hour idle timeout


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


# ── Entry point ───────────────────────────────────────────────────────────────
async def handle_admin_message(session_id: str, raw_message: str, session: dict) -> str:
    message = sanitize(raw_message)
    norm    = normalize(message)
    step    = session.get("currentStep", "START")
    td      = session.get("tempData") or {}

    is_pwd_step = step == "ADMIN_PASSWORD"
    logger.info("ADMIN [%s] step=%s input=%s",
                session_id, step, "***" if is_pwd_step else message[:60])

    # ── Global /start reset ───────────────────────────────────────────────────
    if norm in ("start", "/start"):
        await full_reset(session_id, "MAIN_MENU")
        from src.utils.renderers import render_main_menu
        return render_main_menu()

    # ── Entry trigger from main menu ──────────────────────────────────────────
    if (norm in ("3", "admin login", "admin") and
            session.get("role") != "ADMIN" and
            step not in ("ADMIN_ID", "ADMIN_PASSWORD")):
        await update_step(session_id, "ADMIN_ID")
        return (
            f"{LINE}\n🏥  HEALIX — ADMIN PORTAL\n{LINE}\n\n"
            "🔑 *Secure Admin Login*\n\nEnter your *Admin ID*:\n\n" + THIN
        )

    # ── ADMIN_ID ──────────────────────────────────────────────────────────────
    if step == "ADMIN_ID":
        await bulk_session_update(
            session_id,
            "SET tempData.#aid=:aid, currentStep=:step",
            {"#aid": "adminId"},
            {":aid": raw_message.strip(), ":step": "ADMIN_PASSWORD"},
        )
        return f"🔒 *Step 2 of 2*\n\nAdmin ID received ✅\n\nEnter your *Password*:\n\n{THIN}"

    # ── ADMIN_PASSWORD ────────────────────────────────────────────────────────
    if step == "ADMIN_PASSWORD":
        stored_admin = await get_admin(td.get("adminId", ""))
        if not stored_admin or stored_admin.get("password") != raw_message.strip():
            await full_reset(session_id, "START")
            return (
                f"{LINE}\n❌ *Login Failed*\n{LINE}\n\n"
                "Incorrect Admin ID or Password.\n\n"
                "Type *Admin Login* to try again.\n\n" + THIN
            )
        await bulk_session_update(
            session_id,
            "SET #r=:r, currentStep=:s, tempData=:t, adminLoginTime=:lt",
            {"#r": "role"},
            {":r": "ADMIN", ":s": "ADMIN_MENU", ":t": {}, ":lt": int(time.time())},
        )
        return _render_admin_menu(stored_admin.get("name", "Admin"))

    # ── AUTH GUARD ────────────────────────────────────────────────────────────
    if session.get("role") != "ADMIN":
        return f"🔒 *Access Restricted*\n\nType *Admin Login* to authenticate.\n\n{THIN}"

    # ── 1-HOUR IDLE TIMEOUT ───────────────────────────────────────────────────
    login_time = session.get("adminLoginTime", 0)
    if int(time.time()) - int(login_time) > ADMIN_TTL_SECS:
        await full_reset(session_id, "START")
        return (
            f"{LINE}\n⏰  *Session Expired*\n{LINE}\n\n"
            "Your admin session timed out (1-hour inactivity).\n\n"
            "Type *Admin Login* to sign in again.\n\n" + THIN
        )
    # Refresh idle timer (fire-and-forget)
    asyncio.create_task(_refresh_login_time(session_id))

    # ── Route by step ─────────────────────────────────────────────────────────
    match step:
        case "ADMIN_MENU":
            return await _handle_menu(session_id, norm, td)

        case "ADMIN_APPOINTMENTS":
            return await _handle_appointments(session_id, norm, td)

        case "ADMIN_MANAGE_APPT_ID":
            return await _handle_manage_id(session_id, raw_message.strip(), td)

        case "ADMIN_MANAGE_APPT_ACTION":
            return await _handle_manage_action(session_id, norm, td)

        case "DOCTOR_MENU":
            return await _handle_doctor_menu(session_id, norm)

        case "ADMIN_ADD_SLOT_DOCTOR":
            return await _handle_add_slot(session_id, norm, td, stage="doctor")

        case "ADMIN_ADD_SLOT_DATE":
            return await _handle_add_slot(session_id, raw_message.strip(), td, stage="date")

        case "ADMIN_ADD_SLOT_TIME":
            return await _handle_add_slot(session_id, raw_message.strip(), td, stage="time")

        case "ADMIN_USERS":
            cur_page = int(td.get("page", 0))

            if norm in ("next", "n"):
                new_page = cur_page + 1
                await save_temp(session_id, "page", new_page)
                return await _show_users(new_page)
                
            if norm in ("prev", "p", "previous"):
                new_page = max(0, cur_page - 1)
                await save_temp(session_id, "page", new_page)
                return await _show_users(new_page)

            if norm in ("back", "menu"):
                await update_step(session_id, "ADMIN_MENU")
                return _render_admin_menu()

            return await _show_users(cur_page)

        case _:
            await update_step(session_id, "ADMIN_MENU")
            return _render_admin_menu()


async def _refresh_login_time(session_id: str) -> None:
    try:
        await bulk_session_update(
            session_id, "SET adminLoginTime=:t", {}, {":t": int(time.time())}
        )
    except Exception:
        pass


# ── ADMIN MENU ────────────────────────────────────────────────────────────────
async def _handle_menu(session_id: str, norm: str, td: dict) -> str:
    if norm in ("1", "appointment", "appointments"):
        await bulk_session_update(
            session_id, "SET currentStep=:s, tempData=:t", {},
            {":s": "ADMIN_APPOINTMENTS", ":t": {"filter": "ALL", "page": 0}},
        )
        return _render_appt_menu()

    if norm in ("2", "doctor", "doctors"):
        await update_step(session_id, "DOCTOR_MENU")
        return (
            f"{LINE}\n👨‍⚕️  DOCTOR MANAGEMENT\n{LINE}\n\n"
            "1️⃣  View Departments\n"
            "2️⃣  View Doctors\n"
            "3️⃣  View Time Slots\n\n"
            "4️⃣  ← Back\n\n" + THIN
        )

    if norm in ("3", "user", "users"):
        await bulk_session_update(session_id, "SET currentStep=:s, tempData=:t", {}, {":s": "ADMIN_USERS", ":t": {"page": 0}})
        return await _show_users(0)

    if norm in ("4", "logout"):
        await full_reset(session_id, "START")
        return f"{LINE}\n✅ LOGGED OUT\n{LINE}\n\nType *Admin Login* to sign in again.\n\n{THIN}"

    return _render_admin_menu()


# ── APPOINTMENTS SECTION ──────────────────────────────────────────────────────
_FILTER_MAP = {
    "1": "TODAY", "today": "TODAY",
    "2": "ALL",   "all": "ALL", "total": "ALL",
    "3": "BOOKED",   "booked": "BOOKED",
    "4": "APPROVED",  "approved": "APPROVED",
    "5": "REJECTED",  "rejected": "REJECTED",
    "6": "MANAGE",
}


async def _handle_appointments(session_id: str, norm: str, td: dict) -> str:
    cur_filter = td.get("filter", "ALL")
    cur_page   = int(td.get("page", 0))

    if norm in ("next", "n"):
        new_page = cur_page + 1
        await save_temp(session_id, "page", new_page)
        items = await _fetch_appts(cur_filter)
        return _show_appt_page(items, cur_filter, new_page)

    if norm in ("prev", "p", "previous"):
        new_page = max(0, cur_page - 1)
        await save_temp(session_id, "page", new_page)
        items = await _fetch_appts(cur_filter)
        return _show_appt_page(items, cur_filter, new_page)

    if norm in ("back", "7", "menu"):
        await update_step(session_id, "ADMIN_MENU")
        return _render_admin_menu()

    sel = _FILTER_MAP.get(norm)
    if not sel:
        for k, v in _FILTER_MAP.items():
            if k in norm:
                sel = v
                break

    if sel == "MANAGE":
        await update_step(session_id, "ADMIN_MANAGE_APPT_ID")
        return (
            f"{LINE}\n🔧  MANAGE APPOINTMENT\n{LINE}\n\n"
            "Enter the *Appointment ID* to approve or reject:\n\n" + THIN
        )

    if sel:
        await bulk_session_update(
            session_id, "SET tempData=:t", {},
            {":t": {"filter": sel, "page": 0}},
        )
        items = await _fetch_appts(sel)
        return _show_appt_page(items, sel, 0)

    return _render_appt_menu()


async def _fetch_appts(filter_str: str) -> list[dict]:
    from boto3.dynamodb.conditions import Attr
    today = _today_ist()
    if filter_str == "TODAY":
        return await scan_all(_appointments_table, Attr("date").eq(today))
    if filter_str in ("BOOKED", "APPROVED", "REJECTED", "CANCELED"):
        return await scan_all(_appointments_table, Attr("status").eq(filter_str))
    return await scan_all(_appointments_table)


def _show_appt_page(items: list[dict], filter_str: str, page: int) -> str:
    filter_labels = {
        "TODAY": "📅 Today's Appointments", "ALL": "📋 All Appointments",
        "BOOKED": "✅ Booked", "APPROVED": "✔️ Approved", "REJECTED": "❌ Rejected",
    }
    sorted_items = sorted(items, key=lambda a: a.get("createdAt", a.get("date", "")), reverse=True)
    total  = len(sorted_items)
    start  = page * PAGE_SZ
    sliced = sorted_items[start: start + PAGE_SZ]
    title  = filter_labels.get(filter_str, "📋 Appointments")

    if not sliced:
        return f"{LINE}\n{title}\n{LINE}\n\n📭 No appointments found.\n\n{THIN}\nType *back*."

    total_pages = max(1, (total + PAGE_SZ - 1) // PAGE_SZ)
    page_info   = f"Page {page + 1} of {total_pages}  ({total} total)"

    cards = []
    for i, a in enumerate(sliced):
        icon = {"BOOKED": "✅", "APPROVED": "✔️", "REJECTED": "❌",
                "CANCELED": "🚫", "PENDING": "⏳"}.get(a.get("status", ""), "❓")
        cards.append(
            f"┌─ #{str(start + i + 1).zfill(2)} ──────────────────\n"
            f"│ 👤 {a.get('patientName', a.get('name', 'N/A'))}\n"
            f"│ 👨‍⚕️ {a.get('doctor', a.get('doctorName', 'N/A'))}\n"
            f"│ 🏥 {a.get('department', 'N/A')}\n"
            f"│ 📅 {a.get('date', 'N/A')}  ⏰ {a.get('time', a.get('startTime', 'N/A'))}\n"
            f"│ {icon} {a.get('status', 'N/A')}\n"
            f"│ 🆔 `{a.get('appointmentId', '')}`\n"
            "└────────────────────────"
        )

    nav_parts = []
    if page > 0:                      nav_parts.append("⬅ *prev*")
    if start + PAGE_SZ < total:       nav_parts.append("*next* ➡")
    nav_parts.append("*back* → menu")
    nav = "  |  ".join(nav_parts)

    return (
        f"{LINE}\n{title}\n{page_info}\n{LINE}\n\n"
        + "\n\n".join(cards)
        + f"\n\n{THIN}\n{nav}"
    )


# ── APPROVE / REJECT FLOW ─────────────────────────────────────────────────────
async def _handle_manage_id(session_id: str, appt_id: str, td: dict) -> str:
    if normalize(appt_id) in ("back", "menu"):
        await update_step(session_id, "ADMIN_APPOINTMENTS")
        return _render_appt_menu()
    appt = await get_appointment(appt_id)
    if not appt:
        return f"❌ Appointment ID not found.\n\nTry again or type *back*.\n\n{THIN}"
    await bulk_session_update(
        session_id,
        "SET tempData.#aid=:aid, currentStep=:s",
        {"#aid": "manageApptId"},
        {":aid": appt_id, ":s": "ADMIN_MANAGE_APPT_ACTION"},
    )
    return (
        f"{LINE}\n🔧  MANAGE APPOINTMENT\n{LINE}\n\n"
        f"👤 Patient  : {appt.get('patientName', appt.get('name', 'N/A'))}\n"
        f"👨‍⚕️ Doctor   : {appt.get('doctor', 'N/A')}\n"
        f"📅 Date     : {appt.get('date', 'N/A')}\n"
        f"🔖 Status   : {appt.get('status', 'N/A')}\n\n"
        f"{LINE}\n\n"
        "1️⃣  ✅ Approve\n2️⃣  ❌ Reject\n3️⃣  ← Back\n\n" + THIN
    )


async def _handle_manage_action(session_id: str, norm: str, td: dict) -> str:
    if norm in ("3", "back", "menu"):
        await update_step(session_id, "ADMIN_APPOINTMENTS")
        return _render_appt_menu()

    appt_id    = td.get("manageApptId")
    new_status = "APPROVED" if norm == "1" else "REJECTED" if norm == "2" else None

    if not new_status or not appt_id:
        return f"⚠️ Invalid option. 1=Approve  2=Reject  3=Back\n\n{THIN}"

    appt_data = await get_appointment(appt_id)

    tasks: list = [
        bulk_session_update(session_id, "SET currentStep=:s", {}, {":s": "ADMIN_MENU"}),
        db._run(
            db._appointments_table.update_item,
            Key={"appointmentId": appt_id},
            UpdateExpression="SET #s=:s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": new_status},
        ),
    ]
    if new_status == "REJECTED" and appt_data and appt_data.get("slotId"):
        tasks.append(free_slot(appt_data["slotId"]))

    await asyncio.gather(*tasks)

    badge = "✅ Approved" if new_status == "APPROVED" else "❌ Rejected"
    extra = " The time slot is now available for rebooking." if new_status == "REJECTED" else ""
    return (
        f"{LINE}\n{badge} Successfully!\n{LINE}\n\n"
        f"Appointment `{appt_id}` has been {new_status.lower()}.{extra}\n\n{THIN}\n\n"
        + _render_admin_menu()
    )


# ── DOCTOR SECTION ────────────────────────────────────────────────────────────
async def _handle_doctor_menu(session_id: str, norm: str) -> str:
    if norm in ("1", "department", "departments"):
        depts = await scan_all(_departments_table)
        if not depts:
            return f"{LINE}\n🏥 DEPARTMENTS\n{LINE}\n\n📭 No departments yet.\n\nType *Back*.\n\n{THIN}"
        lines = "\n".join(f"  {i + 1}. 🏥 {d.get('name', 'N/A')}" for i, d in enumerate(depts))
        return f"{LINE}\n🏥 DEPARTMENTS ({len(depts)})\n{LINE}\n\n{lines}\n\n{THIN}\nType *Back*."

    if norm in ("2", "doctor", "doctors"):
        return await _show_doctors()

    if norm in ("3", "time", "slots", "time slots"):
        return await _show_slots()

    if norm in ("4", "add slot", "add time slot"):
        await update_step(session_id, "ADMIN_ADD_SLOT_DOCTOR")
        return (
            f"{LINE}\n➕  ADD TIME SLOT\n{LINE}\n\n"
            "Enter the *Doctor ID* to assign this slot to:\n\n" + THIN
        )

    if norm in ("5", "back", "menu"):
        await update_step(session_id, "ADMIN_MENU")
        return _render_admin_menu()

    return (
        f"{LINE}\n👨‍⚕️  DOCTOR MANAGEMENT\n{LINE}\n\n"
        "1️⃣  View Departments\n2️⃣  View Doctors\n3️⃣  View Time Slots\n\n"
        "4️⃣  ➕ Add Time Slot\n5️⃣  ← Back\n\n" + THIN
    )


async def _handle_add_slot(session_id: str, value: str, td: dict, stage: str) -> str:
    norm = value.lower().strip()
    if norm in ("back", "menu"):
        await update_step(session_id, "DOCTOR_MENU")
        return (
            f"{LINE}\n👨‍⚕️  DOCTOR MANAGEMENT\n{LINE}\n\n"
            "1️⃣  View Departments\n2️⃣  View Doctors\n3️⃣  View Time Slots\n\n"
            "4️⃣  ➕ Add Time Slot\n5️⃣  ← Back\n\n" + THIN
        )

    if stage == "doctor":
        # Verify doctor exists
        doctors = await scan_all(_doctors_table)
        doctor = next((d for d in doctors if d.get("docterId") == value), None)
        if not doctor:
            return (
                f"❌ Doctor ID *{value}* not found.\n\n"
                "Enter a valid Doctor ID or type *back*."
            )
        await bulk_session_update(
            session_id,
            "SET tempData.#did=:did, tempData.#dname=:dname, currentStep=:step",
            {"#did": "slotDoctorId", "#dname": "slotDoctorName"},
            {":did": value, ":dname": doctor.get("name", ""), ":step": "ADMIN_ADD_SLOT_DATE"},
        )
        return (
            f"✅ *Doctor:* {doctor.get('name', value)}\n\n"
            "Enter the *date* for this slot (format: YYYY-MM-DD):\n\n" + THIN
        )

    if stage == "date":
        from datetime import date as _date
        try:
            parsed = _date.fromisoformat(value)
            if parsed < _date.today():
                raise ValueError("past")
        except ValueError:
            return (
                "❌ Invalid date. Use *YYYY-MM-DD* (today or future).\n\n"
                "Try again or type *back*."
            )
        await bulk_session_update(
            session_id,
            "SET tempData.#d=:d, currentStep=:step",
            {"#d": "slotDate"},
            {":d": value, ":step": "ADMIN_ADD_SLOT_TIME"},
        )
        return (
            f"✅ *Date:* {value}\n\n"
            "Enter the *time range* (format: HH:MM - HH:MM, e.g. 09:00 - 09:30):\n\n" + THIN
        )

    if stage == "time":
        import re
        import uuid
        m = re.match(r'^(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})$', value)
        if not m:
            return (
                "❌ Invalid format. Use *HH:MM - HH:MM* (e.g. 09:00 - 09:30).\n\n"
                "Try again or type *back*."
            )
        start_t, end_t = m.group(1), m.group(2)
        if start_t >= end_t:
            return (
                "❌ Start time must be before end time.\n\n"
                "Try again or type *back*."
            )
        slot = {
            "slotId":     str(uuid.uuid4()),
            "doctorId":   td.get("slotDoctorId", ""),
            "doctorName": td.get("slotDoctorName", ""),
            "date":       td.get("slotDate", ""),
            "startTime":  start_t,
            "endTime":    end_t,
            "status":     "AVAILABLE",
        }
        await create_slot(slot)
        await update_step(session_id, "DOCTOR_MENU")
        return (
            f"{LINE}\n✅  TIME SLOT CREATED\n{LINE}\n\n"
            f"👨‍⚕️  {td.get('slotDoctorName', 'Doctor')}\n"
            f"📅  {td.get('slotDate')}  ⏰  {start_t} – {end_t}\n"
            f"🆔  `{slot['slotId']}`\n\n"
            "Slot is now AVAILABLE for booking.\n\n" + THIN
        )

    return ""


# ── Display helpers ───────────────────────────────────────────────────────────
def _render_admin_menu(name: str = "") -> str:
    greeting = f"👤  Welcome, {name}\n" if name else ""
    return (
        f"{LINE}\n🏥  HEALIX — ADMIN DASHBOARD\n{greeting}{LINE}\n\n"
        "1️⃣  📅  Appointments\n"
        "2️⃣  👨‍⚕️  Doctors\n"
        "3️⃣  👥  Users\n\n"
        f"4️⃣  🚪  Logout\n\n{THIN}\n💡 Type a number."
    )


def _render_appt_menu() -> str:
    return (
        f"{LINE}\n📅  APPOINTMENTS MANAGEMENT\n{LINE}\n\n"
        "1️⃣  Today's Appointments\n2️⃣  All Appointments\n"
        "3️⃣  Booked\n4️⃣  Approved\n5️⃣  Rejected\n"
        "6️⃣  Manage (Approve/Reject)\n\n"
        f"7️⃣  ← Back to Dashboard\n\n{THIN}\n💡 Type a number."
    )


async def _show_users(page: int = 0) -> str:
    raw_items = await scan_all(_users_table)
    if not raw_items:
        return f"{LINE}\n👥 USERS\n{LINE}\n\n📭 No users yet.\n\n{THIN}\nType *Back*."
        
    items = sorted(raw_items, key=lambda x: x.get('createdAt', ''), reverse=True)
    total = len(items)
    start = page * PAGE_SZ
    sliced = items[start: start + PAGE_SZ]
    
    total_pages = max(1, (total + PAGE_SZ - 1) // PAGE_SZ)
    page_info   = f"Page {page + 1} of {total_pages}  ({total} total)"

    cards = []
    for i, u in enumerate(sliced):
        cards.append(
            f"┌─ #{str(start + i + 1).zfill(2)} ──────────────────\n"
            f"│ 👤 {u.get('name', 'N/A')}\n"
            f"│ 📞 {u.get('phone', 'N/A')}\n"
            f"│ 📧 {u.get('email', 'N/A')}\n"
            "└────────────────────────"
        )
        
    nav_parts = []
    if page > 0:                      nav_parts.append("⬅ *prev*")
    if start + PAGE_SZ < total:       nav_parts.append("*next* ➡")
    nav_parts.append("*back* → menu")
    nav = "  |  ".join(nav_parts)

    return (
        f"{LINE}\n👥 USERS\n{page_info}\n{LINE}\n\n"
        + "\n\n".join(cards)
        + f"\n\n{THIN}\n{nav}"
    )


async def _show_doctors() -> str:
    items = await scan_all(_doctors_table)
    if not items:
        return f"{LINE}\n👨‍⚕️ DOCTORS\n{LINE}\n\n📭 No doctors.\n\n{THIN}\nType *Back*."
    cards = []
    for i, d in enumerate(items):
        cards.append(
            f"┌─ #{str(i + 1).zfill(2)} ──────────────────\n"
            f"│ 👨‍⚕️ {d.get('name', 'N/A')}\n"
            f"│ 🏥 {d.get('departmentName', d.get('departmentId', 'N/A'))}\n"
            f"│ 📋 {d.get('specialization', 'N/A')}\n"
            "└────────────────────────"
        )
    return (
        f"{LINE}\n👨‍⚕️ DOCTORS ({len(items)})\n{LINE}\n\n"
        + "\n\n".join(cards)
        + f"\n\n{THIN}\nType *Back*."
    )


async def _show_slots() -> str:
    items = await scan_all(_timeslots_table)
    if not items:
        return f"{LINE}\n⏰ TIME SLOTS\n{LINE}\n\n📭 No slots.\n\n{THIN}\nType *Back*."
    _STATUS = {"AVAILABLE": "🟢 Available", "BOOKED": "🔴 Booked"}
    cards = []
    for i, s in enumerate(items):
        cards.append(
            f"┌─ #{str(i + 1).zfill(2)} ──────────────────\n"
            f"│ 👨‍⚕️ {s.get('doctorName', s.get('docterId', 'N/A'))}\n"
            f"│ 📅 {s.get('date', 'N/A')}\n"
            f"│ ⏰ {s.get('startTime', 'N/A')} – {s.get('endTime', 'N/A')}\n"
            f"│ {_STATUS.get(s.get('status', ''), s.get('status', 'N/A'))}\n"
            "└────────────────────────"
        )
    return (
        f"{LINE}\n⏰ TIME SLOTS ({len(items)})\n{LINE}\n\n"
        + "\n\n".join(cards)
        + f"\n\n{THIN}\nType *Back*."
    )
