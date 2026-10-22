"""Runs every 15 minutes. Sends template reminders (24h and 2h before) and post-visit follow-ups.
All messages here are business-initiated, so they MUST be approved WhatsApp templates."""
from datetime import datetime, timedelta, timezone

import config
import db
import eum
import scheduling as sch


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _remind(b):
    try:
        i = b["booking_id"]
        eum.template(b["phone"], "appointment_reminder", config.TEMPLATE_LANG,
                     [b["patient_name"], b["doctor_name"], sch.fmt_date(b["date"]),
                      sch.fmt_time(b["time"]), config.CLINIC["name"]],
                     [f"rem_confirm|{i}", f"rem_resched|{i}", f"rem_cancel|{i}"])
        return True
    except Exception as e:
        print("reminder failed", b["booking_id"], repr(e))
        return False


def _followup(b):
    try:
        eum.template(b["phone"], "visit_followup", config.TEMPLATE_LANG,
                     [b["patient_name"], config.CLINIC["name"], b["doctor_name"]], ["fu_book|"])
    except Exception as e:
        print("followup failed", b["booking_id"], repr(e))


def handler(event, context):
    now = datetime.now(timezone.utc)
    stats = {"r24": 0, "r2": 0, "followup": 0}

    for b in db.by_status("confirmed", sch.utc_iso(now), sch.utc_iso(now + timedelta(hours=25))):
        start = _dt(b["start_utc"])
        hrs = (start - now).total_seconds() / 3600
        lead = (start - _dt(b["booked_at"])).total_seconds() / 3600
        if not b.get("r24") and 3 < hrs <= 24 and lead >= 24:
            kind = "r24"
        elif not b.get("r2") and 0.25 < hrs <= 2.25:
            kind = "r2"
        else:
            continue
        if _remind(b):
            db.update_booking(b["booking_id"], **{kind: True})
            stats[kind] += 1

    # Visits that ended >3h ago and weren't marked no_show by staff count as completed.
    for b in db.by_status("confirmed", sch.utc_iso(now - timedelta(hours=48)),
                          sch.utc_iso(now - timedelta(hours=3))):
        db.update_booking(b["booking_id"], status="completed", followup_sent=True)
        _followup(b)
        stats["followup"] += 1

    print("sweep", stats)
    return stats
