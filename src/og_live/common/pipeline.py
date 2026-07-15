"""Live strategy pipeline: candle snapshot bars in, signal payloads out."""

from __future__ import annotations

import logging

import pandas as pd

from og_core.engine import run_strategy_on_bars
from og_core.signals import payloads_from_frame
from og_live.common.settings import WatchedItem

logger = logging.getLogger(__name__)


def matching_items(symbol: str, tf: str, watched: list[WatchedItem]) -> list[WatchedItem]:
    """Return watched subscriptions that match one incoming snapshot."""
    normalized_symbol = str(symbol).strip().upper()
    normalized_tf = str(tf).strip().upper()
    return [item for item in watched if item.tf == normalized_tf and normalized_symbol in item.symbols]


def signal_payloads_from_bars(item: WatchedItem, symbol: str, tf: str, bars: pd.DataFrame) -> list[dict[str, object]]:
    """Compute live signal payloads for one watched item from snapshot OHLCV bars."""
    try:
        scoped_bars = bars.tail(max(1, item.bars)).reset_index(drop=True)
        result = run_strategy_on_bars(item.strategy, symbol=symbol, tf=tf, bars=scoped_bars)
    except Exception:
        logger.exception("pipeline: compute failed for %s %s %s", item.strategy, symbol, tf)
        return []

    frame = result.enriched.tail(1) if item.latest_only else result.enriched
    return payloads_from_frame(result.strategy, result.symbol, result.tf, frame)
