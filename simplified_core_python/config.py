"""Minimal runtime config for the simplified signal dashboard."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


_ROOT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
_ROOT_SPEC = importlib.util.spec_from_file_location("_sen05_root_config", _ROOT_CONFIG_PATH)
if _ROOT_SPEC is None or _ROOT_SPEC.loader is None:
    raise RuntimeError(f"Cannot load root config from {_ROOT_CONFIG_PATH}")
_ROOT_CONFIG = importlib.util.module_from_spec(_ROOT_SPEC)
_ROOT_SPEC.loader.exec_module(_ROOT_CONFIG)


SQL_SERVER = _ROOT_CONFIG.SQL_SERVER
SQL_DATABASE = _ROOT_CONFIG.SQL_DATABASE
SQL_DRIVER = _ROOT_CONFIG.SQL_DRIVER
SQL_UID = _ROOT_CONFIG.SQL_UID
SQL_PWD = _ROOT_CONFIG.SQL_PWD
SQL_ENCRYPT = _ROOT_CONFIG.SQL_ENCRYPT
SQL_TRUST_SERVER_CERT = _ROOT_CONFIG.SQL_TRUST_SERVER_CERT

TF_MINUTES: dict[str, int] = dict(_ROOT_CONFIG.TF_MINUTES)
TF_DISPLAY_ORDER: list[str] = list(_ROOT_CONFIG.TF_DISPLAY_ORDER)

DEFAULT_SYMBOL = "BTCUSD"
DEFAULT_TF = "M5"
N_BARS = 500


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
    """Return dashboard symbol names in root config order."""
    return list(SYMBOLS.keys())


def timeframe_codes() -> list[str]:
    """Return dashboard timeframe codes in root display order."""
    return list(TF_DISPLAY_ORDER)


def get_symbol(symbol: str) -> dict[str, Any]:
    """Return minimal symbol metadata for a TradingView/DB symbol code."""
    key = str(symbol).strip().upper()
    if key not in SYMBOLS:
        raise KeyError(f"Unknown symbol '{symbol}'. Available: {', '.join(SYMBOLS)}")
    return dict(SYMBOLS[key])


def sql_connection_string() -> str:
    """Build the same SQL Server connection string used by modules.db_connector."""
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

