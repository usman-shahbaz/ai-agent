from datetime import datetime, timedelta, timezone

import config
import db

PARTS = {"morning": ("00:00", "12:00"), "afternoon": ("12:00", "17:00"), "evening": ("17:00", "24:00")}


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_time(s):
    return datetime.strptime(s, "%H:%M").time()


def local_dt(d, hhmm):
    return datetime.combine(d, parse_time(hhmm), tzinfo=config.TZ)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(d, hhmm):
    return utc_iso(local_dt(d, hhmm))


def day_bounds_utc(d):
    start = datetime.combine(d, datetime.min.time(), tzinfo=config.TZ)
    return utc_iso(start), utc_iso(start + timedelta(days=1))


def fmt_time(hhmm):
    return parse_time(hhmm).strftime("%I:%M %p").lstrip("0")


def fmt_date(d):
    if isinstance(d, str):
        d = parse_date(d)
    return d.strftime("%a, %d %b")


def all_slots(doc, d):
    if d.weekday() not in [int(x) for x in doc["days"]]:
        return []
    step = timedelta(minutes=int(doc["slot_min"]))
    out = []
    for start, end in doc["windows"]:
        t = datetime.combine(d, parse_time(start))
        stop = datetime.combine(d, parse_time(end))
        while t + step <= stop:
            out.append(t.strftime("%H:%M"))
            t += step
    return out


def free_slots(doc, d, part=None):
    earliest = datetime.now(config.TZ) + timedelta(minutes=30)
    taken = db.taken_times(doc["doctor_id"], d.isoformat())
    out = [t for t in all_slots(doc, d) if t not in taken and local_dt(d, t) > earliest]
    if part in PARTS:
        lo, hi = PARTS[part]
        out = [t for t in out if lo <= t < hi]
    return out


def validate_slot(doc, d, hhmm):
    """Return an error string, or None if the slot is bookable in principle."""
    today = datetime.now(config.TZ).date()
    if d < today:
        return "That date is in the past."
    if d > today + timedelta(days=config.HORIZON_DAYS):
        return f"Bookings open only {config.HORIZON_DAYS} days ahead."
    if hhmm not in all_slots(doc, d):
        return f"{doc['name']} has no appointment slot at {hhmm} on {d.isoformat()}."
    if local_dt(d, hhmm) <= datetime.now(config.TZ) + timedelta(minutes=30):
        return "That time is too soon to book."
    return None

