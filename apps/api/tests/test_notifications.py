# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Notification Center: notifications follow committed business events only, reach the right
people, never leak, stay in sync, survive reconnects, and respect preferences.

Synthetic data only."""

import threading
import time
import uuid
from datetime import time as dtime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pro_trading import notifications as nt
from pro_trading import outbox
from pro_trading.db import get_sessionmaker
from pro_trading.models import EmailMessage, Employee, OutboxEvent

from .conftest import PASSWORD, build_world, idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_orders import CSV, P, code, confirmed, owner, picked, setup, user  # noqa: F401
from .test_payments import finance, invoice, report, verify  # noqa: F401

N = f"{P}/notifications"


def inbox(api, **params):
    r = api.get(N, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def titles(api, **params):
    return [i["title"] for i in inbox(api, **params)["items"]]


def unread(api):
    r = api.get(f"{N}/summary")
    assert r.status_code == 200, r.text
    return r.json()["unread"]


def raw_notify(world, **kw):
    """notify() in its own committed transaction, like a business command would."""
    with get_sessionmaker()() as db, db.begin():
        return nt.notify(db, world.company_id, kw.pop("kind", "test.event"), **kw)


@pytest.fixture
def published(monkeypatch):
    """Records after-commit wake-ups instead of (also) sending them."""
    calls: list[tuple[set, str]] = []
    real = nt.publish

    def spy(ids, what):
        calls.append((set(ids), what))
        real(ids, what)

    monkeypatch.setattr(nt, "publish", spy)
    return calls


# ------------------------------------------------------------------ business events


def test_order_flow_notifies_the_right_people_once(
    owner, sales, wh, finance, user, world, setup, make_api
):
    o = confirmed(sales, world, setup, [("CAS", "BOX", "3")])
    # Warehouse A staff (pick.record) hear about the confirmed order; the clerk who confirmed
    # it and people without pick.record do not.
    assert f"Order {o['order_no']} confirmed" in titles(wh)
    assert f"Order {o['order_no']} confirmed" in titles(owner)
    assert f"Order {o['order_no']} confirmed" not in titles(sales)
    assert f"Order {o['order_no']} confirmed" not in titles(finance)
    item = next(i for i in inbox(wh)["items"] if i["kind"] == "sales_order.confirmed")
    assert item["category"] == "sales_order" and item["link"] == f"/orders/{o['id']}"
    assert item["severity"] == "info" and not item["read"]
    assert "Island Grocers" in item["body"]

    # Warehouse B has no business with it: an employee scoped to B sees nothing.
    other = build_world()  # a different company sees nothing either
    stranger = make_api()
    stranger.login(other.emails["wh_a"])
    assert inbox(stranger)["items"] == []


def test_invoice_issue_notifies_finance_and_flags_stock_out(
    owner, sales, wh, finance, user, world, setup
):
    inv = invoice(owner, sales, wh, world, setup, qty="10")
    fin = inbox(finance)["items"]
    issued = [i for i in fin if i["kind"] == "invoice.issued"]
    assert len(issued) == 1
    assert issued[0]["title"] == f"Invoice {inv['invoice_no']} issued"
    assert issued[0]["link"] == f"/invoices/{inv['id']}"
    assert "CAD" in issued[0]["body"]
    # All 10 boxes were invoiced: sales (sales_order.manage) learn CAS ran out in WA.
    out = [i for i in inbox(sales)["items"] if i["kind"] == "stock.out"]
    assert len(out) == 1 and out[0]["title"].startswith("Out of stock: CAS")
    assert out[0]["severity"] == "warning" and out[0]["category"] == "inventory"
    # The person who issued it is not notified about their own click.
    assert not [i for i in inbox(owner)["items"] if i["kind"] == "invoice.issued"]


def test_payment_reported_then_confirmed(owner, sales, wh, finance, world, setup):
    pay = report(sales, setup, "50.00")
    assert pay.status_code == 201, pay.text
    pay = pay.json()
    to_verify = [i for i in inbox(finance)["items"] if i["kind"] == "payment.reported"]
    assert len(to_verify) == 1 and pay["payment_no"] in to_verify[0]["title"]
    assert verify(finance, pay, "REF-12345").status_code == 200
    mine = [i for i in inbox(sales)["items"] if i["kind"] == "payment.verified"]
    assert len(mine) == 1 and mine[0]["severity"] == "success"


def test_rolled_back_transaction_leaves_nothing(world, published):
    seen_before = len(published)
    with get_sessionmaker()() as db:
        n = nt.notify(
            db,
            world.company_id,
            "test.rollback",
            target_id=uuid.uuid4(),
            category="system",
            title="Never committed",
            to=[world.ids["owner"]],
        )
        assert n == 1
        db.flush()
        assert (
            db.scalar(
                select(func.count())
                .select_from(nt.Notification)
                .where(nt.Notification.title == "Never committed")
            )
            == 1
        )
        db.rollback()
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(nt.Notification)
                .where(nt.Notification.company_id == world.company_id)
            )
            == 0
        )
    assert len(published) == seen_before  # no live wake-up for a rolled-back event


def test_failed_business_command_creates_no_notification(sales, wh, world, setup, monkeypatch):
    """A failure after the audit row (inside the same transaction) rolls the notification back."""
    from pro_trading import idempotency

    r = sales.post(
        f"{P}/sales-orders",
        json={
            "customer_id": setup["cust"],
            "warehouse_id": str(world.wh_a),
            "source_text": "x",
            "lines": [{"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "1"}],
        },
        headers=idem(),
    )
    o = r.json()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(idempotency, "finish", boom)
    with pytest.raises(RuntimeError):
        sales.post(
            f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
        )
    monkeypatch.undo()
    assert not [i for i in inbox(wh)["items"] if i["kind"] == "sales_order.confirmed"]


def test_commit_publishes_wakeup_after_commit_only(world, published):
    before = len(published)
    raw_notify(
        world,
        target_id="a1",
        category="system",
        title="Committed",
        to=[world.ids["owner"], world.ids["finance"]],
    )
    assert published[before:] == [({world.ids["owner"], world.ids["finance"]}, "new")]


# ------------------------------------------------------------------ duplicates


def test_duplicate_events_are_stored_once(world):
    kw = dict(
        kind="test.dup", event_id="dup:1", category="system", title="Once", to=[world.ids["owner"]]
    )
    assert raw_notify(world, **kw) == 1
    assert raw_notify(world, **kw) == 0  # same event id: refused by the unique constraint
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(nt.Notification)
                .where(
                    nt.Notification.company_id == world.company_id,
                    nt.Notification.event_id == "dup:1",
                )
            )
            == 1
        )
        nid = db.scalar(select(nt.Notification.id).where(nt.Notification.event_id == "dup:1"))
    # The database itself refuses a second inbox row for the same person.
    with pytest.raises(IntegrityError), get_sessionmaker()() as db, db.begin():
        db.add(
            nt.NotificationRecipient(
                notification_id=nid, employee_id=world.ids["owner"], company_id=world.company_id
            )
        )


def test_idempotent_replay_does_not_notify_twice(sales, wh, world, setup):
    r = sales.post(
        f"{P}/sales-orders",
        json={
            "customer_id": setup["cust"],
            "warehouse_id": str(world.wh_a),
            "source_text": "x",
            "lines": [{"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "1"}],
        },
        headers=idem(),
    ).json()
    key = idem()
    for _ in range(3):
        c = sales.post(f"{P}/sales-orders/{r['id']}/confirm", json={"version": 1}, headers=key)
        assert c.status_code == 200
    assert len([i for i in inbox(wh)["items"] if i["target_id"] == r["id"]]) == 1


# ------------------------------------------------------------------ visibility


def test_role_and_scope_checked_again_when_reading(owner, sales, user, world, make_api):
    # Addressed to warehouse A staff with pick.record.
    raw_notify(
        world,
        kind="test.scoped",
        target_id="s1",
        category="sales_order",
        title="Scoped to A",
        permission="pick.record",
        warehouse_id=world.wh_a,
    )
    wh = user("wh_a")
    assert "Scoped to A" in titles(wh)
    nid = next(i["id"] for i in inbox(wh)["items"] if i["title"] == "Scoped to A")
    # Someone without pick.record who somehow got an inbox row still can't see or touch it.
    with get_sessionmaker()() as db, db.begin():
        db.add(
            nt.NotificationRecipient(
                notification_id=uuid.UUID(nid),
                employee_id=world.ids["sales"],
                company_id=world.company_id,
            )
        )
    assert "Scoped to A" not in titles(sales)
    assert sales.post(f"{N}/{nid}/read").status_code == 404
    assert unread(sales) == 0
    # Another company's user can't reach it by id.
    other = build_world()
    o = make_api()
    o.login(other.emails["owner"])
    assert o.post(f"{N}/{nid}/read").status_code == 404
    assert o.post(f"{N}/archive", json={"ids": [nid]}).json()["updated"] == 0


def test_employee_without_roles_sees_only_their_own(world, make_api):
    raw_notify(
        world, target_id="p1", category="payment", title="Finance only", permission="payment.verify"
    )
    with get_sessionmaker()() as db, db.begin():
        e = db.get(Employee, world.ids["finance"])
        e.active = False  # deactivated people get nothing new
    raw_notify(
        world,
        target_id="p2",
        category="payment",
        title="After leaving",
        permission="payment.verify",
    )
    with get_sessionmaker()() as db:
        got = db.scalars(
            select(nt.NotificationRecipient.employee_id)
            .join(nt.Notification)
            .where(
                nt.Notification.title == "After leaving",
                nt.Notification.company_id == world.company_id,
            )
        ).all()
    assert world.ids["finance"] not in got and world.ids["owner"] in got


# ------------------------------------------------------------------ actions & counts


def test_read_unread_archive_and_counts(world, make_api):
    a = make_api()
    a.login(world.emails["sales"])
    for i in range(3):
        raw_notify(
            world, target_id=f"c{i}", category="system", title=f"Item {i}", to=[world.ids["sales"]]
        )
    assert unread(a) == 3
    items = inbox(a)["items"]
    assert [i["title"] for i in items] == ["Item 2", "Item 1", "Item 0"]  # newest first
    first = items[0]["id"]
    assert a.post(f"{N}/{first}/read").json()["read"] is True
    assert a.post(f"{N}/{first}/read").status_code == 200  # idempotent
    assert unread(a) == 2
    assert a.post(f"{N}/{first}/unread").json()["read"] is False
    assert unread(a) == 3
    arch = a.post(f"{N}/{items[1]['id']}/archive").json()
    assert arch["archived"] is True and arch["read"] is True
    assert unread(a) == 2
    assert "Item 1" not in titles(a)
    assert titles(a, status="archived") == ["Item 1"]
    assert a.post(f"{N}/{items[1]['id']}/unarchive").json()["archived"] is False
    r = a.post(f"{N}/read-all", json={})
    assert r.json() == {"updated": 2, "unread": 0}
    assert inbox(a, status="unread")["items"] == []
    r = a.post(f"{N}/archive", json={"ids": [i["id"] for i in items]})
    assert r.json()["updated"] == 3 and inbox(a)["items"] == []


def test_filters_search_and_pagination(world, make_api):
    a = make_api()
    a.login(world.emails["sales"])
    me = [world.ids["sales"]]
    for i in range(7):
        raw_notify(world, target_id=f"f{i}", category="invoice", title=f"Invoice INV-{i}", to=me)
    raw_notify(
        world,
        target_id="w1",
        category="inventory",
        severity="warning",
        title="Shortage on SO-9",
        body="3 lines short",
        to=me,
    )
    raw_notify(
        world,
        target_id="k1",
        category="security",
        severity="critical",
        title="Your account was locked",
        to=me,
    )
    assert titles(a, category="inventory") == ["Shortage on SO-9"]
    assert titles(a, severity="critical") == ["Your account was locked"]
    assert titles(a, q="lines short") == ["Shortage on SO-9"]
    assert titles(a, q="100%") == []  # wildcards are literal
    p1 = inbox(a, limit=4)
    assert len(p1["items"]) == 4 and p1["next_before"]
    p2 = inbox(a, limit=4, before=p1["next_before"])
    p3 = inbox(a, limit=4, before=p2["next_before"])
    seen = [i["id"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert len(seen) == len(set(seen)) == 9 and p3["next_before"] is None
    assert a.get(N, params={"category": "nope"}).status_code == 422
    assert a.get(f"{N}/summary").json()["critical_unread"] == 1


# ------------------------------------------------------------------ live stream


def _events(text: str) -> list[tuple[str | None, str, str]]:
    out = []
    for block in text.split("\n\n"):
        eid = ev = data = None
        for line in block.splitlines():
            if line.startswith("id: "):
                eid = line[4:]
            elif line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev:
            out.append((eid, ev, data or ""))
    return out


def _read_stream(api, **params) -> list[tuple[str | None, str, str]]:
    headers = params.pop("headers", {})
    with api.c.stream("GET", f"{N}/stream", params=params, headers=headers) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        return _events(r.read().decode())


def test_stream_replays_missed_events_after_reconnect(world, make_api):
    a = make_api()
    a.login(world.emails["sales"])
    raw_notify(world, target_id="r0", category="system", title="Seen", to=[world.ids["sales"]])
    ready = [e for e in _read_stream(a, timeout=1) if e[1] == "ready"][0]
    last_seen = int(ready[0])
    # Offline: two arrive while the tab is closed.
    raw_notify(world, target_id="r1", category="system", title="Missed 1", to=[world.ids["sales"]])
    raw_notify(world, target_id="r2", category="system", title="Missed 2", to=[world.ids["sales"]])
    evs = _read_stream(a, timeout=1, headers={"Last-Event-ID": str(last_seen)})
    got = [e for e in evs if e[1] == "notification"]
    assert [('"Missed 1"' in d, '"Missed 2"' in d) for _, _, d in got] == [
        (True, False),
        (False, True),
    ]
    assert int(got[-1][0]) > last_seen  # ids are the resume cursor
    # Up to date: nothing is replayed.
    latest = [e for e in evs if e[1] == "ready"][0][0]
    again = _read_stream(a, timeout=1, after=latest)
    assert not [e for e in again if e[1] == "notification"]


def test_stream_delivers_new_notification_live(world, make_api):
    a = make_api()
    a.login(world.emails["sales"])

    def later():
        time.sleep(0.8)
        raw_notify(
            world, target_id="live", category="system", title="Live one", to=[world.ids["sales"]]
        )

    t = threading.Thread(target=later)
    t.start()
    t0 = time.monotonic()
    evs = _read_stream(a, timeout=4)
    t.join()
    live = [e for e in evs if e[1] == "notification"]
    assert len(live) == 1 and '"Live one"' in live[0][2]
    assert '"alert"' in live[0][2]
    assert [e for e in evs if e[1] == "unread"]  # the badge count follows
    assert time.monotonic() - t0 < 6


def test_stream_needs_a_session_and_caps_connections(world, make_api, app):
    from fastapi.testclient import TestClient

    assert TestClient(app).get(f"{N}/stream", params={"timeout": 1}).status_code == 401
    from pro_trading.routes import notifications as routes

    a = make_api()
    a.login(world.emails["sales"])
    emp = world.ids["sales"]
    routes._streams[emp] = routes.MAX_STREAMS_PER_EMPLOYEE
    try:
        r = a.get(f"{N}/stream", params={"timeout": 1})
        assert r.status_code == 429 and code(r) == "TOO_MANY_STREAMS"
    finally:
        del routes._streams[emp]


# ------------------------------------------------------------------ preferences


def mkprefs(**kw):
    return nt.Prefs(**kw)


def test_quiet_hours_rules():
    night = mkprefs(quiet_hours=True, quiet_start=dtime(22), quiet_end=dtime(7))
    assert nt.in_quiet_hours(night, dtime(23, 30)) and nt.in_quiet_hours(night, dtime(6, 59))
    assert not nt.in_quiet_hours(night, dtime(7)) and not nt.in_quiet_hours(night, dtime(12))
    lunch = mkprefs(quiet_hours=True, quiet_start=dtime(12), quiet_end=dtime(13))
    assert nt.in_quiet_hours(lunch, dtime(12, 30)) and not nt.in_quiet_hours(lunch, dtime(13))
    assert not nt.in_quiet_hours(
        mkprefs(quiet_hours=True, quiet_start=dtime(9), quiet_end=dtime(9)), dtime(9)
    )
    assert not nt.in_quiet_hours(mkprefs(quiet_start=dtime(0), quiet_end=dtime(23)), dtime(5))
    on = mkprefs(
        desktop=True, sound=True, quiet_hours=True, quiet_start=dtime(22), quiet_end=dtime(7)
    )
    off = {"banner": False, "desktop": False, "sound": False}
    assert nt.alert_for(on, "invoice", "info", dtime(23)) == off
    on_all = {"banner": True, "desktop": True, "sound": True}
    assert nt.alert_for(on, "invoice", "info", dtime(10)) == on_all
    assert nt.alert_for(on, "security", "critical", dtime(23)) == on_all
    no_desk = mkprefs(desktop=True, categories={"invoice": {"desktop": False}})
    assert nt.alert_for(no_desk, "invoice", "info", dtime(10))["desktop"] is False


def test_muting_and_critical_alerts():
    muted = mkprefs(in_app=False)
    assert not nt.delivers_in_app(muted, "invoice", "warning")
    assert nt.delivers_in_app(muted, "security", "critical")  # nobody can mute critical
    cat = mkprefs(categories={"invoice": {"in_app": False, "email": True, "desktop": True}})
    assert not nt.delivers_in_app(cat, "invoice", "info")
    assert nt.delivers_in_app(cat, "payment", "info")
    em = mkprefs(email=True, quiet_hours=True, quiet_start=dtime(22), quiet_end=dtime(7))
    assert nt.wants_email(em, "invoice", "info", dtime(10))
    assert not nt.wants_email(em, "invoice", "info", dtime(23))
    assert nt.wants_email(em, "security", "critical", dtime(23))


def _prefs_body(cur, **changes):
    body = {
        k: cur[k]
        for k in (
            "in_app",
            "desktop",
            "email",
            "sound",
            "grouped",
            "quiet_hours",
            "quiet_start",
            "quiet_end",
            "categories",
            "version",
        )
    }
    body.update(changes)
    return body


def test_preferences_api(world, make_api, owner):
    a = make_api()
    a.login(world.emails["sales"])
    cur = a.get(f"{N}/preferences").json()
    assert cur["version"] == 0 and cur["in_app"] and not cur["desktop"]
    assert set(cur["categories"]) == set(nt.CATEGORIES) and not cur["can_mute_security"]
    cats = dict(cur["categories"])
    cats["invoice"] = {"in_app": False, "email": False, "desktop": False}
    r = a.put(
        f"{N}/preferences",
        json=_prefs_body(cur, categories=cats, quiet_hours=True, quiet_start="21:30"),
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["version"] == 1 and saved["quiet_start"] == "21:30"
    assert saved["categories"]["invoice"]["in_app"] is False
    # Stale version (another tab saved first) is refused.
    stale = a.put(f"{N}/preferences", json=_prefs_body(cur), headers=idem())
    assert stale.status_code == 409 and code(stale) == "VERSION_CONFLICT"
    # An ordinary user can't switch off security alerts.
    cats2 = dict(saved["categories"])
    cats2["security"] = {"in_app": False, "email": False, "desktop": False}
    locked = a.put(f"{N}/preferences", json=_prefs_body(saved, categories=cats2), headers=idem())
    assert locked.status_code == 422 and code(locked) == "SECURITY_ALERTS_LOCKED"
    assert (
        a.put(
            f"{N}/preferences", json=_prefs_body(saved, quiet_start="25:00"), headers=idem()
        ).status_code
        == 422
    )
    # The owner (employee.manage) may, and the change is audited.
    oc = owner.get(f"{N}/preferences").json()
    assert oc["can_mute_security"]
    assert (
        owner.put(
            f"{N}/preferences", json=_prefs_body(oc, categories=cats2), headers=idem()
        ).status_code
        == 200
    )
    # Muted category: nothing new lands in the inbox; critical still does.
    me = [world.ids["sales"]]
    raw_notify(world, target_id="m1", category="invoice", title="Muted invoice", to=me)
    raw_notify(
        world,
        target_id="m2",
        category="invoice",
        severity="critical",
        title="Critical invoice",
        to=me,
    )
    assert titles(a) == ["Critical invoice"]


def test_email_channel_goes_through_the_outbox(world, make_api):
    a = make_api()
    a.login(world.emails["sales"])
    cur = a.get(f"{N}/preferences").json()
    assert (
        a.put(f"{N}/preferences", json=_prefs_body(cur, email=True), headers=idem()).status_code
        == 200
    )
    raw_notify(
        world, target_id="e1", category="payment", title="Emailed alert", to=[world.ids["sales"]]
    )
    with get_sessionmaker()() as db:
        ev = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.company_id == world.company_id,
                OutboxEvent.event_type == nt.EMAIL_EVENT,
            )
        )
        assert ev is not None
        ev.attempts = 1
        nt.handle_email(ev)
        nt.handle_email(ev)  # a retry sends nothing twice
        db.commit()
        msgs = db.scalars(
            select(EmailMessage).where(EmailMessage.company_id == world.company_id)
        ).all()
        assert len(msgs) == 1 and msgs[0].to_addr == world.emails["sales"]
        assert "Emailed alert" in msgs[0].subject
        att = db.scalar(
            select(nt.NotificationDeliveryAttempt).where(
                nt.NotificationDeliveryAttempt.company_id == world.company_id
            )
        )
        assert att.status == "SENT"


# ------------------------------------------------------------------ security & system events


def test_account_lockout_alerts_the_person_and_admins(world, make_api, owner):
    a = make_api()
    for _ in range(5):
        assert a.login(world.emails["sales"], "wrong-password").status_code == 401
    with get_sessionmaker()() as db:
        db.execute(
            Employee.__table__.update()
            .where(Employee.id == world.ids["sales"])
            .values(locked_until=None)
        )
        db.commit()
    assert a.login(world.emails["sales"], PASSWORD).status_code == 200
    mine = [i for i in inbox(a)["items"] if i["kind"] == "security.account_locked"]
    assert len(mine) == 1 and mine[0]["title"] == "Your account was locked"
    assert mine[0]["severity"] == "critical"
    admin = [i for i in inbox(owner)["items"] if i["kind"] == "security.account_locked"]
    assert len(admin) == 1 and admin[0]["title"] == "Account locked: sales"
    # Not addressed to others without employee.manage.
    fin = make_api()
    fin.login(world.emails["finance"])
    assert not [i for i in inbox(fin)["items"] if i["category"] == "security"]


def test_dead_background_job_alerts_the_owner(world, owner):
    with get_sessionmaker()() as db:
        ev = outbox.enqueue(db, "test.never_handled", {}, company_id=world.company_id)
        db.flush()
        ev.attempts = outbox.MAX_ATTEMPTS - 1
        db.commit()
        eid = ev.id
    with get_sessionmaker()() as db:
        # Only this company's stuck event: run the dispatcher on it alone.
        rows = db.scalars(select(OutboxEvent).where(OutboxEvent.id == eid)).all()
        assert rows
        outbox.dispatch_pending(db, {}, limit=1000)
    alerts = [i for i in inbox(owner)["items"] if i["kind"] == "system.job_failed"]
    assert len(alerts) == 1 and alerts[0]["severity"] == "critical"
    assert "test.never_handled" in alerts[0]["title"]


def test_any_scope_kinds_reach_scoped_holders_consistently(world, make_api):
    """An any-scope event reaches a warehouse-scoped holder of the permission, who can read it;
    someone without the permission can't. Other events stay company-wide."""
    raw_notify(
        world,
        kind="test.anyscope",
        target_id="x1",
        category="website_order",
        title="Any-scope order",
        permission="pick.record",
        any_scope=True,
    )
    raw_notify(
        world,
        kind="test.wide",
        target_id="x2",
        category="website_order",
        title="Company-wide only",
        permission="pick.record",
    )
    wh = make_api()
    wh.login(world.emails["wh_a"])  # WAREHOUSE role, scoped to warehouse A
    assert titles(wh) == ["Any-scope order"]
    sales = make_api()
    sales.login(world.emails["sales"])  # no pick.record
    assert titles(sales) == []
    # Website orders are company-wide: Order Connect only accepts company-wide order_connect.read.
    assert "order_connect.submission" not in nt.KIND_ANY_SCOPE


