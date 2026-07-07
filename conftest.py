"""Pytest bootstrap for this checkout.

Production code still imports itself as ``core_python`` (fixed properly in
the packaging stage of the refactor, see plan Giai đoạn 3). Until then, tests
need to import this checkout under that name without executing the real
``config.py``, which loads a sibling ``../config.py`` that does not exist in
this extracted copy (it lives in the original SEN05 project).

This shim only affects the test process. It does not modify any production
file and will be deleted once the packaging stage lands.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _install_core_python_package() -> None:
    if "core_python" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "core_python", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["core_python"] = module
    spec.loader.exec_module(module)


def _install_fake_config() -> None:
    """Stub core_python.config so tests don't need the missing root config.py."""
    if "core_python.config" in sys.modules:
        return
    fake = types.ModuleType("core_python.config")
    # Matches the real SEN05_Autotrading config.py exactly (no M1 timeframe
    # exists in production; M90 does) so these tests reflect real TF behavior.
    fake.TF_MINUTES = {
        "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
        "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
        "H6": 360, "H8": 480, "D1": 1440, "W": 10080,
    }
    fake.TF_DISPLAY_ORDER = [
        "M5", "M10", "M15", "M20", "M30", "M45",
        "H1", "M90", "H2", "H3", "H4", "H6", "H8", "D1", "W",
    ]
    fake.DEFAULT_SYMBOL = "TESTSYM"
    fake.DEFAULT_TF = "M5"
    fake.N_BARS = 500
    fake.SYMBOLS = {
        "TESTSYM": {
            "symbol_id": 1,
            "label": "TESTSYM",
            "asset_type": "Indice",
            "point_size": 1.0,
            "digits": 2,
            "x": 1.0,
            "session_hours_utc": [],
        }
    }

    def get_symbol(symbol: str) -> dict:
        return dict(fake.SYMBOLS[str(symbol).strip().upper()])

    def symbol_names() -> list:
        return list(fake.SYMBOLS.keys())

    def timeframe_codes() -> list:
        return list(fake.TF_DISPLAY_ORDER)

    fake.get_symbol = get_symbol
    fake.symbol_names = symbol_names
    fake.timeframe_codes = timeframe_codes
    sys.modules["core_python.config"] = fake
    sys.modules["core_python"].config = fake


_install_core_python_package()
_install_fake_config()
