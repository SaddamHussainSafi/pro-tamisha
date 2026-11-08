"""Idempotency-Key handling. The record is inserted in the same transaction as the mutation,
so a concurrent duplicate blocks on the unique index and then replays the stored response."""

import hashlib
import json
from typing import Annotated, Any

from fastapi import Header
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import IdempotencyRecord

IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=128)]


def request_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def begin(db: Session, scope: str, key: str, payload: Any) -> tuple[int, dict[str, Any]] | None:
    """Claim the key. Returns a stored (status, body) to replay, or None to proceed."""
    h = request_hash(payload)
    claimed = db.execute(
        insert(IdempotencyRecord)
        .values(scope=scope, idem_key=key, request_hash=h)
        .on_conflict_do_nothing(index_elements=["scope", "idem_key"])
        .returning(IdempotencyRecord.id)
    ).first()
    if claimed:
        return None
    rec = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.idem_key == key
        )
    )
    assert rec is not None
    if rec.request_hash != h:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key reused with different body")
    if rec.response_status is None or rec.response_body is None:
        raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "Request with this key is in progress")
    return rec.response_status, rec.response_body


def finish(db: Session, scope: str, key: str, status: int, body: dict[str, Any]) -> None:
    rec = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.idem_key == key
        )
    )
    assert rec is not None
    rec.response_status, rec.response_body = status, body
