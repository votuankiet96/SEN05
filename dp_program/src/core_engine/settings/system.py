"""Fixed, code-level domain configuration.

Nothing here varies by deployment. Changes require a reviewed commit because
they alter the data contract, supported market universe, or storage design.
"""

from __future__ import annotations

LIVE_ASSET_TYPES: tuple[str, ...] = ("Indice", "Metal", "Crypto")
EXPECTED_LIVE_SYMBOLS = 11
STORAGE_MODE = "sql"

TF_INTERVAL_MAP: dict[str, str] = {
    "M5": "5",
    "M10": "10",
    "M15": "15",
    "M20": "20",
    "M30": "30",
    "M45": "45",
    "H1": "60",
    "M90": "90",
    "H2": "120",
    "H3": "180",
    "H4": "240",
    "H6": "360",
    "H8": "480",
    "D1": "1D",
    "W": "1W",
}

TF_STAGING: dict[str, str] = {
    "W": "SEN.TF_W",
    "D1": "SEN.TF_D1",
    "H8": "SEN.TF_H8",
    "H6": "SEN.TF_H6",
    "H4": "SEN.TF_H4",
    "H3": "SEN.TF_H3",
    "H2": "SEN.TF_H2",
    "H1": "SEN.TF_H1",
    "M90": "SEN.TF_M90",
    "M45": "SEN.TF_M45",
    "M30": "SEN.TF_M30",
    "M20": "SEN.TF_M20",
    "M15": "SEN.TF_M15",
    "M10": "SEN.TF_M10",
    "M5": "SEN.TF_M5",
}

DEFAULT_N_BARS: dict[str, int] = {
    "W": 1_000,
    "D1": 5_000,
    "H8": 10_000,
    "H6": 10_000,
    "H4": 10_000,
    "H3": 10_000,
    "H2": 10_000,
    "H1": 20_000,
    "M90": 10_000,
    "M45": 10_000,
    "M30": 20_000,
    "M20": 20_000,
    "M15": 20_000,
    "M10": 20_000,
    "M5": 20_000,
}

SYMBOL_OVERNIGHT_MINS = {
    "US30": 0,
    "US100": 0,
    "US500": 0,
    "DE40": 0,
    "UK100": 0,
    "J225": 0,
    "HK50": 0,
    "FR40": 240,
    "SP35": 800,
}

OVERNIGHT_GAP_MINUTES = {
    "Indice": 1_080,
    "Metal": 180,
    "FOREX": 150,
    "Crypto": 0,
}

_ORDERED_TFS = ["W", "D1", "H8", "H6", "H4", "H3", "H2", "H1", "M90", "M45", "M30", "M20", "M15", "M10", "M5"]


def get_historical_timeframes() -> list[tuple[str, str, str, int]]:
    return [(TF_INTERVAL_MAP[tf], tf, TF_STAGING[tf], DEFAULT_N_BARS[tf]) for tf in _ORDERED_TFS]
