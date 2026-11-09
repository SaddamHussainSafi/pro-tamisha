"""Fixed-window failure counters in Redis (e.g. failed sign-ins per client IP).

Fails open: if Redis is unreachable the request is allowed and a warning is logged, so a Redis
outage cannot lock everyone out. Per-account lockout (employees.locked_until) and the MFA
attempt limit (audit-based, see routes/auth.py) do not depend on Redis."""

import logging
from functools import lru_cache

import redis

from .config import get_settings

log = logging.getLogger("pro_trading.ratelimit")


@lru_cache
def _client() -> "redis.Redis":
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5)


def _key(bucket: str, ident: str) -> str:
    return f"pt:{get_settings().app_env}:rl:{bucket}:{ident}"


def failures(bucket: str, ident: str) -> int:
    try:
        v = _client().get(_key(bucket, ident))
    except redis.RedisError as exc:
        log.warning("ratelimit read failed", extra={"ctx": {"error": type(exc).__name__}})
        return 0
    return int(v) if v else 0


def attempt(bucket: str, ident: str, window_seconds: int) -> int:
    """Count this attempt *before* doing the work and return the new total (0 if Redis is
    down). Counting first means parallel requests can't all slip under the limit together;
    call `undo` when the attempt turns out fine (a correct password, a known scanner)."""
    try:
        pipe = _client().pipeline()
        pipe.incr(_key(bucket, ident))
        pipe.expire(_key(bucket, ident), window_seconds, nx=True)
        n, _ = pipe.execute()
        return int(n)
    except redis.RedisError as exc:
        log.warning("ratelimit write failed", extra={"ctx": {"error": type(exc).__name__}})
        return 0


# Decrement only while the window's key still exists (never below the window's start).
_UNDO = "if redis.call('exists', KEYS[1]) == 1 then return redis.call('decr', KEYS[1]) end return 0"


def undo(bucket: str, ident: str) -> None:
    try:
        _client().eval(_UNDO, 1, _key(bucket, ident))
    except redis.RedisError as exc:
        log.warning("ratelimit undo failed", extra={"ctx": {"error": type(exc).__name__}})


def clear(bucket: str, ident: str) -> None:
    try:
        _client().delete(_key(bucket, ident))
    except redis.RedisError:
        pass