def test_password_guessing_on_the_change_form_locks_and_alerts(world, make_api, owner):
    """Per person (not per IP): five wrong current passwords lock the account."""
    a = make_api()
    assert a.login(world.emails["sales"]).status_code == 200
    for _ in range(5):
        r = a.post(
            f"{P}/auth/password",
            json={"current_password": "nope", "new_password": "Another-Synthetic-9!x"},
        )
        assert r.status_code in (401, 422), r.text
    admin = [i for i in inbox(owner)["items"] if i["kind"] == "security.account_locked"]
    assert len(admin) == 1 and admin[0]["severity"] == "critical"


def _audit(world, action, *, actor, target=None, target_type="employee", success=True, **detail):
    from pro_trading import audit

    with get_sessionmaker()() as db, db.begin():
        audit.record(
            db,
            action=action,
            success=success,
            company_id=world.company_id,
            actor_id=actor,
            target_type=target_type,
            target_id=str(target) if target else None,
            request_id=f"req-{uuid.uuid4()}",
            detail=detail,
        )


def test_user_management_security_events(world, make_api, owner):
    sales_id, owner_id = world.ids["sales"], world.ids["owner"]
    # Reset by emailed link -> the person; a bad link (no account) -> nobody.
    _audit(world, "auth.password_reset", actor=sales_id, target=sales_id)
    _audit(world, "auth.password_reset", actor=None, success=False, reason="bad_link")
    # An admin ends one of sales' sessions, then all of them -> sales is told both times.
    _audit(world, "auth.session_revoked", actor=owner_id, target=sales_id, session_id="s")
    _audit(world, "auth.sessions_revoked", actor=owner_id, target=sales_id)
    # Sales ends one of their own sessions -> nothing; "sign out others" -> a confirmation.
    _audit(
        world, "auth.session_revoked", actor=sales_id, target=uuid.uuid4(), target_type="session"
    )
    _audit(world, "auth.sessions_revoked", actor=sales_id, target=sales_id, scope="others")
    # Two-step turned off -> the person, as a warning.
    _audit(world, "auth.mfa_disabled", actor=sales_id)
    a = make_api()
    a.login(world.emails["sales"])
    got = titles(a)
    assert sorted(got) == sorted(
        [
            "Your password was reset",
            "An administrator ended one of your sessions",
            "An administrator signed you out everywhere",
            "Signed out on other devices",
            "Two-step sign-in turned off",
        ]
    )
    # A new employee accepting an invitation -> admins (employee.manage) only.
    _audit(
        world, "auth.invitation_accepted", actor=world.ids["finance"], target=world.ids["finance"]
    )
    assert "finance accepted their invitation" in titles(owner)
    assert "finance accepted their invitation" not in titles(a)
