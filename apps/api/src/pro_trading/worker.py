"""Celery worker: dispatches the transactional outbox. Run from apps/api:

uv run celery -A pro_trading.worker worker --beat --loglevel=info
"""

from typing import Any

from celery import Celery

from .config import get_settings
from .db import get_sessionmaker
from .documents import DRIVE_EVENT, handle_sync
from .invoicing import EMAIL_EVENT, handle_email
from .ocr import EXTRACT_EVENT, handle_extract
from .outbox import Handler, dispatch_pending

app = Celery("pro_trading", broker=get_settings().redis_url)
app.conf.beat_schedule = {
    "dispatch-outbox": {"task": "pro_trading.dispatch_outbox", "schedule": 5.0}
}

# Module 00 has no external integrations yet; modules register handlers here.
HANDLERS: dict[str, Handler] = {
    EMAIL_EVENT: handle_email,
    EXTRACT_EVENT: handle_extract,
    DRIVE_EVENT: handle_sync,
}


@app.task(name="pro_trading.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox(*_: Any) -> int:
    with get_sessionmaker()() as db:
        return dispatch_pending(db, HANDLERS)
