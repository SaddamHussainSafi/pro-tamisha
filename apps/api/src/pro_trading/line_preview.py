"""Line preview for the fast entry grid (Create invoice › Direct, New sales order).

Read-only and per line: each row gets the rate confirming would freeze right now (same
`orders.quote` path, so a box with no box price is per lb x box weight), the line amount, the
pounds it weighs, and the saleable lots in the warehouse first-expiry-first-out with what is free
in each. A row that can't be priced or stocked gets a `problem` instead of failing the request,
so staff see every issue while typing. Issuing still re-prices and re-checks stock under locks.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import inventory as inv
from . import orders as osvc
from .errors import ApiError
from .invoicing import TAX_CODE, ZERO, fefo_key, money
from .models import Customer, InventoryBalance, InventoryLot, Product, ProductUnitProfile
from .schemas import LinePreviewIn

LB_PER_KG = Decimal(1) / Decimal("0.45359237")


def lots_for(
    db: Session, company_id: uuid.UUID, warehouse_id: uuid.UUID, product_id: uuid.UUID, unit: str
) -> list[tuple[InventoryLot, Decimal]]:
    """Saleable, unexpired lots of a product in a warehouse with the free quantity in `unit`,
    first-expiry-first-out (the order the issue path takes stock in)."""
    rows = db.execute(
        select(InventoryLot, InventoryBalance)
        .join(InventoryBalance, InventoryBalance.lot_id == InventoryLot.id)
        .where(
            InventoryLot.company_id == company_id,
            InventoryLot.product_id == product_id,
            InventoryLot.warehouse_id == warehouse_id,
            InventoryLot.status == "SALEABLE",
            InventoryBalance.unit_code == unit,
        )
    ).all()
    out = [(lot, b.qty - b.reserved_qty) for lot, b in rows if inv.is_sellable(lot)]
    out.sort(key=lambda x: fefo_key(x[0]))
    return out


def _pack_lb(db: Session, prod: Product, unit: str, snap: dict[str, Any]) -> Decimal | None:
    """Pounds in one unit: 1 for LB, 2.2046 for KG, else the box weight the price used or the
    product's active case size for the unit (default first)."""
    if unit == "LB":
        return Decimal(1)
    if unit == "KG":
        return inv.q4(LB_PER_KG)
    if snap.get("box_lb"):
        return Decimal(str(snap["box_lb"]))
    return db.scalar(
        select(ProductUnitProfile.weight_lb_per_unit)
        .where(
            ProductUnitProfile.product_id == prod.id,
            ProductUnitProfile.unit_code == unit,
            ProductUnitProfile.active.is_(True),
            ProductUnitProfile.weight_lb_per_unit.is_not(None),
        )
        .order_by(ProductUnitProfile.is_default.desc(), ProductUnitProfile.code)
        .limit(1)
    )


