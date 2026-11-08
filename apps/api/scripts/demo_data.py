# ruff: noqa: E501  (wide synthetic data tables)
"""Fill the SYNTHETIC demo company with realistic activity across every module.

Every record is created through the real HTTP API (in-process TestClient), acting as the
seeded demo employees, so all validation, stock-ledger rules, idempotency, audit and outbox
events apply exactly as in normal use. Nothing is written to business tables directly.

Refuses to run in staging/production. Run after `python -m pro_trading.seed`:

    PYTHONPATH=src uv run python scripts/demo_data.py

Safe to re-run: it stops if the demo activity is already loaded. Keep the Celery worker running
so invoice emails, Drive copies and OCR extractions complete in the background.
"""

import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from pro_trading import ocr
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.invoice_pdf import _assemble, _Page
from pro_trading.main import app
from pro_trading.models import (
    AuthSession,
    Company,
    Customer,
    CustomerGroup,
    Employee,
    ProductCategory,
    Vendor,
    Warehouse,
)
from pro_trading.security import new_token
from pro_trading.seed import DEMO_COMPANY

P = "/api/v1"
MARKER_REF = "C-1004"  # first customer this script creates


class DemoError(RuntimeError):
    pass


class Actor:
    """An API client signed in as one seeded employee (session minted for this run only)."""

    def __init__(self, emp: Employee) -> None:
        self.name = emp.display_name
        raw, sha = new_token()
        csrf, csrf_sha = new_token()
        with get_sessionmaker()() as db:
            sess = AuthSession(
                employee_id=emp.id,
                token_sha256=sha,
                csrf_token_sha256=csrf_sha,
                mfa_verified=True,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                last_seen_at=datetime.now(UTC),
                user_agent="demo_data.py",
            )
            db.add(sess)
            db.commit()
            self.session_id = sess.id
        self.c = TestClient(app)
        self.c.cookies.set(get_settings().session_cookie_name, raw)
        self.csrf = csrf

    def _h(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, "Idempotency-Key": str(uuid.uuid4()), **(extra or {})}

    def _check(self, r: Any, what: str) -> Any:
        if r.status_code >= 400:
            raise DemoError(f"{self.name}: {what} -> {r.status_code} {r.text[:400]}")
        return r.json() if r.content else None

    def get(self, path: str, **params: Any) -> Any:
        return self._check(self.c.get(P + path, params=params), f"GET {path}")

    def post(self, path: str, body: Any = None) -> Any:
        return self._check(self.c.post(P + path, json=body, headers=self._h()), f"POST {path}")

    def raw(
        self, path: str, data: bytes, params: dict[str, Any], ctype: str = "application/pdf"
    ) -> Any:
        r = self.c.post(
            P + path, params=params, content=data, headers=self._h({"Content-Type": ctype})
        )
        return self._check(r, f"POST {path}")


def revoke(actors: list[Actor]) -> None:
    with get_sessionmaker()() as db:
        for a in actors:
            s = db.get(AuthSession, a.session_id)
            if s is not None and s.revoked_at is None:
                s.revoked_at, s.revoked_reason = datetime.now(UTC), "demo data script finished"
        db.commit()


# ------------------------------------------------------------------ synthetic documents


def paper(title: str, lines: list[str]) -> bytes:
    """A small one-page synthetic PDF (vendor invoice, delivery proof, ...)."""
    pg = _Page()
    y = 780.0
    pg.text(50, y, title, 14, True)
    y -= 18
    pg.text(50, y, "SYNTHETIC DEMO DOCUMENT - not a real transaction", 8)
    y -= 26
    for ln in lines:
        pg.text(50, y, ln, 10)
        y -= 15
    return _assemble([pg], title)


# ------------------------------------------------------------------ master data

