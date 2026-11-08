from typing import Any

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..config import get_settings
from ..db import get_engine

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> JSONResponse:
    checks: dict[str, Any] = {}
    try:
        with get_engine().connect() as c:
            rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = {"ok": True, "migration": rev}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    try:
        redis.Redis.from_url(get_settings().redis_url, socket_timeout=1).ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = {"ok": False, "error": type(exc).__name__}
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks}, 200 if ok else 503
    )
