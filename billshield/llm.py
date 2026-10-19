"""AI decides: reading the bill, plain words, the message. Never the maths."""
import base64
import io
import json
import os

import requests
from pypdf import PdfReader

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

EXTRACT_PROMPT = """Read this bill and list every line exactly as printed. Do not add anything up.
Return JSON: {"lines": [{"description": str, "quantity": int, "amount": number,
"kind": "charge" | "tax" | "discount" | "subtotal" | "total"}]}
- amount is the number printed on that line, always positive.
- kind "discount" is any credit, rebate or amount taken off.
- kind "subtotal" or "total" is a printed sum of other lines.
- Leave out account numbers, phone numbers, names and addresses."""

MATCH_PROMPT = """You compare bill lines to what the customer was promised. Do not do any arithmetic.
For each line, by its index, say whether the promise covers it and, if the promise states an amount for it,
copy that amount exactly as written in the promise. Also rewrite the line in plain words.
Return JSON: {"lines": [{"index": int, "plain": str, "promised": bool, "promised_amount": number | null}]}"""

MESSAGE_PROMPT = """Write a short, polite message the customer can send to the company disputing the lines below.
Use the customer's own tone from their promise text. Use ONLY the amounts that appear in the facts, written exactly
as given. Never compute a new number. Return JSON:
{"line_messages": [{"index": int, "message": str}], "final_message": str}
line_messages: one sentence per fact explaining the problem. final_message: the full message to send."""


def _chat(model: str, messages: list[dict]) -> dict:
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={"model": model, "messages": messages, "response_format": {"type": "json_object"}, "temperature": 0},
        timeout=60,
    )
    r.raise_for_status()  # a Groq error, such as a 429 rate limit, shows by name in the logs
    return json.loads(r.json()["choices"][0]["message"]["content"])


def extract_lines(data: bytes, content_type: str) -> list[dict]:
    if content_type == "application/pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        out = _chat(TEXT_MODEL, [{"role": "user", "content": f"{EXTRACT_PROMPT}\n\nBILL:\n{text}"}])
    else:
        url = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        out = _chat(VISION_MODEL, [{"role": "user", "content": [
            {"type": "text", "text": EXTRACT_PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}])
    return out.get("lines", [])


def match_promise(lines: list[dict], expectation: str) -> list[dict]:
    listing = "\n".join(f"{i}. {l['description']} | {l['amount']}" for i, l in enumerate(lines))
    out = _chat(TEXT_MODEL, [
        {"role": "system", "content": MATCH_PROMPT},
        {"role": "user", "content": f"PROMISE:\n{expectation}\n\nBILL LINES:\n{listing}"},
    ])
    return out.get("lines", [])


def write_messages(facts: list[dict], expectation: str, feedback: str = "") -> dict:
    listing = "\n".join(f"{f['index']}. {f['fact']}" for f in facts)
    user = f"PROMISE:\n{expectation}\n\nFACTS:\n{listing}"
    if feedback:
        user += f"\n\nThe customer asked for this change to the last draft:\n{feedback}"
    return _chat(TEXT_MODEL, [{"role": "system", "content": MESSAGE_PROMPT}, {"role": "user", "content": user}])
