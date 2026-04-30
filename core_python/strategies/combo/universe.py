"""Combo-owned symbol universe.

This module defines which shared instruments belong to the Combo strategy.
Instrument and broker metadata still comes from ``core_python.shared``; Combo
strategy parameters such as breakout buffers live in ``parameters.py``.
"""

from __future__ import annotations

from core_python.shared.market import SYMBOLS as SHARED_SYMBOLS


COMBO_SYMBOL_KEYS = (
    "US30",
    "US500",
    "US100",
    "DE40",
    "UK100",
    "FR40",
    "SP35",
    "HK50",
    "J225",
    "GOLD",
    "BTCUSD",
)


def get_combo_universe_metadata() -> dict[str, dict]:
    """Return shared instrument metadata for the Combo symbol universe."""
    missing = [symbol for symbol in COMBO_SYMBOL_KEYS if symbol not in SHARED_SYMBOLS]
    if missing:
        raise KeyError(f"Combo universe references unknown shared symbols: {missing}")
    return {symbol: dict(SHARED_SYMBOLS[symbol]) for symbol in COMBO_SYMBOL_KEYS}


__all__ = [
    "COMBO_SYMBOL_KEYS",
    "get_combo_universe_metadata",
]
