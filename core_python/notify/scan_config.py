"""Scan group definitions for the 24/7 Telegram signal watcher.

Edit SCAN_GROUPS to add/remove groups.
Each group is polled independently at its own interval.

CLI override (signal_watcher.py --symbols / --tf) bypasses this file entirely.
"""

from __future__ import annotations

# Default poll interval per timeframe (seconds)
TF_POLL_SECONDS: dict[str, int] = {
    "M5": 60,
    "M10": 120,
    "M15": 120,
    "M20": 180,
    "M30": 180,
    "M45": 240,
    "H1": 300,
    "M90": 360,
    "H2": 600,
    "H3": 900,
    "H4": 900,
    "H6": 1800,
    "H8": 1800,
    "D1": 3600,
    "W": 3600,
}

# All Indice symbols available in the DB
INDICE_SYMBOLS = ["FR40", "DE40", "HK50", "J225", "SP35", "UK100", "US500", "US100", "US30"]

SCAN_GROUPS: list[dict] = [
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H1",
        "bars": 500,
        "poll_seconds": TF_POLL_SECONDS["H1"],
        "chat_id": None,   # None → uses TELEGRAM_CHAT_ID env var
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H2",
        "bars": 500,
        "poll_seconds": TF_POLL_SECONDS["H2"],
        "chat_id": None,
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H3",
        "bars": 400,
        "poll_seconds": TF_POLL_SECONDS["H3"],
        "chat_id": None,
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H4",
        "bars": 300,
        "poll_seconds": TF_POLL_SECONDS["H4"],
        "chat_id": None,
        "overrides": {},
    },
]
