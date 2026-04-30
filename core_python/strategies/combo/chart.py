"""Interactive chart dashboard for the Combo strategy.

This file is intentionally strategy-specific. It reuses the Combo scanner and
chart builder instead of redefining signal rules, so visual research stays
aligned with backtest/optimization logic.
"""

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
except ImportError:  # pragma: no cover - import guard for operator-friendly CLI
    print("[ERROR] Flask is not installed. Install it with: pip install flask")
    sys.exit(1)


_ROOT = Path(__file__).resolve().parents[3]
_CORE = _ROOT / "core_python"
_HERE = Path(__file__).resolve().parent
for _path in (_CORE, _ROOT):
    _path_str = str(_path)
    if _path_str in sys.path:
        sys.path.remove(_path_str)
    sys.path.insert(0, _path_str)

from modules.db_connector import test_connection
from core_python.shared.data import load_scan_ohlcv  # noqa: E402
from core_python.strategies.combo.config import (  # noqa: E402
    INDICATOR_COLS,
    SCANNER_DEFAULTS,
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    get_indicator_params,
    get_symbol_params,
    summary as strategy_summary,
)
from core_python.strategies.combo.signals import add_combo_indicators  # noqa: E402
from core_python.strategies.combo.signals import scan_signals_reversal  # noqa: E402
from core_python.strategies.combo.scanner import calc_reversal_stats  # noqa: E402


PORT = 8513
HOST = "127.0.0.1"
TF_DIRECT = ["M5", "M15", "M30", "M45", "H1", "H2", "H3", "H4", "D1", "W"]
TF_COMPUTED = ["M10", "M20", "M90", "H6", "H8"]

APP_DEFAULT_SYMBOL = next(iter(SYMBOLS))
APP_DEFAULT_BARS = 500  # match 03_chart default

app = Flask(__name__)


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _ts(bar_time) -> int:
    """Convert BarTime (naive UTC) → Unix seconds integer."""
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
        "x": params["x"],
        "ktp": params["ktp"],
        "ma_period": params["ma_period"],
        "session_hours_utc": params.get("session_hours_utc", []),
        "spec_verified": params.get("spec_verified", False),
    }


def _runtime_request() -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    symbol = request.args.get("symbol", APP_DEFAULT_SYMBOL).upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown Combo symbol '{symbol}'. Available: {', '.join(SYMBOLS)}")

    defaults = _symbol_defaults(symbol)
    ind = get_indicator_params()
    scan_defaults = SCANNER_DEFAULTS

    tf = request.args.get("tf", TIMEFRAME).upper()
    if tf not in {*TF_DIRECT, *TF_COMPUTED}:
        raise ValueError(f"Unsupported timeframe '{tf}'")

    bars = _to_int(request.args.get("bars"), APP_DEFAULT_BARS, 50, 5000)
    x = _to_float(request.args.get("x"), float(defaults["x"]), 0.0, 1_000_000.0)

    session_hours = _parse_session_hours(
        request.args.get("session_hours"),
        list(defaults.get("session_hours_utc", [])),
    )

    params = {
        "MA_PERIOD": _to_int(
            request.args.get("ma_period"),
            int(defaults.get("ma_period") or ind["MA_PERIOD"]),
            2,
            500,
        ),
        "MACD_FAST": _to_int(request.args.get("macd_fast"), int(ind["MACD_FAST"]), 1, 200),
        "MACD_SLOW": _to_int(request.args.get("macd_slow"), int(ind["MACD_SLOW"]), 2, 300),
        "MACD_SIGNAL": _to_int(request.args.get("macd_signal"), int(ind["MACD_SIGNAL"]), 1, 200),
        "ATR_PERIOD": _to_int(request.args.get("atr_period"), int(ind["ATR_PERIOD"]), 2, 200),
        "KTP": _to_float(request.args.get("ktp"), float(defaults["ktp"]), 0.1, 20.0),
        "MIN_RR": _to_float(request.args.get("min_rr"), float(ind["MIN_RR"]), 0.0, 10.0),
        "ENTRY_LINE_BARS": _to_int(
            request.args.get("entry_line_bars"),
            int(scan_defaults["entry_line_bars"]),
            1,
            200,
        ),
        "SHOW_REJECTED": _to_bool(
            request.args.get("show_rejected"),
            bool(scan_defaults["show_rejected"]),
        ),
        "SHOW_MA": _to_bool(request.args.get("show_ma"), bool(scan_defaults["show_ma"])),
        "SHOW_ENTRY_LINES": _to_bool(
            request.args.get("show_entry_lines"),
            bool(scan_defaults["show_entry_lines"]),
        ),
    }

    cfg = {
        **SYMBOLS[symbol],
        "x": x,
        "session_hours_utc": session_hours,
    }
    return symbol, tf, bars, params, cfg


def _meta(symbol: str, tf: str, bars: int, params: dict[str, Any], cfg: dict[str, Any]) -> str:
    session = cfg.get("session_hours_utc") or "all"
    return (
        f"{symbol} {tf} | bars={bars} | x={cfg['x']} | "
        f"KTP={params['KTP']} | minRR={params['MIN_RR']} | "
        f"MA={params['MA_PERIOD']} | MACD={params['MACD_FAST']}/"
        f"{params['MACD_SLOW']}/{params['MACD_SIGNAL']} | "
        f"ATR={params['ATR_PERIOD']} | session={session}"
    )


