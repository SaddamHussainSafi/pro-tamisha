"""Runs a search for one signed-in employee.

1. Exact numbers and codes are looked up live in the ERP tables (indexed `upper(...)`
   lookups), so an invoice, order, shipment, GRN, airway bill, supplier invoice, batch / lot,
   scan, payment number, SKU or customer / supplier code is found even when the search table is
   behind or unavailable.
2. Everything else comes from `search_documents` in score tiers that never overlap:

       1000 exact number/code   900 exact name     800 number starts with   780 name starts with
        650 number contains     600 all words in name / title (prefix)       500 filters only
        400 words found in linked records (customer on an invoice, ...)
        300 activity log (admins)   200-299 similar spelling (pg_trgm)    100-199 scanned text

   so a fuzzy or OCR match can never outrank or push aside an exact invoice or batch number.

Permissions are applied inside every SQL branch (company + type + warehouse), so results,
group counts and "see all" totals only ever include records the employee may open.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..deps import Principal
from .parser import ParsedQuery, tsquery, words
from .registry import DOC_TYPES, LIVE_ONLY, Visibility, compact, fold, ident_forms

log = logging.getLogger("pro_trading.search")

BRANCH_LIMIT = 300  # candidates per tier
CANDIDATE_CAP = 1000  # results considered per query ("see all" pages through these)
MATCH_LABEL = {
    1000: "number",
    900: "name",
    800: "starts with",
    780: "starts with",
    650: "contains",
    600: "words",
    500: "filter",
    400: "linked record",
    320: "email",
    300: "activity",
    200: "similar spelling",
    100: "scanned text",
}

_DISPLAY = (
    "doc_type, source_id, warehouse_id, title, subtitle, url, status, doc_date, amount, currency,"
    " customer_id, vendor_id"
)


@dataclass
class Hit:
    doc_type: str
    source_id: uuid.UUID
    warehouse_id: uuid.UUID | None
    title: str
    subtitle: str
    url: str
    status: str | None
    doc_date: date | None
    amount: Decimal | None
    currency: str | None
    customer_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    score: float = 0
    snippet: str | None = None
    live: bool = False

    @property
    def tier(self) -> int:
        s = int(self.score)
        for t in sorted(MATCH_LABEL, reverse=True):
            if s >= t:
                return t
        return 100


@dataclass
class SearchOutcome:
    hits: list[Hit]
    counts: dict[str, int]
    capped: bool
    degraded: bool
    terms: list[str]
    queue_pending: int = 0
    behind_seconds: float | None = None
    took_ms: float = 0
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ live exact lookups

_LIVE = {
    "invoice": """
        SELECT 'invoice', i.id, i.warehouse_id, i.invoice_no, c.name || ' · ' || to_char(i.issued_at, 'YYYY-MM-DD'),
               '/invoices/' || i.id, i.state, i.issued_at::date, i.total, i.currency, i.customer_id, NULL::uuid, NULL::uuid, 1000
        FROM invoices i JOIN customers c ON c.id = i.customer_id
        WHERE i.company_id = :cid AND upper(i.invoice_no) = ANY(:forms)""",
    "sales_order": """
        SELECT 'sales_order', o.id, o.warehouse_id, o.order_no, c.name || ' · ' || lower(o.status),
               '/orders/' || o.id, o.status, o.created_at::date, NULL::numeric, NULL, o.customer_id, NULL::uuid, NULL::uuid, 1000
        FROM sales_orders o JOIN customers c ON c.id = o.customer_id
        WHERE o.company_id = :cid AND upper(o.order_no) = ANY(:forms)""",
    "pick": """
        SELECT 'pick', a.id, a.warehouse_id, a.pick_code, 'Packing sheet · ' || lower(a.status),
               '/packing/' || a.id, a.status, a.created_at::date, NULL::numeric, NULL, NULL::uuid, NULL::uuid, NULL::uuid, 1000
        FROM picking_attempts a WHERE a.company_id = :cid AND upper(a.pick_code) = ANY(:forms)""",
    "shipment": """
        SELECT 'shipment', s.id, s.warehouse_id, s.shipment_no, coalesce(v.name, 'Supplier invoice to follow') || ' · ' || lower(s.status),
               '/receiving/' || s.id, s.status, coalesce(s.expected_on, s.created_at::date), NULL::numeric, NULL,
               NULL::uuid, s.vendor_id, NULL::uuid, 1000
        FROM shipments s LEFT JOIN vendors v ON v.id = s.vendor_id
        WHERE s.company_id = :cid AND (
            upper(s.shipment_no) = ANY(:forms) OR upper(s.grn_no) = ANY(:forms)
            OR upper(s.awb_no) = ANY(:forms) OR upper(s.vendor_invoice_no) = ANY(:forms)
            OR s.id IN (SELECT shipment_id FROM shipment_documents WHERE upper(doc_ref) = ANY(:forms))
            OR s.id IN (SELECT shipment_id FROM grn_lines WHERE upper(batch_ref) = ANY(:forms)))""",
    "lot": """
        SELECT 'lot', l.id, l.warehouse_id, l.lot_code, p.sku || ' ' || p.name || ' · ' || lower(l.status),
               '/inventory/lots/' || l.id, l.status, coalesce(l.received_at, l.created_at)::date, NULL::numeric, NULL,
               NULL::uuid, NULL::uuid, l.product_id, 1000
        FROM inventory_lots l JOIN products p ON p.id = l.product_id
        WHERE l.company_id = :cid AND (upper(l.lot_code) = ANY(:forms) OR upper(l.batch_ref) = ANY(:forms))""",
    "stock_count": """
        SELECT 'stock_count', s.id, s.warehouse_id, s.count_no, 'Stock count · ' || lower(s.status),
               '/inventory?tab=counts', s.status, s.created_at::date, NULL::numeric, NULL, NULL::uuid, NULL::uuid, NULL::uuid, 1000
        FROM stock_counts s WHERE s.company_id = :cid AND upper(s.count_no) = ANY(:forms)""",
    "product": """
        SELECT 'product', p.id, NULL::uuid, p.name, p.sku || ' · ' || p.stock_unit,
               '/products/' || p.id, CASE WHEN p.active THEN 'ACTIVE' ELSE 'INACTIVE' END, NULL::date, NULL::numeric, NULL,
               NULL::uuid, NULL::uuid, p.id, CASE WHEN p.sku = ANY(:forms) THEN 1000 ELSE 900 END
        FROM products p WHERE p.company_id = :cid AND (p.sku = ANY(:forms) OR lower(p.name) = :lname)""",
    "customer": """
        SELECT 'customer', c.id, NULL::uuid, c.name, coalesce(c.external_ref, '') || coalesce(' · ' || c.ship_to_name, ''),
               '/customers/' || c.id, CASE WHEN c.active THEN 'ACTIVE' ELSE 'INACTIVE' END, NULL::date, NULL::numeric, NULL,
               c.id, NULL::uuid, NULL::uuid,
               CASE WHEN upper(c.external_ref) = ANY(:forms) OR upper(c.vendor_no) = ANY(:forms) THEN 1000 ELSE 900 END
        FROM customers c WHERE c.company_id = :cid AND (
            upper(c.external_ref) = ANY(:forms) OR upper(c.vendor_no) = ANY(:forms)
            OR lower(c.name) = :lname OR lower(c.ship_to_name) = :lname)""",
    "vendor": """
        SELECT 'vendor', v.id, NULL::uuid, v.name, v.code, '/suppliers/' || v.id,
               CASE WHEN v.active THEN 'ACTIVE' ELSE 'INACTIVE' END, NULL::date, NULL::numeric, NULL,
               NULL::uuid, v.id, NULL::uuid, CASE WHEN upper(v.code) = ANY(:forms) THEN 1000 ELSE 900 END
        FROM vendors v WHERE v.company_id = :cid AND (upper(v.code) = ANY(:forms) OR lower(v.name) = :lname)""",
    "payment": """
        SELECT 'payment', p.id, NULL::uuid, p.payment_no, c.name || ' · ' || lower(p.status),
               '/payments/' || p.id, p.status, p.received_on, p.amount, p.currency, p.customer_id, NULL::uuid, NULL::uuid, 1000
        FROM payments p JOIN customers c ON c.id = p.customer_id
        WHERE p.company_id = :cid AND (upper(p.payment_no) = ANY(:forms) OR upper(p.payer_ref) = ANY(:forms)
            OR upper(p.verification_ref) = ANY(:forms))""",
    "supplier_payment": """
        SELECT 'supplier_payment', p.id, NULL::uuid, p.payment_no, v.name || ' · ' || lower(p.method),
               '/suppliers/' || p.vendor_id || '?tab=payments', CASE WHEN p.voided_at IS NULL THEN 'RECORDED' ELSE 'VOIDED' END,
               p.paid_on, p.amount, p.currency, NULL::uuid, p.vendor_id, NULL::uuid, 1000
        FROM supplier_payments p JOIN vendors v ON v.id = p.vendor_id
        WHERE p.company_id = :cid AND (upper(p.payment_no) = ANY(:forms) OR upper(p.reference) = ANY(:forms))""",
    "scan": """
        SELECT 'scan', j.id, j.warehouse_id, j.job_no, lower(replace(j.job_type, '_', ' ')) || ' · ' || lower(j.status),
               '/scan/' || j.id, j.status, j.created_at::date, NULL::numeric, NULL, NULL::uuid, NULL::uuid, NULL::uuid, 1000
        FROM scan_jobs j WHERE j.company_id = :cid AND upper(j.job_no) = ANY(:forms)""",
}


def live_exact(
    db: Session, p: Principal, vis: Visibility, needle: str, types: set[str]
) -> list[Hit]:
    """Exact identifier / name lookups straight against the ERP tables."""
    forms = ident_forms(needle)
    if not forms or len(needle) > 64:
        return []
    wanted = [t for t in _LIVE if t in types]
    if not wanted:
        return []
    sql = " UNION ALL ".join(f"({_LIVE[t]} LIMIT 20)" for t in wanted)
    rows = db.execute(
        text(sql), {"cid": p.company_id, "forms": forms, "lname": needle.strip().lower()}
    ).all()
    hits = []
    for r in rows:
        h = Hit(*r[:13])
        h.score, h.live = float(r[13]), True
        if vis.can_see(h.doc_type, h.warehouse_id):
            hits.append(h)
    return hits


# ------------------------------------------------------------------ the index query


def _esc_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class _Where:
    sql: str
    params: dict[str, Any]


def _where(
    p: Principal, vis: Visibility, pq: ParsedQuery, types: list[str], ids: dict[str, Any]
) -> _Where:
    parts = [
        "company_id = :cid",
        "doc_type = ANY(:types)",
        "(doc_type = ANY(:full) OR (doc_type || ':' || warehouse_id::text) = ANY(:pairs))",
    ]
    params: dict[str, Any] = {
        "cid": p.company_id,
        "types": types,
        "full": list(vis.full),
        "pairs": list(vis.pairs),
    }
    if "customer_ids" in ids:
        parts.append("customer_id = ANY(:customer_ids)")
        params["customer_ids"] = ids["customer_ids"]
    if "vendor_ids" in ids:
        parts.append("vendor_id = ANY(:vendor_ids)")
        params["vendor_ids"] = ids["vendor_ids"]
    if "product_ids" in ids:
        parts.append("product_ids && CAST(:product_ids AS uuid[])")
        params["product_ids"] = ids["product_ids"]
    if pq.statuses:
        parts.append("status = ANY(:statuses)")
        params["statuses"] = pq.statuses
    if pq.date_from:
        parts.append("doc_date >= :date_from")
        params["date_from"] = pq.date_from
    if pq.date_to:
        parts.append("doc_date <= :date_to")
        params["date_to"] = pq.date_to
    if pq.amount_min is not None:
        parts.append("amount >= :amount_min")
        params["amount_min"] = pq.amount_min
    if pq.amount_max is not None:
        parts.append("amount <= :amount_max")
        params["amount_max"] = pq.amount_max
    return _Where(" AND ".join(parts), params)


def _resolve_filters(db: Session, p: Principal, pq: ParsedQuery) -> dict[str, Any] | None:
    """customer:/store:/supplier:/sku: become id lists (None = a filter matched nothing)."""
    out: dict[str, Any] = {}

    def pat(v: str) -> str:
        return "%" + _esc_like(fold(v)) + "%"

    if pq.customer or pq.store:
        conds: list[str] = []
        params: dict[str, Any] = {"cid": p.company_id}
        if pq.customer:
            conds.append("(upper(external_ref) = :cu OR lower(name) LIKE :cp)")
            params |= {"cu": pq.customer.upper(), "cp": pat(pq.customer)}
        if pq.store:
            conds.append("(lower(coalesce(ship_to_name, '')) LIKE :sp OR lower(name) LIKE :sp)")
            params |= {"sp": pat(pq.store)}
        ids = db.scalars(
            text(
                f"SELECT id FROM customers WHERE company_id = :cid AND {' AND '.join(conds)} LIMIT 500"
            ),
            params,
        ).all()
        if not ids:
            return None
        out["customer_ids"] = list(ids)
    if pq.supplier:
        ids = db.scalars(
            text(
                "SELECT id FROM vendors WHERE company_id = :cid AND (upper(code) = :u OR lower(name) LIKE :p) LIMIT 500"
            ),
            {"cid": p.company_id, "u": pq.supplier.upper(), "p": pat(pq.supplier)},
        ).all()
        if not ids:
            return None
        out["vendor_ids"] = list(ids)
    if pq.sku:
        ids = db.scalars(
            text("SELECT id FROM products WHERE company_id = :cid AND sku LIKE :s LIMIT 500"),
            {"cid": p.company_id, "s": _esc_like(pq.sku) + ("%" if len(pq.sku) < 3 else "")},
        ).all()
        if not ids:
            return None
        out["product_ids"] = list(ids)
    return out


def _index_query(
    db: Session, w: _Where, needle: str, exclude_idents: bool
) -> list[tuple[str, uuid.UUID, float]]:
    """Candidate (type, id, score) from search_documents."""
    branches: list[str] = []
    params = dict(w.params)
    folded = fold(needle)
    lim = BRANCH_LIMIT
    if not folded:
        # Filters only: newest first.
        branches.append(
            f"(SELECT doc_type, source_id, 500.0 AS score FROM search_documents WHERE {w.sql}"
            f" ORDER BY doc_date DESC NULLS LAST LIMIT {CANDIDATE_CAP})"
        )
    else:
        forms = ident_forms(needle)
        up = needle.strip().upper()
        params |= {"forms": forms, "qn": folded}
        if forms and not exclude_idents:
            branches.append(
                f"(SELECT doc_type, source_id, 1000.0 FROM search_documents WHERE {w.sql}"
                f" AND identifiers && CAST(:forms AS text[]) LIMIT {lim})"
            )
        branches.append(
            f"(SELECT doc_type, source_id, 900.0 FROM search_documents WHERE {w.sql}"
            f" AND title_norm = :qn LIMIT {lim})"
        )
        if len(compact(up)) >= 3:
            params["ip"] = "% " + _esc_like(up) + "%"
            params["ic"] = "%" + _esc_like(up) + "%"
            branches.append(
                f"(SELECT doc_type, source_id, 800.0 FROM search_documents WHERE {w.sql}"
                f" AND ident_text LIKE :ip LIMIT {lim})"
            )
            branches.append(
                f"(SELECT doc_type, source_id, 650.0 FROM search_documents WHERE {w.sql}"
                f" AND ident_text LIKE :ic LIMIT {lim})"
            )
        if len(folded) >= 2:
            params["tp"] = _esc_like(folded) + "%"
            branches.append(
                f"(SELECT doc_type, source_id, 780.0 + 10.0 * similarity(title_norm, :qn) FROM search_documents"
                f" WHERE {w.sql} AND title_norm LIKE :tp ORDER BY length(title_norm) LIMIT {lim})"
            )
        tq_main = tsquery(folded, "AB")
        tq_any = tsquery(folded)
        if tq_main:
            params |= {"tqm": tq_main, "tqa": tq_any}
            branches.append(
                f"(SELECT doc_type, source_id, 600.0 + 99.0 * least(ts_rank(tsv, to_tsquery('simple', :tqm)), 1)"
                f" FROM search_documents WHERE {w.sql} AND tsv @@ to_tsquery('simple', :tqm)"
                f" ORDER BY 3 DESC LIMIT {lim})"
            )
            branches.append(
                f"(SELECT doc_type, source_id, 400.0 + 99.0 * least(ts_rank(tsv, to_tsquery('simple', :tqa)), 1)"
                f" FROM search_documents WHERE {w.sql} AND tsv @@ to_tsquery('simple', :tqa)"
                f" ORDER BY 3 DESC LIMIT {lim})"
            )
            branches.append(
                f"(SELECT doc_type, source_id, 100.0 + 99.0 * least(ts_rank(ocr_tsv, to_tsquery('simple', :tqa)), 1)"
                f" FROM search_documents WHERE {w.sql} AND ocr_tsv @@ to_tsquery('simple', :tqa)"
                f" ORDER BY 3 DESC LIMIT {lim})"
            )
        if len(folded) >= 3 and not any(ch.isdigit() for ch in folded):
            # Typo tolerance for names only: numbers are never "close enough" (an invoice or
            # batch number with a typo must not bring up its neighbours).
            # Typo tolerance: trigram similarity of the whole title (pg_trgm, threshold 0.3).
            branches.append(
                f"(SELECT doc_type, source_id, 200.0 + 99.0 * similarity(title_norm, :qn) FROM search_documents"
                f" WHERE {w.sql} AND title_norm % :qn ORDER BY 3 DESC LIMIT {lim})"
            )
    sql = (
        "SELECT doc_type, source_id, max(score) AS score FROM ("
        + " UNION ALL ".join(branches)
        + f") AS c(doc_type, source_id, score) GROUP BY doc_type, source_id ORDER BY score DESC LIMIT {CANDIDATE_CAP + 1}"
    )
    return [(t, i, float(s)) for t, i, s in db.execute(text(sql), params).all()]


def _load(db: Session, keys: list[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], Hit]:
    if not keys:
        return {}
    rows = db.execute(
        text(
            f"SELECT {_DISPLAY} FROM search_documents d"
            " JOIN unnest(CAST(:types AS text[]), CAST(:ids AS uuid[])) AS k(t, i)"
            " ON d.doc_type = k.t AND d.source_id = k.i"
        ),
        {"types": [k[0] for k in keys], "ids": [k[1] for k in keys]},
    ).all()
    return {(r[0], r[1]): Hit(*r) for r in rows}


def queue_lag(db: Session) -> tuple[int, float | None]:
    r = db.execute(
        text(
            "SELECT count(*), extract(epoch FROM now() - min(created_at)) FROM search_index_queue"
            " WHERE status = 'PENDING'"
        )
    ).one()
    return int(r[0] or 0), (float(r[1]) if r[1] is not None else None)


def _activity(db: Session, p: Principal, needle: str) -> list[Hit]:
    """Activity log (admins: company-wide audit.read). Exact target id or action prefix."""
    if not needle or len(needle) > 64:
        return []
    rows = db.execute(
        text(
            "SELECT a.id, a.action, coalesce(e.display_name, 'system'), a.created_at, a.target_type"
            " FROM audit_events a LEFT JOIN employees e ON e.id = a.actor_id"
            " WHERE a.company_id = :cid AND (a.target_id = :raw OR a.action LIKE :ap)"
            " ORDER BY a.created_at DESC LIMIT 5"
        ),
        {"cid": p.company_id, "raw": needle.strip(), "ap": _esc_like(needle.strip().lower()) + "%"},
    ).all()
    return [
        Hit(
            "activity",
            r[0],
            None,
            r[1],
            f"{r[2]} · {r[3]:%Y-%m-%d %H:%M}",
            "/activity",
            None,
            r[3].date(),
            None,
            None,
            score=300.0,
        )
        for r in rows
    ]


def _email(db: Session, p: Principal, needle: str) -> list[Hit]:
    """Email (Lark Mail): only mailboxes this person was given; newest matches first."""
    if not needle.strip() or len(needle) > 200:
        return []
    from ..larkmail.access import accessible_ids
    from ..routes.mail import search_query

    ids = accessible_ids(db, p)
    if not ids:
        return []
    from ..larkmail.models import MailMessage

    try:
        with db.begin_nested():
            db.execute(text("SET LOCAL statement_timeout = '2s'"))
            rows = list(db.scalars(
                search_query(p, ids, needle).order_by(MailMessage.received_at.desc()).limit(8)
            ))  # fmt: skip
    except SQLAlchemyError:
        return []
    return [
        Hit(
            "email", m.id, None, m.subject or "(no subject)",
            f"{m.from_name or m.from_email} · {m.received_at:%Y-%m-%d}",
            f"/email?account={m.account_id}&message={m.id}", None, m.received_at.date(), None,
            None, score=320.0, snippet=m.snippet or None,
        )  # fmt: skip
        for m in rows
    ]


def search(
    db: Session,
    p: Principal,
    vis: Visibility,
    pq: ParsedQuery,
    *,
    use_index: bool = True,
) -> SearchOutcome:
    t0 = time.perf_counter()
    types = set(vis.types) if pq.types is None else set(vis.types) & pq.types
    notes: list[str] = []
    if pq.amount_min is not None or pq.amount_max is not None:
        # Amount filters only apply where the employee may see amounts (no inference leaks).
        types &= vis.money
        notes.append("amount filter: only records with amounts you may see")
    needle = pq.needle
    terms = [w for w in words(fold(needle))] + ident_forms(*pq.idents)
    empty = SearchOutcome([], {}, False, False, terms, notes=notes)
    if not types:
        return empty
    ids = _resolve_filters(db, p, pq)
    if ids is None:
        return empty

    hits: dict[tuple[str, uuid.UUID], Hit] = {}
    if needle:
        for h in live_exact(db, p, vis, needle, types):
            k = (h.doc_type, h.source_id)
            if k not in hits or hits[k].score < h.score:
                hits[k] = h
        if pq.has_filters:
            # A live hit must also pass the filters: keep it only if the index agrees below,
            # or if it carries the filtered value itself.
            for k, h in list(hits.items()):
                if not _live_passes(h, pq, ids):
                    del hits[k]
        if "activity" in types:
            for h in _activity(db, p, needle):
                hits[("activity", h.source_id)] = h
        if "email" in types:
            for h in _email(db, p, needle):
                hits[("email", h.source_id)] = h

    degraded = False
    capped = False
    if use_index and types - LIVE_ONLY:
        w = _where(p, vis, pq, sorted(types - LIVE_ONLY), ids)
        try:
            with db.begin_nested():
                db.execute(text("SET LOCAL statement_timeout = '3s'"))
                cands = _index_query(db, w, needle, exclude_idents=False)
                capped = len(cands) > CANDIDATE_CAP
                cands = cands[:CANDIDATE_CAP]
                loaded = _load(db, [(t, i) for t, i, _ in cands])
        except SQLAlchemyError as exc:
            log.warning(
                "search index unavailable",
                extra={"ctx": {"error": type(exc).__name__}},
                exc_info=exc,
            )
            degraded = True
            cands, loaded = [], {}
        for t, i, s in cands:
            got = loaded.get((t, i))
            if got is None:
                continue
            got.score = s
            k = (t, i)
            if k in hits:
                # Keep the best score; show the index row (richer subtitle).
                got.score = max(s, hits[k].score)
            hits[k] = got
    elif use_index is False:
        degraded = True

    ranked = sorted(
        hits.values(),
        key=lambda h: (
            -h.tier,
            DOC_TYPES[h.doc_type].rank,
            -h.score,
            -(h.doc_date.toordinal() if h.doc_date else 0),
            h.title,
        ),
    )
    counts: dict[str, int] = {}
    for h in ranked:
        counts[h.doc_type] = counts.get(h.doc_type, 0) + 1
    try:
        pending, behind = queue_lag(db)
    except SQLAlchemyError:
        pending, behind = 0, None
    return SearchOutcome(
        ranked, counts, capped, degraded, terms, pending, behind,
        round((time.perf_counter() - t0) * 1000, 1), notes,
    )  # fmt: skip


def _live_passes(h: Hit, pq: ParsedQuery, ids: dict[str, Any]) -> bool:
    if "customer_ids" in ids and h.customer_id not in ids["customer_ids"]:
        return False
    if "vendor_ids" in ids and h.vendor_id not in ids["vendor_ids"]:
        return False
    if "product_ids" in ids and h.product_id not in ids["product_ids"]:
        return False
    if pq.statuses and (h.status or "") not in pq.statuses:
        return False
    if pq.date_from and (h.doc_date is None or h.doc_date < pq.date_from):
        return False
    if pq.date_to and (h.doc_date is None or h.doc_date > pq.date_to):
        return False
    if pq.amount_min is not None and (h.amount is None or h.amount < pq.amount_min):
        return False
    if pq.amount_max is not None and (h.amount is None or h.amount > pq.amount_max):
        return False
    return True


def snippet(db: Session, h: Hit, terms: list[str]) -> str | None:
    """Short text around the first matching word, for linked-record and scanned-text hits."""
    if h.doc_type == "email":
        return h.snippet
    if h.doc_type == "activity" or h.tier not in (400, 100) or not terms:
        return None
    col = "ocr_text" if h.tier == 100 else "body"
    txt = str(
        db.scalar(
            text(f"SELECT {col} FROM search_documents WHERE doc_type = :t AND source_id = :i"),
            {"t": h.doc_type, "i": h.source_id},
        )
        or ""
    )
    if not txt:
        return None
    low = txt.lower()
    pos = min((low.find(t.lower()) for t in terms if t and low.find(t.lower()) >= 0), default=-1)
    if pos < 0:
        return None
    start = max(0, pos - 40)
    return (
        ("…" if start else "")
        + txt[start : pos + 80].strip()
        + ("…" if pos + 80 < len(txt) else "")
    )
