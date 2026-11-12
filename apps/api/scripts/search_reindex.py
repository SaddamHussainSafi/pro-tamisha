"""Universal search maintenance. Run from apps/api:

    PYTHONPATH=src uv run python -m scripts.search_reindex backfill [--type invoice] [--company ID]
    PYTHONPATH=src uv run python -m scripts.search_reindex reconcile     # exit 1 when out of step
    PYTHONPATH=src uv run python -m scripts.search_reindex drain         # process the queue now
    PYTHONPATH=src uv run python -m scripts.search_reindex retry-failed  # re-arm FAILED queue rows

Backfill is idempotent and safe while the app runs: it rebuilds each document from the ERP
tables and removes documents whose record no longer exists. It never changes ERP data.
"""

import argparse
import json
import sys
import time
import uuid

from pro_trading.db import get_sessionmaker
from pro_trading.search import indexer
from pro_trading.search.registry import INDEXED_TYPES


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="search_reindex")
    ap.add_argument("command", choices=["backfill", "reconcile", "drain", "retry-failed"])
    ap.add_argument("--type", action="append", choices=INDEXED_TYPES, help="repeatable")
    ap.add_argument("--company", type=uuid.UUID)
    a = ap.parse_args(argv)
    with get_sessionmaker()() as db:
        if a.command == "backfill":
            t0 = time.perf_counter()
            done = indexer.backfill(
                db,
                a.type or INDEXED_TYPES,
                a.company,
                progress=lambda t, n: print(f"  {t}: {n}", file=sys.stderr, flush=True),
            )
            print(json.dumps({"indexed": done, "seconds": round(time.perf_counter() - t0, 1)}))
            return 0
        if a.command == "drain":
            print(json.dumps({"documents": indexer.drain_all(db)}))
            return 0
        if a.command == "retry-failed":
            print(json.dumps({"rearmed": indexer.retry_failed(db)}))
            return 0
        rep = indexer.reconcile(db, a.company)
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
