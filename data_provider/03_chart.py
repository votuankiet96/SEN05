"""
Backend Flask cho dashboard xem nen va indicator tu kho du lieu.

File nay khong tao du lieu moi. No chi doc du lieu da sach tu layer doc/phan tich
va phuc vu cho `03_chart.html` qua mot so API nho:
- `/api/symbols`
- `/api/timeframes`
- `/api/candles`

Muc tieu chinh la kiem tra nhanh chat luong data va quan sat logic indicator,
khong phai mot web app giao dich production-facing.
"""

# =============================================================================
# data_provider/03_chart.py  —  Chart dashboard (Flask + Lightweight Charts 4.2.1)
# Phiên bản : 5.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# File này là REST API backend cho chart dashboard.
# Frontend là file 03_chart.html (phục vụ tại http://127.0.0.1:8050)
#
# Cấu trúc:
#   GET /                                      → phục vụ 03_chart.html
#   GET /api/symbols                           → danh sách symbol theo nhóm
#   GET /api/timeframes                        → danh sách TF chia 2 nhóm direct/computed
#   GET /api/candles?symbol=X&tf=Y&bars=N      → dữ liệu OHLCV dạng JSON
#   GET /api/candles?...&ind=MA20,BB,MACD,ATR14 → OHLCV + indicator data
#
# Thay đổi từ v4.0:
#   - /api/timeframes trả về {direct:[...], computed:[...]} thay vì flat list
#   - /api/candles hỗ trợ tham số ?ind= để tính indicator trên server
#   - Thêm _calc_indicators() cho MA, Bollinger Bands, MACD, ATR
#
# Cách chạy:
#   python data_provider/03_chart.py
#   Mở trình duyệt: http://127.0.0.1:8050
#   Dừng server  : Ctrl+C
# =============================================================================

import sys
from pathlib import Path

import pandas as pd

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:
    print("[ERROR] Flask chưa được cài. Cài bằng:")
    print("  pip install flask")
    sys.exit(1)

# Bootstrap: thêm project root vào path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.data_loader import load_candles, load_symbols
from modules.db_connector import test_connection

# =============================================================================
# CẤU HÌNH
# =============================================================================

PORT         = 8050
DEFAULT_BARS = 500
_HERE        = Path(__file__).resolve().parent

# Nhóm timeframe — Direct: kéo thẳng từ TradingView; Computed: tổng hợp từ TF nguồn
_TF_DIRECT   = ["M5", "M15", "M30", "M45", "H1", "H2", "H3", "H4", "D1", "W"]
_TF_COMPUTED = ["M10", "M20", "M90", "H6", "H8"]

# =============================================================================
# FLASK APP
# =============================================================================

app = Flask(__name__)


# ─── TRANG CHÍNH ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Phục vụ file 03_chart.html — dashboard chính."""
    return send_from_directory(_HERE, "03_chart.html")


# ─── API: DANH SÁCH SYMBOL ───────────────────────────────────────────────────

@app.route("/api/symbols")
def api_symbols():
    try:
        return jsonify(load_symbols())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── API: DANH SÁCH TIMEFRAME ────────────────────────────────────────────────

@app.route("/api/timeframes")
def api_timeframes():
    """
    Trả về timeframe chia 2 nhóm.

    Response:
        {
          "direct":   ["M5", "M15", "M30", "M45", "H1", "H2", "H3", "H4", "D1", "W"],
          "computed": ["M10", "M20", "M90", "H6", "H8"]
        }
    """
    return jsonify({"direct": _TF_DIRECT, "computed": _TF_COMPUTED})


# ─── HELPER: TIMESTAMP ───────────────────────────────────────────────────────

def _ts(bar_time) -> int:
    """Convert naive UTC BarTime → Unix seconds (integer)."""
    return int(pd.Timestamp(bar_time).tz_localize("UTC").timestamp())


# ─── HELPER: TÍNH INDICATOR ──────────────────────────────────────────────────

