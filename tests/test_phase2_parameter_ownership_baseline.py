from __future__ import annotations

import importlib

import pandas as pd

from core_python.shared import instruments as shared_instruments
from core_python.strategies import combo as combo_package
from core_python.strategies.combo import model as combo_indicators
from core_python.strategies.combo import model as combo_logic
from core_python.strategies.combo import model as combo_rules
from core_python.strategies.combo import model as combo_signals
from core_python.strategies.combo import params as combo_config
from core_python.strategies.combo import params as combo_parameters
from core_python.strategies.ma_cross import params as ma_cross_config
from core_python.strategies.ma_cross import params as ma_cross_parameters


REPRESENTATIVE_SYMBOLS = ("US30", "US500", "GOLD", "BTCUSD")
PHASE2_OWNERSHIP_SCOPE = ("combo", "ma_cross")
PHASE2_DEFERRED_STRATEGIES = ()


EXPECTED_SHARED_METADATA = {
    "US30": {
        "symbol_id": 10,
        "label": "US30 (Dow Jones)",
        "group": "US",
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 2.0,
        "commission_per_lot": 3.5,
        "slippage_pts": 0.5,
        "min_lot_size": 0.01,
        "max_lot_size": 50.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": -3.5,
        "swap_short_per_lot_per_day": 1.2,
    },
    "US500": {
        "symbol_id": 8,
        "label": "US500 (S&P 500)",
        "group": "US",
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 0.4,
        "commission_per_lot": 3.5,
        "slippage_pts": 0.1,
        "min_lot_size": 0.01,
        "max_lot_size": 50.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
    },
    "GOLD": {
        "symbol_id": 56,
        "label": "GOLD (XAU/USD)",
        "group": "METAL",
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 0.3,
        "commission_per_lot": 3.5,
        "slippage_pts": 0.1,
        "min_lot_size": 0.01,
        "max_lot_size": 20.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
    },
    "BTCUSD": {
        "symbol_id": 81,
        "label": "BTCUSD (Bitcoin)",
        "group": "CRYPTO",
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 5.0,
        "commission_per_lot": 3.5,
        "slippage_pts": 2.0,
        "min_lot_size": 0.001,
        "max_lot_size": 5.0,
        "lot_step": 0.001,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
    },
}

EXPECTED_COMBO_STRATEGY_PARAMS = {
    "US30": {"x": 10.0, "ktp": 2.272, "ma_period": 20, "trailing_activation": 1.0},
    "US500": {"x": 1.0, "ktp": 2.272, "ma_period": 20, "trailing_activation": 1.0},
    "GOLD": {"x": 0.5, "ktp": 2.272, "ma_period": 20, "trailing_activation": 1.0},
    "BTCUSD": {"x": 50.0, "ktp": 2.272, "ma_period": 20, "trailing_activation": 1.0},
}

def _subset(source: dict, keys: set[str]) -> dict:
    return {key: source[key] for key in keys}


def test_shared_metadata_snapshot_for_representative_symbols() -> None:
    """Freeze current shared instrument/broker metadata before Phase 2 moves."""
    metadata_keys = set(next(iter(EXPECTED_SHARED_METADATA.values())))

    for symbol in REPRESENTATIVE_SYMBOLS:
        cfg = shared_instruments.get_base_symbol_params(symbol)
        assert _subset(cfg, metadata_keys) == EXPECTED_SHARED_METADATA[symbol]


def test_combo_effective_params_snapshot_for_representative_symbols() -> None:
    """Freeze current Combo effective params while they still read shared x/session fields."""
    metadata_keys = set(next(iter(EXPECTED_SHARED_METADATA.values())))

    for symbol in REPRESENTATIVE_SYMBOLS:
        cfg = combo_config.get_symbol_params(symbol)
        assert _subset(cfg, metadata_keys) == EXPECTED_SHARED_METADATA[symbol]
        assert _subset(cfg, {"x", "ktp", "ma_period", "trailing_activation"}) == (
            EXPECTED_COMBO_STRATEGY_PARAMS[symbol]
        )
        assert cfg["session_hours_utc"] == []
        # Current public shape: min_rr and pending_ttl_bars are account/strategy
        # settings, not per-symbol outputs.
        assert "min_rr" not in cfg
        assert "pending_ttl_bars" not in cfg


