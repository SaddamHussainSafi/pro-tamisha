import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_engine, get_sessionmaker
from pro_trading.models import AuditEvent, OutboxEvent
from pro_trading.outbox import MAX_ATTEMPTS, dispatch_pending, enqueue

from .conftest import alembic_cfg

TABLES = {
    "companies",
    "warehouses",
    "employees",
    "permissions",
    "roles",
    "role_permissions",
    "employee_roles",
    "auth_sessions",
    "feature_flags",
    "idempotency_records",
    "audit_events",
    "outbox_events",
    "product_categories",
    "products",
    "product_aliases",
    "product_unit_profiles",
    "customer_groups",
    "customers",
    "price_book_versions",
    "price_entries",
    "inventory_lots",
    "inventory_balances",
    "stock_movements",
    "stock_reservations",
    "stock_counts",
    "stock_count_lines",
    "sales_orders",
    "sales_order_lines",
    "sales_order_amendments",
    "picking_attempts",
    "picking_lines",
    "invoice_sequences",
    "invoices",
    "invoice_lines",
    "invoice_documents",
    "email_messages",
    "source_documents",
    "scan_devices",
    "scan_jobs",
}


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    from .conftest import alembic_cfg

    return str(ScriptDirectory.from_config(alembic_cfg()).get_current_head())


def test_health(api):
    assert api.get("/health/live").json() == {"status": "ok"}
    r = api.get("/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"]["database"]["migration"] == head_revision()


def test_security_headers(api):
    r = api.get("/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Request-Id"]


def test_audit_events_are_append_only(db):
    db.add(AuditEvent(action="test.event", success=True, detail_redacted={}))
    db.commit()
    for stmt in (
        "UPDATE audit_events SET action = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(stmt))
        db.rollback()


def test_outbox_dispatch_and_retry(db):
    ok = enqueue(db, "test.ok", {"n": 1})
    bad = enqueue(db, "test.unknown", {"n": 2})
    db.commit()
    seen = []
    for _ in range(MAX_ATTEMPTS):
        dispatch_pending(db, {"test.ok": lambda ev: seen.append(ev.payload["n"])})
    db.refresh(ok)
    db.refresh(bad)
    assert seen == [1]
    assert ok.status == "PROCESSED"
    assert bad.status == "FAILED" and bad.attempts == MAX_ATTEMPTS
    assert "no handler" in (bad.last_error or "")
    db.query(OutboxEvent).filter(OutboxEvent.id.in_([ok.id, bad.id])).delete()
    db.commit()


def test_migration_down_and_up():
    """Runs last-ish: proves downgrade removes everything and upgrade restores it."""
    cfg = alembic_cfg()
    command.downgrade(cfg, "base")
    assert TABLES.isdisjoint(inspect(get_engine()).get_table_names())
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(get_engine()).get_table_names())
    from pro_trading.bootstrap import sync_permissions

    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
