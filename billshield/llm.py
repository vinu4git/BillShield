"""AI decides: reading the bill, plain words, the message. Never the maths."""
import base64
import io
import json
import os

import requests
from pypdf import PdfReader

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.8-27b"  # Groq retired Llama 4 Scout

EXTRACT_PROMPT = """Read this bill and list every line exactly as printed. Do not add anything up.
Return JSON: {"provider": str | null, "period_start": "YYYY-MM-DD" | null, "period_end": "YYYY-MM-DD" | null,
"due_date": "YYYY-MM-DD" | null, "lines": [{"description": str, "quantity": number, "amount": number,
"kind": "charge" | "tax" | "discount" | "subtotal" | "total"}]}
- provider is the company that issued the bill, e.g. Airtel or BESCOM.
- amount is the number printed on that line, always positive.
- kind "discount" is any credit, rebate or amount taken off.
- kind "subtotal" or "total" is a printed sum of other lines.
- Leave out account numbers, phone numbers, names and addresses."""

MATCH_PROMPT = """You compare bill lines to what the customer was promised. Do not do any arithmetic.
For each line, by its index, say whether the promise covers it and, if the promise states an amount for it,
copy that amount exactly as written in the promise. Also rewrite the line in plain words.
Return JSON: {"lines": [{"index": int, "plain": str, "promised": bool, "promised_amount": number | null}]}"""

MESSAGE_PROMPT = """Write a short, polite message the customer can send to the company disputing the facts below.
Use the customer's own tone from their promise text. Use ONLY the amounts that appear in the facts, written exactly
as given. Never compute a new number. Return JSON: {"message": str}"""


def _chat(model: str, messages: list[dict]) -> dict:
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={"model": model, "messages": messages, "response_format": {"type": "json_object"}, "temperature": 0},
        timeout=60,
    )
    r.raise_for_status()  # a Groq error, such as a 429 rate limit, shows by name in the logs
    return json.loads(r.json()["choices"][0]["message"]["content"])


def read_bill(data: bytes, content_type: str) -> dict:
    """Returns {"provider", "period_start", "period_end", "due_date", "lines": [...]}."""
    if content_type == "application/pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        out = _chat(TEXT_MODEL, [{"role": "user", "content": f"{EXTRACT_PROMPT}\n\nBILL:\n{text}"}])
    else:
        url = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        out = _chat(VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": EXTRACT_PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}])
    return out


def match_promise(lines: list[dict], promise: str) -> list[dict]:
    listing = "\n".join(f"{i}. {l['description']} | {l['amount']}" for i, l in enumerate(lines))
    out = _chat(TEXT_MODEL, [
        {"role": "system", "content": MATCH_PROMPT},
        {"role": "user", "content": f"PROMISE:\n{promise}\n\nBILL LINES:\n{listing}"},
    ])
    return out.get("lines", [])


def write_message(facts: list[str], promise: str, last_draft: str = "", feedback: str = "") -> str:
    user = f"PROMISE:\n{promise}\n\nFACTS:\n" + "\n".join(f"- {f}" for f in facts)
    if feedback:
        user += f"\n\nLAST DRAFT:\n{last_draft}\n\nThe customer asked for this change:\n{feedback}"
    out = _chat(TEXT_MODEL, [{"role": "system", "content": MESSAGE_PROMPT}, {"role": "user", "content": user}])
    return out.get("message", "")