def _candles_json(df: pd.DataFrame) -> list[dict]:
    """Build [{time, open, high, low, close, volume}, ...] from df_scan columns."""
    rows = []
    for _, row in df.iterrows():
        vol = row.get("volume", row.get("Volume"))
        rows.append({
            "time":   _ts(row["BarTime"]),
            "open":   float(row["open"]),
            "high":   float(row["high"]),
            "low":    float(row["low"]),
            "close":  float(row["close"]),
            "volume": float(vol) if vol is not None and pd.notna(vol) else 0.0,
        })
    return rows


def _indicators_json(df: pd.DataFrame) -> dict:
    """Build indicator series for Lightweight Charts from df_scan."""
    times = [_ts(t) for t in df["BarTime"]]

    def to_list(col: str) -> list[dict]:
        return [
            {"time": t, "value": float(v)}
            for t, v in zip(times, df[col])
            if pd.notna(v)
        ]

    result: dict = {}
    if "ma" in df.columns:
        result["ma"] = to_list("ma")
    if "macd_h" in df.columns:
        result["macd_hist"] = [
            {"time": t, "value": float(v),
             "color": "#26a69a" if v >= 0 else "#ef5350"}
            for t, v in zip(times, df["macd_h"])
            if pd.notna(v)
        ]
    if "atr" in df.columns:
        result["atr"] = to_list("atr")
    return result


def _signals_json(signals: pd.DataFrame) -> list[dict]:
    """Build signals list with unix `time` field added (for Lightweight Charts markers)."""
    if signals.empty:
        return []
    view = signals.copy()
    # Add unix timestamp for chart markers
    view["time"] = view["bar_time"].apply(_ts)
    # Format bar_time as human-readable string for the table
    view["bar_time"] = pd.to_datetime(view["bar_time"]).dt.strftime("%Y-%m-%d %H:%M")
    # Coerce bool/numpy types to Python native for JSON serialization
    view["pass_rr"]     = view["pass_rr"].astype(bool)
    view["is_reversal"] = view["is_reversal"].astype(bool)
    return json.loads(view.to_json(orient="records"))


# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(_HERE, "combo_chart.html")


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
            "scanner_defaults": SCANNER_DEFAULTS,
            "timeframes": {"direct": TF_DIRECT, "computed": TF_COMPUTED},
            "default_symbol": APP_DEFAULT_SYMBOL,
            "default_tf": TIMEFRAME,
            "default_bars": APP_DEFAULT_BARS,
        }
    )


@app.route("/api/scan")
def api_scan():
    try:
        symbol, tf, bars, params, cfg = _runtime_request()

        # Load đúng `bars` nến bằng symbol_id (không phụ thuộc tên symbol trong DB)
        # warmup=0 → SELECT TOP bars (giống hệt 03_chart về số lượng)
        # handle_missing="none" → bỏ qua detect_missing_bars, giữ nguyên tất cả bar thực
        sym_id = SYMBOLS[symbol]["symbol_id"]
        df_raw = load_scan_ohlcv(sym_id, n_bars=bars, tf_code=tf,
                                 warmup=0, handle_missing="none")
        if df_raw.empty:
            raise RuntimeError(
                f"Không có dữ liệu cho {symbol} {tf} (symbol_id={sym_id}). "
                "Kiểm tra kết nối DB."
            )

        # load_scan_ohlcv trả về lowercase columns — đúng định dạng cho add_combo_indicators
        df_ind = add_combo_indicators(df_raw, params)

        # df_display: tất cả bars để vẽ nến + indicator
        # (NaN indicator ở đầu do warmup tự bị bỏ qua bởi _indicators_json)
        df_display = df_ind.reset_index(drop=True)

        # df_scan: chỉ các bar có đủ indicator (để phát hiện signal chính xác)
        df_scan = (
            df_ind
            .dropna(subset=INDICATOR_COLS)
            .tail(bars)
            .reset_index(drop=True)
        )

        signals = scan_signals_reversal(df_scan, cfg, params)
        stats   = calc_reversal_stats(signals)

        return jsonify(
            {
                "candles":    _candles_json(df_display),
                "indicators": _indicators_json(df_display),
                "signals":    _signals_json(signals),
                "stats":      stats,
                "meta":       _meta(symbol, tf, bars, params, cfg),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Combo strategy chart dashboard.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--default-symbol", default=APP_DEFAULT_SYMBOL)
    parser.add_argument("--default-bars", type=int, default=APP_DEFAULT_BARS)
    parser.add_argument("--check-db-startup", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    global APP_DEFAULT_BARS, APP_DEFAULT_SYMBOL

    args = _parse_args()
    default_symbol = args.default_symbol.upper()
    if default_symbol not in SYMBOLS:
        print(f"[WARN] Unknown default symbol '{default_symbol}', fallback to {APP_DEFAULT_SYMBOL}")
    else:
        APP_DEFAULT_SYMBOL = default_symbol
    APP_DEFAULT_BARS = max(50, min(int(args.default_bars), 5000))

    print("\n" + "=" * 60)
    print("  AUTO TRADING - COMBO CHART")
    print("  Backend  : Flask")
    print("  Frontend : Lightweight Charts 4.2.1")
    print(f"  Port     : {args.port}")
    print("=" * 60)

    if args.check_db_startup:
        print("\n[Checking SQL Server connection...]")
        if not test_connection():
            print("  Database : WARN - scan API will show the DB error until connection is fixed.")
        else:
            print("  Database : OK")
    else:
        print("\nDatabase : startup check skipped; scan API will report DB errors if any.")
    print(f"\nReady: http://{args.host}:{args.port}\n")

    app.run(debug=args.debug, host=args.host, port=args.port, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
