"""SMTP for Lark Mail (implicit TLS, port 465). `check` logs in and quits without sending
anything. `submit` sends one prepared message and says whether a failure happened before Lark
could have accepted it (safe to retry) or after (UNCERTAIN: never retried automatically)."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass


class SmtpAuthError(RuntimeError):
    pass


class SmtpError(RuntimeError):
    def __init__(self, msg: str, *, uncertain: bool = False, permanent: bool = False) -> None:
        super().__init__(msg)
        self.uncertain = uncertain
        self.permanent = permanent


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True
    timeout: float = 30


def _open(cfg: SmtpConfig) -> smtplib.SMTP:
    try:
        if cfg.use_ssl:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout, context=ssl.create_default_context()
            )
        else:
            conn = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
        conn.ehlo()
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        raise SmtpError(f"Could not reach {cfg.host}:{cfg.port} ({type(exc).__name__})") from None
    try:
        conn.login(cfg.username, cfg.password)
    except smtplib.SMTPAuthenticationError as exc:
        _quit(conn)
        raise SmtpAuthError(f"Lark refused the login ({exc.smtp_code})") from None
    except (OSError, smtplib.SMTPException) as exc:
        _quit(conn)
        raise SmtpError(f"Login failed ({type(exc).__name__})") from None
    return conn


def _quit(conn: smtplib.SMTP) -> None:
    try:
        conn.quit()
    except (OSError, smtplib.SMTPException):
        try:
            conn.close()
        except OSError:
            pass


def check(cfg: SmtpConfig) -> None:
    """Authenticate only. Sends no message."""
    conn = _open(cfg)
    try:
        conn.noop()
    except (OSError, smtplib.SMTPException):
        pass
    _quit(conn)


def submit(cfg: SmtpConfig, from_addr: str, rcpts: list[str], raw: bytes) -> str:
    """Returns the server's final reply. Raises SmtpError(uncertain=True) when the connection
    dropped after the message data was handed over."""
    conn = _open(cfg)  # failures here: nothing was sent, safe to retry
    try:
        code, resp = conn.mail(from_addr)
        if code != 250:
            raise SmtpError(f"Sender refused ({code})", permanent=code >= 500)
        ok = 0
        for r in rcpts:
            code, resp = conn.rcpt(r)
            if code in (250, 251):
                ok += 1
            elif code >= 500:
                raise SmtpError(f"Recipient {r} refused ({code})", permanent=True)
        if not ok:
            raise SmtpError("No recipient accepted", permanent=True)
    except (TimeoutError, OSError, smtplib.SMTPServerDisconnected) as exc:
        _quit(conn)
        raise SmtpError(f"Connection lost before sending ({type(exc).__name__})") from None
    except SmtpError:
        try:
            conn.rset()
        except (OSError, smtplib.SMTPException):
            pass
        _quit(conn)
        raise
    try:
        code, resp = conn.data(raw)
    except smtplib.SMTPDataError as exc:
        _quit(conn)
        raise SmtpError(
            f"Lark refused the message ({exc.smtp_code})", permanent=exc.smtp_code >= 500
        ) from None
    except (OSError, smtplib.SMTPException) as exc:
        _quit(conn)
        # The data may have reached Lark: do not retry automatically.
        raise SmtpError(
            f"Connection lost while sending ({type(exc).__name__})", uncertain=True
        ) from None
    _quit(conn)
    if code != 250:
        raise SmtpError(f"Lark answered {code}", permanent=code >= 500)
    return resp.decode("utf-8", "replace")[:300] if isinstance(resp, bytes) else str(resp)[:300]
