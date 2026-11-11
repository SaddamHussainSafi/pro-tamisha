"""Read scans with the configured OCR engine and with Tesseract, side by side, for evaluation.

    PYTHONPATH=src uv run python scripts/ocr_eval.py OUT_DIR FILE.pdf [FILE.pdf ...] [--type TYPE]

Reads each file exactly as the worker would (same code path, same limits, same daily cap, same
consent switch) but saves nothing in the application: no scan, no reading, no order. One JSON
per file goes to OUT_DIR, which must be OUTSIDE the repository (results can hold customer names;
real scans and their readings are never committed). Requests to Google are metered like any
other, so a run stops using Gemini when the day's allowance is spent and says so.

These files are evaluation examples to compare against the page by eye; they are not labels and
nothing is trained on them.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from pro_trading import gemini_ocr, ocr
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import Company

MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _line(p: ocr.Proposal) -> dict[str, Any]:
    d = p.detail or {}
    return {
        "page": (p.bbox or {}).get("page"),
        "label": p.label,
        "qty": str(p.qty) if p.qty is not None else None,
        "unit": p.unit_code,
        "status": p.status,
        "raw": p.raw_text,
        "engine": d.get("engine"),
        "flags": d.get("flags", []),
        **{k: d[k] for k in ("crossed_out", "no_mark", "checkmark", "pkg", "notes") if d.get(k)},
    }


def main(argv: list[str]) -> int:
    job_type = "SALES_ORDER"
    if "--type" in argv:
        i = argv.index("--type")
        job_type = argv[i + 1]
        del argv[i : i + 2]
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[0]).resolve()
    repo = Path(__file__).resolve().parents[3]
    if repo in out.parents or out == repo:
        print("OUT_DIR must be outside the repository")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    st = get_settings()
    with get_sessionmaker()() as db:
        company = db.scalars(select(Company).order_by(Company.created_at)).first()
        assert company is not None, "no company in this database"
        for name in argv[1:]:
            f = Path(name)
            doc = SimpleNamespace(content=f.read_bytes(), mime_type=MIME[f.suffix.lower()])
            ex = SimpleNamespace(company_id=company.id, id=uuid.uuid4())
            t0 = time.monotonic()
            m, pages, engine = gemini_ocr.read_document(db, ex, job_type, doc)  # type: ignore[arg-type]
            res = {
                "file": f.name,
                "job_type": job_type,
                "pages": pages,
                "seconds": round(time.monotonic() - t0, 1),
                "configured": st.ocr_provider,
                "engine": {k: v for k, v in engine.items() if k != "tesseract"},
                "warnings": m.warnings,
                "customer_matched": m.customer_id is not None,
                "lines": [_line(p) for p in m.proposals],
                "tesseract_lines": engine.get("tesseract", [_line(p) for p in m.proposals]),
            }
            (out / f"{f.stem}.json").write_text(json.dumps(res, indent=1, default=str))
            eng = engine
            print(
                f"{f.name}: {pages}p {res['seconds']}s read by {eng['provider']}"
                f" ({eng.get('requests', 0)} requests, {eng.get('total_tokens', 0)} tokens)"
                f" lines={len(m.proposals)} to_check={sum(p.status != 'OK' for p in m.proposals)}"
                f" warnings={[w.split(':')[0] for w in m.warnings]}"
            )
            db.rollback()  # nothing of the application's is written
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