def _calc_indicators(df: pd.DataFrame, ind_spec: str) -> dict:
    """
    Tính các indicator từ chuỗi ind_spec.

    ind_spec format (comma-separated, case-insensitive):
        MA<period>          e.g. MA20
        BB<period>-<std>    e.g. BB20-2.0
        MACD<f>-<s>-<sig>   e.g. MACD12-26-9  hoặc chỉ MACD (dùng mặc định)
        ATR<period>         e.g. ATR14
        ADX<period>         e.g. ADX14
        AITREND<m>-<t>-<k>-<smooth>
                            e.g. AITREND5-5-3-50

    df phải có Title-Case columns: Open, High, Low, Close, BarTime (sorted ascending).
    Trả về dict chứa các series [{time, value}, ...] cho mỗi indicator.
    """
    from modules.indicators import (
        calc_sma,
        calc_ema,
        calc_bollinger,
        calc_macd,
        calc_atr,
        calc_adx,
        calc_ai_trend_navigator,
    )

    if df.empty:
        return {}

    times = [_ts(t) for t in df["BarTime"]]

    def to_list(vals):
        return [{"time": t, "value": float(v)}
                for t, v in zip(times, vals)
                if pd.notna(v)]

    def to_colored_list(vals, colors):
        return [
            {"time": t, "value": float(v), "color": str(c)}
            for t, v, c in zip(times, vals, colors)
            if pd.notna(v)
        ]

    result = {}
    for spec in ind_spec.split(","):
        spec = spec.strip().upper()
        if not spec:
            continue
        try:
            if spec.startswith("MACD"):
                raw    = spec[4:]
                parts  = raw.split("-") if raw else []
                fast   = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 12
                slow   = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 26
                signal = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 9
                ml, sl, hist = calc_macd(df["Close"], fast, slow, signal)
                result["macd_line"]   = to_list(ml)
                result["macd_signal"] = to_list(sl)
                result["macd_hist"]   = [
                    {"time": t, "value": float(v),
                     "color": "#26a69a" if v >= 0 else "#ef5350"}
                    for t, v in zip(times, hist) if pd.notna(v)
                ]

            elif spec.startswith("EMA"):
                period = int(spec[3:]) if len(spec) > 3 and spec[3:].isdigit() else 20
                result[f"ema_{period}"] = to_list(calc_ema(df["Close"], period))

            elif spec.startswith("MA"):
                period = int(spec[2:]) if len(spec) > 2 and spec[2:].isdigit() else 20
                result[f"ma_{period}"] = to_list(calc_sma(df["Close"], period))

            elif spec.startswith("BB"):
                raw   = spec[2:]
                parts = raw.split("-") if raw else []
                period = int(parts[0]) if parts and parts[0].isdigit() else 20
                std    = float(parts[1]) if len(parts) > 1 else 2.0
                mid, upper, lower = calc_bollinger(df, period, std)
                result["bb_upper"] = to_list(upper)
                result["bb_mid"]   = to_list(mid)
                result["bb_lower"] = to_list(lower)

            elif spec.startswith("ATR"):
                period = int(spec[3:]) if len(spec) > 3 and spec[3:].isdigit() else 14
                result["atr"] = to_list(calc_atr(df, period))

            elif spec.startswith("ADX"):
                period = int(spec[3:]) if len(spec) > 3 and spec[3:].isdigit() else 14
                adx, pdi, mdi = calc_adx(df, period)
                result["adx"]     = to_list(adx)
                result["adx_pdi"] = to_list(pdi)
                result["adx_mdi"] = to_list(mdi)

            elif spec.startswith("AITREND") or spec.startswith("KNN"):
                raw = spec[7:] if spec.startswith("AITREND") else spec[3:]
                parts = [p for p in raw.replace(":", "-").split("-") if p]
                ma_len = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 5
                target_len = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
                k = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 3
                smooth = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 50

                ai = calc_ai_trend_navigator(
                    df,
                    price_value="hl2",
                    ma_len=ma_len,
                    target_value="Price Action",
                    target_len=target_len,
                    number_of_closest_values=k,
                    smoothing_period=smooth,
                )
                colors = ai["direction"].map({
                    1: "#00e676",
                    -1: "#ff5252",
                    0: "#ff9800",
                }).fillna("#ff9800")
                result["ai_trend_knn"] = to_colored_list(ai["knn"], colors)
                result["ai_trend_avg"] = to_list(ai["average"])
                result["ai_trend_prediction"] = to_list(ai["prediction"])

        except Exception:
            pass  # bỏ qua spec không hợp lệ

    return result


# ─── API: DỮ LIỆU NẾN ───────────────────────────────────────────────────────

@app.route("/api/candles")
def api_candles():
    """
    Trả về dữ liệu OHLCV + indicators (tùy chọn).

    Query params:
        symbol : tên symbol (mặc định: EURUSD)
        tf     : timeframe code (mặc định: H1)
        bars   : số nến tối đa (mặc định: 500, range [50, 5000])
        ind    : indicator specs, vd: "MA20,BB20-2.0,MACD,ATR14"

    Response:
        {
          "candles": [
            {"time": 1704067200, "open": 1.1, "high": 1.2, "low": 1.0,
             "close": 1.15, "volume": 12345},
            ...
          ],
          "indicators": {          // chỉ có khi ind= được truyền
            "ma":          [{time, value}, ...],
            "bb_upper":    [{time, value}, ...],
            "bb_mid":      [{time, value}, ...],
            "bb_lower":    [{time, value}, ...],
            "macd_line":   [{time, value}, ...],
            "macd_signal": [{time, value}, ...],
            "macd_hist":   [{time, value, color}, ...],
            "atr":         [{time, value}, ...]
          }
        }
    """
    symbol = request.args.get("symbol", "EURUSD").upper()
    tf     = request.args.get("tf", "H1").upper()
    ind    = request.args.get("ind", "").strip()
    try:
        bars = max(50, min(int(request.args.get("bars", DEFAULT_BARS)), 5000))
    except (ValueError, TypeError):
        bars = DEFAULT_BARS

    try:
        df = load_candles(symbol, tf, bars)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if df.empty:
        return jsonify({"candles": [], "indicators": {}})

    candles = []
    for _, row in df.iterrows():
        vol = row.get("Volume")
        candles.append({
            "time":   _ts(row["BarTime"]),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": float(vol) if pd.notna(vol) else 0.0,
        })

    response = {"candles": candles}
    if ind:
        response["indicators"] = _calc_indicators(df, ind)
    return jsonify(response)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AUTO TRADING — CHART DASHBOARD  (v5.0)")
    print("  Backend  : Flask 3.x")
    print("  Frontend : TradingView Lightweight Charts 4.2.1")
    print(f"  Port     : {PORT}")
    print("=" * 60)

    print("\n[Checking SQL Server connection...]")
    if not test_connection():
        print("ABORT: Khong ket noi duoc SQL Server.")
        sys.exit(1)
    print("  Database : OK")

    print(f"\nReady!")
    print(f"  -> Mo trinh duyet: http://127.0.0.1:{PORT}")
    print(f"  -> Dung server   : Ctrl+C\n")

    app.run(debug=False, host="127.0.0.1", port=PORT, use_reloader=False)
