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


def record_failure(bucket: str, ident: str, window_seconds: int) -> None:
    try:
        pipe = _client().pipeline()
        pipe.incr(_key(bucket, ident))
        pipe.expire(_key(bucket, ident), window_seconds, nx=True)
        pipe.execute()
    except redis.RedisError as exc:
        log.warning("ratelimit write failed", extra={"ctx": {"error": type(exc).__name__}})


def clear(bucket: str, ident: str) -> None:
    try:
        _client().delete(_key(bucket, ident))
    except redis.RedisError:
        pass