def preview(
    db: Session, company_id: uuid.UUID, body: LinePreviewIn, cust: Customer | None
) -> dict[str, Any]:
    at = datetime.now(UTC)
    seen: dict[tuple[uuid.UUID, str], int] = {}
    lines: list[dict[str, Any]] = []
    for i, ln in enumerate(body.lines, start=1):
        prod = db.get(Product, ln.product_id)
        if prod is None or prod.company_id != company_id:
            raise ApiError(422, "INVALID_PRODUCT", f"Line {i}: product not found", line=i)
        row: dict[str, Any] = {
            "line": i,
            "product_id": prod.id,
            "sku": prod.sku,
            "unit_code": ln.unit_code,
            "qty": inv.q4(ln.qty),
            "rate": None,
            "line_amount": None,
            "basis": None,
            "price_scope": None,
            "price_version_code": None,
            "pack_lb": None,
            "weight_lb": None,
            "available_qty": ZERO,
            "stock_ok": False,
            "lots": [],
            "problem": None,
        }
        lines.append(row)
        problem = _check(db, prod, ln.unit_code, ln.qty, i, seen, ln.weight_lb)
        if problem:
            row["problem"] = problem
            continue
        snap: dict[str, Any] = {}
        if cust is not None:
            try:
                rate, snap = osvc.quote(
                    db, company_id, prod, ln.unit_code, cust, at, i,
                    weighed_box_lb=osvc.line_box_lb(ln.weight_lb, ln.qty),
                )  # fmt: skip
            except ApiError as e:
                row["problem"] = {"code": e.code, "message": e.message}
            else:
                row |= {
                    "rate": rate,
                    "line_amount": money(ln.qty * rate),
                    "basis": snap.get("basis") or "PRICE_BOOK",
                    "price_scope": snap.get("scope"),
                    "price_version_code": snap.get("version_code"),
                    "lb_rate": Decimal(snap["lb_rate"]) if snap.get("lb_rate") else None,
                }
        pack = _pack_lb(db, prod, ln.unit_code, snap)
        row["pack_lb"] = pack
        if ln.weight_lb is not None and ln.unit_code == "BOX":
            row["weight_lb"] = inv.q4(ln.weight_lb)  # weighed: what was entered
        else:
            row["weight_lb"] = inv.q4(ln.qty * pack) if pack is not None else None
        lots = lots_for(db, company_id, body.warehouse_id, prod.id, ln.unit_code)
        if ln.lot_id is not None:
            chosen = [x for x in lots if x[0].id == ln.lot_id]
            if not chosen and row["problem"] is None:
                lot = db.get(InventoryLot, ln.lot_id)
                ours = (
                    lot is not None
                    and lot.company_id == company_id
                    and lot.product_id == prod.id
                    and lot.warehouse_id == body.warehouse_id
                )
                row["problem"] = (
                    {"code": "LOT_NOT_SALEABLE", "message": f"Batch {lot.lot_code} can't be sold"}
                    if ours and lot is not None
                    else {"code": "INVALID_LOT", "message": "That batch is not this product here"}
                )
            avail = sum((q for _, q in chosen), ZERO)
        else:
            avail = sum((q for _, q in lots), ZERO)
        row["available_qty"] = inv.q4(avail)
        row["stock_ok"] = avail >= ln.qty
        row["lots"] = [
            {
                "lot_id": lot.id,
                "lot_code": lot.lot_code,
                "batch_ref": lot.batch_ref,
                "expires_at": lot.expires_at,
                "available_qty": inv.q4(q),
            }
            for lot, q in lots
            if q > 0 or lot.id == ln.lot_id
        ]
    subtotal = sum((r["line_amount"] for r in lines if r["line_amount"] is not None), ZERO)
    tax = ZERO  # D-03: tax rules not assessed yet; same zero the invoice issue path applies
    complete = cust is not None and all(
        r["problem"] is None and r["rate"] is not None and r["qty"] > 0 and r["stock_ok"]
        for r in lines
    )
    return {
        "lines": lines,
        "subtotal": money(subtotal),
        "tax_code": TAX_CODE,
        "tax_amount": money(tax),
        "total": money(subtotal + tax),
        "complete": complete,
    }


def _check(
    db: Session,
    prod: Product,
    unit: str,
    qty: Decimal,
    i: int,
    seen: dict[tuple[uuid.UUID, str], int],
    weight: Decimal | None = None,
) -> dict[str, str] | None:
    """Same per-line rules as saving an order (validate_lines), as a problem instead of a 422."""
    if not prod.active:
        return {"code": "PRODUCT_INACTIVE", "message": f"{prod.sku} is inactive"}
    if weight is not None and unit != "BOX":
        return {"code": "WEIGHT_NOT_ALLOWED", "message": f"A weight is for boxes, not {unit}"}
    try:
        inv.check_unit(db, prod, unit)
        inv.check_qty(unit, qty)
    except ApiError as e:
        return {"code": e.code, "message": e.message}
    k = (prod.id, unit)
    if k in seen:
        msg = f"{prod.sku} in {unit} is already on line {seen[k]}. Add the quantities together."
        return {"code": "DUPLICATE_LINE", "message": msg}
    seen[k] = i
    return None
