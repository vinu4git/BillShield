from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from billshield import billing, llm
from billshield.db import latest_line_items, supabase, upload_bill_file

USER_ID = 1  # hardcoded until auth lands
ALLOWED = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
MAX_BYTES = 4 * 1024 * 1024  # Vercel rejects request bodies over 4.5 MB

app = FastAPI(title="BillShield")


def _draft(lines: list[dict], expectation: str, feedback: str = "") -> tuple[list[dict], str]:
    """Flag lines in code, then let the model word it. Returns (messages per line, final message)."""
    facts = billing.find_issues(lines, llm.match_promise(lines, expectation))
    if not facts:
        return [], billing.all_clear(lines)

    facts_text = " ".join(f["fact"] for f in facts)
    out = llm.write_messages(facts, expectation, feedback)
    by_index = {m.get("index"): m.get("message", "") for m in out.get("line_messages", [])}

    line_messages = []
    for f in facts:
        msg = by_index.get(f["index"], "")
        # Fall back to the code-written fact if the model invented a number.
        line_messages.append({"index": f["index"], "message": msg if billing.only_known_numbers(msg, f["fact"]) else f["fact"]})

    final = out.get("final_message", "")
    if not final or not billing.only_known_numbers(final, facts_text):
        final = "Hello,\n\nI'd like to dispute the following on my bill:\n\n" + \
                "\n".join(f"- {f['fact']}" for f in facts) + "\n\nPlease correct this and confirm.\n\nThank you."
    return line_messages, billing.mask(final)


def _bill_response(bill: dict) -> dict:
    return {**bill, "line_items": latest_line_items(bill["id"])}


def _get_bill(bill_id: int) -> dict:
    rows = supabase.table("bills").select("*").eq("id", bill_id).eq("user_id", USER_ID).execute().data
    if not rows:
        raise HTTPException(404, "Bill not found")
    return rows[0]


@app.post("/api/bills")
async def create_bill(file: UploadFile = File(...), expectation: str = Form(...)):
    if file.content_type not in ALLOWED:
        raise HTTPException(415, f"Upload a PDF or image, not {file.content_type}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is over 4 MB")

    lines = billing.normalise(llm.extract_lines(data, file.content_type))
    if not lines:
        raise HTTPException(422, "Couldn't read any lines from that bill")

    bill = supabase.table("bills").insert({"user_id": USER_ID, "expectation": expectation}).execute().data[0]
    path = upload_bill_file(USER_ID, bill["id"], file.filename or "bill", data, file.content_type)

    line_messages, final = _draft(lines, expectation)
    flagged = {m["index"]: m["message"] for m in line_messages}
    supabase.table("line_items").insert([{
        "bill_id": bill["id"],
        "quantity": l["quantity"],
        "description": l["description"],
        "amount": str(l["amount"]),
        "add_deduct": l["add_deduct"],
        "computed": l["computed"],
        "is_flagged": i in flagged,
        "message": flagged.get(i),
    } for i, l in enumerate(lines)]).execute()

    bill = supabase.table("bills").update({"file_path": path, "final_message": final}) \
        .eq("id", bill["id"]).execute().data[0]
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
    if bill["status"] == "A":
        raise HTTPException(409, "Bill already accepted")

    old = latest_line_items(bill_id)
    lines = [{**l, "amount": billing.money(l["amount"])} for l in old]
    line_messages, final = _draft(lines, bill["expectation"], body.feedback)
    flagged = {m["index"]: m["message"] for m in line_messages}

    # New version of every line; the old ones stay as history.
    supabase.table("line_items").update({"is_latest": False}).eq("bill_id", bill_id).eq("is_latest", True).execute()
    supabase.table("line_items").insert([{
        "bill_id": bill_id,
        "quantity": l["quantity"],
        "description": l["description"],
        "amount": str(l["amount"]),
        "add_deduct": l["add_deduct"],
        "computed": l["computed"],
        "is_flagged": i in flagged,
        "message": flagged.get(i),
        "version": l["version"] + 1,
    } for i, l in enumerate(lines)]).execute()

    bill = supabase.table("bills").update({"final_message": final}).eq("id", bill_id).execute().data[0]
    return _bill_response(bill)


class AcceptIn(BaseModel):
    message: str


@app.post("/api/bills/{bill_id}/accept")
def accept_bill(bill_id: int, body: AcceptIn):
    bill = _get_bill(bill_id)
    changed = billing.words_changed(bill["final_message"] or "", body.message)
    bill = supabase.table("bills").update({
        "final_message": billing.mask(body.message),
        "status": "A",
        "words_changed": changed,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", bill_id).execute().data[0]
    return _bill_response(bill)


# Locally, serve the page from this same server so the page and the API share
# one origin, exactly like on Vercel. On Vercel, its CDN serves public/ itself.
# This mount comes last so the /api routes above are matched first.
PUBLIC = Path(__file__).resolve().parents[1] / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
