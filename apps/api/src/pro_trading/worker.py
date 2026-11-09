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
from .outbox import Handler, dispatch_pending
from .payments import REMINDER_EVENT, handle_reminder, schedule_reminders

app = Celery("pro_trading", broker=get_settings().redis_url)
app.conf.beat_schedule = {
    "dispatch-outbox": {"task": "pro_trading.dispatch_outbox", "schedule": 5.0},
    "payment-reminders": {"task": "pro_trading.schedule_reminders", "schedule": 3600.0},
}

# Module 00 has no external integrations yet; modules register handlers here.
HANDLERS: dict[str, Handler] = {
    EMAIL_EVENT: handle_email,
    EXTRACT_EVENT: handle_extract,
    DRIVE_EVENT: handle_sync,
    REMINDER_EVENT: handle_reminder,
    NOTIFY_EMAIL_EVENT: handle_notify_email,
}


@app.task(name="pro_trading.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox(*_: Any) -> int:
    with get_sessionmaker()() as db:
        return dispatch_pending(db, HANDLERS)


@app.task(name="pro_trading.schedule_reminders")  # type: ignore[untyped-decorator]
def schedule_payment_reminders(*_: Any) -> int:
    with get_sessionmaker()() as db:
        return schedule_reminders(db)
