"""Interactive chart dashboard for the MA Cross strategy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:  # pragma: no cover
    print("[ERROR] Flask is not installed. Install it with: pip install flask")
    sys.exit(1)


_ROOT = Path(__file__).resolve().parents[3]
_CORE = _ROOT / "core_python"
_HERE = Path(__file__).resolve().parent
for _path in (_ROOT, _CORE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.db_connector import test_connection  # noqa: E402
from core_python.shared.execution.prices import calc_dynamic_slippage  # noqa: E402
from core_python.shared.data import load_scan_ohlcv  # noqa: E402
from core_python.strategies.ma_cross.config import (  # noqa: E402
    INDICATOR_COLS,
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    TIMEFRAMES,
    get_indicator_params,
    get_symbol_params,
    summary as strategy_summary,
)
from core_python.strategies.ma_cross.signals import (  # noqa: E402
    add_ma_cross_indicators,
    detect_ma_cross_signals,
    session_mask,
)


PORT = 8514
HOST = "127.0.0.1"
MAX_SIGNAL_ROWS = 120
APP_DEFAULT_SYMBOL = next(iter(SYMBOLS))
APP_DEFAULT_BARS = 700

app = Flask(__name__)


def _ts(bar_time) -> int:
    ts = pd.Timestamp(bar_time)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: str | None, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _parse_session_hours(value: str | None, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    raw = str(value).strip()
    if raw == "":
        return []
    hours: set[int] = set()
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        if not part:
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError("session_hours must contain UTC hours in range 0..23")
        hours.add(hour)
    return sorted(hours)


def _symbol_defaults(symbol: str) -> dict[str, Any]:
    params = get_symbol_params(symbol)
    return {
        "symbol": symbol,
        "label": params["label"],
        "group": params.get("group", ""),
        "symbol_id": params["symbol_id"],
        "session_hours_utc": params.get("session_hours_utc", []),
        "spec_verified": params.get("spec_verified", False),
        "spread_pts": params.get("spread_pts", 0.0),
        "slippage_pts": params.get("slippage_pts", 0.0),
    }


def _runtime_request() -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    symbol = request.args.get("symbol", APP_DEFAULT_SYMBOL).upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown MA Cross symbol '{symbol}'. Available: {', '.join(SYMBOLS)}")

    defaults = _symbol_defaults(symbol)
    ind = get_indicator_params()
    tf = request.args.get("tf", TIMEFRAME).upper()
    if tf not in TIMEFRAMES:
        raise ValueError(f"Unsupported MA Cross timeframe '{tf}'. Use one of {TIMEFRAMES}.")

    bars = _to_int(request.args.get("bars"), APP_DEFAULT_BARS, 50, 5000)
    session_hours = _parse_session_hours(
        request.args.get("session_hours"),
        list(defaults.get("session_hours_utc", [])),
    )
    ma_type = (request.args.get("ma_type") or ind["MA_TYPE"]).lower().strip()
    if ma_type not in {"sma", "ema"}:
        raise ValueError("ma_type must be 'sma' or 'ema'")

    params = {
        "MA_TYPE": ma_type,
        "FAST_MA": _to_int(request.args.get("fast_ma"), int(ind["FAST_MA"]), 1, 300),
        "SLOW_MA": _to_int(request.args.get("slow_ma"), int(ind["SLOW_MA"]), 2, 500),
        "ATR_PERIOD": _to_int(request.args.get("atr_period"), int(ind["ATR_PERIOD"]), 2, 300),
        "ATR_STOP_MULT": _to_float(
            request.args.get("atr_stop_mult"),
            float(STRATEGY["atr_stop_mult"]),
            0.1,
            20.0,
        ),
        "ATR_TP_MULT": _to_float(
            request.args.get("atr_tp_mult"),
            float(STRATEGY["atr_tp_mult"]),
            0.0,
            20.0,
        ),
        "SHOW_MA": _to_bool(request.args.get("show_ma"), True),
        "SHOW_LEVELS": _to_bool(request.args.get("show_levels"), True),
    }
    if params["FAST_MA"] >= params["SLOW_MA"]:
        raise ValueError("FAST_MA must be smaller than SLOW_MA.")

    cfg = {
        **get_symbol_params(symbol),
        "session_hours_utc": session_hours,
    }
    return symbol, tf, bars, params, cfg


def _candles_json(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df.iterrows():
        vol = row.get("volume", row.get("Volume"))
        rows.append({
            "time": _ts(row["BarTime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(vol) if vol is not None and pd.notna(vol) else 0.0,
        })
    return rows


def _indicators_json(df: pd.DataFrame) -> dict[str, list[dict]]:
    times = [_ts(t) for t in df["BarTime"]]

    def to_list(col: str) -> list[dict]:
        return [
            {"time": t, "value": float(v)}
            for t, v in zip(times, df[col])
            if pd.notna(v)
        ]

    return {
        "fast_ma": to_list("fast_ma") if "fast_ma" in df else [],
        "slow_ma": to_list("slow_ma") if "slow_ma" in df else [],
        "atr": to_list("atr") if "atr" in df else [],
    }


def _signals_json(df_sig: pd.DataFrame, params: dict[str, Any], cfg: dict[str, Any]) -> list[dict]:
    if df_sig.empty:
        return []
    rows = df_sig.reset_index().rename(columns={"index": "BarTime"})
    signals = []
    spread = float(cfg.get("spread_pts", 0.0))
    base_slip = float(cfg.get("slippage_pts", 0.0))
    for i, row in rows.iterrows():
        direction = int(row.get("signal", 0) or 0)
        if direction == 0 or i + 1 >= len(rows):
            continue
        next_bar = rows.iloc[i + 1]
        atr = float(row["atr"])
        slip = calc_dynamic_slippage(base_slip, atr=atr, close=float(row["close"]))
        entry = float(next_bar["open"]) + direction * (spread + slip)
        sl_dist = float(params["ATR_STOP_MULT"]) * atr
        tp_dist = float(params["ATR_TP_MULT"]) * atr
        sl = entry - direction * sl_dist
        tp = entry + direction * tp_dist if tp_dist > 0 else None
        signals.append({
            "bar_time": pd.Timestamp(row["BarTime"]).strftime("%Y-%m-%d %H:%M"),
            "entry_time": pd.Timestamp(next_bar["BarTime"]).strftime("%Y-%m-%d %H:%M"),
            "time": _ts(row["BarTime"]),
            "entry_marker_time": _ts(next_bar["BarTime"]),
            "direction": "BUY" if direction == 1 else "SELL",
            "direction_int": direction,
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5) if tp is not None else None,
            "atr": round(atr, 5),
            "fast_ma": round(float(row["fast_ma"]), 5),
            "slow_ma": round(float(row["slow_ma"]), 5),
            "ma_gap_atr": (
                round(float(row["ma_gap_atr"]), 4)
                if pd.notna(row.get("ma_gap_atr"))
                else None
            ),
        })
    return signals[-MAX_SIGNAL_ROWS:]


def _stats(signals: list[dict]) -> dict[str, Any]:
    n_buy = sum(1 for s in signals if s["direction"] == "BUY")
    n_sell = sum(1 for s in signals if s["direction"] == "SELL")
    return {
        "n_total": len(signals),
        "n_buy": n_buy,
        "n_sell": n_sell,
        "last_direction": signals[-1]["direction"] if signals else "-",
    }


def _meta(symbol: str, tf: str, bars: int, params: dict[str, Any], cfg: dict[str, Any]) -> str:
    session = cfg.get("session_hours_utc") or "all"
    return (
        f"{symbol} {tf} | bars={bars} | {params['MA_TYPE'].upper()}"
        f"{params['FAST_MA']}/{params['SLOW_MA']} | ATR={params['ATR_PERIOD']} | "
        f"SL={params['ATR_STOP_MULT']}ATR | TP={params['ATR_TP_MULT']}ATR | session={session}"
    )


@app.route("/")
def index():
    return send_from_directory(_HERE, "ma_chart.html")


@app.route("/api/config")
def api_config():
    return jsonify(
        {
            "strategy": {
                "name": STRATEGY["name"],
                "version": STRATEGY["version"],
                "summary": strategy_summary(),
            },
            "symbols": [_symbol_defaults(symbol) for symbol in SYMBOLS],
            "indicator_defaults": get_indicator_params(),
            "strategy_defaults": STRATEGY,
            "timeframes": {"ma_cross": TIMEFRAMES},
            "default_symbol": APP_DEFAULT_SYMBOL,
            "default_tf": TIMEFRAME,
            "default_bars": APP_DEFAULT_BARS,
        }
    )


@app.route("/api/scan")
def api_scan():
    try:
        symbol, tf, bars, params, cfg = _runtime_request()
        sym_id = cfg["symbol_id"]
        df_raw = load_scan_ohlcv(sym_id, n_bars=bars, tf_code=tf, warmup=0, handle_missing="none")
        if df_raw.empty:
            raise RuntimeError(f"No data for {symbol} {tf} (symbol_id={sym_id}).")

        df_ind = add_ma_cross_indicators(df_raw, params)
        sess = session_mask(df_ind, cfg.get("session_hours_utc", []))
        df_sig = detect_ma_cross_signals(df_ind, sess, sym_key=symbol, params=params)
        df_display = df_sig.reset_index().rename(columns={"index": "BarTime"})
        df_scan = df_sig.dropna(subset=INDICATOR_COLS).tail(bars)
        signals = _signals_json(df_scan, params, cfg)

        return jsonify(
            {
                "candles": _candles_json(df_display),
                "indicators": _indicators_json(df_display),
                "signals": signals,
                "stats": _stats(signals),
                "meta": _meta(symbol, tf, bars, params, cfg),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MA Cross chart dashboard.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--default-symbol", default=APP_DEFAULT_SYMBOL)
    parser.add_argument("--default-bars", type=int, default=APP_DEFAULT_BARS)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    global APP_DEFAULT_BARS, APP_DEFAULT_SYMBOL

    args = _parse_args()
    default_symbol = args.default_symbol.upper()
    if default_symbol in SYMBOLS:
        APP_DEFAULT_SYMBOL = default_symbol
    else:
        print(f"[WARN] Unknown default symbol '{default_symbol}', fallback to {APP_DEFAULT_SYMBOL}")
    APP_DEFAULT_BARS = max(50, min(int(args.default_bars), 5000))

    print("\n" + "=" * 60)
    print("  AUTO TRADING - MA CROSS CHART")
    print("  Backend  : Flask")
    print("  Frontend : Lightweight Charts 4.2.1")
    print(f"  Port     : {args.port}")
    print("=" * 60)

    print("\n[Checking SQL Server connection...]")
    if not test_connection():
        print("  Database : WARN - scan API will show the DB error until connection is fixed.")
    else:
        print("  Database : OK")
    print(f"\nReady: http://{args.host}:{args.port}\n")

    app.run(debug=args.debug, host=args.host, port=args.port, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
