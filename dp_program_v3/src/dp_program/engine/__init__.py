"""Public pair contract for the DP Program V3 engine."""

from __future__ import annotations

from typing import Any

Pair = tuple[dict[str, Any], dict[str, Any]]


def select_pairs(
    config: dict[str, Any],
    *,
    live: bool,
    symbol_filter: str | None = None,
    timeframe_filter: str | None = None,
) -> list[Pair]:
    """Resolve the enabled contract universe after optional CLI filters."""
    requested_symbol = (symbol_filter or "").strip().upper()
    requested_timeframe = (timeframe_filter or "").strip().upper()
    symbols = []
    for symbol in config["data"]["symbols"]:
        full_name = f"{symbol['exchange']}:{symbol['symbol']}"
        if not symbol["enabled"] or (live and not symbol["live"]):
            continue
        if requested_symbol and requested_symbol not in {symbol["symbol"], full_name}:
            continue
        symbols.append(symbol)
    timeframes = [
        timeframe
        for timeframe in config["data"]["timeframes"]
        if not requested_timeframe or timeframe["code"] == requested_timeframe
    ]
    if requested_symbol and not symbols:
        raise ValueError(f"unknown or disabled symbol: {symbol_filter}")
    if requested_timeframe and not timeframes:
        raise ValueError(f"unknown timeframe: {timeframe_filter}")
    return [(symbol, timeframe) for symbol in symbols for timeframe in timeframes]


def pair_key(pair: Pair) -> str:
    """Return the stable, secret-free runtime key for one pair."""
    symbol, timeframe = pair
    return f"{symbol['exchange']}:{symbol['symbol']}/{timeframe['code']}"
