#!/usr/bin/env python3
"""Privacy-first Purdue budget backend with chat, document redaction, and ledger management."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from openai import APIError, AuthenticationError, BadRequestError, OpenAI
from pydantic import BaseModel, Field

from pii_filter import guard_user_question, redact_document_text

app = FastAPI(title="Purdue Budget Privacy Chat API")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DEFAULT_MODEL = os.environ.get(
    "BUDGET_CHAT_MODEL",
    "ft:gpt-3.5-turbo-0125:personal:purdue-budget-v1:DTfjQQ5x",
)
DATABASE_URL = os.environ.get("DATABASE_URL")
WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"
TEXT_EXTENSIONS = {".txt", ".csv", ".json", ".md", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AMOUNT_RE = re.compile(r"\$?\s?(-?\d+(?:,\d{3})*(?:\.\d{2}))")
DATE_ISO_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
DATE_US_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b")
INCOME_TERMS = {"payroll", "stipend", "salary", "refund", "deposit", "income"}
CATEGORY_TERMS = {
    "food": {"food", "cafe", "restaurant", "dining", "grocery", "coffee"},
    "rent": {"rent", "lease", "landlord"},
    "transport": {"lyft", "uber", "bus", "transport", "ride"},
    "subscriptions": {"spotify", "netflix", "subscription", "prime"},
    "utilities": {"electric", "water", "internet", "utility", "gas"},
    "tuition": {"tuition", "bursar", "university"},
    "books": {"book", "textbook"},
    "health": {"pharmacy", "clinic", "health", "medical"},
    "entertainment": {"movie", "game", "concert", "entertainment", "streaming"},
}

SYSTEM_PROMPT = (
    "You are a privacy-first budgeting assistant for Purdue students. "
    "Use only the provided sanitized context. "
    "Never reveal, infer, or reconstruct any personal identifying information. "
    "If asked for PII, refuse briefly and redirect to safe financial help. "
    "Be concise, actionable, and student-friendly."
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    user_id: str | None = None
    sanitized_context: List[Dict[str, Any]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL


class ChatResponse(BaseModel):
    answer: str
    blocked: bool
    blocked_reason: str | None = None
    guardrail_matches: List[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    filename: str
    raw_text: str
    redacted_text: str
    pii_matches: List[str]
    extracted_records: List[Dict[str, Any]]
    persisted_count: int = 0


class PersonalAddRequest(BaseModel):
    user_id: str
    amount: float
    category: str = "misc"
    description_redacted: str = "Manual personal expense"
    expense_date: str | None = None


class OweAddRequest(BaseModel):
    user_id: str
    amount: float
    who: str
    description_redacted: str = "Money owed"
    expense_date: str | None = None
    due_date: str | None = None
    already_paid: bool = False


class CategoryUpdateRequest(BaseModel):
    user_id: str
    category: str


class MarkPaidRequest(BaseModel):
    user_id: str


def _db_required() -> str:
    if not DATABASE_URL:
        raise HTTPException(status_code=400, detail="DATABASE_URL is not set on server.")
    return DATABASE_URL


def _ensure_db_schema() -> None:
    if not DATABASE_URL:
        return
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sanitized_ledger (
                  record_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  type TEXT NOT NULL,
                  amount NUMERIC(12,2) NOT NULL,
                  currency TEXT NOT NULL DEFAULT 'USD',
                  category TEXT NOT NULL,
                  date DATE NOT NULL,
                  description_redacted TEXT,
                  counterparty_alias TEXT,
                  due_date DATE,
                  status TEXT,
                  paid_amount NUMERIC(12,2) DEFAULT 0,
                  remaining_amount NUMERIC(12,2) DEFAULT 0,
                  recurring BOOLEAN DEFAULT FALSE,
                  recurrence_rule TEXT,
                  source TEXT,
                  created_at TIMESTAMPTZ DEFAULT NOW(),
                  updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sanitized_ledger_user_date
                ON sanitized_ledger(user_id, date DESC);
                """
            )
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS description_redacted TEXT;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS source TEXT;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS counterparty_alias TEXT;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS due_date DATE;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS status TEXT;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(12,2) DEFAULT 0;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS remaining_amount NUMERIC(12,2) DEFAULT 0;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS recurring BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS recurrence_rule TEXT;")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();")
            cur.execute("ALTER TABLE sanitized_ledger ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();")


