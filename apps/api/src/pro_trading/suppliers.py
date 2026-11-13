"""Supplier ledger and payments we make to suppliers (owner direction 2026-11-08).

What a supplier "billed" = the value on their documents for POSTED shipments: Σ declared
quantity × declared unit cost, rounded per line to cents, in the shipment's currency. Lines
without a unit cost are counted as `unpriced_lines`, not guessed. Balance = billed − paid per
currency; currencies are never converted (D-04). Damaged and rejected quantities are shown as
returned/not accepted; whether they reduce what we owe is an open owner question, so they do
not change the balance. Landed costs (freight, duty …) are listed per shipment but are paid to
other parties and stay out of the supplier balance.
"""

import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Company,
    DeclaredLine,
    Employee,
    GrnLine,
    LandedCost,
    PurchaseCorrection,
    Shipment,
    SupplierPayment,
    Vendor,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")
QTY = Decimal("0.0001")
UNIT_ORDER = {"BOX": 0, "PIECE": 1, "LB": 2, "KG": 3}


def next_no(db: Session, company_id: uuid.UUID, year: int) -> str:
    db.get(
        Company, company_id, with_for_update={"key_share": True}
    )  # serialize numbering per company
    n = db.scalar(select(func.count()).where(SupplierPayment.company_id == company_id)) or 0
    return f"SPAY-{year}-{n + 1:06d}"


def payment_out(db: Session, sp: SupplierPayment) -> dict[str, Any]:
    names = dict(
        db.execute(
            select(Employee.id, Employee.display_name).where(
                Employee.id.in_([x for x in (sp.recorded_by, sp.voided_by) if x])
            )
        ).all()
    )
    v = db.get(Vendor, sp.vendor_id)
    shp = db.get(Shipment, sp.shipment_id) if sp.shipment_id else None
    return {
        "id": sp.id,
        "payment_no": sp.payment_no,
        "vendor_id": sp.vendor_id,
        "vendor_name": v.name if v else "",
        "shipment_id": sp.shipment_id,
        "shipment_no": shp.shipment_no if shp else None,
        "method": sp.method,
        "amount": sp.amount,
        "currency": sp.currency,
        "paid_on": sp.paid_on,
        "reference": sp.reference,
        "note": sp.note,
        "recorded_by_name": names.get(sp.recorded_by),
        "created_at": sp.created_at,
        "voided_at": sp.voided_at,
        "voided_by_name": names.get(sp.voided_by) if sp.voided_by else None,
        "void_reason": sp.void_reason,
    }


def _billed(db: Session, shipment_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, int]]:
    out: dict[uuid.UUID, list[Any]] = defaultdict(lambda: [ZERO, 0])
    if not shipment_ids:
        return {}
    for sid, q, cost in db.execute(
        select(DeclaredLine.shipment_id, DeclaredLine.declared_qty, DeclaredLine.unit_cost).where(
            DeclaredLine.shipment_id.in_(shipment_ids), DeclaredLine.removed_at.is_(None)
        )
    ):
        if cost is None:
            out[sid][1] += 1
        else:
            out[sid][0] += (Decimal(q) * Decimal(cost)).quantize(CENT, ROUND_HALF_UP)
    return {k: (v[0], v[1]) for k, v in out.items()}


def _posted(
    db: Session, company_id: uuid.UUID, vendor_id: uuid.UUID | None = None
) -> list[Shipment]:
    # A quick receive matched into another purchase's invoice is billed (and listed) there.
    q = select(Shipment).where(
        Shipment.company_id == company_id,
        Shipment.status == "POSTED",
        Shipment.matched_shipment_id.is_(None),
    )
    if vendor_id:
        q = q.where(Shipment.vendor_id == vendor_id)
    return list(db.scalars(q.order_by(Shipment.posted_at, Shipment.shipment_no)))


def _totals(billed: dict[str, Decimal], paid: dict[str, Decimal]) -> list[dict[str, Any]]:
    out = []
    for c in sorted(set(billed) | set(paid)):
        b, pd = billed.get(c, ZERO).quantize(CENT), paid.get(c, ZERO).quantize(CENT)
        out.append({"currency": c, "billed": b, "paid": pd, "balance": b - pd})
    return out


def balances(db: Session, company_id: uuid.UUID) -> list[dict[str, Any]]:
    vendors = list(
        db.scalars(select(Vendor).where(Vendor.company_id == company_id).order_by(Vendor.name))
    )
    ships = _posted(db, company_id)
    billed_by = _billed(db, [s.id for s in ships])
    billed: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    count: dict[uuid.UUID, int] = defaultdict(int)
    last_rx: dict[uuid.UUID, Any] = {}
    for s in ships:
        if s.vendor_id is None:  # a quick receive whose supplier is not known yet
            continue
        billed[s.vendor_id][s.currency] += billed_by.get(s.id, (ZERO, 0))[0]
        count[s.vendor_id] += 1
        last_rx[s.vendor_id] = s.posted_at
    paid: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    last_paid: dict[uuid.UUID, date] = {}
    for vid, cur, amt, last in db.execute(
        select(
            SupplierPayment.vendor_id,
            SupplierPayment.currency,
            func.sum(SupplierPayment.amount),
            func.max(SupplierPayment.paid_on),
        )
        .where(SupplierPayment.company_id == company_id, SupplierPayment.voided_at.is_(None))
        .group_by(SupplierPayment.vendor_id, SupplierPayment.currency)
    ):
        paid[vid][cur] += Decimal(amt)
        last_paid[vid] = max(last, last_paid.get(vid, last))
    return [
        {
            "vendor_id": v.id,
            "code": v.code,
            "name": v.name,
            "country_code": v.country_code,
            "currency": v.currency,
            "active": v.active,
            "shipments": count[v.id],
            "last_received_at": last_rx.get(v.id),
            "last_paid_on": last_paid.get(v.id),
            "totals": _totals(dict(billed[v.id]), dict(paid[v.id])),
        }
        for v in vendors
    ]


