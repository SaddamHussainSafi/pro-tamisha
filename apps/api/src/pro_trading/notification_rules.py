"""Which committed business events become notifications, and for whom.

Every state change in the app already writes an audit row inside its own transaction
(`audit.record`, called by `mutate()` and the auth/worker paths). `on_audit` maps those audit
actions to notifications in the same transaction, so a notification exists exactly when the
business change committed. Events that are not audited (worker-side email failures, overdue
reminders, dead outbox jobs) call `notifications.notify_safely` where they happen.

Titles and amounts come from the stored records only; nothing here invents data.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Customer,
    DocumentFile,
    Employee,
    GrnLine,
    InventoryBalance,
    Invoice,
    InvoiceLine,
    OcrExtraction,
    Payment,
    PickingAttempt,
    PickingLine,
    Product,
    SalesOrder,
    SalesOrderLine,
    ScanJob,
    Shipment,
    Vendor,
    Warehouse,
)
from .notifications import notify_safely

log = logging.getLogger("pro_trading.notifications")


@dataclass(frozen=True)
class AuditInfo:
    action: str
    success: bool
    company_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    warehouse_id: uuid.UUID | None
    request_id: str | None
    detail: dict[str, Any]


Rule = Callable[[Session, AuditInfo], None]
RULES: dict[str, Rule] = {}


def rule(*actions: str) -> Callable[[Rule], Rule]:
    def deco(fn: Rule) -> Rule:
        for a in actions:
            RULES[a] = fn
        return fn

    return deco


def on_audit(db: Session, ev: AuditInfo) -> None:
    """Called by audit.record for every audit row; cheap no-op for unmapped actions."""
    fn = RULES.get(ev.action)
    if fn is None or ev.company_id is None:
        return
    try:
        fn(db, ev)
    except Exception:  # noqa: BLE001 - a notification must never break the business change
        log.exception("notification rule failed", extra={"ctx": {"action": ev.action}})


def _id(s: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(s))
    except ValueError:
        return None


def _get(db: Session, model: Any, ident: str | None) -> Any:
    oid = _id(ident)
    return db.get(model, oid) if oid else None


def _name(db: Session, model: Any, oid: uuid.UUID | None, attr: str = "name") -> str:
    obj = db.get(model, oid) if oid else None
    return str(getattr(obj, attr)) if obj is not None else ""


def _money(v: Decimal, currency: str = "CAD") -> str:
    return f"{currency} {v:,.2f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ sales orders & picking


@rule("sales_order.confirm")
def order_confirmed(db: Session, ev: AuditInfo) -> None:
    o: SalesOrder | None = _get(db, SalesOrder, ev.target_id)
    if o is None:
        return
    db.flush()
    n = db.scalar(select(func.count()).where(SalesOrderLine.order_id == o.id)) or 0
    notify_safely(
        db,
        o.company_id,
        "sales_order.confirmed",
        target_type="sales_order",
        target_id=o.id,
        category="sales_order",
        severity="info",
        source_module="Orders",
        title=f"Order {o.order_no} confirmed",
        body=f"{_name(db, Customer, o.customer_id)} · {_plural(n, 'line')} — ready to pick",
        link=f"/orders/{o.id}",
        permission="pick.record",
        warehouse_id=o.warehouse_id,
        actor_id=ev.actor_id,
    )


@rule("pick.submit")
def pick_submitted(db: Session, ev: AuditInfo) -> None:
    a: PickingAttempt | None = _get(db, PickingAttempt, ev.target_id)
    if a is None:
        return
    o = db.get(SalesOrder, a.order_id)
    assert o is not None
    notify_safely(
        db,
        a.company_id,
        "pick.submitted",
        target_type="pick_attempt",
        target_id=a.id,
        category="sales_order",
        severity="info",
        source_module="Packing",
        title=f"Packing sheet {a.pick_code} ready to check",
        body=f"Order {o.order_no} · {_name(db, Customer, o.customer_id)} — packed, waiting for "
        "verification",
        link=f"/packing/{a.id}",
        permission="pick.verify",
        warehouse_id=a.warehouse_id,
        actor_id=ev.actor_id,
    )


@rule("pick.verify")
def pick_verified(db: Session, ev: AuditInfo) -> None:
    a: PickingAttempt | None = _get(db, PickingAttempt, ev.target_id)
    if a is None:
        return
    o = db.get(SalesOrder, a.order_id)
    assert o is not None
    customer = _name(db, Customer, o.customer_id)
    notify_safely(
        db,
        a.company_id,
        "pick.verified",
        target_type="pick_attempt",
        target_id=a.id,
        category="sales_order",
        severity="success",
        source_module="Packing",
        title=f"Order {o.order_no} checked by the warehouse",
        body=f"{customer} · packing sheet {a.pick_code} verified — ready to invoice",
        link=f"/packing/{a.id}",
        permission="invoice.issue",
        warehouse_id=a.warehouse_id,
        actor_id=ev.actor_id,
    )
    db.flush()
    short = db.execute(
        select(PickingLine.disposition).where(
            PickingLine.attempt_id == a.id,
            PickingLine.picked_qty.is_not(None),
            PickingLine.picked_qty < PickingLine.requested_qty,
        )
    ).all()
    if not short:
        return
    cancelled = sum(1 for (d,) in short if d == "CANCEL")
    back = len(short) - cancelled
    parts = [
        p for p in (back and f"{back} back-ordered", cancelled and f"{cancelled} cancelled") if p
    ]
    notify_safely(
        db,
        a.company_id,
        "pick.shortage",
        target_type="sales_order",
        target_id=a.id,
        category="inventory",
        severity="warning",
        source_module="Packing",
        title=f"Shortage on order {o.order_no}",
        body=f"{customer} · {_plural(len(short), 'line')} packed short ({', '.join(parts)})",
        link=f"/orders/{o.id}",
        permission="sales_order.manage",
        warehouse_id=a.warehouse_id,
        actor_id=ev.actor_id,
    )


# ------------------------------------------------------------------ invoices & stock


@rule("invoice.issue")
def invoice_issued(db: Session, ev: AuditInfo) -> None:
    inv: Invoice | None = _get(db, Invoice, ev.target_id)
    if inv is None:
        return
    snap = inv.snapshot or {}
    customer = (snap.get("customer") or {}).get("name") or _name(db, Customer, inv.customer_id)
    notify_safely(
        db,
        inv.company_id,
        "invoice.issued",
        target_type="invoice",
        target_id=inv.id,
        category="invoice",
        severity="success",
        source_module="Invoices",
        title=f"Invoice {inv.invoice_no} issued",
        body=f"{customer} · {_money(inv.total, inv.currency)} · due {snap.get('due_date', '')}",
        link=f"/invoices/{inv.id}",
        permission="payment.verify",
        warehouse_id=inv.warehouse_id,
        actor_id=ev.actor_id,
    )
    _out_of_stock(db, inv, ev.actor_id)


def _out_of_stock(db: Session, inv: Invoice, actor_id: uuid.UUID | None) -> None:
    """Products this invoice took to zero on hand in its warehouse (all lots, all units)."""
    db.flush()
    pids = set(db.scalars(select(InvoiceLine.product_id).where(InvoiceLine.invoice_id == inv.id)))
    if not pids:
        return
    left = dict(
        db.execute(
            select(InventoryBalance.product_id, func.sum(InventoryBalance.qty))
            .where(
                InventoryBalance.company_id == inv.company_id,
                InventoryBalance.warehouse_id == inv.warehouse_id,
                InventoryBalance.product_id.in_(pids),
            )
            .group_by(InventoryBalance.product_id)
        ).all()
    )
    wh = _name(db, Warehouse, inv.warehouse_id, "code")
    for pid in sorted(pids):
        if (left.get(pid) or Decimal(0)) > 0:
            continue
        prod = db.get(Product, pid)
        if prod is None:
            continue
        notify_safely(
            db,
            inv.company_id,
            "stock.out",
            event_id=f"stock.out:{pid}:{inv.warehouse_id}:{inv.id}",
            target_type="product",
            target_id=pid,
            category="inventory",
            severity="warning",
            source_module="Inventory",
            title=f"Out of stock: {prod.sku} {prod.name}",
            body=f"None left in {wh} after invoice {inv.invoice_no}",
            link=f"/products/{pid}",
            permission="sales_order.manage",
            warehouse_id=inv.warehouse_id,
            actor_id=actor_id,
        )


# ------------------------------------------------------------------ customer payments


_METHOD = {"INTERAC": "Interac e-Transfer", "EFT": "EFT", "CHEQUE": "Cheque", "CASH": "Cash"}


@rule("payment.record")
def payment_reported(db: Session, ev: AuditInfo) -> None:
    pay: Payment | None = _get(db, Payment, ev.target_id)
    if pay is None:
        return
    notify_safely(
        db,
        pay.company_id,
        "payment.reported",
        target_type="payment",
        target_id=pay.id,
        category="payment",
        severity="info",
        source_module="Payments",
        title=f"Payment {pay.payment_no} to verify",
        body=f"{_name(db, Customer, pay.customer_id)} · {_money(pay.amount, pay.currency)} · "
        f"{_METHOD.get(pay.method, pay.method.title())}",
        link=f"/payments/{pay.id}",
        permission="payment.verify",
        actor_id=ev.actor_id,
    )


@rule("payment.verify", "payment.reject")
def payment_decided(db: Session, ev: AuditInfo) -> None:
    pay: Payment | None = _get(db, Payment, ev.target_id)
    if pay is None:
        return
    ok = ev.action == "payment.verify"
    customer = _name(db, Customer, pay.customer_id)
    notify_safely(
        db,
        pay.company_id,
        "payment.verified" if ok else "payment.rejected",
        target_type="payment",
        target_id=pay.id,
        category="payment",
        severity="success" if ok else "warning",
        source_module="Payments",
        title=f"Payment {pay.payment_no} {'confirmed' if ok else 'rejected'}",
        body=f"{customer} · {_money(pay.amount, pay.currency)}"
        + ("" if ok else f" — {getattr(pay, 'rejected_reason', '') or 'see the payment'}"),
        link=f"/payments/{pay.id}",
        permission="payment.record",
        to=[pay.recorded_by],
        actor_id=ev.actor_id,
    )


# ------------------------------------------------------------------ vendor receiving


@rule("shipment.submit_count")
def shipment_counted(db: Session, ev: AuditInfo) -> None:
    s: Shipment | None = _get(db, Shipment, ev.target_id)
    if s is None:
        return
    db.flush()
    n = db.scalar(select(func.count()).where(GrnLine.shipment_id == s.id)) or 0
    notify_safely(
        db,
        s.company_id,
        "shipment.counted",
        target_type="shipment",
        target_id=s.id,
        category="receiving",
        severity="info",
        source_module="Receiving",
        title=f"Delivery {s.shipment_no} counted",
        body=f"{_name(db, Vendor, s.vendor_id)} · {_plural(n, 'line')} — ready to approve",
        link=f"/receiving/{s.id}",
        permission="grn.approve",
        warehouse_id=s.warehouse_id,
        actor_id=ev.actor_id,
    )


@rule("shipment.post")
def shipment_posted(db: Session, ev: AuditInfo) -> None:
    s: Shipment | None = _get(db, Shipment, ev.target_id)
    if s is None:
        return
    diffs = len(ev.detail.get("discrepancies") or [])
    vendor = _name(db, Vendor, s.vendor_id)
    notify_safely(
        db,
        s.company_id,
        "shipment.posted",
        target_type="shipment",
        target_id=s.id,
        category="receiving",
        severity="warning" if diffs else "success",
        source_module="Receiving",
        title=f"Stock received: {s.grn_no or s.shipment_no}",
        body=f"{vendor} · delivery {s.shipment_no} is now in stock"
        + (f" — {_plural(diffs, 'line')} differed from the documents" if diffs else ""),
        link=f"/receiving/{s.id}",
        permission="sales_order.manage",
        warehouse_id=s.warehouse_id,
        actor_id=ev.actor_id,
    )


# ------------------------------------------------------------------ scans & OCR


_JOB_LABEL = {
    "SALES_ORDER": "sales order",
    "PICK_SHEET": "packing sheet",
    "VENDOR_DOCUMENT": "supplier document",
    "PURCHASE_ORDER": "purchase order",
    "DELIVERY_NOTE": "delivery note",
    "PACKING_LIST": "packing list",
    "RECEIPT": "receipt",
    "OTHER": "document",
}


@rule("ocr.extract")
def ocr_finished(db: Session, ev: AuditInfo) -> None:
    ex: OcrExtraction | None = _get(db, OcrExtraction, ev.target_id)
    if ex is None:
        return
    job = db.get(ScanJob, ex.scan_job_id)
    what = _JOB_LABEL.get(job.job_type, "document") if job else "document"
    job_no = job.job_no if job else ""
    if ev.success:
        notify_safely(
            db,
            ex.company_id,
            "ocr.ready",
            target_type="ocr_extraction",
            target_id=ex.id,
            category="scan",
            severity="info",
            source_module="Scan Center",
            title=f"Scanned {what} ready to review",
            body=f"{job_no} · {_plural(int(ev.detail.get('fields') or 0), 'field')} read",
            link=f"/scan/review/{ex.id}",
            permission="scan.review",
            warehouse_id=ex.warehouse_id,
        )
        return
    notify_safely(
        db,
        ex.company_id,
        "ocr.failed",
        target_type="ocr_extraction",
        target_id=ex.id,
        category="scan",
        severity="warning",
        source_module="Scan Center",
        title=f"Couldn't read scanned {what}",
        body=f"{job_no} · {ex.error_code or ev.detail.get('code') or 'OCR failed'} — open the "
        "scan to retry or key it in",
        link=f"/scan/{ex.scan_job_id}",
        permission="scan.review",
        warehouse_id=ex.warehouse_id,
    )


@rule("scan_job.failed")
def scan_failed(db: Session, ev: AuditInfo) -> None:
    job: ScanJob | None = _get(db, ScanJob, ev.target_id)
    if job is None:
        return
    notify_safely(
        db,
        job.company_id,
        "scan.failed",
        target_type="scan_job",
        target_id=job.id,
        category="scan",
        severity="warning",
        source_module="Scan Center",
        title=f"Scan {job.job_no} failed",
        body=job.error_detail or job.error_code or "The scanner reported an error",
        link=f"/scan/{job.id}",
        permission="scan.create",
        warehouse_id=job.warehouse_id,
        to=[job.requested_by],
    )


# ------------------------------------------------------------------ Drive


@rule("document.sync_failed")
def drive_failed(db: Session, ev: AuditInfo) -> None:
    f: DocumentFile | None = _get(db, DocumentFile, ev.target_id)
    if f is None:
        return
    notify_safely(
        db,
        f.company_id,
        "drive.sync_failed",
        event_id=f"drive.sync_failed:{f.id}:{f.sync_attempts}",
        target_type="document_file",
        target_id=f.id,
        category="integration",
        severity="warning",
        source_module="Documents",
        title="Google Drive copy failed",
        body=f"{f.filename} · {ev.detail.get('code') or 'error'} — retry it from Documents",
        link="/documents?status=FAILED",
        permission="document.manage",
        warehouse_id=f.warehouse_id,
    )


# ------------------------------------------------------------------ account & security


def _occurrence(ev: AuditInfo) -> str:
    return ev.request_id or uuid.uuid4().hex


def _security(
    db: Session,
    ev: AuditInfo,
    emp_id: uuid.UUID,
    kind: str,
    *,
    severity: str,
    title: str,
    body: str,
    admin_title: str | None = None,
    admin_body: str = "",
) -> None:
    """The affected person always hears about their own account; admins (employee.manage) get
    a separate, differently worded alert when `admin_title` is given."""
    assert ev.company_id is not None
    occ = _occurrence(ev)
    notify_safely(
        db,
        ev.company_id,
        kind,
        event_id=f"{kind}:{emp_id}:{occ}:self",
        target_type="employee",
        target_id=emp_id,
        category="security",
        severity=severity,
        source_module="Account",
        title=title,
        body=body,
        link="/settings",
        to=[emp_id],
        include_actor=True,
    )
    if admin_title:
        notify_safely(
            db,
            ev.company_id,
            kind,
            event_id=f"{kind}:{emp_id}:{occ}:admin",
            target_type="employee",
            target_id=emp_id,
            category="security",
            severity=severity,
            source_module="Staff & access",
            title=admin_title,
            body=admin_body,
            link="/settings/users",
            permission="employee.manage",
            actor_id=emp_id,  # the locked-out person isn't told twice
        )


@rule("auth.login")
def login_locked(db: Session, ev: AuditInfo) -> None:
    if ev.success or ev.detail.get("reason") != "locked_now" or ev.actor_id is None:
        return
    name = _name(db, Employee, ev.actor_id, "display_name")
    _security(
        db,
        ev,
        ev.actor_id,
        "security.account_locked",
        severity="critical",
        title="Your account was locked",
        body="Too many wrong passwords in a row. It unlocks by itself shortly; if this wasn't "
        "you, tell an administrator.",
        admin_title=f"Account locked: {name}",
        admin_body="Locked after repeated failed sign-ins.",
    )


@rule("auth.mfa_locked")
def mfa_locked(db: Session, ev: AuditInfo) -> None:
    if ev.actor_id is None:
        return
    name = _name(db, Employee, ev.actor_id, "display_name")
    _security(
        db,
        ev,
        ev.actor_id,
        "security.mfa_locked",
        severity="critical",
        title="Two-step sign-in paused",
        body="Too many wrong codes. If this wasn't you, change your password.",
        admin_title=f"Two-step sign-in paused: {name}",
        admin_body="Too many wrong verification codes.",
    )


@rule("employee.password_change")
def password_changed(db: Session, ev: AuditInfo) -> None:
    if ev.actor_id is None:
        return
    if ev.success:
        _security(
            db,
            ev,
            ev.actor_id,
            "security.password_changed",
            severity="warning",
            title="Your password was changed",
            body="If you didn't do this, tell an administrator right away.",
        )
    elif ev.detail.get("locked"):
        name = _name(db, Employee, ev.actor_id, "display_name")
        _security(
            db,
            ev,
            ev.actor_id,
            "security.account_locked",
            severity="critical",
            title="Your account was locked",
            body="Too many wrong current passwords while changing it.",
            admin_title=f"Account locked: {name}",
            admin_body="Locked after wrong current passwords on the password page.",
        )


@rule("auth.reauth")
def reauth_locked(db: Session, ev: AuditInfo) -> None:
    """Wrong passwords when confirming a sensitive change (turning off 2FA, new recovery
    codes) count toward the same lockout as sign-in."""
    if ev.success or not ev.detail.get("locked") or ev.actor_id is None:
        return
    name = _name(db, Employee, ev.actor_id, "display_name")
    _security(
        db,
        ev,
        ev.actor_id,
        "security.account_locked",
        severity="critical",
        title="Your account was locked",
        body="Too many wrong passwords while confirming a change. If this wasn't you, tell an "
        "administrator.",
        admin_title=f"Account locked: {name}",
        admin_body="Locked after wrong passwords on a confirmation prompt.",
    )


_TWO_STEP = {
    "auth.mfa_enabled": ("info", "Two-step sign-in turned on", "Your account now asks for a code "
                         "at sign-in."),
    "auth.mfa_disabled": ("warning", "Two-step sign-in turned off", "If you didn't do this, "
                          "change your password and tell an administrator."),
    "auth.mfa_recovery_used": ("warning", "A recovery code was used to sign in", "If this "
                               "wasn't you, change your password right away."),
    "auth.mfa_recovery_regenerated": ("info", "New recovery codes created", "Your old recovery "
                                      "codes no longer work."),
}  # fmt: skip


@rule(*_TWO_STEP)
def two_step_changed(db: Session, ev: AuditInfo) -> None:
    if not ev.success or ev.actor_id is None:
        return
    severity, title, body = _TWO_STEP[ev.action]
    _security(db, ev, ev.actor_id, f"security.{ev.action.split('.', 1)[1]}", severity=severity,
              title=title, body=body)  # fmt: skip


@rule("employee.password_reset", "employee.mfa_reset")
def admin_reset(db: Session, ev: AuditInfo) -> None:
    emp = _id(ev.target_id)
    if emp is None:
        return
    pw = ev.action == "employee.password_reset"
    _security(
        db,
        ev,
        emp,
        "security.password_reset" if pw else "security.mfa_reset",
        severity="warning",
        title="Your password was reset" if pw else "Your two-step verification was reset",
        body="An administrator did this. "
        + (
            "Sign in with the new password you were given."
            if pw
            else "Set up your authenticator again at your next sign-in."
        ),
    )


@rule("auth.sessions_revoked", "auth.session_revoked")
def sessions_revoked(db: Session, ev: AuditInfo) -> None:
    """Self-service "sign out other devices" confirms to the person; an administrator ending
    someone's session(s) tells that person. Ending one of your own sessions says nothing."""
    if ev.target_type != "employee":
        return  # auth.session_revoked on your own session (target_type "session")
    emp = _id(ev.target_id) or ev.actor_id
    if emp is None:
        return
    by_admin = ev.actor_id is not None and ev.actor_id != emp
    if ev.action == "auth.session_revoked" and not by_admin:
        return
    _security(
        db,
        ev,
        emp,
        "security.sessions_revoked",
        severity="warning" if by_admin else "info",
        title=(
            "An administrator ended one of your sessions"
            if ev.action == "auth.session_revoked"
            else "An administrator signed you out everywhere"
            if by_admin
            else "Signed out on other devices"
        ),
        body="Sign in again if you still need it." if by_admin else "Your other sessions ended.",
    )


