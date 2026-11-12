"""Celery worker: dispatches the transactional outbox. Run from apps/api:

uv run celery -A pro_trading.worker worker --beat --loglevel=info
"""

from typing import Any

from celery import Celery

from .config import get_settings
from .db import get_sessionmaker
from .documents import DRIVE_EVENT, handle_sync
from .gmail.sync import SYNC_EVENT as GMAIL_SYNC_EVENT
from .gmail.sync import handle_sync as handle_gmail_sync
from .gmail.sync import poll_all as poll_gmail
from .invoicing import EMAIL_EVENT, handle_email
from .notifications import EMAIL_EVENT as NOTIFY_EMAIL_EVENT
from .notifications import handle_email as handle_notify_email
from .ocr import EXTRACT_EVENT, handle_extract
from .order_connect.intake import RECEIVED_EVENT as OC_RECEIVED_EVENT
from .order_connect.intake import handle_received as handle_oc_received
from .order_connect.review import purge_expired as purge_order_connect
from .outbox import Handler, dispatch_pending
from .payments import REMINDER_EVENT, handle_reminder, schedule_reminders
from .search.indexer import drain as drain_search_queue
from .trash import purge_expired as purge_trash_items

app = Celery("pro_trading", broker=get_settings().redis_url)
app.conf.beat_schedule = {
    "dispatch-outbox": {"task": "pro_trading.dispatch_outbox", "schedule": 5.0},
    "payment-reminders": {"task": "pro_trading.schedule_reminders", "schedule": 3600.0},
    "order-connect-retention": {"task": "pro_trading.order_connect_retention", "schedule": 86400.0},
    "purge-trash": {"task": "pro_trading.purge_trash", "schedule": 3600.0},
    # Universal search: rebuild documents for records changed since the last run.
    "search-index": {"task": "pro_trading.search_index", "schedule": 3.0},
    # Gmail Inbox: looks for new mail on a timer (the fallback when no Pub/Sub push arrives,
    # and the catch-up for a push Google dropped); also renews the mailbox watch once a day.
    "gmail-poll": {
        "task": "pro_trading.gmail_poll",
        "schedule": float(get_settings().gmail_poll_seconds),
    },
}

# Module 00 has no external integrations yet; modules register handlers here.
HANDLERS: dict[str, Handler] = {
    EMAIL_EVENT: handle_email,
    EXTRACT_EVENT: handle_extract,
    DRIVE_EVENT: handle_sync,
    REMINDER_EVENT: handle_reminder,
    NOTIFY_EMAIL_EVENT: handle_notify_email,
    OC_RECEIVED_EVENT: handle_oc_received,
    GMAIL_SYNC_EVENT: handle_gmail_sync,
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


@app.task(name="pro_trading.purge_trash")  # type: ignore[untyped-decorator]
def purge_trash(*_: Any) -> int:
    """Delete forever whatever has been in the Trash for 30 days."""
    with get_sessionmaker()() as db:
        return purge_trash_items(db)


@app.task(name="pro_trading.gmail_poll")  # type: ignore[untyped-decorator]
def gmail_poll(*_: Any) -> int:
    """Sync every connected Gmail mailbox (nothing to do, and no Google call, when none is)."""
    return int(poll_gmail()["new"])


@app.task(name="pro_trading.search_index")  # type: ignore[untyped-decorator]
def search_index(*_: Any) -> int:
    """Drain the search change queue (search/indexer.py). Never touches the ERP tables."""
    with get_sessionmaker()() as db:
        return drain_search_queue(db).documents
