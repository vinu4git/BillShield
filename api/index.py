from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from billshield import billing, llm
from billshield.db import supabase, upload_bill_file

USER_ID = 1  # hardcoded until auth lands
ALLOWED = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
MAX_BYTES = 4 * 1024 * 1024  # Vercel rejects request bodies over 4.5 MB
LINE_COLUMNS = ("description", "charge_key", "kind", "quantity", "amount", "is_flagged",
                "flag_reason", "expected_amount", "difference", "plain_words")

app = FastAPI(title="BillShield")


@app.exception_handler(requests.HTTPError)
def groq_error(request: Request, exc: requests.HTTPError):
    """Show the model provider's error, such as a 429 rate limit, instead of a bare 500."""
    status = exc.response.status_code if exc.response is not None else 0
    body = exc.response.text[:300] if exc.response is not None else str(exc)
    return JSONResponse({"detail": f"Model call failed ({status}): {body}"}, status_code=502)


def _json(value):
    return str(value) if isinstance(value, Decimal) else value


def _get_bill(bill_id: int) -> dict:
    rows = supabase.table("bills").select("*").eq("id", bill_id).eq("user_id", USER_ID).execute().data
    if not rows:
        raise HTTPException(404, "Bill not found")
    return rows[0]


def _get_promise(promise_id: int) -> dict:
    return supabase.table("promises").select("*").eq("id", promise_id).execute().data[0]


def _latest_message(bill_id: int) -> dict | None:
    rows = supabase.table("messages").select("*").eq("bill_id", bill_id).eq("is_latest", True).execute().data
    return rows[0] if rows else None


def _promise_for(provider: str | None, text: str) -> dict:
    """Reuse the current promise if it hasn't changed; otherwise start a new one from now."""
    q = supabase.table("promises").select("*").eq("user_id", USER_ID)
    q = q.eq("provider", provider) if provider else q.is_("provider", "null")
    rows = q.order("valid_from", desc=True).limit(1).execute().data
    if rows and rows[0]["text"].strip() == text.strip():
        return rows[0]
    return supabase.table("promises").insert({"user_id": USER_ID, "provider": provider, "text": text}).execute().data[0]


def _draft(lines: list[dict], promise: str, last_draft: str = "", feedback: str = "") -> str:
    """Code writes the facts; the model only words them. Any number it invents sends us back to the facts."""
    facts = [f for l in lines if (f := billing.fact(l))]
    if not facts:
        return billing.all_clear(lines)
    message = llm.write_message(facts, promise, last_draft, feedback)
    if not message or not billing.only_known_numbers(message, " ".join(facts)):
        message = billing.fallback_message(facts)
    return billing.mask(message)


def _save_message(bill_id: int, version: int, text: str, lines: list[dict]) -> dict:
    message = supabase.table("messages").insert({"bill_id": bill_id, "version": version, "draft_text": text}) \
        .execute().data[0]
    disputed = [{"message_id": message["id"], "line_item_id": l["id"]} for l in lines if l["is_flagged"]]
    if disputed:
        supabase.table("message_line_items").insert(disputed).execute()
    return message


def _bill_response(bill: dict) -> dict:
    lines = supabase.table("line_items").select("*").eq("bill_id", bill["id"]).order("id").execute().data
    # "Has this charge shown up on my earlier bills?"
    keys = list({l["charge_key"] for l in lines if l["charge_key"]})
    earlier = supabase.table("line_items").select("charge_key, bills!inner(user_id)") \
        .in_("charge_key", keys).lt("bill_id", bill["id"]).eq("bills.user_id", USER_ID).execute().data if keys else []
    seen = Counter(r["charge_key"] for r in earlier)
    for l in lines:
        l["fact"] = billing.fact(l)
        l["seen_before"] = seen[l["charge_key"]]
    promise = _get_promise(bill["promise_id"]) if bill["promise_id"] else None
    return {**bill, "promise": promise, "line_items": lines, "message": _latest_message(bill["id"])}


