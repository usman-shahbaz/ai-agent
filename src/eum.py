import json
import os

import boto3

PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
API_VERSION = os.environ.get("META_API_VERSION", "v21.0")
_client = boto3.client("socialmessaging")


def _post(body):
    return _client.send_whatsapp_message(
        originationPhoneNumberId=PHONE_NUMBER_ID,
        message=json.dumps(body).encode("utf-8"),
        metaApiVersion=API_VERSION,
    )


def _send(to, payload):
    return _post({"messaging_product": "whatsapp", "to": "+" + to.lstrip("+"), **payload})


def text(to, body):
    _send(to, {"type": "text", "text": {"body": body[:4096]}})


def buttons(to, body, buttons):
    """Max 3 buttons, each title <= 20 chars."""
    _send(to, {"type": "interactive", "interactive": {
        "type": "button",
        "body": {"text": body[:1024]},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
            for b in buttons[:3]
        ]},
    }})


def list_picker(to, body, button_text, sections):
    """Max 10 rows in total. button_text <= 20, row title <= 24, description <= 72."""
    left = 10
    trimmed = []
    for s in sections:
        rows = s["rows"][:left]
        left -= len(rows)
        if rows:
            trimmed.append({"title": s["title"][:24], "rows": [
                {"id": r["id"], "title": r["title"][:24],
                 **({"description": r["description"][:72]} if r.get("description") else {})}
                for r in rows]})
    _send(to, {"type": "interactive", "interactive": {
        "type": "list",
        "body": {"text": body[:1024]},
        "action": {"button": button_text[:20], "sections": trimmed},
    }})


def template(to, name, lang, params, quick_replies=()):
    """Business-initiated message. Must be an approved template.
    params fill {{1}}, {{2}}... in the body; quick_replies are button payloads in order."""
    components = [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}]
    for i, payload in enumerate(quick_replies):
        components.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                           "parameters": [{"type": "payload", "payload": payload}]})
    _send(to, {"type": "template", "template": {
        "name": name, "language": {"code": lang}, "components": components}})


def mark_read(message_id, typing=True):
    """Blue ticks + typing dots. Never let this break the flow."""
    body = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    if typing:
        body["typing_indicator"] = {"type": "text"}
    try:
        _post(body)
    except Exception as e:
        print("mark_read failed:", repr(e))


def download_media(media_id, bucket, key):
    """Copy an inbound media file (voice note, image) from WhatsApp into S3."""
    _client.get_whatsapp_message_media(
        mediaId=media_id,
        originationPhoneNumberId=PHONE_NUMBER_ID,
        destinationS3File={"bucketName": bucket, "key": key},
    )