# sku, name, category, stock unit, tracks expiry, box profile (code, lb per box or None)
PRODUCTS: list[tuple[str, str, str, str, bool, tuple[str, str | None]]] = [
    ("YAM-YL", "Yellow yam", "PRODUCE", "BOX", False, ("CASE40", "40")),
    ("EDDO", "Eddoes", "PRODUCE", "BOX", False, ("CASE30", "30")),
    ("SCOT-PEP", "Scotch bonnet peppers", "PRODUCE", "BOX", False, ("CASE10", "10")),
    ("ACKEE-540", "Ackee in brine 540 ml (24)", "DRY", "BOX", True, ("CASE24", None)),
    ("CALL-540", "Callaloo canned 540 ml (24)", "DRY", "BOX", True, ("CASE24", None)),
    ("COCO-400", "Coconut milk 400 ml (24)", "DRY", "BOX", True, ("CASE24", None)),
    ("GARI-4KG", "Gari white 4 kg (6)", "DRY", "BOX", False, ("CASE6", None)),
    ("PALM-1L", "Red palm oil 1 L (12)", "DRY", "BOX", False, ("CASE12", None)),
    ("FUFU-18", "Fufu flour 1.8 kg (10)", "DRY", "BOX", False, ("CASE10", None)),
    ("MANGO-850", "Mango pulp 850 g (12)", "DRY", "BOX", True, ("CASE12", None)),
    ("TILA-FZ", "Tilapia whole frozen", "FROZEN", "BOX", True, ("CASE10", "10")),
    ("GOAT-FZ", "Goat meat cubed frozen", "FROZEN", "BOX", True, ("CASE20", "20")),
]

# ref, name, group, terms, email
CUSTOMERS = [
    ("C-1004", "Lagos Market Toronto (synthetic)", "WHOLESALE", 30, "ap@lagos-market.example.test"),
    (
        "C-1005",
        "Spice Route Restaurant (synthetic)",
        "RESTAURANT",
        14,
        "owner@spice-route.example.test",
    ),
    (
        "C-1006",
        "Caribbean Corner Store (synthetic)",
        "WHOLESALE",
        21,
        "orders@carib-corner.example.test",
    ),
    (
        "C-1007",
        "Accra Kitchen Catering (synthetic)",
        "RESTAURANT",
        7,
        "billing@accra-kitchen.example.test",
    ),
    (
        "C-1008",
        "Northside Halal Grocery (synthetic)",
        "WHOLESALE",
        30,
        "accounts@northside.example.test",
    ),
    ("C-1009", "Ocho Rios Grill (synthetic)", "RESTAURANT", 14, "chef@ochorios.example.test"),
]

STANDARD_BOX = {
    "YAM-YL": "48.00", "EDDO": "39.00", "SCOT-PEP": "32.00", "ACKEE-540": "96.00",
    "CALL-540": "44.00", "COCO-400": "36.00", "GARI-4KG": "54.00", "PALM-1L": "62.00",
    "FUFU-18": "41.00", "MANGO-850": "38.50", "TILA-FZ": "52.00", "GOAT-FZ": "118.00",
}  # fmt: skip
PRICE_CSV_HEAD = "sku,product_name,unit,rate,currency,customer_ref,group_code\r\n"
# the opening book (seed PB-2026-01) is carried forward unchanged, plus new products
CARRIED = [
    ("CAS-FZ", "LB", "1.50", "", ""), ("CAS-FZ", "BOX", "29.00", "", ""),
    ("CAS-FZ", "LB", "1.40", "", "WHOLESALE"), ("CAS-FZ", "LB", "1.35", "C-1001", ""),
    ("PLN-GR", "LB", "0.95", "", ""), ("PLN-GR", "PIECE", "0.40", "", ""),
    ("PLN-GR", "BOX", "45.00", "", "RESTAURANT"), ("RICE-JAS", "KG", "2.10", "", ""),
    ("RICE-JAS", "BOX", "39.50", "", ""),
]  # fmt: skip
SPECIAL = [
    ("ACKEE-540", "BOX", "92.00", "", "WHOLESALE"),
    ("GARI-4KG", "BOX", "51.00", "", "WHOLESALE"),
    ("GOAT-FZ", "BOX", "112.00", "C-1007", ""),
    ("COCO-400", "BOX", "34.50", "C-1004", ""),
]


