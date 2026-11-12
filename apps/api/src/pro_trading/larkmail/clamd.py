"""Optional attachment scanning with a ClamAV daemon (INSTREAM). No CLAMD_HOST = not scanned,
and the screen says so; nothing pretends a file was checked."""

from __future__ import annotations

import socket
import struct

from ..config import get_settings

# Never served to a browser, whatever a scanner says (scripts and executables).
BLOCKED_EXT = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".hta", ".cpl", ".jar", ".lnk", ".reg", ".dll", ".app", ".dmg",
    ".pkg", ".sh", ".iso",
}  # fmt: skip


def is_blocked(filename: str, detected_mime: str) -> bool:
    name = filename.lower().strip()
    if any(name.endswith(e) for e in BLOCKED_EXT):
        return True
    return detected_mime in {"application/x-msdownload", "application/x-dosexec",
                             "application/x-executable", "application/x-mach-binary"}  # fmt: skip


def scan(data: bytes) -> tuple[str, str]:
    """(status, detail): CLEAN / INFECTED / NOT_SCANNED / ERROR."""
    s = get_settings()
    if not s.clamd_host:
        return "NOT_SCANNED", "No virus scanner is configured"
    try:
        with socket.create_connection((s.clamd_host, s.clamd_port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 65536):
                chunk = data[i : i + 65536]
                sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            sock.sendall(struct.pack(">I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                got = sock.recv(4096)
                if not got:
                    break
                reply += got
    except OSError as exc:
        return "ERROR", f"Scanner unreachable ({type(exc).__name__})"
    text = reply.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"):
        return "CLEAN", ""
    if "FOUND" in text:
        return "INFECTED", text.split(":", 1)[-1].strip()[:200]
    return "ERROR", text[:200]
