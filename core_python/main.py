"""Entry point for the simplified Lightweight Charts strategy scanner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simplified_core_python.chart.server import DEFAULT_HOST, DEFAULT_PORT, run


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run simplified SEN05 strategy chart.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    print(f"SEN05 simplified chart: http://{args.host}:{args.port}")
    run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

