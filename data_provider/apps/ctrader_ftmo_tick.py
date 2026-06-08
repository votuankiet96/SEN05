"""Thin app wrapper for the isolated cTrader FTMO tick provider."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_provider.tickdata_ctrader_ftmo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