def test_combo_owned_per_symbol_params_mirror_current_effective_config() -> None:
    """Phase 2.3A: Combo owns a passive destination for x/session migration."""
    assert set(combo_parameters.COMBO_SYMBOL_PARAMS) == set(combo_parameters.SYMBOLS)
    assert combo_parameters.TIMEFRAMES == ["H2", "H3", "H4"]

    for symbol in combo_parameters.SYMBOLS:
        cfg = combo_config.get_symbol_params(symbol)
        owned = combo_parameters.get_combo_symbol_params(symbol)

        assert set(owned) == {"x", "session_hours_utc"}
        assert owned["x"] > 0
        assert owned["x"] == cfg["x"]
        assert owned["session_hours_utc"] == cfg["session_hours_utc"]

    for symbol in REPRESENTATIVE_SYMBOLS:
        cfg = combo_config.get_symbol_params(symbol)
        owned = combo_parameters.get_combo_symbol_params(symbol)

        assert owned["x"] == cfg["x"]
        assert owned["session_hours_utc"] == cfg["session_hours_utc"]


def test_combo_effective_config_uses_combo_owned_symbol_params(monkeypatch) -> None:
    """Phase 2.3B: Combo no longer reads x/session from shared instruments."""
    original = combo_parameters.SYMBOLS["US30"].copy()
    monkeypatch.setitem(
        combo_parameters.SYMBOLS,
        "US30",
        {**original, "x": 999.0, "session_hours_utc": [1, 5, 9]},
    )

    cfg = combo_config.get_symbol_params("US30")
    owned = combo_parameters.get_combo_symbol_params("US30")

    assert cfg["x"] == owned["x"] == EXPECTED_COMBO_STRATEGY_PARAMS["US30"]["x"]
    assert cfg["session_hours_utc"] == owned["session_hours_utc"] == []


def _combo_signal_x_probe_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open": 90.0,
                "high": 91.0,
                "low": 89.0,
                "close": 90.0,
                "ma": 100.0,
                "prev_ma": 100.0,
                "prev_close": 90.0,
                "macd_h": 0.0,
                "atr": 20.0,
            },
            {
                "open": 100.0,
                "high": 106.0,
                "low": 105.0,
                "close": 105.0,
                "ma": 100.0,
                "prev_ma": 100.0,
                "prev_close": 90.0,
                "macd_h": 1.0,
                "atr": 20.0,
            },
        ],
        index=pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 04:00"]),
    )


def test_combo_detect_signals_uses_combo_owned_x_not_shared_x(monkeypatch) -> None:
    """Phase 2.3C: signal logic must not fall back to shared SYMBOLS x."""
    original = shared_instruments.SYMBOLS["US30"].copy()
    monkeypatch.setitem(
        shared_instruments.SYMBOLS,
        "US30",
        {**original, "x": 999.0},
    )

    df = _combo_signal_x_probe_frame()
    mask = pd.Series(True, index=df.index)
    out = combo_logic.detect_combo_signals(
        df,
        mask,
        sym_key="US30",
        params={**combo_config.get_indicator_params(), "MIN_RR": 1.25},
    )

    assert out["signal"].iloc[1] == 1
    assert out["rr"].iloc[1] > 1.25


def test_combo_detect_signals_prefers_optimizer_x_override() -> None:
    """Phase 2.3C: params['X'] remains the highest-priority x override."""
    df = _combo_signal_x_probe_frame()
    mask = pd.Series(True, index=df.index)
    out = combo_logic.detect_combo_signals(
        df,
        mask,
        sym_key="US30",
        params={**combo_config.get_indicator_params(), "X": 999.0, "MIN_RR": 1.25},
    )

    assert out["signal"].iloc[1] == 0
    assert out["rr"].iloc[1] < 1.25


