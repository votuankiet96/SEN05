from __future__ import annotations

import importlib.util
from pathlib import Path


_LEGACY_CORE_PYTHON_TESTS = {
    "test_combo_replay.py",
    "test_core_python_package_imports.py",
    "test_execution_architecture_rebuild.py",
    "test_execution_legacy_equivalence.py",
    "test_execution_safety.py",
    "test_ma_cross_basket_reversal.py",
    "test_ma_cross_replay.py",
    "test_ma_cross_strategy.py",
    "test_phase2_parameter_ownership_baseline.py",
    "test_portfolio_backtest_integrity.py",
    "test_quality_and_robustness.py",
    "test_sprint1_regression.py",
    "test_sprint2_regression.py",
    "test_sprint3_regression.py",
}


def pytest_ignore_collect(collection_path, config) -> bool:  # type: ignore[no-untyped-def]
    """Skip stale legacy tests when their historical modules are not present."""
    _ = config
    path = Path(str(collection_path))
    if "tests" in path.parts and "pytest" in path.parts:
        return True
    if path.name not in _LEGACY_CORE_PYTHON_TESTS:
        return False
    required_legacy_modules = (
        "core_python.shared.data",
        "core_python.shared.contracts",
        "core_python.strategies.combo.execution",
        "core_python.strategies.ma_cross.execution",
    )
    if any(_missing_module(module) for module in required_legacy_modules):
        return True
    return False


def _missing_module(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is None
    except ModuleNotFoundError:
        return True
