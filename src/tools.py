"""Tools the LLM can call. Identity is NEVER taken from the model: `ctx.phone` is the WhatsApp
sender, injected by our code, so a patient can only touch their own bookings."""
import calendar
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import config
import db
import eum
import scheduling as sch

REG = {}


def _S(desc):
    return {"type": "string", "description": desc}


DATE = _S("Date as YYYY-MM-DD")
TIME = _S("24-hour time HH:MM, exactly as returned by check_availability")
PART = {"type": "string", "enum": ["morning", "afternoon", "evening"]}


def tool(name, desc, props=None, required=(), roles=("patient", "staff")):
    def deco(fn):
        REG[name] = {"fn": fn, "roles": roles, "spec": {"toolSpec": {
            "name": name, "description": desc,
            "inputSchema": {"json": {"type": "object", "properties": props or {},
                                     "required": list(required)}}}}}
        return fn
    return deco


class Ctx:
    def __init__(self, phone, role):
        self.phone, self.role = phone, role
        self.notes = []          # what we sent via interactive messages (saved into history)
        self.interactive = False

    def note(self, s):
        self.notes.append(s)
        self.interactive = True


def specs(role):
    return [t["spec"] for t in REG.values() if role in t["roles"]]


def _dec(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def run(ctx, name, args):
    t = REG.get(name)
    if not t or ctx.role not in t["roles"]:
        return {"error": f"Unknown tool {name}"}
    try:
        return json.loads(json.dumps(t["fn"](ctx, args or {}), default=_dec))
    except KeyError as e:
        return {"error": f"Missing argument {e}"}
    except ValueError as e:
        return {"error": f"Invalid value: {e}"}
    except Exception as e:
        print("TOOL ERROR", name, repr(e))
        return {"error": "Internal error, please try again or hand off to staff."}


# ---------- helpers ----------
def _now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _doc_view(d):
    return {"doctor_id": d["doctor_id"], "name": d["name"], "specialty": d["specialty"],
            "fee": d.get("fee"), "currency": config.CURRENCY,
            "languages": d.get("languages", []),
            "works_on": [calendar.day_abbr[int(x)] for x in d["days"]]}


def _pick_doctors(a):
    docs = db.list_doctors()
    if a.get("doctor_id"):
        return [d for d in docs if d["doctor_id"] == a["doctor_id"]]
    q = (a.get("specialty") or "").lower().strip()
    if q:
        return [d for d in docs if q in d["specialty"].lower() or q in d["name"].lower()]
    return docs


def _next_available(docs, start):
    for i in range(config.HORIZON_DAYS):
        d = start + timedelta(days=i)
        for doc in docs:
            free = sch.free_slots(doc, d)
            if free:
                return {"doctor_id": doc["doctor_id"], "doctor": doc["name"],
                        "date": d.isoformat(), "first_time": free[0]}
    return None


def _brief(b):
    return {"booking_id": b["booking_id"], "doctor": b["doctor_name"], "date": b["date"],
            "time": b["time"], "patient_name": b["patient_name"], "reason": b.get("reason", "")}


def _mine(ctx, booking_id):
    b = db.get_booking(booking_id)
    if not b or b["phone"] != ctx.phone:
        return None, {"error": "No booking with that ID found for this phone number."}
    if b["status"] != "confirmed":
        return None, {"error": f"That booking is already {b['status']}."}
    return b, None


def _notify_staff(msg):
    for s in config.STAFF:
        try:
            eum.text(s, msg)
        except Exception as e:   # staff outside the 24h window can't receive free-form text
            print("staff notify failed", s, repr(e))


# ---------- patient + staff ----------
@tool("get_clinic_info", "Clinic address, phone, hours, payment info, notes, and the list of doctors "
      "with specialty, fee and working days.")
def get_clinic_info(ctx, a):
    return {"clinic": config.CLINIC, "doctors": [_doc_view(d) for d in db.list_doctors()]}


@tool("check_availability",
      "Find free appointment slots. Provide date_from (and optionally date_to, max 7 days span). "
      "Filter by doctor_id or specialty. Returns free times per doctor per day.",
      {"date_from": DATE, "date_to": DATE, "doctor_id": _S("Doctor ID"),
       "specialty": _S("e.g. dentist, pediatrician, general physician"), "part_of_day": PART},
      ["date_from"])
def check_availability(ctx, a):
    today = datetime.now(config.TZ).date()
    d0 = max(sch.parse_date(a["date_from"]), today)
    d1 = sch.parse_date(a.get("date_to") or a["date_from"])
    d1 = min(max(d1, d0), d0 + timedelta(days=6), today + timedelta(days=config.HORIZON_DAYS))
    docs = _pick_doctors(a)
    if not docs:
        return {"error": "No matching doctor.", "doctors": [_doc_view(d) for d in db.list_doctors()]}
    out, d = [], d0
    while d <= d1:
        for doc in docs:
            free = sch.free_slots(doc, d, a.get("part_of_day"))
            if free:
                out.append({"doctor_id": doc["doctor_id"], "doctor": doc["name"],
                            "date": d.isoformat(), "weekday": d.strftime("%A"),
                            "free_count": len(free), "free_times": free[:12]})
        d += timedelta(days=1)
    if not out:
        return {"slots": [], "next_available": _next_available(docs, d1 + timedelta(days=1))}
    return {"slots": out[:14]}


# ---------- patient ----------
@tool("offer_slots",
      "Send the patient a tappable WhatsApp list of free times for ONE doctor on ONE date. "
      "Prefer this over typing out many times. Do not repeat the times in your text reply.",
      {"doctor_id": _S("Doctor ID"), "date": DATE, "part_of_day": PART},
      ["doctor_id", "date"], roles=("patient",))
def offer_slots(ctx, a):
    doc = db.get_doctor(a["doctor_id"])
    if not doc:
        return {"error": "Unknown doctor_id."}
    d = sch.parse_date(a["date"])
    free = sch.free_slots(doc, d, a.get("part_of_day"))
    if not free:
        return {"error": "No free slots that day.", "next_available": _next_available([doc], d)}
    idx = sorted({round(i * (len(free) - 1) / 9) for i in range(10)}) if len(free) > 10 else range(len(free))
    picks = [free[i] for i in idx]
    rows = [{"id": f"slot|{doc['doctor_id']}|{d.isoformat()}|{t}", "title": sch.fmt_time(t),
             "description": f"{doc['name']} • {config.CURRENCY}{doc.get('fee', '')}"} for t in picks]
    eum.list_picker(ctx.phone, f"*{doc['name']}* ({doc['specialty']})\n{sch.fmt_date(d)} - pick a time:",
                    "Choose time", [{"title": sch.fmt_date(d), "rows": rows}])
    ctx.note(f"[Sent a tap-list of times for {doc['name']} on {d.isoformat()}: {', '.join(picks)}]")
    return {"sent": True, "shown": len(picks), "total_free": len(free)}


@tool("ask_confirmation",
      "Send a summary with Confirm / Change / Never mind buttons. ALWAYS call this before "
      "book_appointment, reschedule_appointment or cancel_appointment, then wait for the reply.",
      {"summary": _S("Short summary of the action, e.g. 'Book Dr X, Thu 23 Oct 10:30 AM for Sara (fever)?'")},
      ["summary"], roles=("patient",))
def ask_confirmation(ctx, a):
    eum.buttons(ctx.phone, a["summary"], [
        {"id": "ok|", "title": "✅ Confirm"}, {"id": "chg|", "title": "✏️ Change"},
        {"id": "stop|", "title": "✖️ Never mind"}])
    ctx.note(f"[Asked for confirmation: {a['summary']}]")
    return {"sent": True}


@tool("book_appointment", "Book a slot. Only after the patient confirmed via ask_confirmation.",
      {"doctor_id": _S("Doctor ID"), "date": DATE, "time": TIME,
       "patient_name": _S("Name of the person who will be seen"),
       "reason": _S("Brief reason for visit, no sensitive details")},
      ["doctor_id", "date", "time", "patient_name"], roles=("patient",))
def book_appointment(ctx, a):
    doc = db.get_doctor(a["doctor_id"])
    if not doc:
        return {"error": "Unknown doctor_id."}
    d = sch.parse_date(a["date"])
    err = sch.validate_slot(doc, d, a["time"])
    if err:
        return {"error": err}
    upcoming = db.bookings_for_phone(ctx.phone)
    if len(upcoming) >= config.MAX_UPCOMING:
        return {"error": f"Limit of {config.MAX_UPCOMING} upcoming bookings reached. Cancel one first."}
    if any(b["date"] == a["date"] and b["time"] == a["time"] for b in upcoming):
        return {"error": "This phone number already has a booking at that exact time."}
    b = {"booking_id": uuid.uuid4().hex[:6].upper(), "phone": ctx.phone,
         "doctor_id": doc["doctor_id"], "doctor_name": doc["name"], "specialty": doc["specialty"],
         "date": a["date"], "time": a["time"], "start_utc": sch.to_utc_iso(d, a["time"]),
         "patient_name": a["patient_name"][:60], "reason": (a.get("reason") or "")[:200],
         "status": "confirmed", "booked_at": _now_utc(), "r24": False, "r2": False,
         "followup_sent": False, "fee": doc.get("fee", 0)}
    try:
        db.create_booking(b)
    except db.SlotTaken:
        return {"error": "slot_taken", "alternatives": sch.free_slots(doc, d)[:6]}
    db.save_conv(ctx.phone, last_name=b["patient_name"])
    return {"ok": True, **_brief(b), "fee": b["fee"], "currency": config.CURRENCY,
            "address": config.CLINIC["address"], "tip": config.CLINIC["notes"]}


@tool("list_my_bookings", "List this patient's upcoming confirmed bookings.", roles=("patient",))
def list_my_bookings(ctx, a):
    return {"bookings": [_brief(b) for b in db.bookings_for_phone(ctx.phone)]}


@tool("reschedule_appointment", "Move an existing booking to a new slot (same doctor). "
      "Only after confirmation.",
      {"booking_id": _S("Booking ID"), "new_date": DATE, "new_time": TIME},
      ["booking_id", "new_date", "new_time"], roles=("patient",))
def reschedule_appointment(ctx, a):
    b, err = _mine(ctx, a["booking_id"])
    if err:
        return err
    doc = db.get_doctor(b["doctor_id"])
    d = sch.parse_date(a["new_date"])
    err = sch.validate_slot(doc, d, a["new_time"])
    if err:
        return {"error": err}
    try:
        db.move_booking(b, a["new_date"], a["new_time"], sch.to_utc_iso(d, a["new_time"]))
    except db.SlotTaken:
        return {"error": "slot_taken", "alternatives": sch.free_slots(doc, d)[:6]}
    return {"ok": True, "booking_id": b["booking_id"], "doctor": b["doctor_name"],
            "date": a["new_date"], "time": a["new_time"]}


@tool("cancel_appointment", "Cancel a booking. Only after confirmation.",
      {"booking_id": _S("Booking ID"), "reason": _S("Optional reason")},
      ["booking_id"], roles=("patient",))
def cancel_appointment(ctx, a):
    b, err = _mine(ctx, a["booking_id"])
    if err:
        return err
    try:
        db.cancel_booking(b, "patient", a.get("reason", ""))
    except db.SlotTaken:
        return {"error": "That booking can no longer be cancelled."}
    return {"ok": True, **_brief(b)}


@tool("request_human_handoff",
      "Pass the chat to clinic staff: patient asks for a human, is upset, or the request is outside "
      "your scope (medical questions, billing disputes, emergencies). Set urgent=true for emergencies.",
      {"reason": _S("One-line summary for staff"), "urgent": {"type": "boolean"}},
      ["reason"], roles=("patient",))
def request_human_handoff(ctx, a):
    db.save_conv(ctx.phone, handoff_until=int(time.time()) + config.HANDOFF_HOURS * 3600)
    conv = db.get_conv(ctx.phone)
    _notify_staff(f"{'🚨 URGENT' if a.get('urgent') else '🙋 Handoff'} from +{ctx.phone}"
                  f" ({conv.get('last_name', 'unknown')})\nReason: {a['reason']}\n"
                  f"Reply to the patient by telling me: reply to +{ctx.phone}: <your message>")
    return {"ok": True, "staff_configured": bool(config.STAFF),
            "instruction": "Tell the patient a team member will reply here soon."}


# ---------- staff ----------
STAFF = ("staff",)


@tool("day_schedule", "All bookings for a date, optionally for one doctor.",
      {"date": DATE, "doctor_id": _S("Optional doctor ID")}, ["date"], roles=STAFF)
def day_schedule(ctx, a):
    start, end = sch.day_bounds_utc(sch.parse_date(a["date"]))
    rows = []
    for st in ("confirmed", "completed", "no_show"):
        rows += db.by_status(st, start, end)
    if a.get("doctor_id"):
        rows = [b for b in rows if b["doctor_id"] == a["doctor_id"]]
    rows.sort(key=lambda b: b["start_utc"])
    return {"date": a["date"], "count": len(rows), "bookings": [
        {**_brief(b), "phone": b["phone"], "status": b["status"],
         "patient_confirmed": bool(b.get("patient_confirmed"))} for b in rows]}


@tool("clinic_stats", "Booking counts per doctor and status for a date range (max 31 days).",
      {"date_from": DATE, "date_to": DATE}, ["date_from", "date_to"], roles=STAFF)
def clinic_stats(ctx, a):
    d0 = sch.parse_date(a["date_from"])
    d1 = min(sch.parse_date(a["date_to"]), d0 + timedelta(days=30))
    start, _ = sch.day_bounds_utc(d0)
    _, end = sch.day_bounds_utc(d1)
    by_doc, totals = {}, {}
    for st in db.STATUSES:
        for b in db.by_status(st, start, end):
            by_doc.setdefault(b["doctor_name"], {}).setdefault(st, 0)
            by_doc[b["doctor_name"]][st] += 1
            totals[st] = totals.get(st, 0) + 1
    seen = totals.get("completed", 0) + totals.get("no_show", 0)
    return {"by_doctor": by_doc, "totals": totals,
            "no_show_rate_pct": round(100 * totals.get("no_show", 0) / seen, 1) if seen else None}


@tool("mark_visit", "Mark a booking as completed or no_show. no_show sends the patient a rebook message.",
      {"booking_id": _S("Booking ID"), "outcome": {"type": "string", "enum": ["completed", "no_show"]}},
      ["booking_id", "outcome"], roles=STAFF)
def mark_visit(ctx, a):
    b = db.get_booking(a["booking_id"])
    if not b:
        return {"error": "Booking not found."}
    db.update_booking(b["booking_id"], status=a["outcome"], followup_sent=True)
    sent = None
    if a["outcome"] == "no_show":
        try:
            eum.template(b["phone"], "missed_appointment", config.TEMPLATE_LANG,
                         [b["patient_name"], b["doctor_name"], sch.fmt_date(b["date"])],
                         ["miss_rebook|"])
            sent = True
        except Exception as e:
            print("missed template failed", repr(e))
            sent = False
    return {"ok": True, "status": a["outcome"], "rebook_message_sent": sent}


@tool("block_time", "Block a doctor's slots (leave, meeting). Slots already booked are reported, not touched.",
      {"doctor_id": _S("Doctor ID"), "date": DATE, "from_time": TIME,
       "to_time": _S("End HH:MM (exclusive)"), "reason": _S("Reason")},
      ["doctor_id", "date", "from_time", "to_time"], roles=STAFF)
def block_time(ctx, a):
    doc = db.get_doctor(a["doctor_id"])
    if not doc:
        return {"error": "Unknown doctor_id."}
    d = sch.parse_date(a["date"])
    ttl = int(sch.local_dt(d, "00:00").timestamp()) + 8 * 86400
    blocked, busy = [], []
    for s in sch.all_slots(doc, d):
        if a["from_time"] <= s < a["to_time"]:
            ok = db.create_block(doc["doctor_id"], a["date"], s, ttl, a.get("reason", "blocked"))
            (blocked if ok else busy).append(s)
    return {"blocked": blocked, "already_booked_or_blocked": busy,
            "note": "Booked patients in the busy list need to be rescheduled manually."}


@tool("reply_to_patient", "Send a WhatsApp message to a patient (only works within 24h of their last message).",
      {"phone": _S("Patient number, digits with country code"), "text": _S("Message")},
      ["phone", "text"], roles=STAFF)
def reply_to_patient(ctx, a):
    phone = "".join(c for c in a["phone"] if c.isdigit())
    conv = db.get_conv(phone)
    if time.time() - int(conv.get("last_inbound", 0)) > 86400:
        return {"error": "Patient last wrote more than 24h ago; WhatsApp blocks free-form text. "
                         "Use a template or ask them to message first."}
    eum.text(phone, a["text"])
    db.append_history(phone, "assistant", f"[Clinic staff said]: {a['text']}")
    return {"ok": True}


@tool("resume_bot", "Hand the patient's chat back to the AI assistant.",
      {"phone": _S("Patient number, digits")}, ["phone"], roles=STAFF)
def resume_bot(ctx, a):
    phone = "".join(c for c in a["phone"] if c.isdigit())
    db.save_conv(phone, handoff_until=0)
    return {"ok": True}

