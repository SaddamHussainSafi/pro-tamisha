"""Tests run against a real PostgreSQL database (default: tami_test). Synthetic data only."""

import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://tami@127.0.0.1:5432/tami_test")
# The suite rebuilds its schema from nothing each run: migration 0033 may roll back over
# leftover rows of the last run (it refuses on a real database).
os.environ.setdefault("PT_FORCE_DOWNGRADE", "1")
os.environ["APP_ENV"] = "test"
os.environ["OCR_PROVIDER"] = "fake"  # tests that use Tesseract switch it on themselves
os.environ.setdefault("MFA_ENCRYPTION_KEY", "q0f5L2m9ZPZ0g3W9H3nmVvG7Pq0V8k1oJ5sQ0yYy7Uk=")
# Per-IP failure throttles are shared by every test (all requests come from "testclient");
# test_security.py lowers it for its own client address.
os.environ.setdefault("LOGIN_IP_MAX_FAILURES", "1000000")

import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import APIRouter, Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from pro_trading.bootstrap import ensure_default_roles, sync_permissions  # noqa: E402
from pro_trading.db import get_sessionmaker  # noqa: E402
from pro_trading.deps import Principal, require_permission  # noqa: E402
from pro_trading.main import create_app  # noqa: E402
from pro_trading.models import Company, Employee, EmployeeRole, Warehouse  # noqa: E402
from pro_trading.security import hash_password  # noqa: E402

API_DIR = os.path.dirname(os.path.dirname(__file__))
PASSWORD = "Synthetic-Test-Pass-1!"


def alembic_cfg() -> Config:
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    return cfg


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    cfg = alembic_cfg()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
    yield


# Probe route standing in for the future invoice module (SEC-001 needs a real authorization path).
probe = APIRouter(prefix="/api/v1/_probe")


@probe.post("/warehouses/{warehouse_id}/invoices/issue")
def issue_invoice_probe(
    warehouse_id: uuid.UUID,
    p: Principal = Depends(require_permission("invoice.issue", warehouse_param="warehouse_id")),
) -> dict[str, str]:
    return {"ok": "issued"}


@pytest.fixture(scope="session")
def app() -> Any:
    a = create_app()
    a.include_router(probe)
    return a


@pytest.fixture
def db() -> Iterator[Session]:
    with get_sessionmaker()() as s:
        yield s


@dataclass
class World:
    company_id: uuid.UUID
    wh_a: uuid.UUID
    wh_b: uuid.UUID
    emails: dict[str, str] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


def build_world() -> World:
    """Fresh synthetic company per test (audit rows are append-only, so no truncation)."""
    tag = uuid.uuid4().hex[:8]
    with get_sessionmaker()() as db, db.begin():
        co = Company(legal_name=f"Synthetic Co {tag}", timezone="UTC")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        a = Warehouse(company_id=co.id, code="WA", name="Warehouse A")
        b = Warehouse(company_id=co.id, code="WB", name="Warehouse B")
        db.add_all([a, b])
        db.flush()
        w = World(co.id, a.id, b.id)
        pw = hash_password(PASSWORD)
        for name, role, wh in [
            ("owner", "OWNER_ADMIN", None),
            ("manager_a", "MANAGER", a.id),
            ("admin_a", "OWNER_ADMIN", a.id),
            ("sales", "SALES_OPERATOR", None),
            ("wh_a", "WAREHOUSE", a.id),
            ("finance", "FINANCE", None),
        ]:
            email = f"{name}.{tag}@example.test"
            emp = Employee(company_id=co.id, email=email, display_name=name, password_hash=pw)
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=wh))
            w.emails[name], w.ids[name] = email, emp.id
    return w


@pytest.fixture
def world() -> World:
    return build_world()


class Api:
    """TestClient wrapper that carries the CSRF token like the web app does."""

    def __init__(self, app: Any) -> None:
        self.c = TestClient(app)
        self.csrf: str | None = None
        self.totp: pyotp.TOTP | None = None
        self.recovery_codes: list[str] = []

    def login(self, email: str, password: str = PASSWORD) -> Any:
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def _h(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        return {**h, **(extra or {})}

    def get(self, url: str, **kw: Any) -> Any:
        return self.c.get(url, **kw)

    def post(self, url: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.c.post(url, json=json, headers=self._h(headers))

    def patch(self, url: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.c.patch(url, json=json, headers=self._h(headers))

    def put(self, url: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.c.put(url, json=json, headers=self._h(headers))

    def delete(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return self.c.delete(url, headers=self._h(headers))

    def enroll_and_verify(self, password: str = PASSWORD) -> Any:
        """Turn on two-step sign-in from My account (optional since 2026-11-09): returns the
        confirm response ({session, recovery_codes}) and keeps the codes on the client."""
        r = self.post("/api/v1/auth/mfa/enroll", json={"password": password})
        assert r.status_code == 200, r.text
        secret = parse_qs(urlparse(r.json()["otpauth_uri"]).query)["secret"][0]
        self.totp = pyotp.TOTP(secret)
        r = self.post("/api/v1/auth/mfa/enroll/confirm", json={"code": self.totp.now()})
        if r.status_code == 200:
            self.recovery_codes = r.json()["recovery_codes"]
        return r

    def verify(self, code: str | None = None) -> Any:
        """Second sign-in step for someone who turned two-step sign-in on."""
        assert self.totp is not None
        return self.post("/api/v1/auth/mfa/verify", json={"code": code or self.totp.now()})


@pytest.fixture
def api(app: Any) -> Api:
    return Api(app)


@pytest.fixture
def make_api(app: Any) -> Any:
    return lambda: Api(app)


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4()}"}


ERR = re.compile(r"^[A-Z_]+$")


def mailed_token(email: str, kind: str = "accept-invite") -> str:
    """The raw token from the latest dev-mailbox message to `email` whose link is `/kind#...`
    (email_messages is the mailbox in dev/test)."""
    from sqlalchemy import select as _select

    from pro_trading.models import EmailMessage

    with get_sessionmaker()() as db:
        bodies = db.scalars(
            _select(EmailMessage.body)
            .where(EmailMessage.to_addr == email)
            .order_by(EmailMessage.created_at.desc())
        ).all()
    for body in bodies:
        m = re.search(rf"/{kind}#token=([A-Za-z0-9_-]+)", body)
        if m:
            return m.group(1)
    raise AssertionError(f"no {kind} email to {email}")
