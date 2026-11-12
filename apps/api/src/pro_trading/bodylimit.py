"""Request body size cap (pure ASGI, so it sees the raw byte stream).

FastAPI reads a JSON body fully into memory before validating it, so without a cap any client
(even anonymous, e.g. on /auth/login) could make the API buffer gigabytes. File uploads read the
stream themselves with their own SCAN_MAX_BYTES cap (routes/scans.py `raw_body`) and are exempt.
"""

import json
import re
import uuid
from typing import Any

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID = re.compile(r"[^A-Za-z0-9._:-]")
UPLOAD_PATHS = re.compile(
    r"^/api/v1/(scans/uploads|scan-bridge/jobs/[^/]+/upload|shipments/[^/]+/documents/upload)$"
)


class BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "The request is too large")


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in ("GET", "HEAD", "OPTIONS")
            or UPLOAD_PATHS.match(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await _too_large(scope, send, limit)
            return
        seen, exceeded, replaced = 0, False, False

        async def limited() -> Message:
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:  # chunked bodies have no Content-Length to check up front
                    exceeded = True
                    raise BodyTooLarge()
            return msg

        async def respond(msg: Message) -> None:
            # Layers between here and the route may wrap BodyTooLarge (e.g. FastAPI reports
            # "error parsing the body" as 400); whatever they answer, the client gets a 413.
            nonlocal replaced
            if exceeded and msg["type"] == "http.response.start":
                replaced = True
                await _too_large(scope, send, limit)
                return
            if replaced:
                return
            await send(msg)

        await self.app(scope, limited, respond)


async def _too_large(scope: Scope, send: Send, limit: int) -> None:
    # Same cleaning as the request middleware (this layer runs before it): a reference the user
    # can quote to support, never an empty one.
    raw_rid = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
    rid = REQUEST_ID.sub("", raw_rid)[:64] or str(uuid.uuid4())
    body: dict[str, Any] = {
        "error": {
            "code": "PAYLOAD_TOO_LARGE",
            "message": "This is too large to send in one go. Nothing was saved.",
            "request_id": rid,
            "max_bytes": limit,
        }
    }
    raw = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
                (b"connection", b"close"),
                (b"x-request-id", rid.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})
