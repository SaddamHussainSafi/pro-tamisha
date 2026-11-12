"""Universal search benchmark on SYNTHETIC data at realistic volume. Test databases only.

    createdb -h 127.0.0.1 -U tami tami_bench_search
    DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_bench_search \\
      PYTHONPATH=src uv run python -m scripts.search_benchmark [--scale 1.0] [--runs 40]

Builds (scale 1.0): 10,000 products, 5,000 customers, 1,000 suppliers, 50,000 orders / packing
sheets / invoices with 100,000 invoice lines, 20,000 batches, 2,000 shipments, 50,000 scans with
OCR text. Indexes everything, then times each kind of query as the owner and as a warehouse-
scoped employee (in-process: the engine's SQL, not HTTP). Prints JSON with p50 / p95 / max ms,
plus the cost the sync triggers add to a bulk update. Refuses any database whose name does not
contain "test" or "bench".
"""

import argparse
import json
import random
import statistics
import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.deps import Principal, load_grants
from pro_trading.models import Company, Employee, EmployeeRole, Warehouse
from pro_trading.search import engine, indexer
from pro_trading.search.parser import parse
from pro_trading.search.registry import visibility

WORDS = (
    "cassava plantain yam mango okra pepper scotch bonnet jasmine rice ginger garlic callaloo "
    "breadfruit ackee saltfish coconut tamarind sorrel pumpkin dasheen eddoe taro guava papaya "
    "lime lemon thyme pimento allspice cinnamon nutmeg cocoa coffee sugar banana sweet potato "
    "green ripe frozen fresh dried smoked spicy mild golden red yellow white black island harbour "
    "kingston accra lagos lima tropical market grocers kitchen catering bakery store supply farm"
).split()


def _guard() -> None:
    url = get_settings().database_url
    name = url.rsplit("/", 1)[-1]
    if "test" not in name and "bench" not in name:
        raise SystemExit(
            f"refusing to write synthetic data into {name!r} (needs 'test' or 'bench')"
        )


