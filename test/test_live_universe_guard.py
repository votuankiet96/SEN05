"""Tests for the P0-8 fix: live/engine.py's watched-symbol universe is now
operator-configurable (LIVE_ASSET_TYPES) instead of a hardcoded
{"Indice", "Metal", "Crypto"} literal, and an optional EXPECTED_LIVE_SYMBOLS
guard can fail fast if the resolved count silently drifts (e.g. someone
adds a new asset_type to instruments.py without updating LIVE_ASSET_TYPES,
or vice versa).

This is explicitly NOT about deciding whether FOREX should be live - see
docs/OPERATOR_RUNBOOK.md for that open question, left for Kiet to decide.
"""

from __future__ import annotations

import pytest

from core_engine.core.live.engine import (
    _check_expected_live_symbol_count,
    _resolve_ws_symbols,
)


def _symbols():
    return [
        {"tv_symbol": "US500", "asset_type": "Indice"},
        {"tv_symbol": "GOLD", "asset_type": "Metal"},
        {"tv_symbol": "BTCUSD", "asset_type": "Crypto"},
        {"tv_symbol": "EURUSD", "asset_type": "FOREX"},
    ]


def test_resolve_ws_symbols_default_scope_excludes_forex():
    resolved = _resolve_ws_symbols(_symbols(), ("Indice", "Metal", "Crypto"))
    assert {s["tv_symbol"] for s in resolved} == {"US500", "GOLD", "BTCUSD"}


def test_resolve_ws_symbols_is_configurable_to_include_forex():
    # Proves the universe is now driven by settings, not a hardcoded
    # literal - this does NOT mean FOREX should actually be enabled by
    # default; it is a guard test, not a policy change.
    resolved = _resolve_ws_symbols(_symbols(), ("Indice", "Metal", "Crypto", "FOREX"))
    assert {s["tv_symbol"] for s in resolved} == {"US500", "GOLD", "BTCUSD", "EURUSD"}


def test_expected_count_zero_means_no_enforcement():
    _check_expected_live_symbol_count(count=11, expected=0, asset_types=("Indice",))  # must not raise


def test_expected_count_matching_does_not_raise():
    _check_expected_live_symbol_count(count=11, expected=11, asset_types=("Indice", "Metal", "Crypto"))


def test_expected_count_mismatch_raises_with_actionable_message():
    with pytest.raises(RuntimeError, match="EXPECTED_LIVE_SYMBOLS"):
        _check_expected_live_symbol_count(count=12, expected=11, asset_types=("Indice", "Metal", "Crypto"))


def test_expected_symbol_count_default_is_active_and_matches_real_instruments():
    # Round-3 audit NEW finding: EXPECTED_LIVE_SYMBOLS defaulted to 0, which
    # `if expected and count != expected` treats as "guard disabled" - so
    # the drift guard silently never fired in production. The default must
    # be non-zero (enforcing) AND must match what LIVE_ASSET_TYPES actually
    # resolves to today, or every real startup would fail.
    from core_engine.settings import LIVE
    from core_engine.settings.instruments import SYMBOLS

    assert LIVE.expected_symbol_count != 0, "default guard must be enforcing, not disabled"

    resolved = _resolve_ws_symbols(SYMBOLS, LIVE.asset_types)
    _check_expected_live_symbol_count(
        count=len(resolved), expected=LIVE.expected_symbol_count, asset_types=LIVE.asset_types
    )  # must not raise


def test_settings_evidence_reports_11_symbols_and_165_sessions():
    from core_engine.util import cli

    settings = cli._collect_core_settings()
    live = settings["live_fetching"]
    assert live["expected_live_symbols"] == 11
    assert live["resolved_live_symbols"] == 11
    assert live["symbol_timeframe_sessions"] == 165