def master_data(owner: Actor, ids: dict[str, Any]) -> None:
    for sku, name, cat, unit, expiry, (pcode, lb) in PRODUCTS:
        body = {
            "sku": sku,
            "name": name,
            "category_id": ids["cats"][cat],
            "stock_unit": unit,
            "tracks_expiry": expiry,
            "unit_profiles": [
                {"code": pcode, "unit_code": "BOX", "weight_lb_per_unit": lb, "is_default": True}
            ],
        }
        ids["p"][sku] = owner.post("/products", body)["id"]
    for ref, name, grp, terms, email in CUSTOMERS:
        body = {
            "external_ref": ref,
            "name": name,
            "group_id": ids["groups"][grp],
            "payment_terms_days": terms,
            "email": email,
            "phone": "+1-416-555-0" + ref[-3:],
        }
        ids["c"][ref] = owner.post("/customers", body)["id"]
    rows = CARRIED + [(s, "BOX", r, "", "") for s, r in STANDARD_BOX.items()] + SPECIAL
    names = {sku: name for sku, name, *_ in PRODUCTS} | {
        "CAS-FZ": "Cassava frozen",
        "PLN-GR": "Plantain green",
        "RICE-JAS": "Jasmine rice",
    }
    csv = PRICE_CSV_HEAD + "".join(
        f"{s},{names[s]},{u},{r},CAD,{c},{g}\r\n" for s, u, r, c, g in rows
    )
    v = owner.post(
        "/price-books/versions/import",
        {"version_code": "PB-2026-02", "csv": csv, "notes": "Spring list incl. new imports"},
    )
    owner.post(f"/price-books/versions/{v['id']}/activate", {"version": v["version"]})
    # a draft waiting for approval, so the price screens show the review step
    owner.post(
        "/price-books/versions/import",
        {
            "version_code": "PB-2026-03",
            "csv": PRICE_CSV_HEAD
            + "".join(
                f"{s},{names[s]},{u},{r},CAD,{c},{g}\r\n"
                for s, u, r, c, g in rows
                if not (s == "YAM-YL" and u == "BOX")
            )
            + "YAM-YL,Yellow yam,BOX,51.00,CAD,,\r\n",
            "notes": "Draft: yam up 3.00 after freight increase",
        },
    )


# ------------------------------------------------------------------ receiving


def shipment(
    actor: Actor,
    poster: Actor | None,
    ids: dict[str, Any],
    vendor: str,
    wh: str,
    inv_no: str,
    awb: str,
    lines: list[tuple[str, str, str, str, str]],  # sku, declared, accepted, damaged, unit_cost
    costs: list[tuple[str, str]],
    stage: str,  # OPEN | COUNTED | POSTED
    note: str,
) -> dict[str, Any]:
    s = actor.post(
        "/shipments",
        {
            "warehouse_id": ids["wh"][wh],
            "vendor_id": ids["v"][vendor],
            "vendor_invoice_no": inv_no,
            "awb_no": awb,
            "currency": "USD",
            "expected_on": str(date.today()),
            "note": note,
        },
    )
    pdf = paper(
        f"Commercial invoice {inv_no}",
        [f"Air waybill {awb}", ""]
        + [f"{sku:<12} {qty:>6} BOX  @ USD {cost}" for sku, qty, _, _, cost in lines],
    )
    s = actor.raw(
        f"/shipments/{s['id']}/documents/upload",
        pdf,
        {
            "kind": "INVOICE",
            "version": s["version"],
            "doc_ref": inv_no,
            "filename": f"{inv_no}.pdf",
        },
    )
    doc = s["documents"][0]["id"]
    for sku, declared, *_rest in lines:
        cost = _rest[-1]
        s = actor.post(
            f"/shipments/{s['id']}/declared",
            {
                "version": s["version"],
                "shipment_document_id": doc,
                "raw_label": sku.replace("-", " ") + " CTN",
                "product_id": ids["p"][sku],
                "unit_code": "BOX",
                "declared_qty": declared,
                "unit_cost": cost,
            },
        )
    for kind, amount in costs:
        s = actor.post(
            f"/shipments/{s['id']}/costs",
            {"version": s["version"], "kind": kind, "amount": amount, "currency": "USD"},
        )
    if stage == "OPEN":
        return s
    expiring = {sku for sku, _n, _c, _u, exp, _p in PRODUCTS if exp}
    exp_at = (datetime.now(UTC) + timedelta(days=240)).isoformat()
    for sku, _decl, accepted, damaged, _cost in lines:
        s = actor.post(
            f"/shipments/{s['id']}/count",
            {
                "version": s["version"],
                "product_id": ids["p"][sku],
                "unit_code": "BOX",
                "accepted_qty": accepted,
                "damaged_qty": damaged,
                "rejected_qty": "0",
                "batch_ref": f"{inv_no}-{sku}",
                "expires_at": exp_at if sku in expiring else None,
            },
        )
    s = actor.post(f"/shipments/{s['id']}/submit-count", {"version": s["version"]})
    if stage == "COUNTED" or poster is None:
        return s
    differs = any(d != a or dm != "0" for _, d, a, dm, _ in lines)
    return poster.post(
        f"/shipments/{s['id']}/post",
        {
            "version": s["version"],
            "acknowledge_discrepancies": differs,
            "discrepancy_note": "Damaged cases set aside, vendor notified" if differs else None,
        },
    )


