# =============================================================================
# core_engine/settings/instruments.py
# Single source of truth for instrument and timeframe definitions.
#
# Imported by the DP backend and any downstream Python consumers.
# Contains NO credentials, NO pipeline settings, NO package-specific logic.
# =============================================================================

# -----------------------------------------------------------------------------
# SYMBOLS — 37 instruments
#
# symbol_id  : stable integer PK, matches DWH.Dim_Symbol.SymbolID in DB
# tv_symbol  : TradingView/Capital.com ticker used by the DP backend to pull data
# tv_exchange: always CAPITALCOM for this system
# asset_type : FOREX | Indice | Metal | Crypto
# -----------------------------------------------------------------------------
SYMBOLS: list[dict] = [
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
    # FOREX
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

# Asset types that close on weekends (no bars Sat/Sun)
WEEKEND_CLOSED: set[str] = {"FOREX", "Metal", "Indice"}

# -----------------------------------------------------------------------------
# TIMEFRAMES
# -----------------------------------------------------------------------------

# 15 TFs pulled directly from TradingView (the only mode in production)
DIRECT_TFS: set[str] = {
    "W",
    "D1",
    "H8",
    "H6",
    "H4",
    "H3",
    "H2",
    "H1",
    "M90",
    "M45",
    "M30",
    "M20",
    "M15",
    "M10",
    "M5",
}

# Display order for UI and reports (small → large)
TF_DISPLAY_ORDER: list[str] = [
    "M5",
    "M10",
    "M15",
    "M20",
    "M30",
    "M45",
    "H1",
    "M90",
    "H2",
    "H3",
    "H4",
    "H6",
    "H8",
    "D1",
    "W",
]

# Minutes per TF — used for gap detection and freshness calculations
TF_MINUTES: dict[str, int] = {
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "M45": 45,
    "M90": 90,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "D1": 1440,
    "W": 10080,
}
