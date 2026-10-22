import json
import os
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

_res = boto3.resource("dynamodb")
_cli = boto3.client("dynamodb")
_ser = TypeSerializer()

BOOKINGS = os.environ.get("BOOKINGS_TABLE", "")
SLOTS = os.environ.get("SLOTS_TABLE", "")
doctors_t = _res.Table(os.environ.get("DOCTORS_TABLE", "x"))
bookings_t = _res.Table(BOOKINGS or "x")
slots_t = _res.Table(SLOTS or "x")
conv_t = _res.Table(os.environ.get("CONV_TABLE", "x"))
processed_t = _res.Table(os.environ.get("PROCESSED_TABLE", "x"))

STATUSES = ("confirmed", "completed", "no_show", "cancelled")


class SlotTaken(Exception):
    pass


def _code(e):
    return e.response["Error"]["Code"]


def _s(item):
    return {k: _ser.serialize(v) for k, v in item.items()}


def _utc_ts(iso):
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _update(table, key, fields):
    names = {f"#{k}": k for k in fields}
    vals = {f":{k}": v for k, v in fields.items()}
    table.update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def _query_all(table, **kw):
    items = []
    while True:
        r = table.query(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


# ---------- message de-duplication ----------
def claim_message(msg_id):
    try:
        processed_t.put_item(Item={"msg_id": msg_id, "ttl": int(time.time()) + 2 * 86400},
                             ConditionExpression="attribute_not_exists(msg_id)")
        return True
    except ClientError as e:
        if _code(e) == "ConditionalCheckFailedException":
            return False
        raise


def release_message(msg_id):
    processed_t.delete_item(Key={"msg_id": msg_id})


# ---------- conversations ----------
def get_conv(phone):
    return conv_t.get_item(Key={"phone": phone}).get("Item") or {"phone": phone}


def save_conv(phone, **fields):
    fields["ttl"] = int(time.time()) + 30 * 86400
    _update(conv_t, {"phone": phone}, fields)


def load_history(conv):
    return json.loads(conv.get("history", "[]"))


def save_history(phone, history):
    save_conv(phone, history=json.dumps(history, ensure_ascii=False))


def append_history(phone, role, text):
    h = load_history(get_conv(phone))
    h.append({"role": role, "content": [{"text": text}]})
    save_history(phone, h[-20:])


def acquire_lock(phone, secs=100, wait=40):
    """Per-user lock so two quick messages don't race on the same conversation."""
    end = time.time() + wait
    while True:
        now = int(time.time())
        try:
            conv_t.update_item(
                Key={"phone": phone},
                UpdateExpression="SET lock_until = :new",
                ConditionExpression="attribute_not_exists(lock_until) OR lock_until < :now",
                ExpressionAttributeValues={":new": now + secs, ":now": now})
            return True
        except ClientError as e:
            if _code(e) != "ConditionalCheckFailedException":
                raise
            if time.time() > end:
                return False
            time.sleep(1)


def release_lock(phone):
    conv_t.update_item(Key={"phone": phone}, UpdateExpression="REMOVE lock_until")


# ---------- doctors ----------
def list_doctors():
    return doctors_t.scan()["Items"]


def get_doctor(doctor_id):
    return doctors_t.get_item(Key={"doctor_id": doctor_id}).get("Item")


# ---------- slots / bookings ----------
def taken_times(doctor_id, date):
    items = _query_all(slots_t, KeyConditionExpression=Key("pk").eq(f"{doctor_id}#{date}"))
    return {i["sk"] for i in items}


def create_booking(b):
    lock = {"pk": f"{b['doctor_id']}#{b['date']}", "sk": b["time"],
            "booking_id": b["booking_id"], "ttl": _utc_ts(b["start_utc"]) + 7 * 86400}
    try:
        _cli.transact_write_items(TransactItems=[
            {"Put": {"TableName": SLOTS, "Item": _s(lock),
                     "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Put": {"TableName": BOOKINGS, "Item": _s(b)}},
        ])
    except ClientError as e:
        if _code(e) == "TransactionCanceledException":
            raise SlotTaken()
        raise


def create_block(doctor_id, date, time_, ttl, reason):
    try:
        slots_t.put_item(
            Item={"pk": f"{doctor_id}#{date}", "sk": time_, "booking_id": "BLOCK",
                  "reason": reason, "ttl": ttl},
            ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as e:
        if _code(e) == "ConditionalCheckFailedException":
            return False
        raise


def get_booking(booking_id):
    return bookings_t.get_item(Key={"booking_id": booking_id.upper()}).get("Item")


def update_booking(booking_id, **fields):
    _update(bookings_t, {"booking_id": booking_id}, fields)


def cancel_booking(b, by, reason):
    try:
        _cli.transact_write_items(TransactItems=[
            {"Delete": {"TableName": SLOTS,
                        "Key": _s({"pk": f"{b['doctor_id']}#{b['date']}", "sk": b["time"]})}},
            {"Update": {
                "TableName": BOOKINGS, "Key": _s({"booking_id": b["booking_id"]}),
                "UpdateExpression": "SET #s = :c, cancelled_by = :by, cancel_reason = :r",
                "ConditionExpression": "#s = :conf",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": _s({":c": "cancelled", ":conf": "confirmed",
                                                 ":by": by, ":r": reason or "-"})}},
        ])
    except ClientError as e:
        if _code(e) == "TransactionCanceledException":
            raise SlotTaken()   # booking no longer in 'confirmed' state
        raise


def move_booking(b, new_date, new_time, new_start_utc):
    new_lock = {"pk": f"{b['doctor_id']}#{new_date}", "sk": new_time,
                "booking_id": b["booking_id"], "ttl": _utc_ts(new_start_utc) + 7 * 86400}
    try:
        _cli.transact_write_items(TransactItems=[
            {"Put": {"TableName": SLOTS, "Item": _s(new_lock),
                     "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Delete": {"TableName": SLOTS,
                        "Key": _s({"pk": f"{b['doctor_id']}#{b['date']}", "sk": b["time"]})}},
            {"Update": {
                "TableName": BOOKINGS, "Key": _s({"booking_id": b["booking_id"]}),
                "UpdateExpression": ("SET #d = :d, #t = :t, start_utc = :su, r24 = :f, r2 = :f, "
                                     "booked_at = :now, rescheduled = :tr"),
                "ConditionExpression": "#s = :conf",
                "ExpressionAttributeNames": {"#d": "date", "#t": "time", "#s": "status"},
                "ExpressionAttributeValues": _s({
                    ":d": new_date, ":t": new_time, ":su": new_start_utc, ":f": False, ":tr": True,
                    ":now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ":conf": "confirmed"})}},
        ])
    except ClientError as e:
        if _code(e) == "TransactionCanceledException":
            raise SlotTaken()
        raise


def bookings_for_phone(phone):
    """Upcoming confirmed bookings for a phone number."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _query_all(
        bookings_t, IndexName="ByPhone",
        KeyConditionExpression=Key("phone").eq(phone) & Key("start_utc").gte(now),
        FilterExpression=Attr("status").eq("confirmed"))


def by_status(status, start_utc, end_utc):
    return _query_all(
        bookings_t, IndexName="ByStatus",
        KeyConditionExpression=Key("status").eq(status) & Key("start_utc").between(start_utc, end_utc))