def receiving(wh: Actor, mgr: Actor, ids: dict[str, Any]) -> None:
    shipment(
        wh, mgr, ids, "V-ACC", "MAIN", "ACC-24-5521", "AWB-176-0042-1180",
        [("GARI-4KG", "40", "40", "0", "28.5000"), ("PALM-1L", "30", "28", "2", "31.0000"),
         ("FUFU-18", "50", "50", "0", "19.2500"), ("COCO-400", "60", "60", "0", "17.8000")],
        [("FREIGHT", "420.00"), ("DUTY", "85.40"), ("BROKERAGE", "65.00")],
        "POSTED", "Weekly Accra consolidation",
    )  # fmt: skip
    shipment(
        wh, mgr, ids, "V-KIN", "MAIN", "KPC-88213", "AWB-001-7745-2231",
        [("YAM-YL", "30", "30", "0", "24.0000"), ("EDDO", "20", "20", "0", "18.5000"),
         ("SCOT-PEP", "12", "12", "0", "15.0000"), ("ACKEE-540", "40", "40", "0", "55.0000"),
         ("CALL-540", "30", "30", "0", "22.0000"), ("MANGO-850", "25", "25", "0", "19.0000")],
        [("FREIGHT", "610.00"), ("DUTY", "120.00")],
        "POSTED", "Kingston air freight",
    )  # fmt: skip
    shipment(
        mgr, mgr, ids, "V-LIM", "EAST", "LDG-3307", "AWB-145-2290-6612",
        [("MANGO-850", "20", "20", "0", "19.0000"), ("COCO-400", "30", "30", "0", "17.8000"),
         ("GARI-4KG", "15", "15", "0", "28.5000")],
        [("FREIGHT", "260.00")],
        "POSTED", "East warehouse restock",
    )  # fmt: skip
    shipment(
        wh, None, ids, "V-ACC", "MAIN", "ACC-24-5534", "AWB-176-0042-1324",
        [("TILA-FZ", "40", "40", "0", "26.0000"), ("GOAT-FZ", "30", "28", "0", "64.0000")],
        [("FREIGHT", "540.00"), ("DUTY", "96.00")],
        "COUNTED", "Frozen container - 2 goat cases short on count",
    )  # fmt: skip
    shipment(
        wh, None, ids, "V-KIN", "MAIN", "KPC-88240", "AWB-001-7745-2399",
        [("YAM-YL", "25", "", "", "24.0000"), ("SCOT-PEP", "10", "", "", "15.0000")],
        [("FREIGHT", "380.00")],
        "OPEN", "Arriving Friday",
    )  # fmt: skip


# ------------------------------------------------------------------ inventory


def inventory(owner: Actor, wh: Actor, ids: dict[str, Any]) -> None:
    exp = (datetime.now(UTC) + timedelta(days=150)).isoformat()
    for sku, qty in [("TILA-FZ", "18"), ("GOAT-FZ", "9")]:
        owner.post(
            "/inventory/opening-balances",
            {
                "warehouse_id": ids["wh"]["MAIN"],
                "product_id": ids["p"][sku],
                "expires_at": exp,
                "landed_unit_cost": "30.0000",
                "cost_currency": "CAD",
                "lines": [{"unit_code": "BOX", "qty": qty}],
            },
        )
    lots = {
        b["sku"]: b
        for b in owner.get("/inventory/stock", warehouse_id=ids["wh"]["MAIN"], limit=200)["items"]
        if b["unit_code"] == "BOX"
    }
    coco = lots["COCO-400"]
    owner.post(
        "/inventory/damage",
        {"lot_id": coco["lot_id"], "unit_code": "BOX", "qty": "2", "note": "Dented cans, forklift"},
    )
    # a cycle count waiting for a manager to post it
    wh.post(
        "/inventory/counts",
        {
            "warehouse_id": ids["wh"]["MAIN"],
            "note": "Friday dry-goods cycle count",
            "lines": [
                {"lot_id": lots["FUFU-18"]["lot_id"], "unit_code": "BOX", "counted_qty": "49"},
                {"lot_id": lots["GARI-4KG"]["lot_id"], "unit_code": "BOX", "counted_qty": "40"},
            ],
        },
    )


# ------------------------------------------------------------------ orders → picking → invoices

