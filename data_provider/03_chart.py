# =============================================================================
# data_provider/03_chart.py  —  Chart dashboard (Flask + Lightweight Charts 4.2.1)
# Phiên bản : 4.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# File này là REST API backend cho chart dashboard.
# Frontend là file 03_chart.html (phục vụ tại http://127.0.0.1:8050)
#
# Cấu trúc:
#   GET /                                  → phục vụ 03_chart.html
#   GET /api/symbols                       → danh sách symbol theo nhóm
#   GET /api/timeframes                    → danh sách timeframe code
#   GET /api/candles?symbol=X&tf=Y&bars=N  → dữ liệu OHLCV dạng JSON
#
# Thay đổi từ v3.0 (Dash/Plotly):
#   - Không cần dash/plotly — chỉ cần flask (pip install flask)
#   - Frontend dùng TradingView Lightweight Charts 4.2.1 (CDN, không cần cài)
#   - API thuần JSON → dễ mở rộng, dễ debug
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
_ROOT = Path(__file__).resolve().parent.parent   # data_provider/ → project root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.data_loader import load_candles, load_symbols, load_timeframes
from modules.db_connector import test_connection

# =============================================================================
# CẤU HÌNH
# =============================================================================

PORT         = 8050
DEFAULT_BARS = 500
_HERE        = Path(__file__).resolve().parent   # Thư mục chứa file này (data_provider/)

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
    """
    Trả về danh sách symbol đã gom nhóm theo asset type.

    Response:
        {
          "Indices": ["CAC40", "DAX40", ...],
          "FOREX":   ["EURUSD", ...],
          "Metal & Crypto": ["XAUUSD", "BTCUSD"]
        }
    """
    try:
        return jsonify(load_symbols())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── API: DANH SÁCH TIMEFRAME ────────────────────────────────────────────────

@app.route("/api/timeframes")
def api_timeframes():
    """
    Trả về danh sách timeframe code theo thứ tự TimeframeID.

    Response: ["M5", "M10", "M15", ..., "D1", "W"]
    """
    try:
        return jsonify(load_timeframes())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── API: DỮ LIỆU NẾN ───────────────────────────────────────────────────────

@app.route("/api/candles")
def api_candles():
    """
    Trả về dữ liệu OHLCV dạng JSON cho Lightweight Charts.

    Query params:
        symbol : tên symbol (mặc định: EURUSD)
        tf     : timeframe code (mặc định: H1)
        bars   : số nến tối đa (mặc định: 500)

    Response:
        [
          { "time": 1704067200, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "volume": 12345 },
          ...
        ]

    Ghi chú:
        "time" là Unix timestamp giây (UTC) — Lightweight Charts hiểu đúng múi giờ.
    """
    symbol = request.args.get("symbol", "EURUSD").upper()
    tf     = request.args.get("tf", "H1").upper()
    try:
        bars = int(request.args.get("bars", DEFAULT_BARS))
        bars = max(50, min(bars, 5000))   # Giới hạn hợp lý: [50, 5000]
    except (ValueError, TypeError):
        bars = DEFAULT_BARS

    try:
        df = load_candles(symbol, tf, bars)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if df.empty:
        return jsonify([])

    records = []
    for _, row in df.iterrows():
        # Chuyển datetime sang Unix timestamp (giây)
        # BarTime từ DB là UTC (không có tzinfo) → dùng trực tiếp làm UTC
        ts = int(pd.Timestamp(row["BarTime"]).timestamp())
        volume = row.get("Volume")
        records.append({
            "time":   ts,
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": float(volume) if pd.notna(volume) else 0.0,
        })

    return jsonify(records)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AUTO TRADING — CHART DASHBOARD  (v4.0)")
    print("  Backend  : Flask 3.x")
    print("  Frontend : TradingView Lightweight Charts 4.2.1")
    print(f"  Port     : {PORT}")
    print("=" * 60)

    print("\n[Checking SQL Server connection...]")
    if not test_connection():
        print("ABORT: Không kết nối được SQL Server.")
        sys.exit(1)
    print("  Database : OK")

    print(f"\nReady!")
    print(f"  -> Mo trinh duyet: http://127.0.0.1:{PORT}")
    print(f"  -> Dung server   : Ctrl+C\n")

    # debug=False để không chạy reloader (không cần trong môi trường production)
    app.run(debug=False, host="127.0.0.1", port=PORT, use_reloader=False)
