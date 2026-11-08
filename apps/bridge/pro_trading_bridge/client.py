"""Talks to the Pro Trading API with the device credential. Transport is injectable for tests."""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import VERSION

# (method, url, headers, body) -> (status, body bytes)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class BridgeApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def urllib_transport(timeout: float = 60) -> Transport:
    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise BridgeApiError(0, "INSECURE_API_URL", "API must use https (or localhost in dev)")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return int(r.status), bytes(r.read())
        except urllib.error.HTTPError as e:
            return int(e.code), bytes(e.read())
        except (urllib.error.URLError, TimeoutError) as e:
            raise BridgeApiError(503, "API_UNREACHABLE", str(e)) from None

    return send


@dataclass
class ApiClient:
    base_url: str
    transport: Transport
    state_path: Path | None = None
    device_id: str | None = None
    device_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.state_path and self.state_path.exists() and not self.device_id:
            st = json.loads(self.state_path.read_text())
            self.device_id, self.device_secret = st.get("device_id"), st.get("device_secret")

    @property
    def paired(self) -> bool:
        return bool(self.device_id and self.device_secret)

    def _call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        ctype: str = "application/json",
        auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"Content-Type": ctype, "User-Agent": f"pro-trading-bridge/{VERSION}"}
        if auth:
            headers["Authorization"] = f"Device {self.device_id}.{self.device_secret}"
        status, raw = self.transport(method, f"{self.base_url}{path}", headers, body)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if status >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise BridgeApiError(
                status, err.get("code", "API_ERROR"), err.get("message", "API error")
            )
        return data if isinstance(data, dict) else {}

    def _json(
        self, method: str, path: str, payload: dict[str, Any], auth: bool = True
    ) -> dict[str, Any]:
        return self._call(method, path, json.dumps(payload).encode(), auth=auth)

    def pair(self, code: str) -> str:
        """Exchange the one-time code; returns the fingerprint to read out to the operator."""
        out = self._json(
            "POST",
            "/api/v1/scan-bridge/pair",
            {"pairing_code": code, "bridge_version": VERSION},
            auth=False,
        )
        self.device_id, self.device_secret = out["device_id"], out["device_secret"]
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"device_id": self.device_id, "device_secret": self.device_secret})
            )
            os.chmod(self.state_path, stat.S_IRUSR | stat.S_IWUSR)
        return str(out["fingerprint"])

    def heartbeat(
        self, state: str, detail: str | None, scanners: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "scanner_state": state,
            "scanner_detail": detail,
            "bridge_version": VERSION,
        }
        if scanners is not None:
            body["scanners"] = scanners
        return self._json("POST", "/api/v1/scan-bridge/heartbeat", body)

    def claim(self, job_id: str, capability: str) -> dict[str, Any]:
        return self._json(
            "POST", f"/api/v1/scan-bridge/jobs/{job_id}/claim", {"capability": capability}
        )

    def upload(self, job_id: str, data: bytes, mime: str, filename: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"filename": filename})
        return self._call("POST", f"/api/v1/scan-bridge/jobs/{job_id}/upload?{q}", data, mime)

    def fail(self, job_id: str, code: str, detail: str | None) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/api/v1/scan-bridge/jobs/{job_id}/fail",
            {"error_code": code, "detail": detail},
        )