@rule("auth.password_reset")
def password_reset_by_link(db: Session, ev: AuditInfo) -> None:
    if not ev.success or ev.actor_id is None:
        return  # a bad link has no account behind it
    _security(
        db,
        ev,
        ev.actor_id,
        "security.password_reset_link",
        severity="warning",
        title="Your password was reset",
        body="It was changed with an emailed reset link and your other sessions ended. If this "
        "wasn't you, tell an administrator.",
    )


@rule("auth.invitation_accepted")
def invitation_accepted(db: Session, ev: AuditInfo) -> None:
    if not ev.success or ev.actor_id is None or ev.company_id is None:
        return
    name = _name(db, Employee, ev.actor_id, "display_name") or "A new user"
    notify_safely(
        db,
        ev.company_id,
        "security.invitation_accepted",
        event_id=f"security.invitation_accepted:{ev.actor_id}",
        target_type="employee",
        target_id=ev.actor_id,
        category="security",
        severity="success",
        source_module="Staff & access",
        title=f"{name} accepted their invitation",
        body="They set a password and can sign in now.",
        link="/settings/users",
        permission="employee.manage",
        actor_id=ev.actor_id,
    )


@rule("scan_device.fingerprint_mismatch")
def scanner_mismatch(db: Session, ev: AuditInfo) -> None:
    assert ev.company_id is not None
    notify_safely(
        db,
        ev.company_id,
        "security.scanner_mismatch",
        event_id=f"security.scanner_mismatch:{ev.target_id}:{_occurrence(ev)}",
        target_type="scan_device",
        target_id=ev.target_id,
        category="security",
        severity="critical",
        source_module="Scan Center",
        title="Scanner pairing code didn't match",
        body="Someone tried to approve a scanner bridge with the wrong code. Check which PC is "
        "pairing before trying again.",
        link="/scan",
        permission="scan.device.manage",
        warehouse_id=ev.warehouse_id,
        actor_id=ev.actor_id,
        include_actor=True,
    )
