from __future__ import annotations

import importlib


def test_core_python_strategy_packages_import_without_path_hacks() -> None:
    """Core strategy packages should import through the installed package name."""
    for module_name in [
        "core_python",
        "core_python.shared",
        "core_python.strategies.combo",
        "core_python.strategies.ma_cross",
    ]:
        importlib.import_module(module_name)


def test_strategy_public_backtest_entrypoints_are_reachable() -> None:
    combo = importlib.import_module("core_python.strategies.combo")
    ma_cross = importlib.import_module("core_python.strategies.ma_cross")

    assert callable(combo.run_symbol_backtest)
    assert callable(ma_cross.run_symbol_backtest)
