"""Google Gemini (Interactions API) client for the OCR reader.

Server-side only: the key comes from GEMINI_API_KEY and is sent in one request header; it is
never logged, stored, put in an error message or returned to the browser.

Every request is metered in `ocr_engine_calls` (no document content): a row is reserved under
an advisory lock before anything is sent, so the API process and every worker together stay
inside the per-minute limit and the daily cap, and a run of failures opens a circuit breaker.
Requests ask Google not to store the interaction (`store: false`).

`transport` is the only function that touches the network; tests replace it.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from .config import get_settings
from .db import get_sessionmaker
from .models import OcrEngineCall

log = logging.getLogger(__name__)

PROVIDER = "gemini"
QUOTA_ZONE = ZoneInfo("America/Los_Angeles")  # Google's daily quota resets at midnight Pacific
THINKING_LEVEL = "low"  # "minimal" is rejected by gemini-3.8-flash (Google's model page)
MAX_REQUEST_BYTES = 18 * 1024 * 1024  # Google's inline limit is 20 MB per request
_LOCK_KEY = 0x47454D4F4352  # advisory lock: one reservation at a time, all processes
FAILED = ("HTTP_ERROR", "TIMEOUT", "BAD_JSON", "BLOCKED")


class GeminiError(Exception):
    """`code` is what the reading's warnings show; `detail` never holds the key or the page."""

    def __init__(self, code: str, detail: str, transient: bool = False) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.transient = code, detail, transient


@dataclass(frozen=True)
class Answer:
    data: Any  # what `validate` returned
    raw: dict[str, Any]  # the JSON object the model wrote (validated)
    input_tokens: int
    output_tokens: int
    total_tokens: int
    seconds: float
    requests: int


@dataclass(frozen=True)
class Budget:
    cap: int
    used_today: int
    remaining_today: int
    per_minute: int
    used_minute: int
    breaker_open_seconds: int  # 0 = closed
    quota_hit_today: bool


def configured() -> bool:
    return bool(get_settings().gemini_api_key.get_secret_value())


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "data": base64.b64encode(jpeg).decode("ascii"),
        "mime_type": "image/jpeg",
    }


def text_part(s: str) -> dict[str, Any]:
    return {"type": "text", "text": s}


# ------------------------------------------------------------------ network (replaced in tests)


def transport(
    url: str, headers: dict[str, str], body: bytes, timeout: int
) -> tuple[int, dict[str, str], bytes]:
    """POST and return (status, headers, body). HTTP errors are returned, not raised."""
    if not url.startswith("https://"):
        raise GeminiError("GEMINI_NOT_CONFIGURED", "endpoint must be https")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}, e.read()


# ------------------------------------------------------------------ metering


def _day_start(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_ZONE)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _state(s: Any, now: datetime) -> tuple[int, bool, int, float, int]:
    """(used today, quota hit today, used this minute, seconds until a minute slot frees,
    seconds the breaker stays open)."""
    st = get_settings()
    day = _day_start(now)
    base = select(func.count()).select_from(OcrEngineCall).where(OcrEngineCall.provider == PROVIDER)
    used = s.scalar(
        base.where(
            OcrEngineCall.created_at >= day,
            OcrEngineCall.outcome.notin_(("RATE_LIMITED", "QUOTA_DAY")),
        )
    )
    quota = bool(
        s.scalar(base.where(OcrEngineCall.created_at >= day, OcrEngineCall.outcome == "QUOTA_DAY"))
    )
    recent = s.scalars(
        select(OcrEngineCall.created_at)
        .where(
            OcrEngineCall.provider == PROVIDER,
            OcrEngineCall.created_at >= now - timedelta(seconds=60),
        )
        .order_by(OcrEngineCall.created_at)
    ).all()
    wait = 0.0
    if len(recent) >= st.gemini_requests_per_minute:
        oldest = recent[len(recent) - st.gemini_requests_per_minute]
        wait = max(0.0, 60.0 - (now - oldest).total_seconds()) + 0.25
    last = s.execute(
        select(OcrEngineCall.outcome, OcrEngineCall.created_at)
        .where(OcrEngineCall.provider == PROVIDER, OcrEngineCall.outcome != "PENDING")
        .order_by(OcrEngineCall.id.desc())
        .limit(st.gemini_breaker_failures)
    ).all()
    open_for = 0
    if len(last) >= st.gemini_breaker_failures and all(o in FAILED for o, _ in last):
        left = st.gemini_breaker_seconds - (now - last[0][1]).total_seconds()
        open_for = max(0, int(left))
    return int(used or 0), quota, len(recent), wait, open_for


