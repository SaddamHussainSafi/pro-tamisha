"""Write the implemented API contract to contracts/openapi.implemented.json.

Run: PYTHONPATH=src uv run python scripts/export_openapi.py
tests/test_contract.py fails if the committed file drifts from the code.
"""

import json
from pathlib import Path

from pro_trading.main import create_app

OUT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.implemented.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