# customer, [(sku, qty)], target stage, shortages {line index: picked qty}
ORDERS: list[tuple[str, list[tuple[str, str]], str, dict[int, str]]] = [
    ("C-1004", [("GARI-4KG", "8"), ("FUFU-18", "10"), ("PALM-1L", "4")], "INVOICED", {}),
    ("C-1005", [("SCOT-PEP", "2"), ("YAM-YL", "3"), ("COCO-400", "6")], "INVOICED", {}),
    ("C-1006", [("ACKEE-540", "5"), ("CALL-540", "4"), ("MANGO-850", "3")], "INVOICED", {}),
    ("C-1007", [("GOAT-FZ", "4"), ("TILA-FZ", "3"), ("PALM-1L", "2")], "INVOICED", {}),
    ("C-1008", [("GARI-4KG", "6"), ("COCO-400", "10"), ("FUFU-18", "6")], "INVOICED", {2: "4"}),
    ("C-1009", [("YAM-YL", "4"), ("EDDO", "3"), ("SCOT-PEP", "1")], "INVOICED", {}),
    ("C-1004", [("ACKEE-540", "6"), ("MANGO-850", "4")], "INVOICED", {}),
    ("C-1005", [("TILA-FZ", "2"), ("CALL-540", "3")], "VERIFIED", {}),
    ("C-1006", [("EDDO", "4"), ("YAM-YL", "2")], "VERIFIED", {}),
    ("C-1008", [("FUFU-18", "5"), ("GARI-4KG", "3")], "SUBMITTED", {}),
    ("C-1009", [("GOAT-FZ", "2"), ("SCOT-PEP", "1")], "PICKING", {}),
    ("C-1007", [("COCO-400", "8"), ("MANGO-850", "2")], "CONFIRMED", {}),
    ("C-1004", [("PALM-1L", "6"), ("GARI-4KG", "4")], "CONFIRMED", {}),
    ("C-1006", [("ACKEE-540", "3")], "DRAFT", {}),
]
STAGES = ["DRAFT", "CONFIRMED", "PICKING", "SUBMITTED", "VERIFIED", "INVOICED"]


def orders(sales: Actor, wh: Actor, mgr: Actor, ids: dict[str, Any]) -> list[dict[str, Any]]:
    invoices = []
    for cref, lines, stage, short in ORDERS:
        target = STAGES.index(stage)
        o = sales.post(
            "/sales-orders",
            {
                "customer_id": ids["c"][cref],
                "warehouse_id": ids["wh"]["MAIN"],
                "source_text": "\n".join(f"{q} bx {sku}" for sku, q in lines),
                "lines": [
                    {
                        "product_id": ids["p"][sku],
                        "unit_code": "BOX",
                        "qty": q,
                        "source_line_text": f"{q} bx {sku.lower()}",
                    }
                    for sku, q in lines
                ],
            },
        )
        if target < 1:
            continue
        o = sales.post(f"/sales-orders/{o['id']}/confirm", {"version": o["version"]})
        if target < 2:
            continue
        pk = sales.post(f"/sales-orders/{o['id']}/pick-attempts", {"version": o["version"]})
        if target < 3:
            continue
        rows = [
            {
                "line_id": ln["id"],
                "picked_qty": short.get(i, lines[i][1]),
                "shortage_reason": "OUT_OF_STOCK" if i in short else None,
            }
            for i, ln in enumerate(pk["lines"])
        ]
        pk = wh.post(f"/pick-attempts/{pk['id']}/record", {"version": pk["version"], "lines": rows})
        pk = wh.post(f"/pick-attempts/{pk['id']}/submit", {"version": pk["version"]})
        if target < 4:
            continue
        pk = mgr.post(
            f"/pick-attempts/{pk['id']}/verify",
            {"version": pk["version"], "dispositions": {}, "reason": ""},
        )
        if target < 5:
            continue
        invoices.append(
            mgr.post(
                f"/pick-attempts/{pk['id']}/issue-invoice",
                {"version": pk["version"], "dispatch_confirmed": True, "weights": {}, "note": ""},
            )
        )
    return invoices


def proofs_of_delivery(sales: Actor, invoices: list[dict[str, Any]]) -> None:
    for inv in invoices[:3]:
        pdf = paper(
            f"Delivery note {inv['invoice_no']}",
            [f"Customer: {inv['customer_name']}", "Goods received in good order.", "",
             "Signed: ____ (synthetic signature)"],
        )  # fmt: skip
        sales.raw(
            f"/invoices/{inv['id']}/pod",
            pdf,
            {"filename": f"pod-{inv['invoice_no']}.pdf", "note": "Signed by receiver at door"},
        )