def budget() -> Budget:
    st = get_settings()
    with get_sessionmaker()() as s:
        used, quota, minute, _, open_for = _state(s, datetime.now(UTC))
    cap = st.gemini_daily_request_cap
    left = 0 if quota else max(0, cap - used)
    return Budget(cap, used, left, st.gemini_requests_per_minute, minute, open_for, quota)


def usage(extraction_id: uuid.UUID) -> dict[str, int]:
    """What one reading cost so far: every request counts, the failed ones too."""
    with get_sessionmaker()() as s:
        n, tin, tout, tot = s.execute(
            select(
                func.count(),
                func.coalesce(func.sum(OcrEngineCall.input_tokens), 0),
                func.coalesce(func.sum(OcrEngineCall.output_tokens), 0),
                func.coalesce(func.sum(OcrEngineCall.total_tokens), 0),
            ).where(
                OcrEngineCall.extraction_id == extraction_id, OcrEngineCall.provider == PROVIDER
            )
        ).one()
    return {"requests": int(n), "input_tokens": int(tin), "output_tokens": int(tout),
            "total_tokens": int(tot)}  # fmt: skip


def _reserve(
    stage: str, page: int | None, company_id: uuid.UUID | None, extraction_id: uuid.UUID | None,
    deadline: float,
) -> int:  # fmt: skip
    """Wait for a per-minute slot (never past `deadline`) and book the request."""
    st = get_settings()
    while True:
        with get_sessionmaker()() as s, s.begin():
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
            used, quota, _, wait, open_for = _state(s, datetime.now(UTC))
            if open_for:
                raise GeminiError(
                    "GEMINI_CIRCUIT_OPEN", f"paused for {open_for}s after repeated failures", True
                )
            if quota or used >= st.gemini_daily_request_cap:
                raise GeminiError("GEMINI_DAILY_CAP", "today's request allowance is used up")
            if wait <= 0:
                row = OcrEngineCall(
                    company_id=company_id, extraction_id=extraction_id, provider=PROVIDER,
                    model=st.gemini_model, stage=stage, page=page,
                )  # fmt: skip
                s.add(row)
                s.flush()
                return row.id
        if time.monotonic() + wait > deadline:
            raise GeminiError("GEMINI_RATE_LIMIT", "per-minute limit: no slot in time", True)
        time.sleep(wait)


def _finish(
    call_id: int, outcome: str, status: int | None, usage: dict[str, Any], secs: float
) -> None:
    with get_sessionmaker()() as s, s.begin():
        row = s.get(OcrEngineCall, call_id)
        if row is None:
            return
        row.outcome, row.http_status = outcome, status
        row.input_tokens = int(usage.get("total_input_tokens") or 0)
        row.output_tokens = int(usage.get("total_output_tokens") or 0)
        row.total_tokens = int(usage.get("total_tokens") or 0)
        row.seconds = Decimal(f"{secs:.3f}")


# ------------------------------------------------------------------ one validated answer


def _error_info(body: bytes) -> tuple[str, float | None, bool]:
    """(short message, retry delay in seconds, is it the per-day quota)."""
    try:
        err = json.loads(body.decode("utf-8", "replace")).get("error") or {}
    except (ValueError, AttributeError):
        return "", None, False
    blob = json.dumps(err)[:4000]
    daily = "PerDay" in blob or "per day" in blob.lower() or "daily" in blob.lower()
    delay: float | None = None
    for d in err.get("details") or []:
        v = d.get("retryDelay") if isinstance(d, dict) else None
        if isinstance(v, str) and v.endswith("s"):
            try:
                delay = float(v[:-1])
            except ValueError:
                delay = None
    return str(err.get("message") or "")[:200], delay, daily


def _output_text(env: dict[str, Any]) -> str:
    out: list[str] = []
    for step in env.get("steps") or []:
        if isinstance(step, dict) and step.get("type") == "model_output":
            for c in step.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    out.append(str(c.get("text") or ""))
    return "".join(out)


