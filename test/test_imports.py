"""Smoke test: every module under core_engine must import cleanly.

This is intentionally environment-light: it does not require SQL Server,
TradingView, or Redis to be reachable, because importing a module must
never perform network/DB I/O by itself (any such work belongs behind a
function call). If a module fails to import here, that is itself a bug
worth fixing (accidental import-time I/O), not a reason to skip it.
"""

from __future__ import annotations

import importlib
import pkgutil

import core_engine


def _iter_module_names(package):
    prefix = package.__name__ + "."
    for _finder, name, _is_pkg in pkgutil.walk_packages(package.__path__, prefix):
        yield name


def test_import_every_core_engine_module():
    failures: dict[str, str] = {}
    for module_name in _iter_module_names(core_engine):
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - we want to report every failure
            failures[module_name] = f"{type(exc).__name__}: {exc}"

    assert not failures, "Failed to import modules:\n" + "\n".join(
        f"  - {name}: {err}" for name, err in sorted(failures.items())
    )


def test_cli_entrypoint_importable():
    from core_engine import cli

    parser = cli.build_parser()
    assert parser.prog == "python -m core_engine"
