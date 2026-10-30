import json
import time

import agent
import config
import db
import eum
import voice


def _entry(body):
    env = json.loads(body)
    if "Message" in env and "whatsAppWebhookEntry" not in env:   # SNS envelope (raw delivery off)
        env = json.loads(env["Message"])
    entry = env.get("whatsAppWebhookEntry", {})
    return json.loads(entry) if isinstance(entry, str) else entry


def _from_button_id(phone, rid, title):
    """Turn a button/list tap into a sentence the agent understands. None = handled here."""
    action, _, rest = rid.partition("|")
    if action == "slot":
        doc_id, date, t = rest.split("|")
        doc = db.get_doctor(doc_id)
        return f"I choose {doc['name'] if doc else doc_id} (doctor_id {doc_id}) on {date} at {t}."
    if action == "ok":
        return "Yes, confirm."
    if action == "chg":
        return "I want to change something."
    if action == "stop":
        return "Never mind, don't do that."
    if action == "rem_confirm":
        b = db.get_booking(rest)
        if b and b["phone"] == phone:
            db.update_booking(b["booking_id"], patient_confirmed=True)
            eum.text(phone, "Thanks, you're confirmed! See you soon ✅")
        return None
    if action == "rem_resched":
        return f"I want to reschedule my appointment {rest}."
    if action == "rem_cancel":
        return f"I want to cancel my appointment {rest}."
    if action == "fu_book":
        return "I'd like to book a follow-up appointment."
    if action == "miss_rebook":
        return "I missed my appointment and would like to rebook."
    return title or rid


def _to_text(phone, m):
    t = m.get("type")
    if t == "text":
        return m["text"]["body"]
    if t == "interactive":
        r = m["interactive"].get("button_reply") or m["interactive"].get("list_reply")
        return _from_button_id(phone, r["id"], r.get("title", ""))
    if t == "button":     # tap on a template quick-reply button
        return _from_button_id(phone, m["button"].get("payload", ""), m["button"].get("text", ""))
    if t == "audio":
        try:
            heard = voice.transcribe(m["audio"]["id"])
        except Exception as e:
            print("VOICE ERROR", repr(e))
            heard = ""
        if not heard:
            eum.text(phone, "Sorry, I couldn't understand that voice note. Could you type it?")
            return None
        return "[voice note] " + heard
    eum.text(phone, "Sorry, I can read text, voice notes and button taps for now.")
    return None


def process_message(m):
    phone, mid = m["from"], m["id"]
    if not db.claim_message(mid):            # SNS/SQS redelivery -> ignore
        print("duplicate", mid)
        return
    try:
        eum.mark_read(mid)
        text = _to_text(phone, m)
        if text is None:
            return
        role = "staff" if phone in config.STAFF else "patient"
        conv = db.get_conv(phone)
        db.save_conv(phone, last_inbound=int(time.time()))
        print(f"INCOMING role={role} type={m.get('type')} id={mid}")

        if role == "patient" and int(conv.get("handoff_until", 0)) > time.time():
            for s in config.STAFF:           # human is in charge: forward, bot stays quiet
                try:
                    eum.text(s, f"💬 +{phone} ({conv.get('last_name', '?')}): {text}")
                except Exception as e:
                    print("forward failed", repr(e))
            return

        if not db.acquire_lock(phone):
            raise RuntimeError("conversation busy")
        try:
            agent.run(phone, text, role)
        finally:
            db.release_lock(phone)
    except Exception:
        db.release_message(mid)              # let SQS retry (DLQ after 3 tries)
        raise


def handler(event, context):
    failures = []
    for rec in event["Records"]:
        try:
            for change in _entry(rec["body"]).get("changes", []):
                for m in change.get("value", {}).get("messages", []):
                    process_message(m)
        except Exception as e:
            print("ERROR:", repr(e))
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
