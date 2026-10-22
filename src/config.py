import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("CLINIC_TZ", "Asia/Kolkata"))
MODEL_ID = os.environ.get("MODEL_ID", "")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", "")
VOICE_LANGS = [x for x in os.environ.get("VOICE_LANGS", "en-IN,hi-IN").split(",") if x]
TEMPLATE_LANG = os.environ.get("TEMPLATE_LANG", "en")
CURRENCY = os.environ.get("CURRENCY", "₹")

# Staff = people allowed to use the ops assistant (digits only, with country code)
STAFF = {s.strip().lstrip("+") for s in os.environ.get("STAFF_NUMBERS", "").split(",") if s.strip()}

HORIZON_DAYS = 45        # how far ahead patients can book
MAX_UPCOMING = 3         # max upcoming bookings per phone number
HANDOFF_HOURS = 4        # bot stays quiet this long after a human handoff

CLINIC = {
    "name": os.environ.get("CLINIC_NAME", "CityCare Clinic"),
    "address": os.environ.get("CLINIC_ADDRESS", "12 MG Road, Bengaluru"),
    "phone": os.environ.get("CLINIC_PHONE", "+91 80 0000 0000"),
    "hours": os.environ.get("CLINIC_HOURS", "Mon-Sat, see each doctor's schedule"),
    "emergency_number": os.environ.get("EMERGENCY_NUMBER", "112"),
    "payments": os.environ.get("CLINIC_PAYMENTS", "Cash, UPI and cards accepted at the clinic"),
    "notes": os.environ.get("CLINIC_NOTES", "Please arrive 10 minutes early and bring previous reports."),
}