# ------------------------------------------------------------------ scans + OCR


def scans(sales: Actor, ids: dict[str, Any]) -> None:
    with get_sessionmaker()() as db:
        co = db.get(Company, ids["company"])
        wh = db.get(Warehouse, uuid.UUID(ids["wh"]["MAIN"]))
        assert co is not None and wh is not None
        sheets = []
        for ref, fills, extra in [
            ("C-1001", {"CAS-FZ": "12", "PLN-GR": "3?"}, ["Mango pulp 4 box"]),
            ("C-1002", {"RICE-JAS": "6", "CAS-FZ": "2"}, []),
        ]:
            cust = db.scalar(
                select(Customer).where(Customer.company_id == co.id, Customer.external_ref == ref)
            )
            assert cust is not None
            sheets.append(
                (
                    ref,
                    ocr.order_sheet_pdf(db, cust, wh, co.legal_name, fills=fills, write_ins=extra),
                )
            )
    for ref, pdf in sheets:
        sales.raw(
            "/scans/uploads",
            pdf,
            {
                "warehouse_id": ids["wh"]["MAIN"],
                "job_type": "SALES_ORDER",
                "filename": f"order-{ref}.pdf",
            },
        )
    sales.raw(
        "/scans/uploads",
        paper("Customer credit application", ["Accra Kitchen Catering (synthetic)", "Pending"]),
        {
            "warehouse_id": ids["wh"]["MAIN"],
            "job_type": "OTHER",
            "filename": "credit-application.pdf",
        },
    )


# ------------------------------------------------------------------ payments (Module 10)


def payments(sales: Actor, fin: Actor, ids: dict[str, Any], invoices: list[dict[str, Any]]) -> None:
    if not any(getattr(r, "path", "") == f"{P}/payments" for r in app.routes):
        print("  (payments API not available yet - skipped)")
        return
    from demo_payments import run  # type: ignore[import-not-found]  # noqa: PLC0415

    run(sales, fin, ids, invoices)


# ------------------------------------------------------------------ main


def main() -> int:
    if get_settings().is_production_like:
        print("refusing to load synthetic demo data in", get_settings().app_env, file=sys.stderr)
        return 2
    with get_sessionmaker()() as db:
        co = db.scalar(select(Company).where(Company.legal_name == DEMO_COMPANY))
        if co is None:
            print("demo company missing: run `python -m pro_trading.seed` first", file=sys.stderr)
            return 2
        if db.scalar(
            select(Customer.id).where(
                Customer.company_id == co.id, Customer.external_ref == MARKER_REF
            )
        ):
            print("demo activity already loaded")
            return 0
        emps = {
            e.email.split("@")[0]: e
            for e in db.scalars(select(Employee).where(Employee.company_id == co.id))
        }
        ids: dict[str, Any] = {
            "company": co.id,
            "p": {},
            "c": {},
            "wh": {w.code: str(w.id) for w in db.scalars(select(Warehouse).where(Warehouse.company_id == co.id))},
            "v": {v.code: str(v.id) for v in db.scalars(select(Vendor).where(Vendor.company_id == co.id))},
            "cats": {c.code: str(c.id) for c in db.scalars(select(ProductCategory).where(ProductCategory.company_id == co.id))},
            "groups": {g.code: str(g.id) for g in db.scalars(select(CustomerGroup).where(CustomerGroup.company_id == co.id))},
        }  # fmt: skip
    actors = {k: Actor(emps[k]) for k in ("owner", "manager", "sales", "warehouse", "finance")}
    try:
        steps = [
            ("products, customers, price books", lambda: master_data(actors["owner"], ids)),
            ("vendor shipments", lambda: receiving(actors["warehouse"], actors["manager"], ids)),
            ("stock: opening, damage, cycle count", lambda: inventory(actors["owner"], actors["warehouse"], ids)),
        ]  # fmt: skip
        for label, fn in steps:
            print("•", label)
            fn()
        print("• sales orders, picking, invoices")
        invoices = orders(actors["sales"], actors["warehouse"], actors["manager"], ids)
        print("• proofs of delivery")
        proofs_of_delivery(actors["sales"], invoices)
        print("• scanned order sheets")
        scans(actors["sales"], ids)
        print("• payments")
        payments(actors["sales"], actors["finance"], ids, invoices)
    finally:
        revoke(list(actors.values()))
    print(f"demo activity loaded: {len(invoices)} invoices issued")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