if DATABASE_URL:
    _ensure_db_schema()


def _infer_category(line: str) -> str:
    lower = line.lower()
    for category, terms in CATEGORY_TERMS.items():
        if any(term in lower for term in terms):
            return category
    return "misc"


def _infer_type(line: str) -> str:
    lower = line.lower()
    if any(term in lower for term in INCOME_TERMS):
        return "income"
    return "expense"


def _extract_date(line: str) -> str | None:
    iso = DATE_ISO_RE.search(line)
    if iso:
        return iso.group(1)
    us = DATE_US_RE.search(line)
    if us:
        mm, dd, yyyy = us.group(1).split("/")
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    return None


def extract_records_from_text(redacted_text: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for idx, raw_line in enumerate(redacted_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        amount_match = AMOUNT_RE.search(line)
        if not amount_match:
            continue
        amount = float(amount_match.group(1).replace(",", ""))
        if amount <= 0 or amount > 1_000_000:
            continue
        records.append(
            {
                "temp_id": str(uuid4()),
                "line_no": idx,
                "type": _infer_type(line),
                "amount": amount,
                "currency": "USD",
                "category": _infer_category(line),
                "date": _extract_date(line),
                "description_redacted": line[:180],
            }
        )
        if len(records) >= 120:
            break
    return records


async def extract_text_from_upload(file: UploadFile) -> str:
    filename = file.filename or "uploaded_document"
    ext = Path(filename).suffix.lower()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if ext in TEXT_EXTENSIONS or not ext:
        return data.decode("utf-8", errors="ignore")

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise HTTPException(status_code=400, detail="PDF parsing requires pypdf. Install dependencies and retry.") from exc
        reader = PdfReader(BytesIO(data))
        text_parts: List[str] = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts).strip()

    if ext in IMAGE_EXTENSIONS:
        try:
            from PIL import Image
            import pytesseract
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Image OCR requires Pillow + pytesseract + system tesseract. Install and retry.",
            ) from exc
        image = Image.open(BytesIO(data))
        text = pytesseract.image_to_string(image)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from image.")
        return text

    raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")


def fetch_sanitized_context(user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    if not DATABASE_URL:
        return []
    query = """
        SELECT record_id, type, amount, category, date, description_redacted, counterparty_alias, due_date, status,
               paid_amount, remaining_amount
        FROM sanitized_ledger
        WHERE user_id = %s
        ORDER BY date DESC
        LIMIT %s
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (user_id, limit))
            rows = cur.fetchall()
    return [
        {
            "record_id": row[0],
            "type": row[1],
            "amount": float(row[2]),
            "category": row[3],
            "date": str(row[4]),
            "description_redacted": row[5] or "",
            "counterparty_alias": row[6],
            "due_date": str(row[7]) if row[7] else None,
            "status": row[8],
            "paid_amount": float(row[9] or 0),
            "remaining_amount": float(row[10] or 0),
        }
        for row in rows
    ]


def _insert_record(
    *,
    user_id: str,
    record_type: str,
    amount: float,
    category: str,
    record_date: str | None,
    description_redacted: str,
    counterparty_alias: str | None = None,
    due_date: str | None = None,
    status: str = "paid",
    source: str = "manual",
) -> str:
    db_url = _db_required()
    record_id = str(uuid4())
    paid_amount = amount if status == "paid" else 0
    remaining_amount = 0 if status == "paid" else amount
    query = """
        INSERT INTO sanitized_ledger
        (record_id, user_id, type, amount, currency, category, date, description_redacted, counterparty_alias,
         due_date, status, paid_amount, remaining_amount, source, updated_at)
        VALUES (%s, %s, %s, %s, 'USD', %s, COALESCE(%s::date, CURRENT_DATE), %s, %s,
                %s::date, %s, %s, %s, %s, NOW())
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    record_id,
                    user_id,
                    record_type,
                    amount,
                    category,
                    record_date,
                    description_redacted,
                    counterparty_alias,
                    due_date,
                    status,
                    paid_amount,
                    remaining_amount,
                    source,
                ),
            )
    return record_id


