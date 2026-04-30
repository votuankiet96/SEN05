"""MA Cross symbol universe."""

from __future__ import annotations

from core_python.shared.market import SYMBOLS as SHARED_SYMBOLS

MA_CROSS_SYMBOL_KEYS = tuple(SHARED_SYMBOLS.keys())


def get_ma_cross_universe_metadata() -> dict[str, dict]:
    return {key: dict(SHARED_SYMBOLS[key]) for key in MA_CROSS_SYMBOL_KEYS}


__all__ = ["MA_CROSS_SYMBOL_KEYS", "get_ma_cross_universe_metadata"]
