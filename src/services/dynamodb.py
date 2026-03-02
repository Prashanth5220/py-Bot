"""
dynamodb.py — boto3 DynamoDB helpers.

Java equivalent: a @Repository / DAO class.
All public functions are async (they run the blocking boto3 calls in a
thread pool so they don't block FastAPI's event loop).
"""
import asyncio
import logging
import uuid
from functools import partial
from typing import Any
import cachetools

import boto3
from boto3.dynamodb.conditions import Attr, Key

from src.config import settings

logger = logging.getLogger(__name__)

# ── DynamoDB resource (one per process) ───────────────────────────────────────
import os
_dynamodb = boto3.resource(
    "dynamodb",
    region_name=settings.aws_region,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

# ── Table references ──────────────────────────────────────────────────────────
_sessions_table     = _dynamodb.Table(settings.session_table)
_users_table        = _dynamodb.Table(settings.user_table)
_departments_table  = _dynamodb.Table(settings.department_table)
_doctors_table      = _dynamodb.Table(settings.doctor_table)
_timeslots_table    = _dynamodb.Table(settings.timeslot_table)
_appointments_table = _dynamodb.Table(settings.appointment_table)
_feedback_table     = _dynamodb.Table(settings.feedback_table)
_admins_table       = _dynamodb.Table(settings.admins_table)

SESSION_TTL = 86400  # 24 hours in seconds

# ── Enterprise level Caching ──────────────────────────────────────────────────
_dept_cache = cachetools.TTLCache(maxsize=1, ttl=300) # 5 minutes
_doc_cache = cachetools.TTLCache(maxsize=1, ttl=300)
_dept_doc_cache = cachetools.TTLCache(maxsize=50, ttl=300)
_slot_cache = cachetools.TTLCache(maxsize=500, ttl=10) # 10 seconds for slots as they change fast


def _get_ttl() -> int:
    import time
    return int(time.time()) + SESSION_TTL


# ── async wrapper — runs blocking boto3 calls off the event loop ───────────────
# ── async wrapper — runs blocking boto3 calls off the event loop ───────────────
async def _run(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def scan_all(table, filter_expression=None) -> list[dict]:
    """
    Paginated scan — fetches ALL records across multiple pages.
    A single scan returns at most 1MB; this loops via LastEvaluatedKey.
    """
    items: list[dict] = []
    kwargs: dict = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression
    while True:
        resp = await _run(table.scan, **kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_session(session_id: str) -> dict:
    """Load session from DynamoDB; create a fresh one if missing."""
    resp = await _run(
        _sessions_table.get_item,
        Key={"sessionId": session_id},
        ConsistentRead=True,
    )
    item = resp.get("Item")
    if item:
        return item
    return await _create_session(session_id)


async def _create_session(session_id: str) -> dict:
    new_session = {
        "sessionId": session_id,
        "currentStep": "START",
        "role": "GUEST",
        "tempData": {},
        "stepStack": [],
        "expiresAt": _get_ttl(),
    }
    await _run(_sessions_table.put_item, Item=new_session)
    return new_session


async def update_step(session_id: str, step: str) -> None:
    await _run(
        _sessions_table.update_item,
        Key={"sessionId": session_id},
        UpdateExpression="SET currentStep = :s",
        ExpressionAttributeValues={":s": step},
    )


async def full_reset(session_id: str, step: str = "START") -> None:
    await _run(
        _sessions_table.update_item,
        Key={"sessionId": session_id},
        UpdateExpression=(
            "SET currentStep=:s, tempData=:t, stepStack=:stk, #r=:r, expiresAt=:ttl"
        ),
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={
            ":s": step,
            ":t": {},
            ":stk": [],
            ":r": "GUEST",
            ":ttl": _get_ttl(),
        },
    )


async def save_temp(session_id: str, key: str, value: Any) -> None:
    """Save a single key inside tempData."""
    await _run(
        _sessions_table.update_item,
        Key={"sessionId": session_id},
        UpdateExpression="SET tempData.#k = :v",
        ExpressionAttributeNames={"#k": key},
        ExpressionAttributeValues={":v": value},
    )


async def bulk_session_update(session_id: str, expression: str,
                               names: dict, values: dict) -> None:
    """Run a raw UpdateExpression — for complex multi-field updates."""
    kwargs: dict[str, Any] = {
        "Key": {"sessionId": session_id},
        "UpdateExpression": expression,
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    await _run(_sessions_table.update_item, **kwargs)


async def get_session_by_token(token: str) -> dict | None:
    """Scan for a session that has tempData.bookingToken == token."""
    resp = await _run(
        _sessions_table.scan,
        FilterExpression=Attr("tempData.bookingToken").eq(token),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ═══════════════════════════════════════════════════════════════════════════════
#  USER HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_user(phone: str) -> dict | None:
    resp = await _run(_users_table.get_item, Key={"phone": phone})
    return resp.get("Item")


async def put_user(user: dict) -> None:
    await _run(_users_table.put_item, Item=user)


async def update_user_password(phone: str, hashed_pw: str, salt: str) -> None:
    await _run(
        _users_table.update_item,
        Key={"phone": phone},
        UpdateExpression="SET #pw = :pw, salt = :salt",
        ExpressionAttributeNames={"#pw": "password"},
        ExpressionAttributeValues={":pw": hashed_pw, ":salt": salt},
    )


async def get_admin(admin_id: str) -> dict | None:
    """Fetch admin record from Admins table by adminId."""
    resp = await _run(_admins_table.get_item, Key={"adminId": admin_id})
    return resp.get("Item")


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPARTMENT & DOCTOR HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_all_departments() -> list[dict]:
    if "all" in _dept_cache:
        return _dept_cache["all"]
    resp = await _run(_departments_table.scan)
    items = resp.get("Items", [])
    _dept_cache["all"] = items
    return items


async def get_doctors_by_department(department_id: str) -> list[dict]:
    """Query using departmentId-index GSI."""
    if department_id in _dept_doc_cache:
        return _dept_doc_cache[department_id]
        
    try:
        resp = await _run(
            _doctors_table.query,
            IndexName="departmentId-index",
            KeyConditionExpression=Key("departmentId").eq(department_id),
        )
        items = resp.get("Items", [])
    except Exception:
        # Fallback: scan (for local DynamoDB without GSI)
        resp = await _run(_doctors_table.scan)
        items = [d for d in resp.get("Items", [])
                if str(d.get("departmentId")) == str(department_id)]
                
    _dept_doc_cache[department_id] = items
    return items


async def get_all_doctors() -> list[dict]:
    """Scan all doctors — used by admin dashboard and NLU matching."""
    if "all" in _doc_cache:
        return _doc_cache["all"]
    resp = await _run(_doctors_table.scan)
    items = sorted(resp.get("Items", []), key=lambda d: d.get("name", ""))
    _doc_cache["all"] = items
    return items


async def get_appointments_by_date(date: str) -> list[dict]:
    """Scan appointments filtered by date — used by admin today-view."""
    resp = await _run(
        _appointments_table.scan,
        FilterExpression=Attr("date").eq(date),
    )
    items = resp.get("Items", [])
    return sorted(items, key=lambda a: a.get("time", ""))


async def get_all_timeslots_for_date(date: str) -> list[dict]:
    """Scan all time slots for a given date — used by admin slot-view."""
    resp = await _run(
        _timeslots_table.scan,
        FilterExpression=Attr("date").eq(date),
    )
    items = resp.get("Items", [])
    return sorted(items, key=lambda s: (s.get("doctorName", ""), s.get("startTime", "")))


# ═══════════════════════════════════════════════════════════════════════════════
#  TIME SLOT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_available_slots(docter_id: str, date: str) -> list[dict]:
    resp = await _run(
        _timeslots_table.scan,
        FilterExpression=(
            Attr("docterId").eq(docter_id)
            & Attr("date").eq(date)
            & Attr("status").eq("AVAILABLE")
        ),
    )
    slots = resp.get("Items", [])
    return sorted(slots, key=lambda s: s.get("startTime", ""))


async def book_slot(slot_id: str) -> None:
    """
    Conditionally mark slot BOOKED — raises ConditionalCheckFailedException
    if the slot was already booked (double-booking guard).
    """
    await _run(
        _timeslots_table.update_item,
        Key={"slotId": slot_id},
        UpdateExpression="SET #s = :b",
        ConditionExpression=Attr("status").eq("AVAILABLE"),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":b": "BOOKED"},
    )


async def free_slot(slot_id: str) -> None:
    """Release a slot back to AVAILABLE (used on appointment cancellation)."""
    await _run(
        _timeslots_table.update_item,
        Key={"slotId": slot_id},
        UpdateExpression="SET #s = :a",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":a": "AVAILABLE"},
    )


async def get_available_slots_for_doctor_on_date(doctor_id: str, date: str) -> list[dict]:
    """Return AVAILABLE slots for a given doctor on a specific date, sorted by start time."""
    cache_key = f"{doctor_id}_{date}"
    if cache_key in _slot_cache:
        return _slot_cache[cache_key]

    items = await scan_all(
        _timeslots_table,
        Attr("docterId").eq(doctor_id) & Attr("date").eq(date) & Attr("status").eq("AVAILABLE"),
    )
    sorted_items = sorted(items, key=lambda s: s.get("startTime", ""))
    _slot_cache[cache_key] = sorted_items
    return sorted_items



async def create_slot(slot: dict) -> None:
    """Persist a brand-new time slot (status=AVAILABLE) into the TimeSlotsTable."""
    await _run(_timeslots_table.put_item, Item=slot)

# ═══════════════════════════════════════════════════════════════════════════════
#  APPOINTMENT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def put_appointment(appointment: dict) -> None:
    await _run(_appointments_table.put_item, Item=appointment)


async def get_appointment(appointment_id: str) -> dict | None:
    resp = await _run(
        _appointments_table.get_item,
        Key={"appointmentId": appointment_id},
    )
    return resp.get("Item")


async def cancel_appointment(appointment_id: str) -> None:
    await _run(
        _appointments_table.update_item,
        Key={"appointmentId": appointment_id},
        UpdateExpression="SET #s = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "CANCELED"},
    )


async def reschedule_appointment(
    appointment_id: str,
    old_slot_id: str | None,
    new_slot_id: str,
    new_date: str,
    new_start_time: str,
    new_end_time: str,
    new_doctor_id: str,
    new_doctor_name: str,
) -> None:
    """
    Update the appointment record and manage slots correctly.
    Note: The new slot should already be marked as BOOKED before calling this.
    """
    import asyncio
    tasks = [
        _run(
            _appointments_table.update_item,
            Key={"appointmentId": appointment_id},
            UpdateExpression=(
                "SET #d=:d, #t=:t, endTime=:et, slotId=:sid, "
                "docterId=:did, doctor=:doc, doctorName=:doc, #s=:s"
            ),
            ExpressionAttributeNames={"#d": "date", "#t": "time", "#s": "status"},
            ExpressionAttributeValues={
                ":d": new_date,
                ":t": f"{new_start_time} - {new_end_time}",
                ":et": new_end_time,
                ":sid": new_slot_id,
                ":did": new_doctor_id,
                ":doc": new_doctor_name,
                ":s": "BOOKED",
            },
        ),
    ]
    if old_slot_id:
        # Release the old slot
        tasks.append(free_slot(old_slot_id))

    await asyncio.gather(*tasks)


async def get_appointments_by_phone(phone: str) -> list[dict]:
    resp = await _run(
        _appointments_table.scan,
        FilterExpression=(
            Attr("userPhone").eq(phone) | Attr("phone").eq(phone)
        ),
    )
    return resp.get("Items", [])


# ═══════════════════════════════════════════════════════════════════════════════
#  FEEDBACK HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def put_feedback(feedback: dict) -> None:
    await _run(_feedback_table.put_item, Item=feedback)


async def update_feedback_text(feedback_id: str, text: str) -> None:
    await _run(
        _feedback_table.update_item,
        Key={"feedbackId": feedback_id},
        UpdateExpression="SET feedbackText = :t, #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":t": text, ":s": "COMPLETED"},
    )


async def get_doctor(docter_id: str) -> dict | None:
    """Fetch a single doctor record by ID."""
    resp = await _run(_doctors_table.get_item, Key={"docterId": docter_id})
    return resp.get("Item")
