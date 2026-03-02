"""
user_handler.py — Full user-facing state machine.

This is the Python port of booking_handler.mjs.
Java equivalent: a @Service class with a big switch statement.

State machine steps:
  START / MAIN_MENU → REG_* or LOGIN_* flows
  USER_MENU         → booking, cancel, feedback, etc.
  PATIENT_* / DEPARTMENT / DOCTOR / AWAITING_CALENDAR → booking flow
  CANCEL_*          → cancellation flow
  FEEDBACK_*        → feedback flow
  FP_*              → forgot password flow
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta

from src.config import settings
from src.services import dynamodb as db
from src.services.agent_service import agent_service
from src.utils.crypto import (
    check_password, generate_otp, generate_salt,
    hash_with_salt, is_strong_password, new_uuid
)
from src.utils.renderers import render_main_menu, render_user_menu, render_list, status_label
from src.utils.validators import is_valid_indian_phone, is_valid_email, normalize, sanitize, is_valid_name

logger = logging.getLogger(__name__)

MAX_OTP_TRIES = 3

def _generate_booking_token() -> tuple[str, int]:
    """Helper to generate a booking token and an expiry time (1 hour from now)."""
    return new_uuid(), int(time.time()) + 3600


async def handle_user_message(session_id: str, raw_message: str, session: dict) -> str:
    """
    Main entry point — called by webhook.py for every non-admin message.
    Returns the reply string to send back to the user.
    """
    message = sanitize(raw_message)
    norm = normalize(message)
    step = session.get("currentStep", "START")
    temp = session.get("tempData", {})
    
    role = session.get("role", "GUEST")
    is_logged_in = role == "USER"

    # ── Global reset ──────────────────────────────────────────────────────────
    if norm in ("start", "menu", "hi", "hello", "hey", "/start"):
        await db.full_reset(session_id, "MAIN_MENU")
        # User requested that hi/hello always go to login/register/admin options
        return (
            "🏥 *Welcome to Healix Premier Healthcare* 💙\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "To provide you with secure medical services, we need you to identify yourself first.\n\n"
            "1️⃣  *Register*  — Create your patient profile\n"
            "2️⃣  *Login*     — Access your existing records\n"
            "3️⃣  *Admin*     — Hospital management\n\n"
            "📌 _Please type a number to continue._"
        )

    # ── Global back ───────────────────────────────────────────────────────────
    if norm == "back":
        stack: list = session.get("stepStack", [])
        if stack:
            prev = stack[-1]
            new_stack = stack[:-1]
            await db.bulk_session_update(
                session_id,
                "SET currentStep = :c, stepStack = :s",
                {},
                {":c": prev, ":s": new_stack},
            )
            return await _render_step(prev, temp, session_id)

    # ── Global Navigation Shortcuts ───────────────────────────────────────────
    if not is_logged_in:
        if any(k in norm for k in ("1", "register", "new patient", "create profile")) and step in ("START", "MAIN_MENU"):
            await db.update_step(session_id, "REG_NAME")
            return "🎉 *Let's get you set up!*\n\nFirst — what's your *Full Name*?"

        if any(k in norm for k in ("2", "login", "returning", "sign in")) and step in ("START", "MAIN_MENU"):
            await db.update_step(session_id, "LOGIN_PHONE")
            return "👋 *Welcome back!*\n\nEnter your registered *Phone Number*:"
            
        if norm in ("book", "appointment", "booking") and step in ("START", "MAIN_MENU"):
            logger.info(f"Guest Booking Intent: {norm}")
            agent_replies = await agent_service.handle_message(raw_message, session_id, session)
            if agent_replies: return "\n\n".join(agent_replies)

    # ── Step routing ──────────────────────────────────────────────────────────
    match step:

        # ── MAIN MENU ─────────────────────────────────────────────────────────
        case "START" | "MAIN_MENU":
            return render_main_menu()

        # ════════════════════════════════════════════════════════════════════════
        #  REGISTRATION FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "REG_NAME":
            if not is_valid_name(message):
                return "❌ Invalid name. Please enter a real name (no numbers, and cannot be 'back')."
            await db.bulk_session_update(
                session_id,
                "SET tempData.#n = :n, currentStep = :step",
                {"#n": "name"},
                {":n": message, ":step": "REG_PHONE"},
            )
            return f"✨ *Nice to meet you, {message}!*\n\n📞 Enter your *10-digit Phone Number*:"

        case "REG_PHONE":
            if norm in ("login", "2"):
                await db.update_step(session_id, "LOGIN_PHONE")
                return "👋 Switching to Login!\n\nEnter your registered *Phone Number*:"

            if not is_valid_indian_phone(message):
                return "🤔 Invalid number. Enter a valid *10-digit Indian mobile number* (e.g. 9876543210)."

            clean_phone = message.replace("+91", "").strip()
            existing = await db.get_user(clean_phone)
            if existing:
                await db.update_step(session_id, "LOGIN_PHONE")
                return "👀 Number already registered! Enter your phone number to log in."

            await db.bulk_session_update(
                session_id,
                "SET tempData.#p = :p, currentStep = :step",
                {"#p": "phone"},
                {":p": clean_phone, ":step": "REG_EMAIL"},
            )
            return "✅ *Phone verified!*\n\n📧 Enter your *Email Address*:"

        case "REG_EMAIL":
            if not is_valid_email(message.lower()):
                return "📧 Invalid email. Try again (e.g. `name@example.com`)"
            await db.bulk_session_update(
                session_id,
                "SET tempData.#e = :e, currentStep = :step",
                {"#e": "email"},
                {":e": message.lower(), ":step": "REG_PASSWORD"},
            )
            return (
                "📬 *Email saved!*\n\n🔐 Create a *Password*:\n"
                "• 8+ chars  • Uppercase & lowercase  • Number & symbol (@#$…)"
            )

        case "REG_PASSWORD":
            if not is_strong_password(message):
                return "🔒 Password too weak! Needs: 8+ chars, A-Z, a-z, 0-9, symbol. Try again:"

            salt = generate_salt()
            otp = generate_otp()
            await db.bulk_session_update(
                session_id,
                "SET tempData.#pw=:pw, tempData.#salt=:salt, tempData.#otp=:otp, tempData.#tries=:t, currentStep=:step",
                {"#pw": "password", "#salt": "salt", "#otp": "regOtp", "#tries": "otpTries"},
                {":pw": hash_with_salt(message, salt), ":salt": salt,
                 ":otp": otp, ":t": 0, ":step": "REG_OTP"},
            )
            return f"🔐 *Verify your account!*\n\nYour OTP: *{otp}*\n\nEnter it to complete registration.\n_Type RESEND if needed._"

        case "REG_OTP":
            if norm == "resend":
                new_otp = generate_otp()
                await db.bulk_session_update(
                    session_id, "SET tempData.#otp=:otp, tempData.#t=:t",
                    {"#otp": "regOtp", "#t": "otpTries"}, {":otp": new_otp, ":t": 0}
                )
                return f"🔄 *New OTP:* *{new_otp}*\n\nEnter it to verify."

            tries = temp.get("otpTries", 0) + 1
            if message == temp.get("regOtp"):
                user = {
                    "phone": temp["phone"], "userId": new_uuid(),
                    "name": temp["name"], "email": temp["email"],
                    "password": temp["password"], "salt": temp["salt"],
                    "role": "USER", "createdAt": datetime.now(timezone.utc).isoformat()
                }
                await db.put_user(user)

                # CONTEXTUAL RESUMPTION
                if temp.get("intendedDocId") or temp.get("intendedDeptId"):
                    doc_id = temp.get("intendedDocId")
                    doc_name = temp.get("intendedDocName", "Doctor")
                    await db.bulk_session_update(
                        session_id,
                        "SET currentStep=:step, #role=:role, userPhone=:phone, tempData.#did=:did, tempData.#dn=:dn, tempData.#dept=:dept, tempData.#deptid=:deptid",
                        {"#role": "role", "#did": "docterId", "#dn": "doctorName", "#dept": "departmentName", "#deptid": "departmentId"},
                        {":step": "PATIENT_NAME", ":role": "USER", ":phone": temp["phone"], ":did": doc_id or "", ":dn": doc_name, ":dept": temp.get("intendedDept", "General Medicine"), ":deptid": temp.get("intendedDeptId", "")}
                    )
                    return f"🎊 *Welcome to Healix, {temp['name']}!* 💙\n\nAccount created. Let's continue your booking.\n\n👤 Enter the *Patient's Full Name*:"

                await db.full_reset(session_id, "START")
                return "🎊 *Welcome to Healix!*\n\nAccount created. Type *Login* to access your account. 💙"
            elif tries >= MAX_OTP_TRIES:
                await db.full_reset(session_id, "START")
                return "❌ Too many wrong attempts. Type *Register* to start again."
            else:
                await db.bulk_session_update(
                    session_id, "SET tempData.#t=:t", {"#t": "otpTries"}, {":t": tries}
                )
                return f"❌ Wrong OTP. *{MAX_OTP_TRIES - tries}* attempt(s) left. Type *RESEND* for a new OTP."

        # ════════════════════════════════════════════════════════════════════════
        #  LOGIN FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "LOGIN_PHONE":
            if not is_valid_indian_phone(message):
                return "📞 Invalid number. Enter your *10-digit registered phone number*:"

            phone = message.replace("+91", "").strip()
            user = await db.get_user(phone)
            if not user:
                return "🤷 Number not registered.\n\nType *Register* to create an account. 🚀"

            await db.bulk_session_update(
                session_id, "SET tempData.#p=:p, currentStep=:step",
                {"#p": "phone"}, {":p": phone, ":step": "LOGIN_PASSWORD"}
            )
            return "✅ *Account found!*\n\n🔐 Enter your *Password*:\n_(Type FORGOT to reset your password)_"

        case "LOGIN_PASSWORD":
            phone = temp.get("phone")
            if not phone:
                return "❌ Session error. Type /start."

            if norm == "forgot":
                otp = generate_otp()
                await db.bulk_session_update(
                    session_id, "SET tempData.#otp=:otp, tempData.#t=:t, currentStep=:step",
                    {"#otp": "fpOtp", "#t": "otpTries"}, {":otp": otp, ":t": 0, ":step": "FP_OTP"}
                )
                return f"🔑 *Password Reset*\n\nYour OTP: *{otp}*\n\nEnter it to proceed.\n_Type RESEND if needed._"

            user = await db.get_user(phone)
            if user and user.get("password") and \
                    check_password(message, user["password"], user.get("salt")):
                otp = generate_otp()
                await db.bulk_session_update(
                    session_id,
                    "SET tempData.#otp=:otp, tempData.#t=:t, currentStep=:step",
                    {"#otp": "loginOtp", "#t": "otpTries"},
                    {":otp": otp, ":t": 0, ":step": "LOGIN_OTP"}
                )
                return f"🔐 *Security check!*\n\nYour OTP: *{otp}*\n\nEnter it to verify. 🛡️\n_Type RESEND if needed._"
            return "❌ *Incorrect Password.*\n_(Type FORGOT to reset your password)_"

        case "LOGIN_OTP":
            if norm == "resend":
                new_otp = generate_otp()
                await db.bulk_session_update(
                    session_id, "SET tempData.#otp=:otp, tempData.#t=:t",
                    {"#otp": "loginOtp", "#t": "otpTries"}, {":otp": new_otp, ":t": 0}
                )
                return f"🔄 *New OTP:* *{new_otp}*\n\nEnter it to verify."

            tries = temp.get("otpTries", 0) + 1
            if message == temp.get("loginOtp"):
                # CONTEXTUAL RESUMPTION
                if temp.get("intendedDocId") or temp.get("intendedDeptId"):
                    doc_id = temp.get("intendedDocId")
                    doc_name = temp.get("intendedDocName", "Doctor")
                    await db.bulk_session_update(
                        session_id, 
                        "SET currentStep=:step, #role=:role, userPhone=:phone, tempData.#did=:did, tempData.#dn=:dn, tempData.#dept=:dept, tempData.#deptid=:deptid", 
                        {"#role": "role", "#did": "docterId", "#dn": "doctorName", "#dept": "departmentName", "#deptid": "departmentId"}, 
                        {":step": "PATIENT_NAME", ":role": "USER", ":phone": temp.get("phone", ""), ":did": doc_id or "", ":dn": doc_name, ":dept": temp.get("intendedDept", "General Medicine"), ":deptid": temp.get("intendedDeptId", "")}
                    )
                    return f"🔓 *Welcome back!* 🎉\n\nLet's continue your booking.\n\n👤 Enter the *Patient's Full Name*:"

                await db.bulk_session_update(
                    session_id,
                    "SET #r=:r, currentStep=:step, userPhone=:phone",
                    {"#r": "role"},
                    {":r": "USER", ":step": "USER_MENU", ":phone": temp.get("phone", "")}
                )
                return "🔓 *You're in! Welcome back!* 🎉\n\n" + render_user_menu()
            elif tries >= MAX_OTP_TRIES:
                await db.full_reset(session_id, "START")
                return "❌ Too many wrong attempts. Type *Login* to start again."
            else:
                await db.bulk_session_update(
                    session_id, "SET tempData.#t=:t", {"#t": "otpTries"}, {":t": tries}
                )
                return f"❌ Wrong OTP. *{MAX_OTP_TRIES - tries}* attempt(s) left. Type *RESEND* for a new OTP."

        # ════════════════════════════════════════════════════════════════════════
        #  FORGOT PASSWORD FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "FP_OTP":
            if norm == "resend":
                new_otp = generate_otp()
                await db.bulk_session_update(
                    session_id, "SET tempData.#otp=:otp, tempData.#t=:t",
                    {"#otp": "fpOtp", "#t": "otpTries"}, {":otp": new_otp, ":t": 0}
                )
                return f"🔄 *New OTP:* *{new_otp}*\n\nEnter it to reset your password."

            tries = temp.get("otpTries", 0) + 1
            if message == temp.get("fpOtp"):
                await db.update_step(session_id, "FP_NEW_PW")
                return "✅ *OTP verified!*\n\n🔐 Enter your *New Password*:\n• 8+ chars • A-Z, a-z • Number & symbol"
            elif tries >= MAX_OTP_TRIES:
                await db.full_reset(session_id, "START")
                return "❌ Too many wrong attempts. Type *Login* to try again."
            else:
                await db.bulk_session_update(
                    session_id, "SET tempData.#t=:t", {"#t": "otpTries"}, {":t": tries}
                )
                return f"❌ Wrong OTP. *{MAX_OTP_TRIES - tries}* attempt(s) left."

        case "FP_NEW_PW":
            if not is_strong_password(message):
                return "🔒 Password too weak! Needs: 8+ chars, A-Z, a-z, 0-9, symbol. Try again:"

            phone = temp.get("phone")
            if not phone:
                return "❌ Session error. Type /start."

            new_salt = generate_salt()
            await asyncio.gather(
                db.update_user_password(phone, hash_with_salt(message, new_salt), new_salt),
                db.full_reset(session_id, "START"),
            )
            return "✅ *Password reset successfully!*\n\nType *Login* to access your account. 🔓"

        # ════════════════════════════════════════════════════════════════════════
        #  USER MENU
        # ════════════════════════════════════════════════════════════════════════

        case "USER_MENU":
            return await _handle_user_menu(session_id, norm, raw_message, session)

        # ════════════════════════════════════════════════════════════════════════
        #  BOOKING FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "PATIENT_NAME":
            if not is_valid_name(message):
                return "❌ Invalid name. Please enter a real name (no numbers, and cannot be 'back').\n_Type 'back' to return to menu._"
            await db.bulk_session_update(
                session_id,
                "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), tempData.#pn=:pn, currentStep=:step",
                {"#pn": "patientName"},
                {":sv": ["PATIENT_NAME"], ":e": [], ":pn": message, ":step": "PATIENT_EMAIL"},
            )
            return f"👤 *Patient: {message}* ✅\n\n📧 Enter the *Patient's Email Address*:"

        case "PATIENT_EMAIL":
            if not is_valid_email(message.lower()):
                return "📧 Invalid email. Try again (e.g. `name@example.com`)"
            await db.bulk_session_update(
                session_id,
                "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), tempData.#pe=:pe, currentStep=:step",
                {"#pe": "patientEmail"},
                {":sv": ["PATIENT_EMAIL"], ":e": [], ":pe": message.lower(), ":step": "PATIENT_PHONE"},
            )
            return "📧 *Email saved!* 🙌\n\n📞 Enter the *Patient's Contact Number* (10-digit):"

        case "PATIENT_PHONE":
            if not re.match(r"^[6-9]\d{9}$", message):
                return "📞 Invalid phone. Enter a *10-digit mobile number* (starts 6–9)."

            # SMART SHORTCUT: If doctor is already selected via NLU, skip to calendar link
            if temp.get("docterId"):
                booking_token, token_expiry = _generate_booking_token()
                
                doc_name = temp.get("doctorName", "Doctor")

                await db.bulk_session_update(
                    session_id,
                    "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), tempData.#pp=:pp, "
                    "tempData.#tok=:tok, tempData.#tokExp=:tokExp, currentStep=:step",
                    {"#pp": "patientPhone", "#tok": "bookingToken", "#tokExp": "tokenExpiry"},
                    {
                        ":sv": ["PATIENT_PHONE"], ":e": [], ":pp": message,
                        ":tok": booking_token, ":tokExp": token_expiry,
                        ":step": "AWAITING_CALENDAR"
                    }
                )
                
                calendar_url = f"{settings.booking_calendar_url}?token={booking_token}"
                return (
                    f"✨ *Details saved!* 📝\n\n"
                    f"Patient: *{temp.get('patientName')}* ({message})\n"
                    f"Doctor: *Dr. {doc_name}*\n\n"
                    f"📅 *Select your slot inside the calendar:*\n"
                    f"🔗 {calendar_url}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📌 Pick your slot, then return here and type *done*."
                )

            # SMART SHORTCUT: If department is already selected via NLU, skip to Doctor selection
            dept_id = temp.get("departmentId")
            dept_name = temp.get("departmentName")

            if not dept_id and dept_name:
                depts = await db.get_all_departments()
                match = next((d for d in depts if dept_name.lower() in d["name"].lower() or d["name"].lower() in dept_name.lower()), None)
                if match:
                    dept_id = match["departmentId"]
                    dept_name = match["name"]

            if dept_id:
                docs = await db.get_doctors_by_department(dept_id)
                await db.bulk_session_update(
                    session_id,
                    "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), "
                    "tempData.#pp=:pp, tempData.#did=:did, tempData.#dn=:dn, currentStep=:step",
                    {"#pp": "patientPhone", "#did": "departmentId", "#dn": "departmentName"},
                    {":sv": ["PATIENT_PHONE"], ":e": [], ":pp": message, ":did": dept_id, ":dn": dept_name, ":step": "DOCTOR"}
                )
                return render_list(
                    [d["name"] for d in docs], 
                    f"🏥 *{dept_name}*\n\n👨‍⚕️ Choose your *Doctor*:"
                )

            # STANDARD FLOW: Choice of department
            depts = await db.get_all_departments()
            await db.bulk_session_update(
                session_id,
                "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), tempData.#pp=:pp, currentStep=:step",
                {"#pp": "patientPhone"},
                {":sv": ["PATIENT_PHONE"], ":e": [], ":pp": message, ":step": "DEPARTMENT"},
            )
            return render_list([d["name"] for d in depts], "🏥 *Select Department:*")

        case "DEPARTMENT":
            depts = await db.get_all_departments()
            sel = _pick(message, norm, depts, "name")
            if not sel:
                return "❌ Invalid selection. Type the number next to your department."

            docs = await db.get_doctors_by_department(sel["departmentId"])
            await db.bulk_session_update(
                session_id,
                "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), tempData.#did=:did, tempData.#dn=:dn, currentStep=:step",
                {"#did": "departmentId", "#dn": "departmentName"},
                {":sv": ["DEPARTMENT"], ":e": [], ":did": sel["departmentId"], ":dn": sel["name"], ":step": "DOCTOR"},
            )
            if not docs:
                return "⚠️ No doctors in this department right now. Type *back*."
            lines = [f"{d['name']}{' — ' + d.get('specialization','') if d.get('specialization') else ''}"
                     for d in docs]
            return render_list(lines, f"🏥 *{sel['name']}*\n\n👨‍⚕️ *Choose your Doctor:*")

        case "DOCTOR":
            docs = await db.get_doctors_by_department(temp.get("departmentId", ""))
            sel = _pick(message, norm, docs, "name")
            if not sel:
                return "❌ Invalid selection. Type the number next to your doctor."

            booking_token, token_expiry = _generate_booking_token()

            # Strip existing "Dr. " to avoid "Dr. Dr."
            doc_display_name = sel['name']
            if doc_display_name.lower().startswith("dr. "):
                doc_display_name = doc_display_name[4:].strip()

            await db.bulk_session_update(
                session_id,
                "SET stepStack=list_append(if_not_exists(stepStack,:e),:sv), "
                "tempData.#docId=:docId, tempData.#docName=:docName, "
                "tempData.#deptName=:deptName, tempData.#tok=:tok, "
                "tempData.#tokExp=:tokExp, currentStep=:step",
                {
                    "#docId": "docterId", "#docName": "doctorName",
                    "#deptName": "departmentName", "#tok": "bookingToken",
                    "#tokExp": "tokenExpiry"
                },
                {
                    ":sv": ["DOCTOR"], ":e": [],
                    ":docId": sel.get("docterId", sel.get("doctorId", "")),
                    ":docName": doc_display_name,
                    ":deptName": temp.get("departmentName", ""),
                    ":tok": booking_token, ":tokExp": token_expiry,
                    ":step": "AWAITING_CALENDAR"
                },
            )

            calendar_url = f"{settings.booking_calendar_url}?token={booking_token}"
            return (
                f"👨‍⚕️ *Dr. {doc_display_name}* selected!\n\n"
                f"📅 *Choose your appointment date & time* using the calendar:\n\n"
                f"🔗 {calendar_url}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 Open the link, pick your date & slot, then come back here.\n"
                "_Type *done* once booked, or *back* to choose a different doctor._"
            )

        case "AWAITING_CALENDAR":
            if norm in ("done", "booked", "confirm", "yes", "ok"):
                fresh = await db.get_session(session_id)
                appt_id = fresh.get("tempData", {}).get("calendarBookingId")
                if appt_id:
                    await db.update_step(session_id, "USER_MENU")
                    return (
                        "🎊 *Appointment Confirmed!*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🆔 *Appointment ID*: `{appt_id}`\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "📌 Save your ID to cancel if needed.\n\nWishing you good health! 💙\n\n"
                        + render_user_menu()
                    )
                cal_url = f"{settings.booking_calendar_url}?token={temp.get('bookingToken', '')}"
                return (
                    f"⏳ *Booking not yet completed.*\n\n"
                    f"Please open the link and confirm your slot:\n🔗 {cal_url}\n\n"
                    "_Type *done* after selecting your date & time._"
                )
            elif norm == "back":
                stack = session.get("stepStack", [])
                prev = stack[-1] if stack else "USER_MENU"
                new_stack = stack[:-1]
                await db.bulk_session_update(
                    session_id, "SET stepStack=:s, currentStep=:c",
                    {}, {":s": new_stack, ":c": prev}
                )
                return await _render_step(prev, temp, session_id)
            else:
                cal_url = f"{settings.booking_calendar_url}?token={temp.get('bookingToken', '')}"
                return (
                    "📅 *Please use the calendar link to book your appointment:*\n\n"
                    f"🔗 {cal_url}\n\n"
                    "_Type *done* after completing your booking, or *back* to go back._"
                )

        # ════════════════════════════════════════════════════════════════════════
        #  MY APPOINTMENTS — INLINE ACTION
        # ════════════════════════════════════════════════════════════════════════

        case "USER_APPT_ACTION":
            if norm in ("back", "menu"):
                await db.update_step(session_id, "USER_MENU")
                return render_user_menu()
            appt_ids = temp.get("apptList") or []
            try:
                idx = int(norm) - 1
                if not (0 <= idx < len(appt_ids)):
                    raise ValueError
            except (ValueError, TypeError):
                return (
                    f"⚠️ Please type a number between 1 and {len(appt_ids)},\n"
                    "or type *back* to return to the menu."
                )
            chosen_id = appt_ids[idx]
            appt = await db.get_appointment(chosen_id)
            if not appt:
                return "❌ Appointment not found. Try again or type *back*."
            status = appt.get("status", "")
            detail = (
                f"📋 *Appointment #{idx + 1}*\n\n"
                f"🏥 {appt.get('department', 'N/A')} — 👨‍⚕️ {appt.get('doctor', 'N/A')}\n"
                f"📅 {appt.get('date', 'N/A')}  ⏰ {appt.get('time', 'N/A')}\n"
                f"🔖 {status_label(status)}\n\n"
            )
            if status not in ("BOOKED", "APPROVED"):
                await db.update_step(session_id, "USER_MENU")
                return detail + "ℹ️ This appointment cannot be modified.\n\n" + render_user_menu()
            # Resolve doctor Id and name
            doctor_id = appt.get("docterId") or appt.get("doctorId")
            doctor_name = appt.get("doctor") or appt.get("doctorName")
            
            if not doctor_name:
                doc_rec = await db._run(db._doctors_table.get_item, Key={"docterId": doctor_id}) if doctor_id else {}
                doctor_name = doc_rec.get("Item", {}).get("name", "Doctor")

            slot_id = appt.get("slotId")

            # Store resolved info and generate a valid UUID token for the booking link
            booking_token, token_expiry = _generate_booking_token()

            expr_names = {
                "#cid": "cancelId", "#rid": "reschedApptId",
                "#rdid": "reschedDoctorId", "#rdn": "reschedDoctorName",
                "#bt": "bookingToken", "#be": "tokenExpiry"
            }
            expr_values = {
                ":cid": chosen_id, ":rid": chosen_id,
                ":rdid": doctor_id or "", ":rdn": doctor_name,
                ":bt": booking_token, ":be": token_expiry,
                ":step": "APPT_ACTION_CHOICE",
            }
            set_clauses = (
                "SET tempData.#cid=:cid, tempData.#rid=:rid, "
                "tempData.#rdid=:rdid, tempData.#rdn=:rdn, "
                "tempData.#bt=:bt, tempData.#be=:be, currentStep=:step"
            )
            if slot_id:
                expr_names["#csid"] = "cancelIdSlotId"
                expr_names["#rsid"] = "reschedOldSlotId"
                expr_values[":csid"] = slot_id
                expr_values[":rsid"] = slot_id
                set_clauses += ", tempData.#csid=:csid, tempData.#rsid=:rsid"

            await db.bulk_session_update(session_id, set_clauses, expr_names, expr_values)
            return (
                detail
                + "What would you like to do?\n\n"
                  "1️⃣  ❌ Cancel\n"
                  "2️⃣  🗓️ Reschedule\n"
                  "3️⃣  ↩️ Back to list"
            )

        case "APPT_ACTION_CHOICE":
            if norm in ("3", "back"):
                # Re-run view appointments
                await db.update_step(session_id, "USER_APPT_ACTION")
                appt_ids = temp.get("apptList") or []
                return (
                    "📋 Type the *number* of the appointment to manage, or *back* to return to menu.\n"
                    f"_(You have {len(appt_ids)} appointment(s) listed.)_"
                )
            if norm in ("1", "cancel"):
                await db.update_step(session_id, "CANCEL_CONFIRM")
                appt = await db.get_appointment(temp.get("cancelId", ""))
                return (
                    "⚠️ *Confirm Cancellation?*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 *Patient* : {appt.get('patientName') if appt else 'N/A'}\n"
                    f"👨‍⚕️ *Doctor*  : {appt.get('doctor') if appt else 'N/A'}\n"
                    f"📅 *Date*    : {appt.get('date') if appt else 'N/A'}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nType *Yes* to cancel  |  *No* to keep it"
                )
            if norm in ("2", "reschedule"):
                await db.update_step(session_id, "AWAITING_CALENDAR")
                doc_name = temp.get("reschedDoctorName", "Doctor")
                booking_token = temp.get("bookingToken")
                cal_url = f"{settings.booking_calendar_url}?token={booking_token}"
                return (
                    f"👨‍⚕️ *{doc_name}* selected!\n\n"
                    "📅 Choose your appointment date & time using the calendar:\n\n"
                    f"🔗 {cal_url}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📌 Open the link, pick your date & slot, then come back here.\n"
                    "Type *done* once booked, or *back* to choose a different doctor."
                )
            return "Please type *1* (Cancel), *2* (Reschedule) or *3* (Back)."

        # ════════════════════════════════════════════════════════════════════════
        #  CANCEL FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "CANCEL_ID":
            appt = await db.get_appointment(message)
            if not appt or appt.get("status") != "BOOKED":
                return "⚠️ Appointment not found or already cancelled. Check the ID and try again."

            await db.bulk_session_update(
                session_id,
                "SET tempData.#cid=:cid, tempData.#csid=:csid, currentStep=:step",
                {"#cid": "cancelId", "#csid": "cancelSlotId"},
                {":cid": message, ":csid": appt.get("slotId"), ":step": "CANCEL_CONFIRM"}
            )
            return (
                "⚠️ *Confirm Cancellation?*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Patient* : {appt.get('patientName')}\n"
                f"👨‍⚕️ *Doctor*  : {appt.get('doctor')}\n"
                f"📅 *Date*    : {appt.get('date')}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nType *Yes* to cancel  |  *No* to keep it"
            )

        case "CANCEL_CONFIRM":
            if norm in ("yes", "1", "true", "confirm"):
                cancel_id = temp.get("cancelId")
                slot_id = temp.get("cancelSlotId")
                tasks = [
                    db.cancel_appointment(cancel_id),
                    db.update_step(session_id, "USER_MENU"),
                ]
                if slot_id:
                    tasks.append(db.free_slot(slot_id))
                await asyncio.gather(*tasks)
                return (
                    f"✅ *Appointment Cancelled.*\n\n"
                    f"ID `{cancel_id}` cancelled and slot is now available for others. 💙\n\n"
                    + render_user_menu()
                )
            else:
                await db.update_step(session_id, "USER_MENU")
                return "💚 *Cancellation Aborted!* Appointment is safe. 👍\n\n" + render_user_menu()

        # ════════════════════════════════════════════════════════════════════════
        #  RESCHEDULE FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "RESCHEDULE_ID":
            if norm in ("back", "menu"):
                await db.update_step(session_id, "USER_MENU")
                return render_user_menu()
            phone = session.get("userPhone") or temp.get("phone")
            appt = await db.get_appointment(message)
            if not appt or appt.get("status") not in ("BOOKED", "APPROVED"):
                return (
                    "❌ Appointment not found or cannot be rescheduled.\n"
                    "_(Only BOOKED or APPROVED appointments can be rescheduled.)_\n\n"
                    "Try again or type *back*."
                )
            if phone and appt.get("userPhone", appt.get("phone", "")) != phone:
                return "🔒 You can only reschedule your own appointments.\n\nTry again or type *back*."
            # Resolve doctor name if missing
            doc_name = appt.get("doctor") or appt.get("doctorName")
            doc_id = appt.get("docterId") or appt.get("doctorId")
            if not doc_name and doc_id:
                doc_rec = await db._run(db._doctors_table.get_item, Key={"docterId": doc_id})
                doc_name = doc_rec.get("Item", {}).get("name", "Doctor")

            # Strip prefix
            if doc_name and doc_name.lower().startswith("dr. "):
                doc_name = doc_name[4:].strip()

            booking_token, token_expiry = _generate_booking_token()

            await db.bulk_session_update(
                session_id,
                "SET tempData.#rid=:rid, tempData.#rsid=:rsid, tempData.#rdid=:rdid, "
                "tempData.#rdn=:rdn, tempData.#tok=:tok, tempData.#tokExp=:tokExp, currentStep=:step",
                {
                    "#rid": "reschedApptId", "#rsid": "reschedOldSlotId", 
                    "#rdid": "reschedDoctorId", "#rdn": "reschedDoctorName",
                    "#tok": "bookingToken", "#tokExp": "tokenExpiry"
                },
                {
                    ":rid": message, ":rsid": appt.get("slotId"), 
                    ":rdid": doc_id, ":rdn": doc_name or "Doctor",
                    ":tok": booking_token, ":tokExp": token_expiry,
                    ":step": "AWAITING_CALENDAR"
                }
            )
            cal_url = f"{settings.booking_calendar_url}?token={booking_token}"
            return (
                f"👨‍⚕️ *Dr. {doc_name or 'Doctor'}* selected!\n\n"
                "📅 Choose your *new* appointment date & time using the calendar:\n\n"
                f"🔗 {cal_url}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 Open the link, pick your date & slot, then come back here.\n"
                "Type *done* once booked, or *back* to go back."
            )

        case "RESCHEDULE_DATE":
            if norm in ("back", "menu"):
                await db.update_step(session_id, "RESCHEDULE_ID")
                return "🗓️ *Reschedule Appointment*\n\nEnter your *Appointment ID* to reschedule:\n_Type 'back' to return to menu._"
            try:
                from datetime import date as _date
                new_date = datetime.strptime(message.strip(), "%Y-%m-%d").date()
                if new_date < _date.today():
                    raise ValueError("past date")
                new_date_str = new_date.strftime("%Y-%m-%d")
            except ValueError:
                return (
                    "❌ Invalid date. Use *YYYY-MM-DD* and the date must be today or in the future.\n\n"
                    "Try again or type *back*."
                )
            docter_id = temp.get("reschedDoctorId")
            if not docter_id:
                # Fallback resolve by name comparison
                appt_id = temp.get("reschedApptId")
                _appt = await db.get_appointment(appt_id) if appt_id else None
                doc_name = _appt.get("doctor") if _appt else None
                if doc_name:
                    all_docs = await db.get_all_doctors()
                    matched = next((d for d in all_docs if d.get("name", "").strip().lower() == doc_name.strip().lower()), None)
                    if matched:
                        docter_id = matched.get("docterId")
                        await db.save_temp(session_id, "reschedDoctorId", docter_id)

            if not docter_id:
                await db.update_step(session_id, "USER_MENU")
                return "❌ Doctor resolution error. Please book a new appointment.\n\n" + render_user_menu()

            slots = await db.get_available_slots_for_doctor_on_date(docter_id, new_date_str)
            if not slots:
                return f"😔 No available slots on *{new_date_str}*. Try another date or type *back*."
            
            slot_ids = [s["slotId"] for s in slots]
            await db.bulk_session_update(
                session_id,
                "SET tempData.#rd=:rd, tempData.#rs=:rs, currentStep=:step",
                {"#rd": "reschedDate", "#rs": "reschedSlots"},
                {":rd": new_date_str, ":rs": slot_ids, ":step": "RESCHEDULE_SLOT"}
            )
            slot_lines = "\n".join(f"{i+1}️⃣  {s.get('startTime','?')} – {s.get('endTime','?')}" for i, s in enumerate(slots))
            return f"🗓️ *Available Slots on {new_date_str}*\n\n{slot_lines}\n\nType the *slot number* to confirm."

        case "RESCHEDULE_SLOT":
            if norm in ("back", "menu"):
                await db.update_step(session_id, "RESCHEDULE_DATE")
                return "Enter the *new date* (format: YYYY-MM-DD):"
            
            appt_id     = temp.get("reschedApptId")
            old_slot_id = temp.get("reschedOldSlotId")
            new_date    = temp.get("reschedDate")
            slot_ids    = temp.get("reschedSlots") or []
            try:
                idx = int(norm) - 1
                if not (0 <= idx < len(slot_ids)): raise ValueError
            except (ValueError, TypeError):
                return f"⚠️ Please type a valid number between 1 and {len(slot_ids)}."
            
            new_slot_id = slot_ids[idx]
            from boto3.dynamodb.conditions import Attr as _Attr
            slot_records = await db.scan_all(db._timeslots_table, _Attr("slotId").eq(new_slot_id))
            slot = slot_records[0] if slot_records else {}
            
            try:
                await db.reschedule_appointment(appt_id, old_slot_id, new_slot_id, new_date, slot.get("startTime", ""), slot.get("endTime", ""), slot.get("doctorName", ""))
            except Exception:
                return "❌ Slot taken. Type *back* to choose another."
                
            await db.update_step(session_id, "USER_MENU")
            return "✅ *Appointment Rescheduled!* 🎉\n\n" + render_user_menu()

        # ════════════════════════════════════════════════════════════════════════
        #  CHANGE PASSWORD FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "CHANGE_PW_OLD":
            if norm in ("back", "menu"):
                await db.update_step(session_id, "USER_MENU")
                return render_user_menu()
            phone = session.get("userPhone") or temp.get("phone")
            user = await db.get_user(phone)
            if not user or not check_password(message, user.get("password", ""), user.get("salt", "")):
                return "❌ *Incorrect password.* Try again or type *back*."
            await db.update_step(session_id, "CHANGE_PW_NEW")
            return "✅ Identity verified! Enter your *new password*:"

        case "CHANGE_PW_NEW":
            if not is_strong_password(message):
                return "❌ Password too weak. Try again:"
            phone = session.get("userPhone") or temp.get("phone")
            salt = generate_salt()
            hashed = hash_with_salt(message, salt)
            await asyncio.gather(db.update_user_password(phone, hashed, salt), db.update_step(session_id, "USER_MENU"))
            return "🎉 *Password changed!* 🔒\n\n" + render_user_menu()

        # ════════════════════════════════════════════════════════════════════════
        #  FEEDBACK FLOW
        # ════════════════════════════════════════════════════════════════════════

        case "FEEDBACK_RATING":
            if message in ("1", "2", "3", "4", "5"):
                f_id = new_uuid()
                feedback_item = {
                    "feedbackId": f_id, "sessionId": session_id,
                    "rating": int(message), "feedbackText": "",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "status": "PENDING_TEXT"
                }
                await asyncio.gather(
                    db.put_feedback(feedback_item),
                    db.bulk_session_update(
                        session_id, "SET tempData.#fid=:fid, currentStep=:step",
                        {"#fid": "feedbackId"}, {":fid": f_id, ":step": "FEEDBACK_TEXT"}
                    )
                )
                return "⭐ *Thanks!* Comment, or type *Skip* to finish."
            return "📝 Rate 1–5:"

        case "FEEDBACK_TEXT":
            f_id = temp.get("feedbackId")
            text = "" if norm == "skip" else message
            await asyncio.gather(db.update_feedback_text(f_id, text), db.update_step(session_id, "USER_MENU"))
            return "🙏 *Thank you!* 💙\n\n" + render_user_menu()

        # ════════════════════════════════════════════════════════════════════════
        #  AGENT CHAT STATE (LANGCHAIN)
        # ════════════════════════════════════════════════════════════════════════

        case "AGENT_CHAT":
            if norm in ("menu", "back", "exit", "cancel", "return"):
                await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "USER_MENU", ":s": []})
                return render_user_menu()
                
            replies = await agent_service.handle_message(raw_message, session_id, session)
            if replies: return "\n\n".join(replies)
            
            return "❌ Agent unavailable. Type *menu* to exit."


# ════════════════════════════════════════════════════════════════════════════════
#  USER MENU DISPATCH
# ════════════════════════════════════════════════════════════════════════════════

async def _handle_user_menu(session_id: str, norm: str, raw_message: str, session: dict) -> str:
    role = session.get("role", "GUEST")
    if role not in ("USER", "ADMIN"):
        from src.utils.renderers import render_main_menu
        return (
            "⚠️ *Login Required*\n\n"
            "To access this feature or menu, you need to identify yourself first.\n\n"
            "1️⃣  *Register*  — Create your patient profile\n"
            "2️⃣  *Login*     — Access your existing records\n"
            "3️⃣  *Admin*     — Hospital management\n\n"
            "📌 _Please type a number to continue._"
        )

    # ── Prioritized Intelligent Intent Mapping ────────────────────────────────
    # specific keywords first to prevent mis-routing (e.g. "cancel my order" shouldn't hit "booking")

    if any(k in norm for k in ("menu", "patient dashboard", "dashboard", "home")):
        await db.update_step(session_id, "USER_MENU")
        return render_user_menu()

    if "admin" in norm:
        if role != "ADMIN":
            return "⛔ *Unauthorized Access*\n\nYou do not have admin privileges.\n\n" + render_user_menu()
        return await _handle_user_menu(session_id, "0", raw_message, session) # Or direct to main menu? Wait, if admin, let them use agent? No, logout or let them fail gracefully. Actually just block access for now.

    if any(k in norm for k in ("emergency", "ambulance", "emer", "ambu", "accident", "severe", "bleeding", "critical", "blood")):
        return await _handle_user_menu(session_id, "5", raw_message, session)

    if any(k in norm for k in ("cancel", "stop", "abort", "remove", "order")):
        return await _handle_user_menu(session_id, "7", raw_message, session)

    if any(k in norm for k in ("account", "security", "pass", "manage", "private")):
        return await _handle_user_menu(session_id, "9", raw_message, session)

    if any(k in norm for k in ("support", "agent", "help", "contact", "human")):
        return await _handle_user_menu(session_id, "6", raw_message, session)

    # View appointments MUST come before Booking logic because the word "appointment" is shared
    if any(k in norm for k in ("view appointment", "view appt", "my appointment", "my consultations", "appointments", "appts", "modify")):
        return await _handle_user_menu(session_id, "2", raw_message, session)

    # Re-schedule also shares words with booking
    if any(k in norm for k in ("reschedule", "change time", "postpone")):
        return await _handle_user_menu(session_id, "8", raw_message, session)

    if any(k in norm for k in ("book", "appointment", "booking")):
        # Enter the intelligent agent state so manual menu numbers don't hijack the conversation
        await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "AGENT_CHAT", ":s": ["USER_MENU"]})
        replies = await agent_service.handle_message(raw_message, session_id, session)
        if replies: return "\n\n".join(replies)
        
        # Fallback if agent is down
        return await _handle_user_menu(session_id, "1", raw_message, session)

    if any(k in norm for k in ("service", "facility", "facilities", "faci", "serv")):
        return await _handle_user_menu(session_id, "3", raw_message, session)

    if any(k in norm for k in ("feedback", "rating", "rate", "review")):
        return await _handle_user_menu(session_id, "4", raw_message, session)

    # Emergency logic moved up, remove duplicates if any.

    match norm:
        case "1":
            stack = session.get("stepStack", []) + ["USER_MENU"]
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "PATIENT_NAME", ":s": stack})
            return "🗓️ *Book Appointment*\n\n👤 Enter the *Patient's Full Name*:"

        case "2":
            phone = session.get("userPhone") or session.get("tempData", {}).get("phone")
            appts = await db.get_appointments_by_phone(phone)
            if not appts: return "💭 *No Appointments Found.*\n\n" + render_user_menu()
            
            appts_sorted = sorted(appts, key=lambda a: (1 if a.get("status") in ("BOOKED", "APPROVED") else 0, a.get("createdAt", "")), reverse=True)
            await db.save_temp(session_id, "apptList", [a["appointmentId"] for a in appts_sorted])
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "USER_APPT_ACTION", ":s": []})
            
            lines = [f"┌─ #{str(i+1).zfill(2)} ─────────────────\n│ 🆔 {a.get('appointmentId','N/A')}\n│ 🏥 {a.get('department','N/A')} — 👨‍⚕️ {a.get('doctor','N/A')}\n│ 📅 {a.get('date','N/A')} ⏰ {a.get('time','N/A')}\n│ 🔖 {status_label(a.get('status',''))}\n└────────────────────────" for i, a in enumerate(appts_sorted)]
            return f"📋 *Your Appointments*\n\n" + "\n\n".join(lines) + "\n\n💡 Type the number to manage it."

        case "3":
            return "🏥 *Healix Services*\n\n✅ 24/7 casualty\n✅ ICU\n✅ Path Labs\n✅ Pharmacy\n\n" + render_user_menu()

        case "4":
            await db.update_step(session_id, "FEEDBACK_RATING")
            return "📝 *Rate your experience (1–5):*"

        case "5":
            return "🚨 *EMERGENCY!*\n\n🚑 Ambulance: *108*\n📞 +91-12345-67890\n\n" + render_user_menu()

        case "6": # Support
            return "🤝 *Customer Support*\n\n📞 +91-98765-43210\n📧 support@healix.com\n\n" + render_user_menu()

        case "7": # Cancel by ID
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "CANCEL_ID", ":s": []})
            return "❌ *Cancel Appointment*\n\nKeep your Appointment ID ready. Enter the ID to cancel:"

        case "8": # Reschedule by ID
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "RESCHEDULE_ID", ":s": []})
            return "🗓️ *Reschedule Appointment*\n\nEnter your *Appointment ID* to select a new slot:"

        case "9": # Account Security
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "CHANGE_PW_OLD", ":s": ["USER_MENU"]})
            return "🔐 *Account Security*\n\nEnter your *current password* to change it:\n\n💡 _Tip: Type 'back' to return to menu._"

        case "0" | "logout":
            await db.full_reset(session_id, "START")
            return "👋 *Logged out successfully.* 🔒"

        case _:
            # Fallback to Agent for any other natural language
            await db.bulk_session_update(session_id, "SET currentStep=:c, stepStack=:s", {}, {":c": "AGENT_CHAT", ":s": ["USER_MENU"]})
            replies = await agent_service.handle_message(raw_message, session_id, session)
            if replies: return "\n\n".join(replies)
            return render_user_menu()


# ════════════════════════════════════════════════════════════════════════════════
#  HELPER: render a step's prompt
# ════════════════════════════════════════════════════════════════════════════════

async def _render_step(step: str, temp: dict, session_id: str) -> str:
    match step:
        case "MAIN_MENU" | "START": return render_main_menu()
        case "USER_MENU":  return render_user_menu()
        
        # Registration & Login
        case "REG_NAME": return "🎉 *Let's get you set up!*\n\nFirst — what's your *Full Name*?"
        case "REG_PHONE": return "📞 Enter your *10-digit Phone Number*:"
        case "REG_EMAIL": return "✅ *Phone verified!*\n\n📧 Enter your *Email Address*:"
        case "REG_PASSWORD": return "📬 *Email saved!*\n\n🔐 Create a *Password*:\n• 8+ chars  • Uppercase & lowercase  • Number & symbol (@#$…)"
        case "REG_OTP": return f"🔐 *Verify your account!*\n\nYour OTP: *{temp.get('regOtp', '...')}*\n\nEnter it to complete registration.\n_Type RESEND if needed._"
        
        case "LOGIN_PHONE": return "👋 *Welcome back!*\n\nEnter your registered *Phone Number*:"
        case "LOGIN_PASSWORD": return "✅ *Account found!*\n\n🔐 Enter your *Password*:\n_(Type FORGOT to reset your password)_"
        case "LOGIN_OTP": return f"🔐 *Security check!*\n\nYour OTP: *{temp.get('loginOtp', '...')}*\n\nEnter it to verify. 🛡️\n_Type RESEND if needed._"

        case "FP_OTP": return f"🔑 *Password Reset*\n\nYour OTP: *{temp.get('fpOtp', '...')}*\n\nEnter it to proceed.\n_Type RESEND if needed._"
        case "FP_NEW_PW": return "✅ *OTP verified!*\n\n🔐 Enter your *New Password*:\n• 8+ chars • A-Z, a-z • Number & symbol"

        # Booking
        case "PATIENT_NAME": return "👤 Patient's Full Name?"
        case "PATIENT_EMAIL": return "👤 *Patient saved* ✅\n\n📧 Enter the *Patient's Email Address*:"
        case "PATIENT_PHONE": return "📧 *Email saved!* 🙌\n\n📞 Enter the *Patient's Contact Number* (10-digit):"
        case "DEPARTMENT":
            depts = await db.get_all_departments()
            return render_list([d["name"] for d in depts], "🏥 Select Department:")
        case "DOCTOR":
            docs = await db.get_doctors_by_department(temp.get("departmentId", ""))
            return render_list([d["name"] for d in docs], "👨‍⚕️ Select Doctor:")
        case "AWAITING_CALENDAR":
            return "📅 Please use the calendar link, then type *done*."

        # Appt Actions
        case "USER_APPT_ACTION": 
            appt_ids = temp.get("apptList") or []
            return (
                "📋 Type the *number* of the appointment to manage, or *back* to return to menu.\n"
                f"_(You have {len(appt_ids)} appointment(s) listed.)_"
            )
        case "APPT_ACTION_CHOICE": return "Please type *1* (Cancel), *2* (Reschedule) or *3* (Back)."
        case "CANCEL_ID": return "❌ *Cancel Appointment*\n\nKeep your Appointment ID ready. Enter the ID to cancel:"
        case "CANCEL_CONFIRM": return "⚠️ *Confirm Cancellation?*\n\nType *Yes* to cancel  |  *No* to keep it"
        case "RESCHEDULE_ID": return "🗓️ *Reschedule Appointment*\n\nEnter your *Appointment ID* to select a new slot:"
        case "RESCHEDULE_DATE": return "Enter the *new date* (format: YYYY-MM-DD):"
        case "RESCHEDULE_SLOT": return "🗓️ *Available Slots:*\n\nType the *slot number* to confirm."

        # Other Options
        case "CHANGE_PW_OLD": return "🔐 *Account Security*\n\nEnter your *current password* to change it:"
        case "CHANGE_PW_NEW": return "✅ Identity verified! Enter your *new password*:"
        case "FEEDBACK_RATING": return "📝 *Rate your experience (1–5):*"
        case "FEEDBACK_TEXT": return "⭐ *Thanks!* Comment, or type *Skip* to finish."

        case _: return render_user_menu()

def _pick(message: str, norm: str, items: list[dict], name_key: str) -> dict | None:
    if message.isdigit():
        idx = int(message) - 1
        return items[idx] if 0 <= idx < len(items) else None
    for item in items:
        if item.get(name_key, "").lower() == norm: return item
    return None
