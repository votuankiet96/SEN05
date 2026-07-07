"""
Cấu hình trung tâm cho dashboard tín hiệu OG (Order Generation).

Mô tả:
    Trước refactor, module này nạp metadata (SQL connection, SYMBOLS,
    TF_MINUTES, TF_DISPLAY_ORDER) từ config.py ở thư mục cha (SEN05 root)
    bằng importlib — nghĩa là OG chỉ chạy được khi đứng đúng vị trí trong cây
    thư mục SEN05_Autotrading đầy đủ.

    Sau refactor "tự chủ hoá": OG không còn phụ thuộc cây thư mục SEN05 root
    nữa. SQL_* đọc từ biến môi trường/.env (xem .env.example). SYMBOLS/
    TF_MINUTES/TF_DISPLAY_ORDER là dữ liệu tĩnh sao chép nguyên văn từ
    SEN05_Autotrading/config.py — đây là dữ liệu tra cứu (symbol_id, mã TF),
    không phải bí mật, nên an toàn khi hardcode trong repo của OG.

Đầu ra:
    - SYMBOLS: dict symbol → metadata (symbol_id, asset_type, buffer X, ...)
    - TF_MINUTES, TF_DISPLAY_ORDER: thông tin khung thời gian
    - Hàm get_symbol(), symbol_names(), timeframe_codes(), sql_connection_string()

Phụ thuộc ngoài:
    Biến môi trường / file .env cho thông tin kết nối SQL Server
    (xem .env.example). Không còn phụ thuộc file config.py ở thư mục cha.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv là optional — vẫn chạy được qua OS env vars
    pass


# -----------------------------------------------------------------------------
# 1. SQL SERVER CONNECTION
#    Mặc định khớp với SEN05_Autotrading/config.py gốc: để trống SQL_UID/
#    SQL_PWD nghĩa là dùng Windows Integrated Auth (Trusted_Connection=yes).
# -----------------------------------------------------------------------------
SQL_SERVER = os.environ.get("SQL_SERVER", "localhost")
SQL_DATABASE = "SEN05_AutoTrading"
SQL_DRIVER = os.environ.get("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
SQL_PORT = os.environ.get("SQL_PORT", "")
SQL_TDS_VERSION = os.environ.get("SQL_TDS_VERSION", "7.4")
SQL_UID = os.environ.get("SQL_UID", "")   # rỗng = Windows Auth
SQL_PWD = os.environ.get("SQL_PWD", "")   # rỗng = Windows Auth
SQL_ENCRYPT = os.environ.get("SQL_ENCRYPT", "no")
SQL_TRUST_SERVER_CERT = os.environ.get("SQL_TRUST_SERVER_CERT", "yes")


# -----------------------------------------------------------------------------
# 2. TIMEFRAMES — sao chép nguyên từ SEN05_Autotrading/config.py.
# -----------------------------------------------------------------------------
# Ánh xạ mã khung thời gian → số phút (ví dụ: "H1" → 60, "M5" → 5).
TF_MINUTES: dict[str, int] = {
    "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
    "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "D1": 1440, "W": 10080,
}

# Thứ tự hiển thị TF trên dropdown của dashboard.
TF_DISPLAY_ORDER: list[str] = [
    "M5", "M10", "M15", "M20", "M30", "M45",
    "H1", "M90", "H2", "H3", "H4", "H6", "H8", "D1", "W",
]

DEFAULT_SYMBOL = "BTCUSD"
DEFAULT_TF = "M5"
N_BARS = 500  # Số bar mặc định tải về nếu không chỉ định


# -----------------------------------------------------------------------------
# 3. SYMBOLS — sao chép nguyên từ SEN05_Autotrading/config.py.
# -----------------------------------------------------------------------------
# Buffer X cho chiến lược Combo — điều chỉnh theo từng symbol.
# X là khoảng cách tính thêm vào đỉnh/đáy bar khi xác định điểm Entry và SL.
# Đơn vị: điểm (points), phù hợp với volatility đặc trưng của từng tài sản.
_COMBO_X = {
    "US30": 10.0,
    "US500": 1.0,
    "US100": 5.0,
    "DE40": 5.0,
    "UK100": 5.0,
    "FR40": 5.0,
    "SP35": 5.0,
    "HK50": 15.0,
    "J225": 15.0,
    "GOLD": 0.5,
    "BTCUSD": 50.0,
}

# Danh sách symbol đầy đủ (37 symbol) — dữ liệu tra cứu, không phải bí mật.
# symbol_id khớp với DWH.Dim_Symbol trên DP6. tv_exchange giữ lại để tham
# chiếu dù OG hiện không dùng trực tiếp (không pull dữ liệu từ TradingView).
_RAW_SYMBOLS: list[dict[str, Any]] = [
    # Indices — Capital.com CFD tickers
    {"symbol_id": 2, "tv_symbol": "FR40", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 3, "tv_symbol": "DE40", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 4, "tv_symbol": "HK50", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 5, "tv_symbol": "J225", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 6, "tv_symbol": "SP35", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 7, "tv_symbol": "UK100", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 8, "tv_symbol": "US500", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 9, "tv_symbol": "US100", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    {"symbol_id": 10, "tv_symbol": "US30", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},
    # FOREX — all use CAPITALCOM exchange
    {"symbol_id": 11, "tv_symbol": "AUDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 12, "tv_symbol": "AUDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 13, "tv_symbol": "AUDNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 14, "tv_symbol": "AUDCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 15, "tv_symbol": "AUDUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 16, "tv_symbol": "GBPAUD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 17, "tv_symbol": "GBPCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 18, "tv_symbol": "GBPJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 19, "tv_symbol": "GBPNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 20, "tv_symbol": "GBPCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 21, "tv_symbol": "GBPUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 22, "tv_symbol": "CADJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 23, "tv_symbol": "CADCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 24, "tv_symbol": "EURAUD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 25, "tv_symbol": "EURGBP", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 26, "tv_symbol": "EURCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 27, "tv_symbol": "EURJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 28, "tv_symbol": "EURNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 32, "tv_symbol": "EURCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 33, "tv_symbol": "EURUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 34, "tv_symbol": "NZDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 35, "tv_symbol": "NZDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 36, "tv_symbol": "NZDUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 37, "tv_symbol": "USDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 41, "tv_symbol": "USDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 48, "tv_symbol": "USDCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    # Metal & Crypto
    {"symbol_id": 56, "tv_symbol": "GOLD", "tv_exchange": "CAPITALCOM", "asset_type": "Metal"},
    {"symbol_id": 81, "tv_symbol": "BTCUSD", "tv_exchange": "CAPITALCOM", "asset_type": "Crypto"},
]

# SYMBOLS: dict chính tra cứu metadata symbol.
# Key = mã TradingView (uppercase). Value chứa:
#   symbol_id   — ID trong bảng DWH.Dim_Symbol (dùng để query DB)
#   label       — Tên hiển thị trên dashboard
#   asset_type  — Loại tài sản (Indice, Metal, Crypto, Forex...)
#   digits      — Số chữ số thập phân (2 cho Index/Metal/Crypto, 5 cho Forex)
#   x           — Buffer breakout cho Combo strategy (lấy từ _COMBO_X)
#   session_hours_utc — Giờ UTC được phép giao dịch ([] = tất cả giờ)
SYMBOLS: dict[str, dict[str, Any]] = {
    str(row["tv_symbol"]).upper(): {
        "symbol_id": int(row["symbol_id"]),
        "label": str(row["tv_symbol"]).upper(),
        "asset_type": row.get("asset_type", ""),
        "point_size": 1.0,
        "digits": 2 if row.get("asset_type") in {"Indice", "Metal", "Crypto"} else 5,
        "x": float(_COMBO_X.get(str(row["tv_symbol"]).upper(), 0.0)),
        "session_hours_utc": [],
    }
    for row in _RAW_SYMBOLS
}


def symbol_names() -> list[str]:
    """
    Trả về danh sách tên symbol theo thứ tự khai báo.

    Returns:
        List các mã symbol (uppercase, ví dụ: ["FR40", "DE40", ..., "BTCUSD"]).
    """
    return list(SYMBOLS.keys())


def timeframe_codes() -> list[str]:
    """
    Trả về danh sách mã khung thời gian theo thứ tự hiển thị.

    Returns:
        List mã TF (ví dụ: ["M5", "M10", ..., "D1", "W"]).
    """
    return list(TF_DISPLAY_ORDER)


def get_symbol(symbol: str) -> dict[str, Any]:
    """
    Tra cứu metadata đầy đủ cho một symbol.

    Args:
        symbol: Mã TradingView (không phân biệt hoa/thường).

    Returns:
        Bản sao dict metadata gồm symbol_id, label, asset_type, digits, x, session_hours_utc.

    Raises:
        KeyError: Nếu symbol không có trong danh sách SYMBOLS.
    """
    key = str(symbol).strip().upper()
    if key not in SYMBOLS:
        raise KeyError(f"Unknown symbol '{symbol}'. Available: {', '.join(SYMBOLS)}")
    return dict(SYMBOLS[key])


def sql_connection_string() -> str:
    """
    Xây dựng chuỗi kết nối SQL Server theo cùng định dạng với
    data/db_connector.py.

    Returns:
        Chuỗi ODBC connection string. Dùng SQL auth nếu SQL_UID/SQL_PWD được cấu hình,
        ngược lại dùng Windows Integrated Security (Trusted_Connection=yes).
    """
    if str(SQL_DRIVER).lower() == "freetds":
        server_part = f"SERVER={SQL_SERVER};"
        if SQL_PORT:
            server_part += f"PORT={SQL_PORT};"
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"{server_part}"
            f"DATABASE={SQL_DATABASE};"
            f"TDS_Version={SQL_TDS_VERSION};"
        )
    else:
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"Encrypt={SQL_ENCRYPT};"
            f"TrustServerCertificate={SQL_TRUST_SERVER_CERT};"
        )
    if SQL_UID and SQL_PWD:
        return base + f"UID={SQL_UID};PWD={SQL_PWD};"
    return base + "Trusted_Connection=yes;"
