from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core_python"

SHARED_LEGACY_IMPORT_WHITELIST: set[Path] = set()

COMBO_EXECUTION_COMPAT_NAMES = {
    "backtest_fast",
    "backtest_portfolio",
    "backtest_symbol",
    "build_pending_order",
}


def _py_files(path: Path) -> list[Path]:
    return [
        file
        for file in path.rglob("*.py")
        if "__pycache__" not in file.parts
    ]


def _imports(file: Path) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _imported_module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []


def test_shared_does_not_import_strategy_packages_outside_legacy_whitelist() -> None:
    offenders: list[str] = []
    for file in _py_files(CORE / "shared"):
        if file in SHARED_LEGACY_IMPORT_WHITELIST:
            continue
        for node in _imports(file):
            if any(name.startswith("core_python.strategies") for name in _imported_module_names(node)):
                offenders.append(str(file.relative_to(ROOT)))

    assert offenders == []


def test_combo_and_ma_cross_do_not_import_each_other() -> None:
    offenders: list[str] = []
    strategy_roots = {
        "combo": CORE / "strategies" / "combo",
        "ma_cross": CORE / "strategies" / "ma_cross",
    }
    forbidden = {
        "combo": "core_python.strategies.ma_cross",
        "ma_cross": "core_python.strategies.combo",
    }

    for strategy, root in strategy_roots.items():
        for file in _py_files(root):
            for node in _imports(file):
                if any(name.startswith(forbidden[strategy]) for name in _imported_module_names(node)):
                    offenders.append(str(file.relative_to(ROOT)))

    assert offenders == []


def test_strategy_code_does_not_import_combo_engine_through_shared_execution() -> None:
    offenders: list[str] = []
    for file in _py_files(CORE / "strategies") + _py_files(ROOT / "tests"):
        for node in _imports(file):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "core_python.shared.execution":
                continue
            imported_names = {alias.name for alias in node.names}
            if imported_names & COMBO_EXECUTION_COMPAT_NAMES:
                offenders.append(str(file.relative_to(ROOT)))

    assert offenders == []
