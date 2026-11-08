"""Localhost HTTP endpoint the web app calls to start a scan.

Defences (docs/architecture/04-security-and-scanner-bridge.md):
- binds 127.0.0.1 only;
- Host header must be 127.0.0.1:<port> or localhost:<port> (DNS rebinding);
- Origin must be in the allow-list; missing / "null" / other origins are refused and the
  driver is never called (SEC-002);
- the request carries a server-signed, single-use, short-lived capability; the bridge cannot
  scan without the API accepting it (claim), so a page that merely reaches localhost gets nothing;
- single flight (one scan at a time), small JSON bodies only, no shell, random filenames.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import VERSION
from .client import ApiClient, BridgeApiError
from .drivers import Driver, ScanError

log = logging.getLogger("pro_trading_bridge")

DEFAULT_PORT = 47615
MAX_BODY = 4096


@dataclass
class Bridge:
    """Core logic, independent of the HTTP server so tests can drive it directly."""

    api: ApiClient
    driver: Driver
    allowed_origins: frozenset[str]
    port: int = DEFAULT_PORT
    security_events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -------------------------------------------------- request gate

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def gate(self, host: str | None, origin: str | None) -> tuple[int, str] | None:
        """None when the caller may proceed, else (status, code). Every refusal is logged."""
        if host not in self.allowed_hosts():
            return self._deny("HOST_REJECTED", host=host, origin=origin)
        if not origin or origin == "null" or origin not in self.allowed_origins:
            return self._deny("ORIGIN_REJECTED", host=host, origin=origin)
        return None

    def _deny(self, code: str, **detail: Any) -> tuple[int, str]:
        ev = {"event": "bridge.request_denied", "code": code, **detail}
        self.security_events.append(ev)
        log.warning("security event %s", json.dumps(ev))
        return 403, code

    # -------------------------------------------------- actions

    def status(self) -> dict[str, Any]:
        state, detail = self.driver.probe()
        return {
            "bridge_version": VERSION,
            "paired": self.api.paired,
            "device_id": self.api.device_id,
            "scanner_state": "BUSY" if self._lock.locked() else state,
            "scanner_detail": detail,
        }

    def scan(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        job_id = body.get("job_id")
        cap = body.get("capability")
        try:
            jid = str(uuid.UUID(str(job_id)))
        except ValueError:
            return 422, {"code": "INVALID_REQUEST", "message": "job_id is required"}
        if not isinstance(cap, str) or not (20 <= len(cap) <= 2048):
            return 422, {"code": "INVALID_REQUEST", "message": "capability is required"}
        if not self.api.paired:
            return 409, {"code": "NOT_PAIRED", "message": "This bridge is not paired yet"}
        if not self._lock.acquire(blocking=False):
            return 409, {"code": "BUSY", "message": "The scanner is busy with another scan"}
        try:
            try:
                claim = self.api.claim(jid, cap)  # API validates capability + device
            except BridgeApiError as e:
                return e.status, {"code": e.code, "message": e.message}
            try:
                result = self.driver.scan(int(claim["timeout_seconds"]))
                if len(result.data) > int(claim["max_bytes"]):
                    raise ScanError("OUTPUT_INVALID", "Scan is larger than allowed")
            except ScanError as e:
                job = self.api.fail(jid, e.code, e.detail)
                return 200, job
            try:
                job = self.api.upload(jid, result.data, result.mime_type, result.filename)
            except BridgeApiError as e:
                return e.status, {"code": e.code, "message": e.message}
            return 200, job
        finally:
            self._lock.release()


def make_handler(bridge: Bridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ProTradingBridge"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.info("%s %s", self.address_string(), format % args)

        def _send(self, status: int, payload: dict[str, Any], origin_ok: bool) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if origin_ok:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def _gate(self) -> bool:
            denied = bridge.gate(self.headers.get("Host"), self.headers.get("Origin"))
            if denied:
                status, code = denied
                self._send(status, {"code": code, "message": "Request not allowed"}, False)
                return False
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._gate():
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._gate():
                return
            if self.path != "/v1/status":
                self._send(404, {"code": "NOT_FOUND", "message": "Not found"}, True)
                return
            self._send(200, bridge.status(), True)

        def do_POST(self) -> None:  # noqa: N802
            if not self._gate():
                return
            if self.path != "/v1/scan":
                self._send(404, {"code": "NOT_FOUND", "message": "Not found"}, True)
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._send(415, {"code": "UNSUPPORTED", "message": "JSON only"}, True)
                return
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                self._send(413, {"code": "BODY_SIZE", "message": "Bad request size"}, True)
                return
            try:
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send(422, {"code": "INVALID_REQUEST", "message": "Bad JSON"}, True)
                return
            status, payload = bridge.scan(body)
            self._send(status, payload, True)

    return Handler


def serve(bridge: Bridge, ready: Callable[[ThreadingHTTPServer], None] | None = None) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", bridge.port), make_handler(bridge))
    if ready:
        ready(httpd)
    log.info("bridge listening on 127.0.0.1:%s", bridge.port)
    httpd.serve_forever()