def build(db: Session, scale: float) -> dict[str, Any]:
    n = lambda base: max(10, int(base * scale))  # noqa: E731
    sync_permissions(db)
    co = Company(legal_name=f"Bench Synthetic Co {uuid.uuid4().hex[:6]}", timezone="UTC")
    db.add(co)
    db.flush()
    roles = ensure_default_roles(db, co.id)
    wa = Warehouse(company_id=co.id, code="BA", name="Bench A")
    wb = Warehouse(company_id=co.id, code="BB", name="Bench B")
    db.add_all([wa, wb])
    db.flush()
    owner = Employee(
        company_id=co.id,
        email=f"bench.owner.{co.id.hex[:6]}@example.test",
        display_name="Bench owner",
    )
    whu = Employee(
        company_id=co.id,
        email=f"bench.wh.{co.id.hex[:6]}@example.test",
        display_name="Bench warehouse",
    )
    db.add_all([owner, whu])
    db.flush()
    db.add(EmployeeRole(employee_id=owner.id, role_id=roles["OWNER_ADMIN"].id, warehouse_id=None))
    db.add(EmployeeRole(employee_id=whu.id, role_id=roles["WAREHOUSE"].id, warehouse_id=wa.id))
    db.commit()
    p = {"c": co.id, "wa": wa.id, "wb": wb.id, "me": owner.id, "w": WORDS, "nw": len(WORDS)}
    counts = {
        "products": n(10_000), "customers": n(5_000), "vendors": n(1_000), "invoices": n(50_000),
        "lots": n(20_000), "shipments": n(2_000), "scans": n(50_000),
    }  # fmt: skip
    steps = [
        (
            "products",
            f"""
        INSERT INTO products (company_id, sku, name, stock_unit)
        SELECT :c, chr(65 + (i / 676) % 26) || chr(65 + (i / 26) % 26) || chr(65 + i % 26),
               initcap((CAST(:w AS text[]))[1 + (i * 7) % :nw] || ' ' || (CAST(:w AS text[]))[1 + (i * 13) % :nw] || ' ' || (CAST(:w AS text[]))[1 + (i * 31) % :nw]),
               'BOX'
        FROM generate_series(0, {counts["products"] - 1}) i""",
        ),
        (
            "customers",
            f"""
        INSERT INTO customers (company_id, external_ref, name, ship_to_name, phone)
        SELECT :c, 'C-' || (100000 + i), initcap((CAST(:w AS text[]))[1 + (i * 11) % :nw] || ' ' || (CAST(:w AS text[]))[1 + (i * 17) % :nw] || ' ' || (CAST(:w AS text[]))[1 + (i * 3) % :nw]) || ' ' || i,
               initcap((CAST(:w AS text[]))[1 + (i * 5) % :nw]) || ' St ' || i, '555-' || lpad(i::text, 6, '0')
        FROM generate_series(1, {counts["customers"]}) i""",
        ),
        (
            "vendors",
            f"""
        INSERT INTO vendors (company_id, code, name, currency)
        SELECT :c, 'V-' || i, initcap((CAST(:w AS text[]))[1 + (i * 19) % :nw] || ' ' || (CAST(:w AS text[]))[1 + (i * 23) % :nw]) || ' Supply ' || i, 'USD'
        FROM generate_series(1, {counts["vendors"]}) i""",
        ),
        (
            "orders",
            f"""
        WITH c AS (SELECT array_agg(id ORDER BY external_ref) a FROM customers WHERE company_id = :c)
        INSERT INTO sales_orders (company_id, order_no, customer_id, warehouse_id, status, source_snapshot, source_text, created_at)
        SELECT :c, 'SO-B-' || lpad(i::text, 7, '0'), c.a[1 + i % array_length(c.a, 1)], CASE WHEN i % 2 = 0 THEN :wa ELSE :wb END,
               'FULFILLED', '{{}}'::jsonb, CASE WHEN i % 10 = 0 THEN (CAST(:w AS text[]))[1 + i % :nw] || ' 4 box' ELSE '' END,
               now() - make_interval(days => i % 700)
        FROM generate_series(1, {counts["invoices"]}) i, c""",
        ),
        (
            "order lines",
            """
        WITH p AS (SELECT array_agg(id ORDER BY sku) a FROM products WHERE company_id = :c)
        INSERT INTO sales_order_lines (order_id, line_no, product_id, unit_code, ordered_qty)
        SELECT o.id, l, p.a[1 + (abs(hashtext(o.order_no)) + l * 101) % array_length(p.a, 1)], 'BOX', 2
        FROM sales_orders o, generate_series(1, 2) l, p WHERE o.company_id = :c""",
        ),
        (
            "picks",
            """
        INSERT INTO picking_attempts (company_id, order_id, warehouse_id, attempt_no, pick_code, status)
        SELECT :c, id, warehouse_id, 1, order_no || '-P1', 'INVOICED' FROM sales_orders WHERE company_id = :c""",
        ),
        (
            "pick lines",
            """
        INSERT INTO picking_lines (attempt_id, order_line_id, requested_qty, picked_qty, outcome)
        SELECT a.id, l.id, 2, 2, 'PICKED' FROM picking_attempts a JOIN sales_order_lines l ON l.order_id = a.order_id
        WHERE a.company_id = :c""",
        ),
        (
            "invoices",
            """
        INSERT INTO invoices (company_id, invoice_no, fiscal_year, seq_no, sales_order_id, picking_attempt_id, customer_id,
                              warehouse_id, state, subtotal, tax_amount, total, issued_at, due_at, snapshot, pdf_sha256,
                              template_version, email_status)
        SELECT :c, 'TB' || to_char(o.created_at, 'YYYY') || '-' || lpad(row_number() OVER (ORDER BY o.order_no)::text, 6, '0'),
               2026, row_number() OVER (ORDER BY o.order_no), o.id, a.id, o.customer_id, o.warehouse_id,
               CASE WHEN random() < 0.6 THEN 'PAID' ELSE 'ISSUED' END,
               ((abs(hashtext(o.order_no)) % 500000) / 100.0)::numeric(18,2), 0,
               ((abs(hashtext(o.order_no)) % 500000) / 100.0)::numeric(18,2), o.created_at, o.created_at + interval '30 days',
               '{}'::jsonb, repeat('0', 64), 'bench', 'NO_EMAIL'
        FROM sales_orders o JOIN picking_attempts a ON a.order_id = o.id WHERE o.company_id = :c""",
        ),
        (
            "invoice lines",
            """
        INSERT INTO invoice_lines (invoice_id, line_no, order_line_id, pick_line_id, product_id, sku, description, unit_code,
                                   billed_qty, unit_rate, line_amount, tax_code, tax_rate, tax_amount, pricing_snapshot)
        SELECT i.id, l.line_no, l.id, pl.id, l.product_id, p.sku, p.name, 'BOX', 2, 10, 20, 'EXEMPT', 0, 0, '{}'::jsonb
        FROM invoices i JOIN sales_order_lines l ON l.order_id = i.sales_order_id
        JOIN picking_lines pl ON pl.order_line_id = l.id JOIN products p ON p.id = l.product_id
        WHERE i.company_id = :c""",
        ),
        (
            "shipments",
            f"""
        WITH v AS (SELECT array_agg(id ORDER BY code) a FROM vendors WHERE company_id = :c)
        INSERT INTO shipments (company_id, warehouse_id, vendor_id, shipment_no, vendor_invoice_no, awb_no, currency, created_by)
        SELECT :c, CASE WHEN i % 2 = 0 THEN :wa ELSE :wb END, v.a[1 + i % array_length(v.a, 1)], 'SHP-B-' || lpad(i::text, 6, '0'),
               'INV-' || (700000 + i), (100 + i % 900) || '-' || lpad((i * 7919 % 99999999)::text, 8, '0'), 'USD', :me
        FROM generate_series(1, {counts["shipments"]}) i, v""",
        ),
        (
            "lots",
            f"""
        WITH p AS (SELECT array_agg(id ORDER BY sku) a FROM products WHERE company_id = :c)
        INSERT INTO inventory_lots (company_id, product_id, warehouse_id, lot_code, batch_ref, received_at)
        SELECT :c, p.a[1 + i % array_length(p.a, 1)], CASE WHEN i % 2 = 0 THEN :wa ELSE :wb END,
               'LOT-B-' || lpad(i::text, 6, '0'), 'BAT-' || lpad(i::text, 5, '0'), now() - make_interval(days => i % 400)
        FROM generate_series(1, {counts["lots"]}) i, p""",
        ),
        (
            "grn lines",
            """
        INSERT INTO grn_lines (shipment_id, seq, product_id, unit_code, accepted_qty, damaged_qty, rejected_qty, batch_ref, lot_id, counted_by)
        SELECT s.id, 1, l.product_id, 'BOX', 10, 0, 0, l.batch_ref, l.id, :me
        FROM inventory_lots l JOIN shipments s ON s.shipment_no = 'SHP-B-' || lpad((1 + (substr(l.lot_code, 7)::int % (SELECT count(*) FROM shipments WHERE company_id = :c)))::text, 6, '0')
         AND s.company_id = :c
        WHERE l.company_id = :c""",
        ),
        (
            "source documents",
            f"""
        INSERT INTO source_documents (company_id, sha256, mime_type, byte_size, original_filename, content, uploaded_via)
        SELECT :c, md5('a' || i || CAST(:c AS text)) || md5('b' || i || CAST(:c AS text)), 'application/pdf', 1, 'order-sheet-' || i || '.pdf', '\\x00', 'MANUAL'
        FROM generate_series(1, {counts["scans"]}) i""",
        ),
        (
            "scans",
            """
        INSERT INTO scan_jobs (company_id, warehouse_id, job_no, job_type, channel, status, requested_by, document_id, completed_at)
        SELECT :c, CASE WHEN row_number() OVER () % 2 = 0 THEN :wa ELSE :wb END, 'SCN-B-' || lpad((row_number() OVER ())::text, 6, '0'),
               'SALES_ORDER', 'MANUAL', 'UPLOADED', :me, d.id, now()
        FROM source_documents d WHERE d.company_id = :c""",
        ),
        (
            "ocr readings",
            """
        INSERT INTO ocr_extractions (company_id, warehouse_id, scan_job_id, document_id, run_no, status, provider, text_blocks)
        SELECT :c, j.warehouse_id, j.id, j.document_id, 1, 'ACCEPTED', 'fake',
               jsonb_build_array(
                 jsonb_build_object('page', 1, 'text', 'ORDER SHEET ' || j.job_no),
                 jsonb_build_object('page', 1, 'text', (CAST(:w AS text[]))[1 + abs(hashtext(j.job_no)) % :nw] || ' ' || (CAST(:w AS text[]))[1 + abs(hashtext(j.job_no || 'x')) % :nw] || ' 12 box'),
                 jsonb_build_object('page', 1, 'text', 'deliver to ' || (CAST(:w AS text[]))[1 + abs(hashtext(j.job_no || 'y')) % :nw] || ' warehouse door ' || (abs(hashtext(j.job_no)) % 90)))
        FROM scan_jobs j WHERE j.company_id = :c""",
        ),
    ]
    times: dict[str, float] = {}
    for name, sql in steps:
        t0 = time.perf_counter()
        db.execute(text(sql), p)
        db.commit()
        times[name] = round(time.perf_counter() - t0, 1)
        print(f"  built {name} in {times[name]} s", flush=True)
    return {
        "company_id": co.id,
        "owner": owner.id,
        "warehouse_user": whu.id,
        "counts": counts,
        "build_seconds": times,
    }


