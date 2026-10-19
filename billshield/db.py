import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BUCKET = "bills"

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def upload_bill_file(user_id: int, bill_id: int, filename: str, data: bytes, content_type: str) -> str:
    path = f"{user_id}/{bill_id}/{filename}"
    supabase.storage.from_(BUCKET).upload(path, data, {"content-type": content_type, "upsert": "true"})
    return path
