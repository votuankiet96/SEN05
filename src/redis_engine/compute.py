"""
Đường tính toán dùng CHUNG bởi candle_snapshot_consumer và safety_net_poller.

Không viết lại logic chiến lược ở đây — chỉ gọi vào og_core (qua
run_strategy_request() hoặc trực tiếp 4 hàm của StrategySpec), trích các
dòng có signal != 0 thành payload sẵn sàng publish, kèm signal_id xác định
(xem delivery/signal_id.py).

Có 2 đường tính, PHẢI cho ra payload giống hệt nhau cho cùng 1 bar (dùng
chung _build_payload/_num) để dedup ở delivery/state.py hoạt động đúng:
    run_watched_item(): safety_net_poller dùng — tự query SQL Server qua
        run_strategy_request().
    run_from_bars(): candle_snapshot_consumer dùng — nhận thẳng DataFrame
        đã có sẵn (từ triggers/candle_store.py), không query gì thêm.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from og_core.engine import run_strategy_request
from og_core.strategies.registry import get_strategy
from redis_engine.delivery.signal_id import build_signal_id

logger = logging.getLogger(__name__)

# ai_trend/knn_combo cần 2 khung thời gian (trend + entry) — candle_snapshot
# chỉ mang 1 khung thời gian/lần gửi nên không đủ dữ liệu qua đường nhanh.
# Vẫn tính bình thường qua safety_net_poller (run_watched_item tự query SQL
# đủ khung thời gian cần, không đi qua run_from_bars).
MULTI_TIMEFRAME_STRATEGIES = {"ai_trend", "knn_combo"}


def run_watched_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Tính tín hiệu cho 1 mục trong config.WATCHED (nhiều symbol, cùng
    strategy/tf/bars) — dùng bởi safety_net_poller để quét lại toàn bộ
    danh sách theo dõi bằng SQL Server.

    Lỗi ở 1 symbol (dữ liệu thiếu, edge case DB...) chỉ bỏ qua đúng symbol
    đó — không được để 1 symbol lỗi làm các symbol khác trong cùng item
    (vd DE40 đứng sau US30 trong cùng WATCHED entry) không được tính vòng đó.
    """
    strategy = str(item["strategy"])
    tf = str(item["tf"])
    bars = int(item["bars"])
    rows: list[dict[str, Any]] = []
    for symbol in item["symbols"]:
        try:
            rows.extend(_run_symbol(strategy, str(symbol), tf, bars))
        except Exception:
            logger.exception("compute: failed for %s %s %s", strategy, symbol, tf)
    return rows


def _run_symbol(strategy: str, symbol: str, tf: str, bars: int) -> list[dict[str, Any]]:
    result = run_strategy_request(strategy, symbol=symbol, tf=tf, bars=bars)
    df = result.enriched
    if df.empty or "signal" not in df.columns:
        return []
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]
    return [_build_payload(strategy, symbol, tf, row) for _, row in signals.iterrows()]


def run_from_bars(strategy: str, symbol: str, tf: str, raw: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Tính tín hiệu cho 1 symbol/tf từ DataFrame đã có sẵn (vd. parse từ 1
    entry candle_snapshot) — không tự load lại dữ liệu từ SQL hay Redis.

    Args:
        raw: DataFrame [bartime, open, high, low, close, volume], sắp tăng
            dần — cùng schema với og_core.data.loader.load().

    Raises:
        ValueError: strategy cần nhiều khung thời gian (ai_trend/knn_combo),
            hoặc Combo đang bật HTF_TREND_ENABLED — cả 2 trường hợp cần dữ
            liệu ngoài phạm vi 1 khung thời gian mà candle_snapshot mang.
    """
    spec = get_strategy(strategy)
    if spec.key in MULTI_TIMEFRAME_STRATEGIES:
        raise ValueError(f"{spec.key}: cần nhiều khung thời gian, không hỗ trợ qua candle_snapshot")

    params = spec.normalize_params(None, symbol)
    if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
        raise ValueError("combo (HTF_TREND_ENABLED=True): cần thêm khung thời gian, không hỗ trợ qua candle_snapshot")

    with_indicators = spec.add_indicators(raw, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    enriched = spec.add_levels(with_signals, params, symbol)
    if enriched.empty or "signal" not in enriched.columns:
        return []
    signals = enriched[enriched["signal"].fillna(0).astype(int).ne(0)]
    return [_build_payload(strategy, symbol, tf, row) for _, row in signals.iterrows()]


def _build_payload(strategy: str, symbol: str, tf: str, row: pd.Series) -> dict[str, Any]:
    direction = int(row["signal"])
    bar_time = row["bartime"]
    return {
        "signal_id": build_signal_id(strategy, symbol, tf, bar_time, direction),
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "side": "BUY" if direction == 1 else "SELL",
        "bar_time": pd.Timestamp(bar_time).isoformat(),
        "event_close": _num(row.get("close")),
        "entry_price": _num(row.get("entry_price")),
        "sl_price": _num(row.get("sl_price")),
        "tp_price": _num(row.get("tp_price")),
        "risk_reward": _num(row.get("risk_reward")),
        "atr": _num(row.get("atr")),
        "signal_reason": str(row.get("signal_reason", "") or ""),
        "produced_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }


def _num(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