def test_combo_logic_split_import_compatibility() -> None:
    """Wide clean: logic.py remains a compatibility re-export surface."""
    for module_name in (
        "core_python.strategies.combo.model",
        "core_python.strategies.combo.model",
        "core_python.strategies.combo.model",
        "core_python.strategies.combo.model",
    ):
        importlib.import_module(module_name)

    assert combo_logic.add_combo_indicators is combo_indicators.add_combo_indicators
    assert combo_logic.add_backtest_indicators is combo_indicators.add_backtest_indicators
    assert combo_logic.session_mask is combo_signals.session_mask
    assert combo_logic.detect_combo_signals is combo_signals.detect_combo_signals
    assert combo_logic.detect_signals is combo_signals.detect_signals
    assert combo_logic.build_raw_signal_masks is combo_rules.build_raw_signal_masks
    assert combo_logic.build_signal_record is combo_rules.build_signal_record
    assert combo_logic.resolve_trade_hit is combo_rules.resolve_trade_hit
    assert combo_logic.scan_signals_reversal is combo_rules.scan_signals_reversal


def test_combo_split_modules_match_logic_wrapper_behavior() -> None:
    """Wide clean: split modules and legacy logic wrapper produce identical output."""
    df = _combo_signal_x_probe_frame()
    mask = pd.Series(True, index=df.index)
    params = combo_config.get_indicator_params()

    via_logic = combo_logic.detect_combo_signals(
        df,
        mask,
        sym_key="US30",
        params=params,
    )
    via_signals = combo_signals.detect_combo_signals(
        df,
        mask,
        sym_key="US30",
        params=params,
    )

    pd.testing.assert_frame_equal(via_logic, via_signals)


def test_combo_parameters_are_canonical_internal_api_and_config_matches() -> None:
    """Phase 2.2A: Combo internals should target parameters; config remains wrapper."""
    assert combo_config.STRATEGY == combo_parameters.STRATEGY
    assert combo_config.ACCOUNT_MODES == combo_parameters.ACCOUNT_MODES
    assert combo_config.OPTIMIZATION == combo_parameters.OPTIMIZATION
    assert combo_config.SCANNER_DEFAULTS == combo_parameters.SCANNER_DEFAULTS
    assert combo_config.SYMBOLS is combo_parameters.SYMBOLS

    for symbol in REPRESENTATIVE_SYMBOLS:
        assert combo_config.get_symbol_params(symbol) == combo_parameters.get_symbol_params(symbol)
        assert combo_config.get_symbol_search_space(symbol) == (
            combo_parameters.get_symbol_search_space(symbol)
        )

    assert combo_config.get_indicator_params() == combo_parameters.get_indicator_params()
    assert combo_config.get_account_settings("standard") == (
        combo_parameters.get_account_settings("standard")
    )
    assert combo_config.get_account_settings("ftmo") == combo_parameters.get_account_settings("ftmo")
    assert combo_config.summary() == combo_parameters.summary()
    assert combo_config.validate_config() == combo_parameters.validate_config()


def test_combo_public_exports_keep_wrapper_compatibility() -> None:
    """Batch Clean 2: parameters, config, and package exports stay aligned."""
    required_exports = {
        "COMBO_SYMBOL_PARAMS",
        "TIMEFRAMES",
        "get_combo_symbol_params",
        "get_symbol_config",
        "get_symbol_params",
        "summary",
        "validate_config",
    }

    assert required_exports.issubset(set(combo_parameters.__all__))
    assert required_exports.issubset(set(combo_config.__all__))
    assert required_exports.issubset(set(combo_package.__all__))

    for name in required_exports:
        assert getattr(combo_config, name) is getattr(combo_parameters, name)
        assert getattr(combo_package, name) is getattr(combo_config, name)


def test_phase2_ownership_direction_is_combo_and_ma_cross_only() -> None:
    """Document Phase 2 scope: config modules are canonical, params names are aliases."""
    assert PHASE2_OWNERSHIP_SCOPE == ("combo", "ma_cross")
    assert PHASE2_DEFERRED_STRATEGIES == ()

    assert combo_parameters.__name__ == "core_python.strategies.combo.config"
    assert combo_config.__name__ == "core_python.strategies.combo.config"
    assert ma_cross_parameters.__name__ == "core_python.strategies.ma_cross.config"
    assert ma_cross_config.__name__ == "core_python.strategies.ma_cross.config"

    # Current wrapper style is sys.modules aliasing: public compatibility names
    # point at the canonical implementation objects in config.py.
    assert combo_config.get_symbol_params is combo_parameters.get_symbol_params
    assert ma_cross_config.get_symbol_params is ma_cross_parameters.get_symbol_params


