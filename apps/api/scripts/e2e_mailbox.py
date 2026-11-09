"""Print the latest account link token emailed to an address (dev mailbox), for browser E2E.

Refuses to run outside development/test. In dev/test the email_messages table is the mailbox.
Usage: uv run python scripts/e2e_mailbox.py EMAIL accept-invite|reset-password
Prints {"token": "..."} or exits 1 when no such email exists.
"""

import json
import re
import sys

from sqlalchemy import select

from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import EmailMessage

KINDS = {"accept-invite", "reset-password"}


def main(argv: list[str]) -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    if len(argv) != 2 or argv[1] not in KINDS:
        print(__doc__, file=sys.stderr)
        return 2
    email, kind = argv
    with get_sessionmaker()() as db:
        bodies = db.scalars(
            select(EmailMessage.body)
            .where(EmailMessage.to_addr == email)
            .order_by(EmailMessage.created_at.desc())
        ).all()
    for body in bodies:
        m = re.search(rf"/{kind}#token=([A-Za-z0-9_-]+)", body)
        if m:
            print(json.dumps({"token": m.group(1)}))
            return 0
    print(f"no {kind} email to {email}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
