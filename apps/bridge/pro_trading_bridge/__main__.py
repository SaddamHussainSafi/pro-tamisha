"""python -m pro_trading_bridge pair CODE | run

Environment:
  PT_API_URL          https://erp.example (http://127.0.0.1:8000 allowed for development)
  PT_ALLOWED_ORIGINS  comma-separated web origins allowed to start scans (exact match)
  PT_BRIDGE_PORT      default 47615
  PT_BRIDGE_STATE     device credential file (default ~/.pro-trading-bridge.json, mode 600)
  PT_SCAN_DRIVER      naps2 (default) | fake
  PT_NAPS2_EXE        path to NAPS2.Console.exe
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from .client import ApiClient, BridgeApiError, urllib_transport
from .drivers import Driver, FakeDriver, Naps2Driver
from .server import DEFAULT_PORT, Bridge, serve


def _driver() -> Driver:
    if os.environ.get("PT_SCAN_DRIVER", "naps2") == "fake":
        return FakeDriver(mode=os.environ.get("PT_FAKE_MODE", "ok"))
    exe = os.environ.get("PT_NAPS2_EXE")
    return Naps2Driver(exe=exe) if exe else Naps2Driver()


def _heartbeat(bridge: Bridge, every: float = 30) -> None:
    while True:
        try:
            if bridge.api.paired:
                state, detail = bridge.driver.probe()
                bridge.api.heartbeat(state, detail)
        except BridgeApiError as e:
            logging.warning("heartbeat failed: %s", e)
        time.sleep(every)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = Path(os.environ.get("PT_BRIDGE_STATE", "~/.pro-trading-bridge.json")).expanduser()
    api = ApiClient(
        os.environ.get("PT_API_URL", "http://127.0.0.1:8000"), urllib_transport(), state
    )
    if len(argv) == 2 and argv[0] == "pair":
        fp = api.pair(argv[1])
        print(f"Paired. Confirm this code in Pro Trading > Scans > Scanners: {fp}")
        return 0
    if argv != ["run"]:
        print(__doc__)
        return 2
    origins = frozenset(
        o.strip() for o in os.environ.get("PT_ALLOWED_ORIGINS", "").split(",") if o.strip()
    )
    if not origins:
        print("PT_ALLOWED_ORIGINS is required")
        return 2
    bridge = Bridge(
        api=api,
        driver=_driver(),
        allowed_origins=origins,
        port=int(os.environ.get("PT_BRIDGE_PORT", DEFAULT_PORT)),
    )
    threading.Thread(target=_heartbeat, args=(bridge,), daemon=True).start()
    serve(bridge)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
