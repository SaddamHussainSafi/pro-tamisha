"""Celery worker: dispatches the transactional outbox. Run from apps/api:

uv run celery -A pro_trading.worker worker --beat --loglevel=info
"""

from typing import Any

from celery import Celery

from .config import get_settings
from .db import get_sessionmaker
from .documents import DRIVE_EVENT, handle_sync
from .invoicing import EMAIL_EVENT, handle_email
from .notifications import EMAIL_EVENT as NOTIFY_EMAIL_EVENT
from .notifications import handle_email as handle_notify_email
from .ocr import EXTRACT_EVENT, handle_extract
from .order_connect.intake import RECEIVED_EVENT as OC_RECEIVED_EVENT
from .order_connect.intake import handle_received as handle_oc_received
from .order_connect.review import purge_expired as purge_order_connect
from .outbox import Handler, dispatch_pending
from .payments import REMINDER_EVENT, handle_reminder, schedule_reminders

app = Celery("pro_trading", broker=get_settings().redis_url)
app.conf.beat_schedule = {
    "dispatch-outbox": {"task": "pro_trading.dispatch_outbox", "schedule": 5.0},
    "payment-reminders": {"task": "pro_trading.schedule_reminders", "schedule": 3600.0},
    "order-connect-retention": {"task": "pro_trading.order_connect_retention", "schedule": 86400.0},
}

# Module 00 has no external integrations yet; modules register handlers here.
HANDLERS: dict[str, Handler] = {
    EMAIL_EVENT: handle_email,
    EXTRACT_EVENT: handle_extract,
    DRIVE_EVENT: handle_sync,
    REMINDER_EVENT: handle_reminder,
    NOTIFY_EMAIL_EVENT: handle_notify_email,
    OC_RECEIVED_EVENT: handle_oc_received,
}


@app.task(name="pro_trading.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox(*_: Any) -> int:
    with get_sessionmaker()() as db:
        return dispatch_pending(db, HANDLERS)


@app.task(name="pro_trading.schedule_reminders")  # type: ignore[untyped-decorator]
def schedule_payment_reminders(*_: Any) -> int:
    with get_sessionmaker()() as db:
        return schedule_reminders(db)


@app.task(name="pro_trading.order_connect_retention")  # type: ignore[untyped-decorator]
def order_connect_retention(*_: Any) -> int:
    """Erase personal data of finished website submissions past their retention period."""
    with get_sessionmaker()() as db:
        return purge_order_connect(db)