def ledger(db: Session, company_id: uuid.UUID, v: Vendor) -> dict[str, Any]:
    co = db.get(Company, company_id)
    tz = ZoneInfo(co.timezone if co else "UTC")
    ships = _posted(db, company_id, v.id)
    ids = [s.id for s in ships]
    billed_by = _billed(db, ids)
    units: dict[uuid.UUID, dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: ZERO))
    )
    if ids:
        for sid, unit, q in db.execute(
            select(
                DeclaredLine.shipment_id, DeclaredLine.unit_code, DeclaredLine.declared_qty
            ).where(DeclaredLine.shipment_id.in_(ids), DeclaredLine.removed_at.is_(None))
        ):
            units[sid][unit]["declared"] += Decimal(q)
        home = func.coalesce(Shipment.matched_shipment_id, Shipment.id)
        for sid, unit, a, d, r in db.execute(
            select(
                home,
                GrnLine.unit_code,
                GrnLine.accepted_qty,
                GrnLine.damaged_qty,
                GrnLine.rejected_qty,
            )
            .join(Shipment, Shipment.id == GrnLine.shipment_id)
            .where(home.in_(ids))
        ):
            row = units[sid][unit]
            row["accepted"] += Decimal(a)
            row["damaged"] += Decimal(d)
            row["rejected"] += Decimal(r)
        # Later corrections to a purchase: short / returned = not accepted, damaged = damaged,
        # more than invoiced = accepted. Only what was really posted to stock counts.
        for sid, unit, kind, posted in db.execute(
            select(
                PurchaseCorrection.shipment_id,
                PurchaseCorrection.unit_code,
                PurchaseCorrection.kind,
                PurchaseCorrection.posted_qty,
            ).where(PurchaseCorrection.shipment_id.in_(ids), PurchaseCorrection.status == "POSTED")
        ):
            row, qty = units[sid][unit], Decimal(posted or 0)
            if kind == "OVER":
                row["accepted"] += qty
            else:
                row["accepted"] -= qty
                row["damaged" if kind == "DAMAGED" else "rejected"] += qty
    costs: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if ids:
        for sid, kind, amt, cur in db.execute(
            select(LandedCost.shipment_id, LandedCost.kind, LandedCost.amount, LandedCost.currency)
            .where(LandedCost.shipment_id.in_(ids), LandedCost.removed_at.is_(None))
            .order_by(LandedCost.created_at)
        ):
            costs[sid].append({"kind": kind, "amount": amt, "currency": cur})

    def unit_rows(by_unit: dict[str, dict[str, Decimal]]) -> list[dict[str, Any]]:
        return [
            {"unit_code": u, **{k: by_unit[u][k].quantize(QTY) for k in
                                ("declared", "accepted", "damaged", "rejected")}}
            for u in sorted(by_unit, key=lambda x: UNIT_ORDER.get(x, 9))
        ]  # fmt: skip

    returned: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    shipments: list[dict[str, Any]] = []
    for s in ships:
        for unit_code, vals in units[s.id].items():
            for k, q in vals.items():
                returned[unit_code][k] += q
        b, unpriced = billed_by.get(s.id, (ZERO, 0))
        shipments.append(
            {
                "id": s.id,
                "shipment_no": s.shipment_no,
                "grn_no": s.grn_no,
                "vendor_invoice_no": s.vendor_invoice_no,
                "posted_at": s.posted_at,
                "currency": s.currency,
                "billed": b,
                "unpriced_lines": unpriced,
                "units": unit_rows(units[s.id]),
                "landed_costs": costs[s.id],
            }
        )
    pays = list(
        db.scalars(
            select(SupplierPayment)
            .where(SupplierPayment.company_id == company_id, SupplierPayment.vendor_id == v.id)
            .order_by(SupplierPayment.paid_on, SupplierPayment.payment_no)
        )
    )
    raw: list[dict[str, Any]] = [
        {"on": s["posted_at"].astimezone(tz).date(), "kind": "SHIPMENT", "ref": s["shipment_no"],
         "id": s["id"], "currency": s["currency"], "billed": s["billed"], "paid": ZERO,
         "voided": False}
        for s in shipments
    ] + [
        {"on": p.paid_on, "kind": "PAYMENT", "ref": p.payment_no, "id": p.id,
         "currency": p.currency, "billed": ZERO, "paid": p.amount,
         "voided": p.voided_at is not None}
        for p in pays
    ]  # fmt: skip
    raw.sort(key=lambda e: (e["on"], 0 if e["kind"] == "SHIPMENT" else 1, e["ref"]))
    running: dict[str, Decimal] = defaultdict(lambda: ZERO)
    billed_t: dict[str, Decimal] = defaultdict(lambda: ZERO)
    paid_t: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in raw:
        if not e["voided"]:
            running[e["currency"]] += e["billed"] - e["paid"]
            billed_t[e["currency"]] += e["billed"]
            paid_t[e["currency"]] += e["paid"]
        e["balance"] = running[e["currency"]]
    return {
        "vendor_id": v.id,
        "code": v.code,
        "name": v.name,
        "country_code": v.country_code,
        "email": v.email,
        "currency": v.currency,
        "active": v.active,
        "totals": _totals(dict(billed_t), dict(paid_t)),
        "returned": unit_rows(returned),
        "shipments": shipments,
        "payments": [payment_out(db, p) for p in reversed(pays)],
        "entries": raw,
    }
