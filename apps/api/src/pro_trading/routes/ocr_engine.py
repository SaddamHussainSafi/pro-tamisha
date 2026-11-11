"""Admin OCR test page: how the OCR engine is set up, what it has used, and one reading in full
(each page's validated answer beside the local Tesseract reading). Read-only; company.manage.
The API key is never returned: only whether one is set."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .. import gemini_client
from ..config import get_settings
from ..deps import DbDep, PrincipalDep, authorize
from ..models import OcrCorrection, OcrEngineCall, OcrExtraction, OcrField
from ..schemas import OcrEngineReadingOut, OcrEngineStatusOut
from .ocr import _readable, get_extraction

router = APIRouter(tags=["ocr"])
PERM = "company.manage"


def _accuracy(db: Any, company_id: uuid.UUID, warehouses: Any) -> list[dict[str, Any]]:
    """Per engine, over accepted readings: a line a reviewer confirmed as read is correct; a
    corrected, removed or manually added line is not. The reviewers' decisions are the verified
    references; nothing is trained on them."""
    rows = db.execute(
        select(OcrExtraction.provider, OcrExtraction.engine, OcrField.detail, OcrField.kind,
               OcrField.resolution)
        .join(OcrExtraction, OcrExtraction.id == OcrField.extraction_id)
        .where(
            OcrExtraction.company_id == company_id,
            OcrExtraction.status == "ACCEPTED",
            OcrExtraction.warehouse_id.in_(warehouses),
        )
    ).all()  # fmt: skip
    tally: dict[str, list[int]] = {}
    for provider, engine, detail, kind, resolution in rows:
        used = (detail or {}).get("engine") or (engine or {}).get("provider") or provider
        t = tally.setdefault(str(used), [0, 0])
        t[0] += 1
        t[1] += int(kind != "MANUAL" and resolution == "CONFIRMED")
    return [
        {"engine": k, "samples": n, "correct": c,
         "accuracy": str((Decimal(c) / Decimal(n)).quantize(Decimal("0.0001"))) if n else None}
        for k, (n, c) in sorted(tally.items())
    ]  # fmt: skip


@router.get("/ocr/engine", response_model=OcrEngineStatusOut)
def engine_status(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, PERM)
    st = get_settings()
    b = gemini_client.budget()
    calls = db.scalars(
        select(OcrEngineCall)
        .where(OcrEngineCall.company_id == p.company_id)
        .order_by(OcrEngineCall.id.desc())
        .limit(30)
    ).all()
    return {
        "provider": st.ocr_provider,
        "fallback_provider": st.ocr_fallback_provider,
        "model": st.gemini_model,
        "key_set": gemini_client.configured(),
        "data_consent": st.gemini_data_consent,
        "target_seconds": st.ocr_target_seconds,
        "requests_per_minute": b.per_minute,
        "daily_request_cap": b.cap,
        "used_today": b.used_today,
        "remaining_today": b.remaining_today,
        "used_this_minute": b.used_minute,
        "breaker_open_seconds": b.breaker_open_seconds,
        "quota_hit_today": b.quota_hit_today,
        "recent_calls": [
            {
                "at": c.created_at,
                "model": c.model,
                "stage": c.stage,
                "page": c.page,
                "outcome": c.outcome,
                "http_status": c.http_status,
                "total_tokens": c.total_tokens,
                "seconds": str(c.seconds) if c.seconds is not None else None,
            }
            for c in calls
        ],  # fmt: skip
        "accuracy": _accuracy(db, p.company_id, _readable(db, p)),
    }


@router.get("/ocr/extractions/{extraction_id}/engine", response_model=OcrEngineReadingOut)
def engine_reading(
    extraction_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    authorize(request, p, PERM)
    ex = get_extraction(db, request, p, extraction_id)
    fields = db.scalars(select(OcrField).where(OcrField.extraction_id == ex.id)).all()
    flags = [f for fl in fields for f in (fl.detail or {}).get("flags", [])]
    corrections = db.scalar(
        select(func.count())
        .select_from(OcrCorrection)
        .join(OcrField, OcrField.id == OcrCorrection.field_id)
        .where(OcrField.extraction_id == ex.id, OcrCorrection.action != "CONFIRM")
    )
    engine = dict(ex.engine or {})
    errors = [w for w in ex.warnings or [] if str(w).startswith(("GEMINI_FALLBACK", "RECHECK_"))]
    errors += [
        f"p{pg.get('page')}: {pg['error']}" for pg in engine.get("pages", []) if pg.get("error")
    ]
    return {
        "extraction_id": ex.id,
        "status": ex.status,
        "provider": ex.provider,
        "page_count": ex.page_count,
        "warnings": list(ex.warnings or []),
        "engine": {k: v for k, v in engine.items() if k != "tesseract"},
        "lines": len(fields),
        "uncertain": sum(1 for f in fields if f.status != "OK"),
        "no_marks": sum(1 for f in fields if (f.detail or {}).get("no_mark")),
        "crossed_out": sum(1 for f in fields if (f.detail or {}).get("crossed_out")),
        "corrections": int(corrections or 0),
        "validation_errors": errors + sorted({f for f in flags if f.startswith("RECHECK_DIFFERS")}),
        "tesseract_lines": list(engine.get("tesseract", [])),
    }
