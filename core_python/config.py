"""
Cấu hình trung tâm cho dashboard tín hiệu SEN05.

Mô tả:
    Nạp metadata từ config.py gốc ở thư mục cha (SEN05 root) bằng importlib,
    sau đó cung cấp các hằng số và hàm truy cứu được dùng bởi toàn bộ core_python.

Đầu vào:
    File config.py tại thư mục cha — chứa danh sách SYMBOLS, TF_MINUTES,
    TF_DISPLAY_ORDER, và thông tin kết nối SQL Server.

Đầu ra:
    - SYMBOLS: dict symbol → metadata (symbol_id, asset_type, buffer X, ...)
    - TF_MINUTES, TF_DISPLAY_ORDER: thông tin khung thời gian
    - Hàm get_symbol(), symbol_names(), timeframe_codes(), sql_connection_string()

Phụ thuộc ngoài:
    config.py ở thư mục gốc SEN05 (nạp động bằng importlib).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


# Nạp config.py gốc từ thư mục cha bằng importlib để tránh circular import.
# Dùng tên module riêng "_sen05_root_config" để không xung đột với namespace.
_ROOT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
_ROOT_SPEC = importlib.util.spec_from_file_location("_sen05_root_config", _ROOT_CONFIG_PATH)
if _ROOT_SPEC is None or _ROOT_SPEC.loader is None:
    raise RuntimeError(f"Cannot load root config from {_ROOT_CONFIG_PATH}")
_ROOT_CONFIG = importlib.util.module_from_spec(_ROOT_SPEC)
_ROOT_SPEC.loader.exec_module(_ROOT_CONFIG)


# Thông tin kết nối SQL Server — lấy từ root config (không hardcode ở đây).
SQL_SERVER = _ROOT_CONFIG.SQL_SERVER
SQL_DATABASE = _ROOT_CONFIG.SQL_DATABASE
SQL_DRIVER = _ROOT_CONFIG.SQL_DRIVER
SQL_UID = _ROOT_CONFIG.SQL_UID
SQL_PWD = _ROOT_CONFIG.SQL_PWD
SQL_ENCRYPT = _ROOT_CONFIG.SQL_ENCRYPT
SQL_TRUST_SERVER_CERT = _ROOT_CONFIG.SQL_TRUST_SERVER_CERT

# Ánh xạ mã khung thời gian → số phút (ví dụ: "H1" → 60, "M5" → 5).
TF_MINUTES: dict[str, int] = dict(_ROOT_CONFIG.TF_MINUTES)

# Thứ tự hiển thị TF trên dropdown của dashboard.
TF_DISPLAY_ORDER: list[str] = list(_ROOT_CONFIG.TF_DISPLAY_ORDER)

DEFAULT_SYMBOL = "BTCUSD"
DEFAULT_TF = "M5"
N_BARS = 500  # Số bar mặc định tải về nếu không chỉ định


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
    for row in _ROOT_CONFIG.SYMBOLS
}


def symbol_names() -> list[str]:
    """
    Trả về danh sách tên symbol theo thứ tự trong root config.

    Returns:
        List các mã symbol (uppercase, ví dụ: ["BTCUSD", "GOLD", "US30", ...]).
    """
    return list(SYMBOLS.keys())


def timeframe_codes() -> list[str]:
    """
    Trả về danh sách mã khung thời gian theo thứ tự hiển thị.

    Returns:
        List mã TF (ví dụ: ["M1", "M5", "H1", "H4", "D1", ...]).
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
    Xây dựng chuỗi kết nối SQL Server theo cùng định dạng với modules.db_connector.

    Returns:
        Chuỗi ODBC connection string. Dùng SQL auth nếu SQL_UID/SQL_PWD được cấu hình,
        ngược lại dùng Windows Integrated Security (Trusted_Connection=yes).
    """
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