def test_ma_cross_symbol_params_do_not_inherit_combo_only_keys() -> None:
    cfg = ma_cross_config.get_symbol_params("US30")

    assert cfg["order_type"] == "market"
    for combo_only_key in ("x", "ktp", "ma_period", "min_rr"):
        assert combo_only_key not in cfg

    # Transitional Phase 2 behavior: shared base params still expose this session
    # filter field. Future Phase 2 migration should move strategy sessions out
    # of shared instruments without breaking this baseline first.
    assert cfg["session_hours_utc"] == []


def test_ma_cross_parameters_are_canonical_internal_api_and_config_matches() -> None:
    """Phase 2.2A: MA Cross internals should target parameters; config remains wrapper."""
    assert ma_cross_config.STRATEGY == ma_cross_parameters.STRATEGY
    assert ma_cross_config.ACCOUNT_MODES == ma_cross_parameters.ACCOUNT_MODES
    assert ma_cross_config.OPTIMIZATION == ma_cross_parameters.OPTIMIZATION
    assert ma_cross_config.SYMBOLS is ma_cross_parameters.SYMBOLS

    for symbol in REPRESENTATIVE_SYMBOLS:
        cfg = ma_cross_config.get_symbol_params(symbol)
        assert cfg == ma_cross_parameters.get_symbol_params(symbol)
        assert cfg["order_type"] == "market"
        for combo_only_key in ("x", "ktp", "ma_period", "min_rr"):
            assert combo_only_key not in cfg
        assert ma_cross_config.get_symbol_search_space(symbol) == (
            ma_cross_parameters.get_symbol_search_space(symbol)
        )

    assert ma_cross_config.get_indicator_params() == ma_cross_parameters.get_indicator_params()
    assert ma_cross_config.get_account_settings("standard") == (
        ma_cross_parameters.get_account_settings("standard")
    )
    assert ma_cross_config.get_account_settings("ftmo") == (
        ma_cross_parameters.get_account_settings("ftmo")
    )
    assert ma_cross_config.summary() == ma_cross_parameters.summary()


def test_config_api_snapshots_remain_stable() -> None:
    assert combo_config.get_indicator_params() == {
        "MA_PERIOD": 20,
        "MACD_FAST": 5,
        "MACD_SLOW": 25,
        "MACD_SIGNAL": 5,
        "ATR_PERIOD": 5,
        "KTP": 2.272,
        "MIN_RR": None,
    }
    assert _subset(
        combo_config.get_account_settings("standard"),
        {"risk_per_trade", "daily_loss_limit", "max_drawdown_limit", "pending_ttl_bars",
         "partial_tp_fraction", "trailing_activation", "min_rr", "ktp"},
    ) == {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.5,
        "trailing_activation": 1.0,
        "min_rr": None,
        "ktp": 2.272,
    }
    assert _subset(
        combo_config.get_account_settings("ftmo"),
        {"risk_per_trade", "daily_loss_limit", "max_drawdown_limit", "pending_ttl_bars",
         "partial_tp_fraction", "trailing_activation", "min_rr", "ktp"},
    ) == {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 0.05,
        "max_drawdown_limit": 0.10,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.5,
        "trailing_activation": 1.0,
        "min_rr": None,
        "ktp": 2.272,
    }
    assert combo_config.get_symbol_search_space("US30") == {
        "ktp": [1.618, 2.0, 2.272, 2.618, 3.0],
        "x": [8.0, 10.0, 12.0],
        "min_rr": [None],
    }
    assert ma_cross_config.get_indicator_params() == {
        "MA_TYPE": "sma",
        "FAST_MA": 10,
        "SLOW_MA": 20,
        "ATR_PERIOD": 14,
    }
def test_no_forex_symbol_is_currently_available_for_phase2_baseline() -> None:
    """Document current coverage: Phase 2 cannot snapshot Forex until metadata exists."""
    forex_symbols = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD",
        "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
    }

    assert forex_symbols.isdisjoint(shared_instruments.SYMBOLS)