def call(
    stage: str,
    system: str,
    parts: list[dict[str, Any]],
    schema: dict[str, Any],
    validate: Callable[[dict[str, Any]], Any],
    *,
    page: int | None = None,
    company_id: uuid.UUID | None = None,
    extraction_id: uuid.UUID | None = None,
    max_wait: float = 180.0,
) -> Answer:
    """One request (plus bounded retries) whose answer is JSON that passed `validate`.
    Raises GeminiError; the caller falls back to the local reader."""
    st = get_settings()
    key = st.gemini_api_key.get_secret_value()
    if not key:
        raise GeminiError("GEMINI_NOT_CONFIGURED", "GEMINI_API_KEY is not set")
    body = json.dumps(
        {
            "model": st.gemini_model,
            "system_instruction": system,
            "input": parts,
            "generation_config": {"thinking_level": THINKING_LEVEL},
            "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
            "store": False,
        }
    ).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise GeminiError("GEMINI_TOO_LARGE", "the page images are too large to send")
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json",
        "Api-Revision": st.gemini_api_revision,
    }
    deadline = time.monotonic() + max_wait
    started = time.monotonic()
    tin = tout = ttot = requests = 0
    last = GeminiError("GEMINI_UNAVAILABLE", "no answer")
    for attempt in range(st.gemini_max_retries + 1):
        call_id = _reserve(stage, page, company_id, extraction_id, deadline)
        requests += 1
        t0 = time.monotonic()
        pause = min(2.0 * 2**attempt, 20.0)
        try:
            status, _, raw = transport(st.gemini_endpoint, headers, body, st.gemini_timeout_seconds)
        except GeminiError:
            _finish(call_id, "HTTP_ERROR", None, {}, time.monotonic() - t0)
            raise
        except (OSError, TimeoutError) as e:  # URLError is an OSError
            _finish(call_id, "TIMEOUT", None, {}, time.monotonic() - t0)
            last = GeminiError("GEMINI_UNAVAILABLE", type(e).__name__, True)
        else:
            secs = time.monotonic() - t0
            if status == 200:
                try:
                    env = json.loads(raw.decode("utf-8"))
                except ValueError:
                    env = {}
                usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}
                tin += int(usage.get("total_input_tokens") or 0)
                tout += int(usage.get("total_output_tokens") or 0)
                ttot += int(usage.get("total_tokens") or 0)
                if env.get("status") not in (None, "completed"):
                    _finish(call_id, "BLOCKED", status, usage, secs)
                    last = GeminiError("GEMINI_NO_ANSWER", f"status {env.get('status')}"[:80])
                    break
                try:
                    obj = json.loads(_output_text(env))
                    if not isinstance(obj, dict):
                        raise ValueError("not an object")
                    data = validate(obj)
                except ValueError as e:  # pydantic's ValidationError is a ValueError
                    _finish(call_id, "BAD_JSON", status, usage, secs)
                    last = GeminiError("GEMINI_BAD_JSON", type(e).__name__)
                    continue  # ask once more: a second answer is usually well-formed
                _finish(call_id, "OK", status, usage, secs)
                return Answer(data, obj, tin, tout, ttot, time.monotonic() - started, requests)
            msg, delay, daily = _error_info(raw)
            if status == 429:
                _finish(call_id, "QUOTA_DAY" if daily else "RATE_LIMITED", status, {}, secs)
                if daily:
                    raise GeminiError("GEMINI_QUOTA", "Google's daily free allowance is used up")
                last = GeminiError("GEMINI_RATE_LIMIT", "Google asked to slow down", True)
                pause = min(delay if delay is not None else 15.0 * 2**attempt, 65.0)
            elif status >= 500:
                _finish(call_id, "HTTP_ERROR", status, {}, secs)
                last = GeminiError("GEMINI_UNAVAILABLE", f"HTTP {status}", True)
            else:
                _finish(call_id, "HTTP_ERROR", status, {}, secs)
                code = "GEMINI_NOT_CONFIGURED" if status in (401, 403) else "GEMINI_REJECTED"
                log.warning("gemini request rejected", extra={"ctx": {"status": status}})
                raise GeminiError(code, f"HTTP {status} {msg}"[:200])
        if attempt < st.gemini_max_retries:
            if time.monotonic() + pause > deadline:
                break
            time.sleep(pause)
    raise last
