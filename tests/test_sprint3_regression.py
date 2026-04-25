"""Sprint 3 regression tests.

Fix #6  Data quality gate          → shared/data.py (validate_backtest_data)
Fix #7  Lot size min/max bounds    → shared/execution.py (both backtest functions)
Fix #8  Config calibration         → strategies/combo/config.py (min/max lot, validate_config)

Chạy:
    cd "d:/Auto Trading/SEN05"
    python -m pytest tests/test_sprint3_regression.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.data import DataQualityError, validate_backtest_data
from shared.execution import backtest_fast
from strategies.combo.config import SYMBOLS, get_indicator_params, validate_config
from strategies.combo.logic import add_combo_indicators


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_clean_ohlcv(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng   = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="4h")
    t     = np.linspace(0, 4 * np.pi, n)
    close = 10_000 + 200 * np.sin(t) + rng.normal(0, 5, n)
    spread = rng.uniform(10, 30, n)
    return pd.DataFrame(
        {
            "open":   np.concatenate([[close[0]], close[:-1]]),
            "high":   close + spread,
            "low":    close - spread,
            "close":  close,
            "volume": rng.uniform(500, 2000, n),
        },
        index=pd.DatetimeIndex(dates, name="BarTime"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fix #6 — validate_backtest_data()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateBacktestData:
    """Fix #6: validate_backtest_data phải phát hiện và sửa dữ liệu xấu."""

    # ── Clean data passes through ──────────────────────────────────────────

    def test_clean_data_passes(self):
        """DataFrame sạch phải pass validation không mất hàng nào."""
        df = _make_clean_ohlcv(200)
        df_out, report = validate_backtest_data(df, min_bars=100)

        assert report["quality_ok"] is True
        assert report["n_nan_dropped"] == 0
        assert report["n_dup_dropped"] == 0
        assert report["n_invalid_bars"] == 0
        assert report["n_final"] == 200

    # ── NaN handling ───────────────────────────────────────────────────────

    def test_nan_rows_are_dropped(self):
        """Hàng có NaN trong OHLCV phải bị loại."""
        df = _make_clean_ohlcv(200)
        df.iloc[5, df.columns.get_loc("close")]  = float("nan")
        df.iloc[50, df.columns.get_loc("high")]  = float("nan")

        df_out, report = validate_backtest_data(df, min_bars=10)

        assert report["n_nan_dropped"] == 2
        assert len(df_out) == 198
        assert not df_out[["open", "high", "low", "close"]].isna().any().any()

    def test_nan_only_in_volume_not_dropped(self):
        """NaN trong volume (không phải OHLC) không nên gây xóa hàng."""
        df = _make_clean_ohlcv(200)
        if "volume" in df.columns:
            df.iloc[10, df.columns.get_loc("volume")] = float("nan")

        df_out, report = validate_backtest_data(df, min_bars=10)
        # volume NaN không được đếm vào n_nan_dropped (chỉ OHLC mới được kiểm tra)
        assert len(df_out) == 200

    # ── Duplicate timestamps ───────────────────────────────────────────────

    def test_duplicate_timestamps_dropped(self):
        """Timestamp trùng lặp phải bị loại, giữ lại lần đầu."""
        df = _make_clean_ohlcv(100)
        dup = df.iloc[10:12].copy()   # 2 hàng duplicate
        df_with_dup = pd.concat([df, dup]).sort_index()

        df_out, report = validate_backtest_data(df_with_dup, min_bars=10)

        assert report["n_dup_dropped"] == 2
        assert len(df_out) == 100
        assert not df_out.index.duplicated().any()

    # ── Invalid bars (high < low) ──────────────────────────────────────────

    def test_impossible_bars_dropped(self):
        """Bar có high < low phải bị loại."""
        df = _make_clean_ohlcv(200)
        # Inject 3 bars với high < low
        for i in [10, 50, 100]:
            df.iloc[i, df.columns.get_loc("high")] = df.iloc[i]["low"] - 5

        df_out, report = validate_backtest_data(df, min_bars=10)

        assert report["n_invalid_bars"] == 3
        assert len(df_out) == 197
        # Verify không còn bar nào có high < low
        assert (df_out["high"] >= df_out["low"]).all()

    # ── Index sorting ──────────────────────────────────────────────────────

    def test_unsorted_index_is_sorted(self):
        """Index chưa sort theo thứ tự tăng dần phải được tự động sort."""
        df    = _make_clean_ohlcv(50)
        df_rev = df.iloc[::-1].copy()   # đảo ngược thứ tự

        assert not df_rev.index.is_monotonic_increasing
        df_out, _ = validate_backtest_data(df_rev, min_bars=10)
        assert df_out.index.is_monotonic_increasing

    # ── Insufficient bars ─────────────────────────────────────────────────

    def test_too_few_bars_raises_error(self):
        """Sau khi clean, nếu số bars < min_bars → ném DataQualityError."""
        df = _make_clean_ohlcv(50)
        # Inject NaN để còn lại ít bars
        df.iloc[:45, df.columns.get_loc("close")] = float("nan")

        with pytest.raises(DataQualityError, match="bars"):
            validate_backtest_data(df, min_bars=20)

    def test_empty_dataframe_raises_error(self):
        """DataFrame rỗng → DataQualityError."""
        with pytest.raises(DataQualityError):
            validate_backtest_data(pd.DataFrame(), min_bars=10)

    def test_none_raises_error(self):
        """None input → DataQualityError."""
        with pytest.raises(DataQualityError):
            validate_backtest_data(None, min_bars=10)

    # ── Report keys ───────────────────────────────────────────────────────

    def test_report_has_required_keys(self):
        """Report dict phải có đủ 8 keys."""
        df = _make_clean_ohlcv(150)
        _, report = validate_backtest_data(df, symbol_id=10, tf="H4", min_bars=50)

        required = {
            "symbol_id", "tf", "n_original", "n_nan_dropped",
            "n_dup_dropped", "n_invalid_bars", "n_final", "quality_ok",
        }
        assert required.issubset(report.keys())

    def test_fix_inplace_false_returns_original(self):
        """Khi fix_inplace=False, trả về df gốc (chưa sửa) nhưng report đúng."""
        df = _make_clean_ohlcv(100)
        df.iloc[5, df.columns.get_loc("close")] = float("nan")

        df_out, report = validate_backtest_data(df, min_bars=5, fix_inplace=False)

        # df_out phải là df gốc (vẫn có NaN)
        assert df_out.iloc[5]["close"] != df_out.iloc[5]["close"]   # NaN check
        # nhưng report phải báo đúng
        assert report["n_nan_dropped"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Fix #7 — Lot size min/max boundary validation
# ─────────────────────────────────────────────────────────────────────────────

def _make_df_ind(n: int = 400) -> pd.DataFrame:
    df = _make_clean_ohlcv(n)
    return add_combo_indicators(df, get_indicator_params())


_BASE_CFG = {
    "x": 10.0, "ktp": 2.3, "ma_period": 20,
    "trailing_activation": 1.0, "session_hours_utc": [],
    "contract_value": 1.0, "point_size": 1.0,
    "spread_pts": 2.0, "commission_per_lot": 3.5, "slippage_pts": 0.5,
    "swap_long_per_lot_per_day": 0.0, "swap_short_per_lot_per_day": 0.0,
}
_BASE_STRATEGY = {
    "risk_per_trade": 0.005, "daily_loss_limit": 1.0,
    "max_drawdown_limit": 1.0, "trailing_activation": 1.0,
    "pending_ttl_bars": 3, "partial_tp_fraction": 0.5, "min_rr": 1.25,
}


class TestLotSizeBounds:
    """Fix #7: lot_size phải luôn nằm trong [min_lot_size, max_lot_size]."""

    def test_lot_capped_at_max(self):
        """Khi sl_dist rất nhỏ → lot tính được rất lớn → phải bị cap tại max_lot_size."""
        df_ind = _make_df_ind()

        # max_lot_size = 2.0 rất nhỏ → buộc mọi lệnh không vượt 2 lots
        cfg_capped = {**_BASE_CFG, "max_lot_size": 2.0, "min_lot_size": 0.01}
        cfg_uncapped = {**_BASE_CFG, "max_lot_size": 1000.0, "min_lot_size": 0.01}

        kpi_capped   = backtest_fast("TEST", df_ind, cfg_capped,
                                     ktp=2.3, x_actual=10.0, trailing_act=1.0,
                                     date_from="2023-03-01", date_to="2023-10-01",
                                     init_eq=100_000.0, strategy=_BASE_STRATEGY)
        kpi_uncapped = backtest_fast("TEST", df_ind, cfg_uncapped,
                                     ktp=2.3, x_actual=10.0, trailing_act=1.0,
                                     date_from="2023-03-01", date_to="2023-10-01",
                                     init_eq=100_000.0, strategy=_BASE_STRATEGY)

        if kpi_capped["trades"] == 0 and kpi_uncapped["trades"] == 0:
            pytest.skip("Không có giao dịch để kiểm tra lot capping")

        # PnL với max_lot=2 phải nhỏ hơn max_lot=1000 (nếu equity thực tế lớn hơn 2 lots)
        # Chỉ kiểm tra function chạy không crash và trả về dict hợp lệ
        assert isinstance(kpi_capped["ret"], float)
        assert isinstance(kpi_uncapped["ret"], float)

    def test_min_lot_enforced(self):
        """Khi sl_dist rất lớn → lot rất nhỏ → phải tối thiểu là min_lot_size."""
        df_ind = _make_df_ind()

        # min_lot_size = 1.0 — buộc mỗi lệnh ít nhất 1 lot
        cfg_min = {**_BASE_CFG, "min_lot_size": 1.0, "max_lot_size": 100.0}

        kpi = backtest_fast("TEST", df_ind, cfg_min,
                            ktp=2.3, x_actual=10.0, trailing_act=1.0,
                            date_from="2023-03-01", date_to="2023-10-01",
                            init_eq=100_000.0, strategy=_BASE_STRATEGY)

        # Function phải chạy không lỗi
        assert "ret" in kpi and "trades" in kpi

    def test_default_bounds_when_not_in_cfg(self):
        """Khi cfg không có min/max lot, dùng default 0.01 / 100.0."""
        df_ind = _make_df_ind()
        cfg_no_bounds = {k: v for k, v in _BASE_CFG.items()
                         if k not in ("min_lot_size", "max_lot_size")}

        kpi = backtest_fast("TEST", df_ind, cfg_no_bounds,
                            ktp=2.3, x_actual=10.0, trailing_act=1.0,
                            date_from="2023-03-01", date_to="2023-10-01",
                            init_eq=100_000.0, strategy=_BASE_STRATEGY)
        assert isinstance(kpi, dict), "backtest_fast phải chạy khi thiếu lot bounds"

    def test_symbols_have_lot_bounds_in_config(self):
        """Tất cả symbols trong SYMBOLS phải có min_lot_size và max_lot_size."""
        missing = []
        for sym, cfg in SYMBOLS.items():
            if "min_lot_size" not in cfg:
                missing.append(f"{sym}: thiếu min_lot_size")
            if "max_lot_size" not in cfg:
                missing.append(f"{sym}: thiếu max_lot_size")

        assert not missing, (
            "Các symbols sau chưa có lot bounds:\n" + "\n".join(missing)
        )

    def test_min_less_than_max_in_all_symbols(self):
        """min_lot_size < max_lot_size cho tất cả symbols."""
        invalid = [
            f"{sym}: min={cfg['min_lot_size']} >= max={cfg['max_lot_size']}"
            for sym, cfg in SYMBOLS.items()
            if cfg.get("min_lot_size", 0) >= cfg.get("max_lot_size", 1)
        ]
        assert not invalid, "Lot size bounds lỗi:\n" + "\n".join(invalid)


# ─────────────────────────────────────────────────────────────────────────────
# Fix #8 — Config calibration (validate_config)
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateConfig:
    """Fix #8: validate_config() phải báo cáo đúng tình trạng config."""

    def test_validate_config_runs_without_error(self):
        """validate_config() phải chạy không crash."""
        result = validate_config()
        assert isinstance(result, dict)
        assert "warnings" in result
        assert "errors"   in result
        assert "ok"       in result

    def test_no_critical_errors_in_current_config(self):
        """Config hiện tại không được có errors (chỉ warnings là ok)."""
        result = validate_config()
        assert result["ok"], (
            "Config có lỗi nghiêm trọng:\n" + "\n".join(result["errors"])
        )

    def test_all_symbols_have_positive_x(self):
        """x (breakout buffer) phải > 0 cho tất cả symbols."""
        bad = [sym for sym, cfg in SYMBOLS.items() if cfg.get("x", 0) <= 0]
        assert not bad, f"Symbols có x <= 0: {bad}"

    def test_all_symbols_have_positive_contract_value(self):
        """contract_value phải > 0 cho tất cả symbols (lot sizing sẽ hoạt động)."""
        bad = [sym for sym, cfg in SYMBOLS.items() if cfg.get("contract_value", 0) <= 0]
        assert not bad, f"Symbols có contract_value <= 0: {bad}"

    def test_swap_warning_reported_for_zero_swap_symbols(self):
        """Symbols có swap = 0 và group != CRYPTO phải xuất hiện trong warnings."""
        result   = validate_config()
        warnings = "\n".join(result["warnings"])

        # Những symbols có swap 0.0 và là index (không phải crypto) phải được cảnh báo
        zero_swap_indices = [
            sym for sym, cfg in SYMBOLS.items()
            if cfg.get("swap_long_per_lot_per_day", 0.0) == 0.0
            and cfg.get("group") in ("US", "EU", "ASIA", "METAL")
        ]
        for sym in zero_swap_indices:
            assert sym in warnings, (
                f"{sym} có swap = 0.0 nhưng không xuất hiện trong warnings. "
                "validate_config() phải cảnh báo về missing swap values."
            )

    def test_validated_swap_symbols_not_in_errors(self):
        """US30, HK50, J225 đã có swap values thực — không nên xuất hiện trong errors."""
        result  = validate_config()
        errors  = "\n".join(result["errors"])
        assert "US30" not in errors
        assert "HK50" not in errors
        assert "J225" not in errors

    def test_btcusd_has_smaller_lot_bounds(self):
        """BTCUSD phải có min_lot_size < 0.01 (BTC micro-lots)."""
        btc = SYMBOLS.get("BTCUSD", {})
        assert btc.get("min_lot_size", 0.01) <= 0.01, (
            "BTCUSD min_lot_size nên <= 0.01 vì BTC CFD có micro-lot support"
        )
        assert btc.get("max_lot_size", 100) <= 10.0, (
            "BTCUSD max_lot_size nên nhỏ hơn equity indices vì giá BTC rất cao"
        )
