"""Scan group definitions for the 24/7 Telegram signal watcher.

Edit this file to add or remove symbol × TF × strategy groups.
Each group is polled independently at its own interval.
"""

from __future__ import annotations

SCAN_GROUPS: list[dict] = [
    {
        "strategy": "combo",
        "symbols": [
            "BTCUSD", "GOLD",
            "US30", "US100", "US500", "DE40", "UK100",
        ],
        "tf": "M5",
        "bars": 500,
        "poll_seconds": 60,
        "chat_id": None,   # None → uses TELEGRAM_CHAT_ID env var
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": [
            "BTCUSD", "GOLD",
            "US30", "US100", "US500",
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
        ],
        "tf": "H1",
        "bars": 500,
        "poll_seconds": 300,
        "chat_id": None,
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": [
            "BTCUSD", "GOLD",
            "US30", "US100",
            "EURUSD", "GBPUSD",
        ],
        "tf": "H4",
        "bars": 300,
        "poll_seconds": 900,
        "chat_id": None,
        "overrides": {},
    },
]