def principal(db: Session, emp_id: uuid.UUID) -> Principal:
    emp = db.get(Employee, emp_id)

    class _S:
        mfa_verified = True

    return Principal(emp, _S(), load_grants(db, emp_id), False)  # type: ignore[arg-type]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1)


def run_queries(db: Session, info: dict[str, Any], runs: int) -> dict[str, Any]:
    rnd = random.Random(7)
    inv_nos = list(
        db.scalars(
            text(
                "SELECT invoice_no FROM invoices WHERE company_id = :c ORDER BY random() LIMIT 200"
            ),
            {"c": info["company_id"]},
        )
    )
    lots = list(
        db.scalars(
            text(
                "SELECT batch_ref FROM inventory_lots WHERE company_id = :c ORDER BY random() LIMIT 200"
            ),
            {"c": info["company_id"]},
        )
    )
    awbs = list(
        db.scalars(
            text("SELECT awb_no FROM shipments WHERE company_id = :c ORDER BY random() LIMIT 200"),
            {"c": info["company_id"]},
        )
    )
    custs = list(
        db.scalars(
            text("SELECT name FROM customers WHERE company_id = :c ORDER BY random() LIMIT 200"),
            {"c": info["company_id"]},
        )
    )
    prods = list(
        db.scalars(
            text("SELECT name FROM products WHERE company_id = :c ORDER BY random() LIMIT 200"),
            {"c": info["company_id"]},
        )
    )

    def typo(s: str) -> str:
        w = s.split()[0].lower()
        i = rnd.randrange(1, len(w) - 1) if len(w) > 3 else 1
        return w[:i] + w[i + 1 :]

    kinds: dict[str, Any] = {
        "exact invoice no": lambda: rnd.choice(inv_nos),
        "exact batch": lambda: rnd.choice(lots),
        "exact airway bill (spaces)": lambda: rnd.choice(awbs).replace("-", " "),
        "invoice prefix": lambda: rnd.choice(inv_nos)[:9],
        "customer name (2 words)": lambda: " ".join(rnd.choice(custs).split()[:2]),
        "product prefix words": lambda: " ".join(w[:4] for w in rnd.choice(prods).split()[:2]),
        "single common word": lambda: rnd.choice(WORDS),
        "typo (fuzzy)": lambda: typo(rnd.choice(prods)),
        "OCR text word pair": lambda: f"door {rnd.randrange(90)}",
        "filters: customer + status": lambda: (
            f'customer:"{rnd.choice(custs).split()[0]}" status:paid'
        ),
        "filters: date + amount": lambda: (
            f"date:2026-{rnd.randrange(1, 12):02d} amount:>{rnd.randrange(100, 4000)}"
        ),
        "invoice: key + text": lambda: f"invoice:{rnd.choice(inv_nos)[:8]} {rnd.choice(WORDS)}",
    }
    out: dict[str, Any] = {}
    for who in ("owner", "warehouse_user"):
        p = principal(db, info[who])
        vis = visibility(p)
        for name, gen in kinds.items():
            for _ in range(3):  # warm-up
                engine.search(db, p, vis, parse(gen()))
            ms: list[float] = []
            hits: list[int] = []
            for _ in range(runs):
                qtext = gen()
                t0 = time.perf_counter()
                r = engine.search(db, p, vis, parse(qtext))
                ms.append((time.perf_counter() - t0) * 1000)
                hits.append(len(r.hits))
                db.rollback()
            out[f"{who}: {name}"] = {
                "p50": pct(ms, 0.5),
                "p95": pct(ms, 0.95),
                "max": round(max(ms), 1),
                "avg_hits": round(statistics.mean(hits), 1),
            }
            print(
                f"  {who:15} {name:30} p50 {out[f'{who}: {name}']['p50']:7} p95 {out[f'{who}: {name}']['p95']:7} ms",
                flush=True,
            )
    # previews
    p = principal(db, info["owner"])
    vis = visibility(p)
    from pro_trading.search.preview import build_preview

    ids = list(
        db.execute(
            text("SELECT 'invoice', id FROM invoices WHERE company_id = :c LIMIT 30"),
            {"c": info["company_id"]},
        )
    )
    ids += list(
        db.execute(
            text("SELECT 'lot', id FROM inventory_lots WHERE company_id = :c LIMIT 30"),
            {"c": info["company_id"]},
        )
    )
    ids += list(
        db.execute(
            text("SELECT 'customer', id FROM customers WHERE company_id = :c LIMIT 30"),
            {"c": info["company_id"]},
        )
    )
    ms = []
    for t, i in ids:
        t0 = time.perf_counter()
        build_preview(db, p, vis, t, i)
        ms.append((time.perf_counter() - t0) * 1000)
    out["owner: preview (invoice/lot/customer)"] = {
        "p50": pct(ms, 0.5),
        "p95": pct(ms, 0.95),
        "max": round(max(ms), 1),
    }
    return out


