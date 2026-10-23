from datetime import datetime

import boto3
from botocore.config import Config

import config
import db
import eum
import tools

_br = boto3.client("bedrock-runtime", config=Config(read_timeout=60, retries={"max_attempts": 3}))
MAX_STEPS = 6
MAX_HISTORY = 20

PATIENT_PROMPT = """You are the WhatsApp assistant of {clinic}. You help patients book, reschedule and \
cancel appointments and answer basic clinic questions.

Current time: {now}.
Known patient name (may be empty): {name}
This patient's upcoming bookings: {bookings}

    system = [{"text": _system(role, conv, phone)}]
    tool_cfg = {"tools": tools.specs(role)}

    final = None
    for _ in range(MAX_STEPS):
        resp = _br.converse(modelId=config.MODEL_ID, system=system, messages=messages,
                            toolConfig=tool_cfg,
                            inferenceConfig={"maxTokens": 1024, "temperature": 0.2})
        msg = resp["output"]["message"]
        messages.append(msg)
        if resp["stopReason"] != "tool_use":
            final = "".join(b.get("text", "") for b in msg["content"]).strip()
            break
        results = []
        for block in msg["content"]:
            if "toolUse" in block:
                tu = block["toolUse"]
                out = tools.run(ctx, tu["name"], tu["input"])
                results.append({"toolResult": {
                    "toolUseId": tu["toolUseId"], "content": [{"json": out}],
                    "status": "error" if "error" in out else "success"}})
        messages.append({"role": "user", "content": results})

    if final is None:   # ran out of steps
        final = "Sorry, I'm having trouble with that. A team member will help you shortly."
    if final:
        eum.text(phone, final)
    elif not ctx.interactive:
        eum.text(phone, "Sorry, I didn't get that. Could you rephrase?")

    # Persist a compact transcript (text only); tool turns are re-derived from the DB each time.
    record = (final + "\n" + "\n".join(ctx.notes)).strip() or "[no reply]"
    history = (history + [user_msg, {"role": "assistant", "content": [{"text": record[:1500]}]}])[-MAX_HISTORY:]
    db.save_history(phone, history)