def persist_extracted_records(user_id: str, records: List[Dict[str, Any]]) -> int:
    if not DATABASE_URL or not records:
        return 0
    count = 0
    for rec in records:
        rec_type = rec.get("type", "expense")
        status = "paid" if rec_type in {"expense", "income"} else "pending"
        _insert_record(
            user_id=user_id,
            record_type=rec_type,
            amount=float(rec.get("amount", 0)),
            category=rec.get("category", "misc"),
            record_date=rec.get("date"),
            description_redacted=rec.get("description_redacted", "Extracted record"),
            status=status,
            source="document_import",
        )
        count += 1
    return count


def _generate_answer(model: str, payload: Dict[str, Any]) -> str:
    user_content = json.dumps(payload, ensure_ascii=True)

    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
        )
        answer = response.output_text.strip() if hasattr(response, "output_text") else ""
        if answer:
            return answer
    except (AuthenticationError, BadRequestError, APIError):
        pass

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    content = completion.choices[0].message.content
    return content.strip() if content else ""


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    guard = guard_user_question(req.question)
    if not guard.allowed:
        return ChatResponse(
            answer=guard.reason or "Request blocked.",
            blocked=True,
            blocked_reason=guard.reason,
            guardrail_matches=guard.matched_rules,
        )

    context = req.sanitized_context
    if not context and req.user_id:
        context = fetch_sanitized_context(req.user_id)

    payload = {
        "question": guard.redacted_question,
        "sanitized_context": context,
    }

    try:
        answer = _generate_answer(req.model, payload)
        if not answer:
            answer = "I couldn't generate a response. Please try again."
        return ChatResponse(answer=answer, blocked=False, guardrail_matches=guard.matched_rules)
    except Exception:
        return ChatResponse(
            answer=(
                "I couldn't process that request due to API permissions/config. "
                "Please check your API key scopes and model access."
            ),
            blocked=False,
            guardrail_matches=guard.matched_rules,
        )


@app.post("/upload-document", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form("USER_001"),
    persist: bool = Form(False),
) -> UploadResponse:
    raw_text = await extract_text_from_upload(file)
    redaction = redact_document_text(raw_text)
    extracted_records = extract_records_from_text(redaction.redacted_text)
    persisted_count = persist_extracted_records(user_id, extracted_records) if persist else 0
    return UploadResponse(
        filename=file.filename or "uploaded_document",
        raw_text=redaction.raw_text,
        redacted_text=redaction.redacted_text,
        pii_matches=redaction.pii_matches,
        extracted_records=extracted_records,
        persisted_count=persisted_count,
    )


@app.post("/records/personal")
def add_personal(req: PersonalAddRequest) -> Dict[str, Any]:
    record_id = _insert_record(
        user_id=req.user_id,
        record_type="expense",
        amount=req.amount,
        category=req.category,
        record_date=req.expense_date,
        description_redacted=req.description_redacted,
        status="paid",
        source="personal_tab",
    )
    return {"record_id": record_id, "ok": True}


@app.post("/records/owe")
def add_owe(req: OweAddRequest) -> Dict[str, Any]:
    status = "paid" if req.already_paid else "pending"
    record_id = _insert_record(
        user_id=req.user_id,
        record_type="debt_owed_by_me",
        amount=req.amount,
        category="owe",
        record_date=req.expense_date,
        description_redacted=req.description_redacted,
        counterparty_alias=req.who,
        due_date=req.due_date,
        status=status,
        source="owe_tab",
    )
    return {"record_id": record_id, "ok": True}


