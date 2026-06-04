"""Notebook chart payload and rendering helpers."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction
from backtest_optimize.io.market_data import normalize_ohlcv_frame
from backtest_optimize.io.signal_loader import normalize_timestamp


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
TEMPLATE_PATH = WEB_DIR / "lightweight_signal_chart.html"
APP_JS_PATH = WEB_DIR / "lightweight_signal_chart.js"
VENDOR_JS_PATH = WEB_DIR / "vendor" / "lightweight-charts.js"


def build_signal_chart_payload(
    *,
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    max_bars: int = 8000,
    sma_period: int = 20,
    macd_fast: int = 5,
    macd_slow: int = 25,
    macd_signal: int = 5,
) -> dict[str, Any]:
    """Return JSON-serializable data for the notebook signal chart."""
    if max_bars <= 0:
        raise ValueError("max_bars must be positive.")
    if sma_period <= 0:
        raise ValueError("sma_period must be positive.")
    if macd_fast <= 0 or macd_slow <= 0 or macd_signal <= 0:
        raise ValueError("MACD periods must be positive.")

    normalized = normalize_ohlcv_frame(bars)
    enriched = normalized.copy()
    close = enriched["close"].astype(float)

    enriched["sma"] = close.rolling(sma_period, min_periods=1).mean()
    ema_fast = close.ewm(span=macd_fast, adjust=False).mean()
    ema_slow = close.ewm(span=macd_slow, adjust=False).mean()
    enriched["macd_line"] = ema_fast - ema_slow
    enriched["macd_signal"] = enriched["macd_line"].ewm(span=macd_signal, adjust=False).mean()
    enriched["macd_hist"] = enriched["macd_line"] - enriched["macd_signal"]

    visible = enriched.tail(int(max_bars)).reset_index(drop=True)
    epoch_by_time = {
        normalize_timestamp(row["bartime"]): _to_epoch_seconds(row["bartime"])
        for row in visible.to_dict("records")
    }

    candles = [
        {
            "time": epoch_by_time[normalize_timestamp(row["bartime"])],
            "open": _as_float(row["open"]),
            "high": _as_float(row["high"]),
            "low": _as_float(row["low"]),
            "close": _as_float(row["close"]),
        }
        for row in visible.to_dict("records")
    ]

    sma = _line_payload(visible, "sma", epoch_by_time)
    macd_line = _line_payload(visible, "macd_line", epoch_by_time)
    macd_signal_line = _line_payload(visible, "macd_signal", epoch_by_time)
    macd_histogram = _histogram_payload(visible, "macd_hist", epoch_by_time)
    markers = _signal_markers(signals, epoch_by_time)

    return {
        "meta": {
            "symbol": str(symbol).upper(),
            "timeframe": str(timeframe).upper(),
            "bar_count": int(len(normalized)),
            "rendered_bar_count": int(len(visible)),
            "signal_count": int(len(signals)),
            "rendered_signal_count": int(len(markers)),
            "max_bars": int(max_bars),
            "sma_period": int(sma_period),
            "macd": {
                "fast": int(macd_fast),
                "slow": int(macd_slow),
                "signal": int(macd_signal),
            },
        },
        "candles": candles,
        "sma20": sma,
        "macd": {
            "histogram": macd_histogram,
            "line": macd_line,
            "signal": macd_signal_line,
        },
        "markers": markers,
    }


def render_signal_chart_html(payload: dict[str, Any], *, height: int = 760) -> str:
    """Return an isolated iframe HTML output for IPython.display.HTML."""
    _ensure_chart_assets_exist()
    document = (
        TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__LIGHTWEIGHT_CHARTS_JS__", VENDOR_JS_PATH.read_text(encoding="utf-8"))
        .replace("__SIGNAL_CHART_JS__", APP_JS_PATH.read_text(encoding="utf-8"))
        .replace("__PAYLOAD_JSON__", json.dumps(payload, ensure_ascii=False))
        .replace("__CHART_HEIGHT__", str(int(height)))
    )
    return (
        '<iframe class="bt-signal-chart-frame" '
        'style="width:100%; min-width:320px; '
        f'height:{int(height)}px; border:0; display:block;" '
        f'srcdoc="{escape(document, quote=True)}"></iframe>'
    )


def _line_payload(
    frame: pd.DataFrame,
    column: str,
    epoch_by_time: dict[pd.Timestamp, int],
) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for record in frame.to_dict("records"):
        value = _optional_float(record.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": epoch_by_time[normalize_timestamp(record["bartime"])],
                "value": value,
            }
        )
    return rows


def _histogram_payload(
    frame: pd.DataFrame,
    column: str,
    epoch_by_time: dict[pd.Timestamp, int],
) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    for record in frame.to_dict("records"):
        value = _optional_float(record.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": epoch_by_time[normalize_timestamp(record["bartime"])],
                "value": value,
                "color": "#26a69a" if value >= 0 else "#ef5350",
            }
        )
    return rows


def _signal_markers(
    signals: pd.DataFrame,
    epoch_by_time: dict[pd.Timestamp, int],
) -> list[dict[str, Any]]:
    if signals.empty:
        return []

    time_col = _first_present(signals.columns, ("bartime", "bar_time", "time", "timestamp"))
    direction_col = _first_present(signals.columns, ("direction", "side", "signal"))
    if time_col is None or direction_col is None:
        return []

    markers: list[dict[str, Any]] = []
    for record in signals.to_dict("records"):
        try:
            bartime = normalize_timestamp(record[time_col])
            direction = _direction_label(record[direction_col])
        except (TypeError, ValueError):
            continue

        epoch = epoch_by_time.get(bartime)
        if epoch is None:
            continue

        is_buy = direction == "BUY"
        markers.append(
            {
                "time": epoch,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#26a69a" if is_buy else "#ef5350",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": direction,
            }
        )

    return sorted(markers, key=lambda item: int(item["time"]))


def _direction_label(value: Any) -> str:
    if isinstance(value, Direction):
        return value.value
    raw = value
    if isinstance(value, str):
        raw = value.strip().upper()
    if raw in {"BUY", "LONG", "BULL", "BULLISH", "1", 1, "DIRECTION.BUY"}:
        return "BUY"
    if raw in {"SELL", "SHORT", "BEAR", "BEARISH", "-1", -1, "DIRECTION.SELL"}:
        return "SELL"
    raise ValueError(f"Unsupported direction value: {value!r}")


def _first_present(columns: Any, candidates: tuple[str, ...]) -> str | None:
    lower_map = {str(col).strip().lower(): str(col) for col in columns}
    for candidate in candidates:
        if candidate in lower_map:
            return lower_map[candidate]
    return None


def _to_epoch_seconds(value: Any) -> int:
    ts = normalize_timestamp(value)
    return int(ts.tz_localize("UTC").timestamp())


def _as_float(value: Any) -> float:
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _ensure_chart_assets_exist() -> None:
    missing = [
        path
        for path in (TEMPLATE_PATH, APP_JS_PATH, VENDOR_JS_PATH)
        if not path.exists()
    ]
    if missing:
        labels = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing chart asset(s): {labels}")
