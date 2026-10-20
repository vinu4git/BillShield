# BillShield

Turn a bill you don't trust into one message worth sending.

Upload a bill (PDF or photo) and what you were promised. BillShield reads every line, checks the maths against the promise in code, flags the lines that don't add up, and drafts one message to send to the company.

## The line

| Code decides (`billshield/billing.py`) | AI decides (`billshield/llm.py`) |
|---|---|
| Totals, differences from the promise | Reading the bill into lines |
| Which lines get flagged | Matching each line to the promise, in plain words |
| Masking before saving, `charge_key` | The message, in their tone |
| Words changed before accepting | |

**No-go:** the model never states an amount code didn't compute. `billing.only_known_numbers` checks every number in a generated message against the code-written facts and the user's own promise text; if one doesn't match, the model gets one retry naming the number, then the fact itself is used instead.

**Reading first:** if the lines we read miss a printed total by more than ₹5, or the taxes come to more than all the charges, no message is drafted. The user checks the amounts against their bill and confirms, or uploads a clearer photo. Gaps under ₹1 are accepted as the bill's rounding.

## Layout

```
api/index.py         FastAPI routes (Vercel entrypoint)
billshield/billing.py  maths, flags, masking, word diff
billshield/llm.py      Groq calls: extract, match, write
billshield/db.py       Supabase client + Storage upload
public/index.html    the page (served by Vercel's CDN, or by FastAPI locally)
schema.sql           tables + storage bucket, run once in Supabase
```

## Data

`users` → `bills` → `line_items` (facts, written once). Each bill keeps the promise it was checked against, so promise history comes from the bills themselves.
`bills` → `messages` (a new version on each Revise) → `message_line_items` → `line_items`: the link that shows which lines a sent message disputes.

## API

Every route except `POST /api/users` needs `Authorization: Bearer <token>`.

| Method | Path | In | Out |
|---|---|---|---|
| POST | `/api/users` | `{"name", "email"}` | the user and their token (creates or finds by email) |
| GET | `/api/me` | | the signed-in user |
| POST | `/api/bills` | multipart `file`, `promise`, optional `provider` | bill + line items + latest message |
| GET | `/api/bills` | | your bills, newest first |
| GET | `/api/bills/{id}` | | bill + line items + latest message |
| POST | `/api/bills/{id}/confirm` | | the user checked the reading: drafts the first message |
| POST | `/api/bills/{id}/revise` | `{"feedback"}` | a new message version |
| POST | `/api/bills/{id}/accept` | `{"message"}` | message marked sent, with `words_changed` |
| GET | `/api/messages` | | sent messages and the lines they dispute |
| GET | `/api/promises?provider=` | | each time a provider's promise changed, newest first |
| GET | `/api/charges/{charge_key}` | | every bill a charge appeared on |

## Setup

1. Create a Supabase project and run `schema.sql` in the SQL editor. If you already ran an earlier version, run the files in `migrations/` in order instead.
2. Copy the env file and fill it in:

```bash
cp .env.example .env
```

3. Install and run locally:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt uvicorn
```

```bash
uvicorn api.index:app --reload
```

Open http://127.0.0.1:8000.

## Deploy to Vercel

```bash
npm i -g vercel && vercel login && vercel link
```

```bash
while IFS== read -r k v; do printf %s "$v" | vercel env add "$k" production --force; done < .env
```

```bash
vercel --prod
```

Vercel finds the FastAPI `app` in `api/index.py` on its own and serves `public/` from its CDN.

## Flags

- Vercel rejects request bodies over 4.5 MB, so uploads are capped at 4 MB.
- Sign-in is name + email, no password: anyone who knows your email can sign in as you. Fine for user testing; move to Supabase Auth (magic link) before real use.
- Images go to `qwen/qwen3.8-27b` (vision); PDFs are read with `pypdf` and go to `openai/gpt-oss-120b`. Scanned PDFs with no text layer need to be uploaded as photos.
- The bill file is stored in the private `bills` bucket. Line descriptions and messages are masked before saving.
