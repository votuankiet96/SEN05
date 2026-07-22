"""Fixed, code-level domain configuration.

Unlike `core_engine.settings.operational`, nothing here is meant to differ
between deployments: timeframe/interval mappings, staging table names, and
default bar counts are part of how the DP Program domain model works, not
something an operator tunes per environment. A couple of tables still allow
an env override for historical compatibility (e.g. `N_BARS_*`), which is why
this module imports the `env_int` helper from `operational`.
"""

from __future__ import annotations

from core_engine.settings.operational import env_int


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
    "W": env_int("N_BARS_W", 1000),
    "D1": env_int("N_BARS_D1", 5000),
    "H8": env_int("N_BARS_H8", 10000),
    "H6": env_int("N_BARS_H6", 10000),
    "H4": env_int("N_BARS_H4", 10000),
    "H3": env_int("N_BARS_H3", 10000),
    "H2": env_int("N_BARS_H2", 10000),
    "H1": env_int("N_BARS_H1", 20000),
    "M90": env_int("N_BARS_M90", 10000),
    "M45": env_int("N_BARS_M45", 10000),
    "M30": env_int("N_BARS_M30", 20000),
    "M20": env_int("N_BARS_M20", 20000),
    "M15": env_int("N_BARS_M15", 20000),
    "M10": env_int("N_BARS_M10", 20000),
    "M5": env_int("N_BARS_M5", 20000),
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
    "Indice": env_int("PIPELINE_OVERNIGHT_GAP_INDICE", 1080, minimum=0),
    "Metal": env_int("PIPELINE_OVERNIGHT_GAP_METAL", 180, minimum=0),
    "FOREX": env_int("PIPELINE_OVERNIGHT_GAP_FOREX", 150, minimum=0),
    "Crypto": env_int("PIPELINE_OVERNIGHT_GAP_CRYPTO", 0, minimum=0),
}

_ORDERED_TFS = ["W", "D1", "H8", "H6", "H4", "H3", "H2", "H1", "M90", "M45", "M30", "M20", "M15", "M10", "M5"]


def get_historical_timeframes() -> list[tuple[str, str, str, int]]:
    return [(TF_INTERVAL_MAP[tf], tf, TF_STAGING[tf], DEFAULT_N_BARS[tf]) for tf in _ORDERED_TFS]
