

"""
seed_data.py — Populate DynamoDB with test data for local development.

Run once after your .env is filled with real AWS credentials:
    py -3.12 seed_data.py

Creates:
  - 3 Departments
  - 6 Doctors (2 per department)
  - 30 TimeSlots (5 per doctor, today + tomorrow)
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "ap-south-2")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# ── Tables ────────────────────────────────────────────────────────────────────
sessions_tbl     = dynamodb.Table(os.getenv("SESSION_TABLE",     "ChatSessions"))
users_tbl        = dynamodb.Table(os.getenv("USER_TABLE",        "Users"))
departments_tbl  = dynamodb.Table(os.getenv("DEPARTMENT_TABLE",  "Departments"))
doctors_tbl      = dynamodb.Table(os.getenv("DOCTOR_TABLE",      "Doctors"))
timeslots_tbl    = dynamodb.Table(os.getenv("TIMESLOT_TABLE",    "TimeSlots"))
appointments_tbl = dynamodb.Table(os.getenv("APPOINTMENT_TABLE", "Appointments"))
feedback_tbl     = dynamodb.Table(os.getenv("FEEDBACK_TABLE",    "Feedback"))

# ── Data ──────────────────────────────────────────────────────────────────────
DEPARTMENTS = [
    {"departmentId": "dept-001", "name": "Cardiology"},
    {"departmentId": "dept-002", "name": "Orthopaedics"},
    {"departmentId": "dept-003", "name": "Neurology"},
]

DOCTORS = [
    # Cardiology
    {"docterId": "doc-001", "departmentId": "dept-001", "name": "Dr. Arjun Mehta",    "specialization": "Heart & Vascular"},
    {"docterId": "doc-002", "departmentId": "dept-001", "name": "Dr. Priya Sharma",   "specialization": "Cardiac Surgery"},
    # Orthopaedics
    {"docterId": "doc-003", "departmentId": "dept-002", "name": "Dr. Ramesh Patil",   "specialization": "Knee & Hip"},
    {"docterId": "doc-004", "departmentId": "dept-002", "name": "Dr. Sneha Kulkarni", "specialization": "Spine Surgery"},
    # Neurology
    {"docterId": "doc-005", "departmentId": "dept-003", "name": "Dr. Anil Desai",     "specialization": "Brain & Spine"},
    {"docterId": "doc-006", "departmentId": "dept-003", "name": "Dr. Meera Joshi",    "specialization": "Epilepsy & Stroke"},
]

# Time slots: 5 slots per doctor, across today and tomorrow
SLOT_TIMES = [
    ("09:00", "09:30"),
    ("10:00", "10:30"),
    ("11:00", "11:30"),
    ("14:00", "14:30"),
    ("15:00", "15:30"),
]

IST = timezone(timedelta(hours=5, minutes=30))


def generate_slots() -> list[dict]:
    slots = []
    today = datetime.now(IST).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")

    for doctor in DOCTORS:
        for date in [today, tomorrow]:
            for start, end in SLOT_TIMES:
                slots.append({
                    "slotId":     str(uuid.uuid4()),
                    "docterId":   doctor["docterId"],
                    "doctorName": doctor["name"],
                    "date":       date,
                    "startTime":  start,
                    "endTime":    end,
                    "status":     "AVAILABLE",
                })
    return slots


# ── Seeding functions ─────────────────────────────────────────────────────────

def seed_table(table, items: list[dict], label: str):
    print(f"\n📥 Seeding {label} ({len(items)} items)...")
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"   ✅ Done — {label}")


def main():
    print("=" * 55)
    print("  🏥 Healix — DynamoDB Seed Script")
    print("=" * 55)
    print(f"  Region : {AWS_REGION}")
    print(f"  Tables : Departments / Doctors / TimeSlots")

    slots = generate_slots()

    seed_table(departments_tbl, DEPARTMENTS, "Departments")
    seed_table(doctors_tbl,     DOCTORS,     "Doctors")
    seed_table(timeslots_tbl,   slots,       "TimeSlots")

    print("\n" + "=" * 55)
    print("  ✅ Seed complete!")
    print(f"  {len(DEPARTMENTS)} departments | {len(DOCTORS)} doctors | {len(slots)} time slots")
    print("=" * 55)
    print("\n  Next: start the server →")
    print("  py -3.12 -m uvicorn src.main:app --reload --port 3000\n")


if __name__ == "__main__":
    main()