@app.post("/api/bills")
async def create_bill(file: UploadFile = File(...), expectation: str = Form(...), provider: str = Form("")):
    if file.content_type not in ALLOWED:
        raise HTTPException(415, f"Upload a PDF or image, not {file.content_type}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is over 4 MB")

    reading = llm.read_bill(data, file.content_type)
    lines = billing.normalise(reading.get("lines", []))
    if not lines:
        raise HTTPException(422, "Couldn't read any lines from that bill")
    billing.flag(lines, llm.match_promise(lines, expectation))

    provider = provider.strip() or reading.get("provider") or None
    promise = _promise_for(provider, expectation)
    i = billing.printed_total_index(lines)
    bill = supabase.table("bills").insert({
        "user_id": USER_ID,
        "promise_id": promise["id"],
        "provider": provider,
        "period_start": billing.parse_date(reading.get("period_start")),
        "period_end": billing.parse_date(reading.get("period_end")),
        "due_date": billing.parse_date(reading.get("due_date")),
        "printed_total": _json(lines[i]["amount"]) if i is not None else None,
        "computed_total": _json(billing.computed_total(lines)),
    }).execute().data[0]

    path = upload_bill_file(USER_ID, bill["id"], file.filename or "bill", data, file.content_type)
    bill = supabase.table("bills").update({"file_path": path}).eq("id", bill["id"]).execute().data[0]

    saved = supabase.table("line_items").insert([
        {"bill_id": bill["id"], **{c: _json(l[c]) for c in LINE_COLUMNS}} for l in lines
    ]).execute().data
    _save_message(bill["id"], 1, _draft(saved, expectation), saved)
    return _bill_response(bill)


@app.get("/api/bills")
def list_bills():
    return supabase.table("bills").select("*").eq("user_id", USER_ID).order("uploaded_at", desc=True).execute().data


@app.get("/api/bills/{bill_id}")
def get_bill(bill_id: int):
    return _bill_response(_get_bill(bill_id))


class ReviseIn(BaseModel):
    feedback: str


@app.post("/api/bills/{bill_id}/revise")
def revise_bill(bill_id: int, body: ReviseIn):
    bill = _get_bill(bill_id)
    last = _latest_message(bill_id)
    if last and last["status"] == "sent":
        raise HTTPException(409, "Message already sent")

    lines = supabase.table("line_items").select("*").eq("bill_id", bill_id).order("id").execute().data
    promise = _get_promise(bill["promise_id"])["text"] if bill["promise_id"] else ""
    text = _draft(lines, promise, last["draft_text"] if last else "", body.feedback)

    # A new version of the message; the line items are facts and stay as they are.
    supabase.table("messages").update({"is_latest": False}).eq("bill_id", bill_id).eq("is_latest", True).execute()
    _save_message(bill_id, (last["version"] if last else 0) + 1, text, lines)
    return _bill_response(bill)


class AcceptIn(BaseModel):
    message: str


@app.post("/api/bills/{bill_id}/accept")
def accept_bill(bill_id: int, body: AcceptIn):
    bill = _get_bill(bill_id)
    last = _latest_message(bill_id)
    if not last:
        raise HTTPException(404, "No message to accept")
    if last["status"] == "sent":
        raise HTTPException(409, "Message already sent")
    supabase.table("messages").update({
        "sent_text": billing.mask(body.message),
        "words_changed": billing.words_changed(last["draft_text"], body.message),
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", last["id"]).execute()
    return _bill_response(bill)


@app.get("/api/messages")
def list_sent_messages():
    """Which messages did I send, and about which line?"""
    return supabase.table("messages") \
        .select("id, bill_id, version, sent_text, words_changed, sent_at, bills!inner(provider, user_id), "
                "message_line_items(line_items(id, description, amount, flag_reason))") \
        .eq("status", "sent").eq("bills.user_id", USER_ID).order("sent_at", desc=True).execute().data


@app.get("/api/promises")
def list_promises(provider: str | None = None):
    """What was I promised, and when did it change?"""
    q = supabase.table("promises").select("*").eq("user_id", USER_ID)
    if provider:
        q = q.eq("provider", provider)
    return q.order("valid_from", desc=True).execute().data


@app.get("/api/charges/{charge_key}")
def charge_history(charge_key: str):
    """Has this charge shown up on my earlier bills?"""
    return supabase.table("line_items") \
        .select("id, description, amount, bill_id, bills!inner(provider, period_start, period_end, user_id)") \
        .eq("charge_key", charge_key).eq("bills.user_id", USER_ID).order("bill_id").execute().data


# Locally, serve the page from this same server so the page and the API share
# one origin, exactly like on Vercel. On Vercel, its CDN serves public/ itself.
# This mount comes last so the /api routes above are matched first.
PUBLIC = Path(__file__).resolve().parents[1] / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