@app.patch("/records/{record_id}/category")
def update_category(record_id: str, req: CategoryUpdateRequest) -> Dict[str, Any]:
    db_url = _db_required()
    query = """
        UPDATE sanitized_ledger
        SET category = %s, updated_at = NOW()
        WHERE record_id = %s AND user_id = %s
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (req.category, record_id, req.user_id))
            updated = cur.rowcount
    return {"ok": updated > 0}


@app.patch("/records/{record_id}/payback")
def mark_already_paid(record_id: str, req: MarkPaidRequest) -> Dict[str, Any]:
    db_url = _db_required()
    query = """
        UPDATE sanitized_ledger
        SET status = 'paid', paid_amount = amount, remaining_amount = 0, updated_at = NOW()
        WHERE record_id = %s AND user_id = %s AND type = 'debt_owed_by_me'
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (record_id, req.user_id))
            updated = cur.rowcount
    return {"ok": updated > 0}


@app.get("/records/personal")
def list_personal(user_id: str = Query(...)) -> Dict[str, Any]:
    rows = [
        row
        for row in fetch_sanitized_context(user_id, limit=1000)
        if row["type"] == "expense" and row.get("category") != "owe"
    ]
    return {"records": rows}


@app.get("/records/owes")
def list_owes(user_id: str = Query(...)) -> Dict[str, Any]:
    rows = [row for row in fetch_sanitized_context(user_id, limit=1000) if row["type"] == "debt_owed_by_me"]
    return {"records": rows}


@app.delete("/records/{record_id}")
def delete_record(record_id: str, user_id: str = Query(...)) -> Dict[str, Any]:
    db_url = _db_required()
    query = """
        DELETE FROM sanitized_ledger
        WHERE record_id = %s AND user_id = %s
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (record_id, user_id))
            deleted = cur.rowcount
    return {"ok": deleted > 0}


@app.delete("/records/reset")
def reset_user_records(user_id: str = Query(...)) -> Dict[str, Any]:
    db_url = _db_required()
    query = "DELETE FROM sanitized_ledger WHERE user_id = %s"
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (user_id,))
            deleted = cur.rowcount
    return {"ok": True, "deleted_count": int(deleted or 0)}


@app.get("/dashboard/summary")
def dashboard_summary(user_id: str = Query(...)) -> Dict[str, Any]:
    rows = fetch_sanitized_context(user_id, limit=2000)
    personal = [r for r in rows if r["type"] == "expense" and r.get("category") != "owe"]
    owes = [r for r in rows if r["type"] == "debt_owed_by_me"]

    today = date.today()
    this_month = [r for r in personal if str(r.get("date", "")).startswith(today.strftime("%Y-%m"))]
    total_personal = sum(r["amount"] for r in personal)
    monthly_personal = sum(r["amount"] for r in this_month)
    owed_outstanding = sum((r.get("remaining_amount") or 0) for r in owes if r.get("status") != "paid")
    owed_paid = sum((r.get("paid_amount") or 0) for r in owes if r.get("status") == "paid")

    category_totals: Dict[str, float] = {}
    for r in personal:
        cat = r.get("category") or "misc"
        category_totals[cat] = category_totals.get(cat, 0) + float(r.get("amount", 0))

    top_category = None
    if category_totals:
        top_category = max(category_totals.items(), key=lambda x: x[1])[0]

    budget_health_score = max(0, 100 - int(owed_outstanding / 20))
    return {
        "totals": {
            "personal_all_time": round(total_personal, 2),
            "personal_this_month": round(monthly_personal, 2),
            "owed_outstanding": round(owed_outstanding, 2),
            "owed_paid_back": round(owed_paid, 2),
            "budget_health_score": budget_health_score,
            "top_category": top_category,
        },
        "category_totals": category_totals,
    }


@app.get("/")
def home() -> FileResponse:
    return FileResponse(INDEX_HTML)
