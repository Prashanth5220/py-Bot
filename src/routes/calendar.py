"""
calendar.py — Visual appointment calendar routes.

GET  /book?token=xxx          → serve the calendar HTML page
GET  /api/slots?token=xxx&date=YYYY-MM-DD → available slots JSON
POST /api/confirm              → confirm booking, write appointmentId to session
"""
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.config import settings
from src.services.dynamodb import (
    get_session_by_token, get_available_slots, book_slot,
    put_appointment, bulk_session_update, _sessions_table, _run,
    reschedule_appointment, get_doctor
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Booking Calendar"])

# ── Indian public holidays 2026 ────────────────────────────────────────────────
HOLIDAYS_2026: set[str] = {
    "2026-01-01", "2026-01-26", "2026-03-03", "2026-03-31",
    "2026-04-01", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-08-15", "2026-08-23", "2026-10-02", "2026-10-22",
    "2026-11-10", "2026-11-11", "2026-12-25",
}

HOLIDAY_NAMES: dict[str, str] = {
    "2026-01-01": "New Year's Day",    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",              "2026-03-31": "Eid ul-Fitr",
    "2026-04-01": "Ram Navami",        "2026-04-03": "Good Friday",
    "2026-04-14": "Ambedkar Jayanti",  "2026-05-01": "Maharashtra / Labour Day",
    "2026-08-15": "Independence Day",  "2026-08-23": "Janmashtami",
    "2026-10-02": "Gandhi Jayanti",    "2026-10-22": "Dussehra",
    "2026-11-10": "Diwali",            "2026-11-11": "Diwali Padwa",
    "2026-12-25": "Christmas",
}


def _today_ist() -> str:
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%Y-%m-%d")


def _now_ist_time() -> str:
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%H:%M")


# ── GET /book ──────────────────────────────────────────────────────────────────
@router.get("/book", response_class=HTMLResponse)
async def serve_calendar(token: str = ""):
    if not token:
        return HTMLResponse(_error_page("Invalid or missing booking token."), status_code=400)

    session = await get_session_by_token(token)
    if not session:
        return HTMLResponse(_error_page("This booking link has expired or is invalid."), status_code=400)

    if int(time.time()) > session.get("tempData", {}).get("tokenExpiry", 0):
        return HTMLResponse(
            _error_page("This booking link has expired. Please go back to Telegram and start again."),
            status_code=400
        )

    td = session.get("tempData", {})
    return HTMLResponse(
        _build_calendar_page(
            token=token,
            doctor_name=td.get("doctorName", "Doctor"),
            department_name=td.get("departmentName", "Department"),
            patient_name=td.get("patientName", ""),
            holidays=sorted(HOLIDAYS_2026),
            holiday_names=HOLIDAY_NAMES,
        )
    )


# ── GET /api/slots ─────────────────────────────────────────────────────────────
@router.get("/api/slots")
async def get_slots(token: str = "", date: str = ""):
    if not token or not date:
        return JSONResponse({"error": "token and date required"}, status_code=400)

    session = await get_session_by_token(token)
    if not session:
        return JSONResponse({"error": "Invalid token"}, status_code=400)

    today = _today_ist()
    logger.info(f"📅 Fetching slots for date: {date} (Today: {today})")
    
    if date < today:
        return JSONResponse({"slots": [], "message": "Cannot book for past dates."})
    if date in HOLIDAYS_2026:
        name = HOLIDAY_NAMES.get(date, "public holiday")
        return JSONResponse({"slots": [], "message": f"{name} — Hospital closed. Please select another date."})

    td = session.get("tempData", {})
    docter_id = td.get("docterId") or td.get("doctorId") or td.get("reschedDoctorId")
    
    if not docter_id:
        logger.error(f"❌ Session {token} missing doctor identifier")
        return JSONResponse({"error": "Session missing doctor"}, status_code=400)

    logger.info(f"🔍 Searching slots for Doctor: {docter_id} on {date}")
    slots = await get_available_slots(docter_id, date)
    logger.info(f"📊 Found {len(slots)} raw slots from DB")

    # Filter past slots if today
    if date == today:
        now_t = _now_ist_time()
        # Add 5 mins buffer effectively by comparing > now_t
        # If it's 10:05, a 10:00 slot is already in progress/passed.
        before_count = len(slots)
        slots = [s for s in slots if s.get("startTime", "00:00") > now_t]
        after_count = len(slots)
        if before_count > 0 and after_count == 0:
            logger.info(f"⏳ All {before_count} slots for today have already passed (Current time: {now_t}).")

    return JSONResponse({
        "slots": [
            {"slotId": s["slotId"], "startTime": s["startTime"], "endTime": s["endTime"]}
            for s in slots
        ],
        "message": "All slots for today have passed. Please pick a future date." if (date == today and not slots) else None
    })


# ── POST /api/confirm ──────────────────────────────────────────────────────────
class ConfirmRequest(BaseModel):
    token: str
    slotId: str
    date: str
    startTime: str
    endTime: str


@router.post("/api/confirm")
async def confirm_booking(body: ConfirmRequest):
    session = await get_session_by_token(body.token)
    if not session:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=400)

    if int(time.time()) > session.get("tempData", {}).get("tokenExpiry", 0):
        return JSONResponse({"error": "Booking link expired"}, status_code=400)

    td = session.get("tempData", {})
    resched_id = td.get("reschedApptId")

    # Resolve identifies
    docter_id = td.get("docterId") or td.get("doctorId") or td.get("reschedDoctorId")
    doctor_name = td.get("doctorName") or td.get("reschedDoctorName")

    if not doctor_name and docter_id:
        # Final fallback: fetch doctor name from DB if we have an ID
        doc_rec = await get_doctor(docter_id)
        doctor_name = doc_rec.get("name", "Doctor") if doc_rec else "Doctor"

    appointment_id = resched_id if resched_id else str(uuid.uuid4())

    # Atomic slot booking (conditional update — prevents double booking)
    from botocore.exceptions import ClientError
    try:
        await book_slot(body.slotId)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return JSONResponse(
                {"error": "This slot was just taken. Please go back and pick another."},
                status_code=409
            )
        raise

    # Create/Update appointment record + write booking result back to session
    if resched_id:
        # RESCHEDULE CASE
        old_slot_id = td.get("reschedOldSlotId")
        await reschedule_appointment(
            appointment_id=resched_id,
            old_slot_id=old_slot_id,
            new_slot_id=body.slotId,
            new_date=body.date,
            new_start_time=body.startTime,
            new_end_time=body.endTime,
            new_doctor_id=docter_id,
            new_doctor_name=doctor_name,
        )
    else:
        # NEW BOOKING CASE
        await put_appointment({
            "appointmentId": appointment_id,
            "docterId": docter_id,
            "slotId": body.slotId,
            "date": body.date,
            "time": f"{body.startTime} - {body.endTime}",
            "status": "BOOKED",
            "patientName": td.get("patientName"),
            "patientEmail": td.get("patientEmail"),
            "phone": td.get("patientPhone"),
            "userPhone": session.get("userPhone") or td.get("phone") or td.get("patientPhone"),
            "doctor": doctor_name,
            "department": td.get("departmentName"),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

    # Update session with the result
    await bulk_session_update(
        session["sessionId"],
        "SET tempData.#cbid=:apptId, tempData.#cdate=:date, tempData.#ctime=:ctime",
        {"#cbid": "calendarBookingId", "#cdate": "calendarDate", "#ctime": "calendarTime"},
        {":apptId": appointment_id, ":date": body.date,
         ":ctime": f"{body.startTime} - {body.endTime}"},
    )

    return JSONResponse({"success": True, "appointmentId": appointment_id})


# ── HTML page builder ──────────────────────────────────────────────────────────
def _build_calendar_page(token, doctor_name, department_name, patient_name,
                          holidays: list, holiday_names: dict) -> str:
    import json
    holidays_json = json.dumps(holidays)
    holiday_names_json = json.dumps(holiday_names)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Healix — Book Appointment</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
<style>
  :root {{--teal:#0d9488;--teal-d:#0f7a70;--teal-l:#ccfbf1;--red:#ef4444;--red-l:#fee2e2;--gray:#94a3b8;--gray-l:#f1f5f9;--text:#0f172a;--text-m:#475569;--border:#e2e8f0;--white:#ffffff;--radius:14px;--shadow:0 4px 24px rgba(0,0,0,.10);}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#0d9488 0%,#0369a1 100%);min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:24px 12px 48px;}}
  .card{{background:var(--white);border-radius:var(--radius);box-shadow:var(--shadow);width:100%;max-width:480px;overflow:hidden;animation:slideUp .4s ease;}}
  @keyframes slideUp{{from{{opacity:0;transform:translateY(20px)}}to{{opacity:1;transform:none}}}}
  .header{{background:linear-gradient(90deg,#0d9488,#0369a1);color:var(--white);padding:22px 24px 18px;}}
  .header h1{{font-size:1.25rem;font-weight:700;}}
  .header p{{font-size:.85rem;opacity:.85;margin-top:4px;}}
  .steps{{display:flex;gap:6px;padding:16px 24px 0;}}
  .step{{flex:1;height:4px;border-radius:4px;background:var(--border);}}
  .step.active{{background:var(--teal);}} .step.done{{background:var(--teal-d);}}
  .section{{padding:20px 24px;display:none;}} .section.active{{display:block;}}
  .section-title{{font-size:.8rem;font-weight:600;color:var(--teal);text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px;}}
  .cal-nav{{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}}
  .cal-nav button{{background:var(--gray-l);border:none;width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:1rem;color:var(--text);transition:background .15s;}}
  .cal-nav button:hover{{background:var(--teal-l);color:var(--teal);}}
  .cal-month{{font-size:1rem;font-weight:600;color:var(--text);}}
  .cal-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;}}
  .cal-head{{text-align:center;font-size:.7rem;font-weight:600;color:var(--gray);padding:4px 0 8px;}}
  .cal-day{{aspect-ratio:1;display:flex;align-items:center;justify-content:center;border-radius:8px;font-size:.85rem;cursor:pointer;transition:all .15s;position:relative;}}
  .cal-day.empty{{cursor:default;}} .cal-day.past{{color:var(--gray);cursor:not-allowed;}}
  .cal-day.holiday{{background:var(--red-l);color:var(--red);font-weight:600;}}
  .cal-day.holiday::after{{content:'';position:absolute;bottom:4px;left:50%;transform:translateX(-50%);width:4px;height:4px;border-radius:50%;background:var(--red);}}
  .cal-day.available{{color:var(--text);font-weight:500;}}
  .cal-day.available:hover{{background:var(--teal-l);color:var(--teal);}}
  .cal-day.selected{{background:var(--teal);color:var(--white);font-weight:700;}}
  .cal-day.today:not(.selected){{border:2px solid var(--teal);color:var(--teal);font-weight:700;}}
  .toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(80px);background:#1e293b;color:#fff;padding:10px 20px;border-radius:30px;font-size:.85rem;transition:transform .3s;z-index:999;white-space:nowrap;}}
  .toast.show{{transform:translateX(-50%) translateY(0);}} .toast.error{{background:var(--red);}}
  .slots-loader{{text-align:center;padding:20px;color:var(--gray);font-size:.9rem;}}
  .slot-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;}}
  .slot-btn{{padding:12px 8px;border:2px solid var(--border);border-radius:10px;background:var(--white);font-size:.85rem;font-weight:500;cursor:pointer;text-align:center;color:var(--text);transition:all .15s;}}
  .slot-btn:hover{{border-color:var(--teal);background:var(--teal-l);color:var(--teal);}}
  .slot-btn.picked{{border-color:var(--teal);background:var(--teal);color:#fff;}}
  .no-slots{{text-align:center;color:var(--gray);font-size:.9rem;padding:16px 0;}}
  .summary{{background:var(--gray-l);border-radius:10px;padding:16px;margin-bottom:18px;font-size:.9rem;}}
  .summary-row{{display:flex;gap:8px;margin-bottom:8px;}} .summary-row:last-child{{margin-bottom:0;}}
  .summary-label{{color:var(--text-m);min-width:80px;}} .summary-value{{color:var(--text);font-weight:600;}}
  .btn{{width:100%;padding:14px;border:none;border-radius:10px;font-size:1rem;font-weight:600;cursor:pointer;transition:all .15s;margin-top:8px;}}
  .btn-primary{{background:var(--teal);color:#fff;}} .btn-primary:hover{{background:var(--teal-d);}} .btn-primary:disabled{{background:var(--gray);cursor:not-allowed;}}
  .btn-ghost{{background:var(--gray-l);color:var(--text-m);}} .btn-ghost:hover{{background:var(--border);}}
  .success-icon{{font-size:3.5rem;text-align:center;margin:8px 0 12px;}}
  .success-title{{font-size:1.2rem;font-weight:700;text-align:center;color:var(--teal);margin-bottom:6px;}}
  .success-sub{{font-size:.9rem;color:var(--text-m);text-align:center;margin-bottom:18px;}}
  .appt-id-box{{background:var(--teal-l);border:2px dashed var(--teal);border-radius:10px;padding:12px 16px;text-align:center;font-family:monospace;font-size:.9rem;color:var(--teal-d);font-weight:700;word-break:break-all;margin-bottom:16px;}}
  .note{{font-size:.78rem;color:var(--text-m);text-align:center;line-height:1.5;}}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>🏥 Healix — Book Appointment</h1>
    <p>👨‍⚕️ {doctor_name} &nbsp;|&nbsp; 🏥 {department_name}{(' &nbsp;|&nbsp; 👤 ' + patient_name) if patient_name else ''}</p>
  </div>
  <div class="steps">
    <div class="step active" id="step1"></div>
    <div class="step" id="step2"></div>
    <div class="step" id="step3"></div>
  </div>
  <!-- Calendar -->
  <div class="section active" id="sec-calendar">
    <div class="section-title">📅 Select Date</div>
    <div class="cal-nav">
      <button id="prevMonth">&#8592;</button>
      <div class="cal-month" id="calMonthLabel"></div>
      <button id="nextMonth">&#8594;</button>
    </div>
    <div class="cal-grid" id="calGrid"></div>
  </div>
  <!-- Slots -->
  <div class="section" id="sec-slots">
    <div class="section-title">⏰ Select Time Slot</div>
    <div id="slotsContainer"><div class="slots-loader">Loading available slots…</div></div>
    <button class="btn btn-ghost" id="backToCalBtn" style="margin-top:16px;">← Change Date</button>
  </div>
  <!-- Confirm -->
  <div class="section" id="sec-confirm">
    <div class="section-title">✅ Confirm Booking</div>
    <div class="summary" id="summaryBox"></div>
    <button class="btn btn-primary" id="confirmBtn">Confirm Appointment ✅</button>
    <button class="btn btn-ghost" id="backToSlotsBtn">← Change Slot</button>
  </div>
  <!-- Success -->
  <div class="section" id="sec-success">
    <div class="success-icon">🎊</div>
    <div class="success-title">Appointment Booked!</div>
    <div class="success-sub">Your appointment is confirmed. Save your ID below.</div>
    <div class="appt-id-box" id="appointmentIdBox"></div>
    <p class="note">📌 Share this ID in Telegram to cancel.<br/>You can close this window now.</p>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
(function(){{
  const TOKEN={json.dumps(token)};
  const HOLIDAYS=new Set({holidays_json});
  const HOLIDAY_NAMES={holiday_names_json};
  function getIST(){{const n=new Date(),i=new Date(n.getTime()+5.5*3600000);return{{date:i.toISOString().slice(0,10),time:i.toISOString().slice(11,16)}};}}
  function toISO(y,m,d){{return y+'-'+String(m+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');}}
  function fDate(iso){{const[y,m,d]=iso.split('-'),ms=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];return parseInt(d)+' '+ms[parseInt(m)-1]+' '+y;}}
  const {{date:today,time:nowTime}}=getIST();
  let viewYear=+today.slice(0,4),viewMonth=+today.slice(5,7)-1,selDate=null,selSlot=null,toastT;
  function toast(m,err=false){{const t=document.getElementById('toast');t.textContent=m;t.className='toast show'+(err?' error':'');clearTimeout(toastT);toastT=setTimeout(()=>t.className='toast',3500);}}
  function goTo(id,si){{document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.step').forEach((s,i)=>s.className='step'+(i<si?' done':i===si?' active':''));}}
  function renderCal(){{
    document.getElementById('calMonthLabel').textContent=new Date(viewYear,viewMonth).toLocaleDateString('en-IN',{{month:'long',year:'numeric'}});
    const g=document.getElementById('calGrid');g.innerHTML='';
    ['Su','Mo','Tu','We','Th','Fr','Sa'].forEach(d=>{{const e=document.createElement('div');e.className='cal-head';e.textContent=d;g.appendChild(e);}});
    const fd=new Date(viewYear,viewMonth,1).getDay(),dm=new Date(viewYear,viewMonth+1,0).getDate();
    for(let i=0;i<fd;i++){{const e=document.createElement('div');e.className='cal-day empty';g.appendChild(e);}}
    for(let d=1;d<=dm;d++){{
      const iso=toISO(viewYear,viewMonth,d),e=document.createElement('div');
      e.textContent=d;
      const iT=iso===today,iP=iso<today,iH=HOLIDAYS.has(iso),iS=iso===selDate;
      e.className='cal-day'+(iP?' past':'')+(iH?' holiday':'')+((!iP&&!iH)?' available':'')+(iT?' today':'')+(iS?' selected':'');
      e.addEventListener('click',()=>{{
        if(iP){{toast('⛔ Cannot book for a past date.',true);return;}}
        if(iH){{toast('🎉 '+(HOLIDAY_NAMES[iso]||'Public holiday')+' — Hospital closed.',true);return;}}
        selDate=iso;selSlot=null;renderCal();loadSlots(iso);
      }});
      g.appendChild(e);
    }}
  }}
  async function loadSlots(date){{
    goTo('sec-slots',1);
    const c=document.getElementById('slotsContainer');c.innerHTML='<div class="slots-loader">Loading slots…</div>';
    try{{
      const r=await fetch('/api/slots?token='+encodeURIComponent(TOKEN)+'&date='+date),data=await r.json();
      if(data.message){{c.innerHTML='<div class="no-slots">😔 '+data.message+'</div>';return;}}
      let slots=data.slots||[];
      if(date===today)slots=slots.filter(s=>s.startTime>nowTime);
      if(!slots.length){{c.innerHTML='<div class="no-slots">⏰ All slots have passed. Pick another date.</div>';return;}}
      const grid=document.createElement('div');grid.className='slot-grid';
      slots.forEach(slot=>{{const b=document.createElement('div');b.className='slot-btn';b.textContent=slot.startTime+' – '+slot.endTime;b.addEventListener('click',()=>{{document.querySelectorAll('.slot-btn').forEach(x=>x.classList.remove('picked'));b.classList.add('picked');selSlot=slot;}});grid.appendChild(b);}});
      c.innerHTML='';c.appendChild(grid);
      const nb=document.createElement('button');nb.className='btn btn-primary';nb.textContent='Continue →';nb.style.marginTop='16px';nb.addEventListener('click',()=>{{if(!selSlot){{toast('Please select a time slot.',true);return;}}showConfirm();}});c.appendChild(nb);
    }}catch{{c.innerHTML='<div class="no-slots">⚠️ Failed to load. Please retry.</div>';}}
  }}
  function showConfirm(){{document.getElementById('summaryBox').innerHTML='<div class="summary-row"><span class="summary-label">📅 Date</span><span class="summary-value">'+fDate(selDate)+'</span></div><div class="summary-row"><span class="summary-label">⏰ Time</span><span class="summary-value">'+selSlot.startTime+' – '+selSlot.endTime+'</span></div>';goTo('sec-confirm',2);}}
  document.getElementById('confirmBtn').addEventListener('click',async function(){{
    this.disabled=true;this.textContent='Confirming…';
    try{{
      const r=await fetch('/api/confirm',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:TOKEN,slotId:selSlot.slotId,date:selDate,startTime:selSlot.startTime,endTime:selSlot.endTime}})}});
      const d=await r.json();
      if(d.success){{document.getElementById('appointmentIdBox').textContent=d.appointmentId;goTo('sec-success',3);document.querySelectorAll('.step').forEach(s=>s.classList.add('done'));}}
      else{{toast('❌ '+(d.error||'Booking failed.'),true);this.disabled=false;this.textContent='Confirm Appointment ✅';}}
    }}catch{{toast('⚠️ Network error. Retry.',true);this.disabled=false;this.textContent='Confirm Appointment ✅';}}
  }});
  document.getElementById('backToCalBtn').addEventListener('click',()=>{{selSlot=null;goTo('sec-calendar',0);}});
  document.getElementById('backToSlotsBtn').addEventListener('click',()=>goTo('sec-slots',1));
  document.getElementById('prevMonth').addEventListener('click',()=>{{viewMonth--;if(viewMonth<0){{viewMonth=11;viewYear--;}}renderCal();}});
  document.getElementById('nextMonth').addEventListener('click',()=>{{viewMonth++;if(viewMonth>11){{viewMonth=0;viewYear++;}}renderCal();}});
  renderCal();
}})();
</script>
</body>
</html>"""


def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/><title>Healix</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet"/>
<style>body{{font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#0d9488,#0369a1);margin:0;}}
.box{{background:#fff;border-radius:16px;padding:40px 32px;max-width:380px;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.15);}}
h2{{color:#0f172a;margin-bottom:8px;}}p{{color:#64748b;font-size:.9rem;}}</style>
</head><body><div class="box"><div style="font-size:3rem;margin-bottom:12px">⚠️</div>
<h2>Booking Link Issue</h2><p>{msg}</p>
<p style="margin-top:16px;">Please go back to <strong>Telegram</strong> and request a new booking link.</p>
</div></body></html>"""