def trigger_cost(db: Session, info: dict[str, Any]) -> dict[str, float]:
    """Time the same bulk update with and without the search triggers (rolled back)."""
    res = {}
    for label, toggle in (("with_triggers_ms", None), ("without_triggers_ms", "DISABLE")):
        if toggle:
            db.execute(text("ALTER TABLE invoices DISABLE TRIGGER trg_search_invoices"))
        t0 = time.perf_counter()
        db.execute(
            text(
                "UPDATE invoices SET email_sends = email_sends WHERE company_id = :c AND seq_no <= 5000"
            ),
            {"c": info["company_id"]},
        )
        res[label] = round((time.perf_counter() - t0) * 1000, 1)
        db.rollback()
    res["rows"] = 5000
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--runs", type=int, default=40)
    a = ap.parse_args()
    _guard()
    with get_sessionmaker()() as db:
        print("building synthetic data…", flush=True)
        t0 = time.perf_counter()
        info = build(db, a.scale)
        print(
            f"built in {time.perf_counter() - t0:.0f} s; queue rows: {db.scalar(text('SELECT count(*) FROM search_index_queue'))}",
            flush=True,
        )
        # Bulk loads are indexed by the backfill script, not through the queue.
        db.execute(text("DELETE FROM search_index_queue"))
        db.commit()
        t0 = time.perf_counter()
        done = indexer.backfill(
            db,
            company_id=info["company_id"],
            progress=lambda t, n: n % 10000 == 0 and print(f"  indexed {t} {n}", flush=True),
        )
        backfill_s = round(time.perf_counter() - t0, 1)
        db.execute(text("ANALYZE search_documents"))
        db.commit()
        docs = db.scalar(
            text("SELECT count(*) FROM search_documents WHERE company_id = :c"),
            {"c": info["company_id"]},
        )
        size = db.scalar(text("SELECT pg_size_pretty(pg_total_relation_size('search_documents'))"))
        print(f"indexed {docs} documents in {backfill_s} s ({size})", flush=True)
        results = run_queries(db, info, a.runs)
        trig = trigger_cost(db, info)
        rec = indexer.reconcile(db, info["company_id"])
    report = {
        "counts": info["counts"],
        "documents": docs,
        "table_size": size,
        "backfill_seconds": backfill_s,
        "indexed_per_type": done,
        "queries_ms": results,
        "trigger_cost": trig,
        "reconcile_ok": rec["ok"],
        "worst_p95_ms": max(v["p95"] for v in results.values()),
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
