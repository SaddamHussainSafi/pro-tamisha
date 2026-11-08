"""Scanner drivers. The bridge never builds a shell command: argument lists only, fixed
executable path, random output filename inside a private temp directory, hard timeout."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Smallest valid one-page PDF, used by the fake driver (synthetic content only).
_FAKE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


class ScanError(Exception):
    """code is one of the API's ScanFailCode values."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail[:300]


@dataclass
class ScanResult:
    data: bytes
    mime_type: str
    filename: str


class Driver:
    name = "base"

    def probe(self) -> tuple[str, str | None]:
        """(scanner_state, detail) for heartbeats: READY, NOT_FOUND, DRIVER_MISSING, ..."""
        raise NotImplementedError

    def scan(self, timeout: int) -> ScanResult:
        raise NotImplementedError


@dataclass
class FakeDriver(Driver):
    """Development/test driver. mode: ok | unplugged | driver_missing | timeout | empty."""

    mode: str = "ok"
    payload: bytes = _FAKE_PDF
    calls: int = 0
    name: str = field(default="fake", init=False)

    def probe(self) -> tuple[str, str | None]:
        return {
            "unplugged": ("NOT_FOUND", "No scanner connected"),
            "driver_missing": ("DRIVER_MISSING", "Scanner driver not installed"),
        }.get(self.mode, ("READY", None))

    def scan(self, timeout: int) -> ScanResult:
        self.calls += 1
        if self.mode == "unplugged":
            raise ScanError("SCANNER_NOT_FOUND", "No scanner connected")
        if self.mode == "driver_missing":
            raise ScanError("DRIVER_MISSING", "Scanner driver not installed")
        if self.mode == "timeout":
            raise ScanError("SCAN_TIMEOUT", f"Scanner did not finish in {timeout}s")
        if self.mode == "empty":
            raise ScanError("NO_PAGES", "No pages in the feeder")
        return ScanResult(self.payload, "application/pdf", f"scan-{secrets.token_hex(4)}.pdf")


@dataclass
class Naps2Driver(Driver):
    """NAPS2 console (Windows, Toshiba via WIA/TWAIN). D-14: exact device driver unconfirmed,
    so the manual-upload fallback is always available in the web app."""

    exe: str = r"C:\Program Files\NAPS2\NAPS2.Console.exe"
    profile: str | None = None
    name: str = field(default="naps2", init=False)

    def _exe(self) -> str | None:
        if os.path.isfile(self.exe):
            return self.exe
        return shutil.which("naps2.console") or shutil.which("NAPS2.Console")

    def probe(self) -> tuple[str, str | None]:
        exe = self._exe()
        if exe is None:
            return "DRIVER_MISSING", "NAPS2 console not found"
        return "READY", None

    def scan(self, timeout: int) -> ScanResult:
        exe = self._exe()
        if exe is None:
            raise ScanError("DRIVER_MISSING", "NAPS2 console not found")
        with tempfile.TemporaryDirectory(prefix="ptscan-") as tmp:
            out = Path(tmp) / f"{secrets.token_hex(12)}.pdf"
            args = [exe, "-o", str(out), "--force", "-n", "1"]
            if self.profile:
                args += ["-p", self.profile]
            try:
                proc = subprocess.run(  # noqa: S603 - fixed exe, list args, no shell
                    args, capture_output=True, timeout=timeout, check=False, shell=False
                )
            except subprocess.TimeoutExpired:
                raise ScanError("SCAN_TIMEOUT", f"Scanner did not finish in {timeout}s") from None
            except OSError as e:
                raise ScanError("DRIVER_MISSING", str(e)) from None
            err = (proc.stderr or b"").decode("utf-8", "replace").lower()
            if proc.returncode != 0 or not out.exists():
                if "no scanned pages" in err or "no pages" in err:
                    raise ScanError("NO_PAGES", "No pages in the feeder")
                if "not found" in err or "no device" in err or "offline" in err:
                    raise ScanError("SCANNER_NOT_FOUND", "Scanner not found or switched off")
                raise ScanError("ERROR", f"Scanner exited with code {proc.returncode}")
            data = out.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ScanError("OUTPUT_INVALID", "Scanner output is not a PDF")
        return ScanResult(data, "application/pdf", out.name)
