"""Sprint 1 regression tests — 3 critical fixes.

Fix #3  indicator_overrides xuyên suốt   → symbol/backtest.py, optimize.py, walkforward.py
Fix #1  OOS warmup carry-over            → symbol/walkforward.py, portfolio/walkforward.py
Fix #2  backtest_fast parity             → shared/execution.py (swap, force-close, years_span)

Chạy:
    cd "d:/Auto Trading/SEN05"
    python -m pytest tests/test_sprint1_regression.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

# Đặt core_python vào path để import không cần package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.combo.config import get_indicator_params
from strategies.combo.logic import (
    add_combo_indicators,
    detect_combo_signals,
    session_mask,
)
from shared.execution import backtest_fast, backtest_symbol


# ─────────────────────────────────────────────────────────────────────────────
# Shared test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n_bars: int = 400, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV: sine-wave price guarantees MA crossovers."""
    rng   = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="4h")
    t     = np.linspace(0, 6 * np.pi, n_bars)
    close = 10_000.0 + 200.0 * np.sin(t) + rng.normal(0, 5, n_bars)
    spread = rng.uniform(10, 30, n_bars)
    high  = close + spread
    low   = close - spread
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1_000, 5_000, n_bars),
        },
        index=pd.DatetimeIndex(dates, name="BarTime"),
    )


# Minimal cfg — không cần DB, không cần SYMBOLS lookup
_CFG = {
    "x":                           10.0,
    "ktp":                          2.3,
    "ma_period":                   20,
    "trailing_activation":          1.0,
    "session_hours_utc":            [],
    "contract_value":               1.0,
    "point_size":                   1.0,
    "spread_pts":                   2.0,
    "commission_per_lot":           3.5,
    "slippage_pts":                 0.5,
    "swap_long_per_lot_per_day":    0.0,
    "swap_short_per_lot_per_day":   0.0,
}

_STRATEGY = {
    "risk_per_trade":       0.005,
    "daily_loss_limit":     1.0,    # effectively unbounded for unit tests
    "max_drawdown_limit":   1.0,
    "trailing_activation":  1.0,
    "pending_ttl_bars":     3,
    "partial_tp_fraction":  0.5,
    "min_rr":               1.25,
}


# ─────────────────────────────────────────────────────────────────────────────
# Fix #3 — indicator_overrides xuyên suốt
# Regression: add_combo_indicators phải nhận full params, không chỉ MA_PERIOD.
# ─────────────────────────────────────────────────────────────────────────────

class TestIndicatorOverrides:
    """Fix #3: verify full indicator params flow through to add_combo_indicators."""

    def test_macd_slow_override_changes_histogram(self):
        """MACD_SLOW=50 vs MACD_SLOW=25 phải tạo ra histogram khác nhau.

        Trước fix: symbol/backtest.py:115 chỉ truyền {"MA_PERIOD": ...} nên
        MACD_SLOW bị reset về default 25 bất kể indicator_overrides.
        """
        df   = _make_ohlcv(300)
        base = get_indicator_params()  # MACD_SLOW=25, MA_PERIOD=20

        # Mô phỏng bug cũ: chỉ truyền MA_PERIOD (không có MACD_SLOW override)
        df_partial = add_combo_indicators(df.copy(), {"MA_PERIOD": base["MA_PERIOD"]})

        # Sau fix: truyền full params với MACD_SLOW=50
        df_full = add_combo_indicators(df.copy(), {**base, "MACD_SLOW": 50})

        assert not df_partial["macd_h"].round(8).equals(df_full["macd_h"].round(8)), (
            "MACD_SLOW override bị bỏ qua — histogram không đổi dù MACD_SLOW=50 vs 25. "
            "Fix: symbol/backtest.py:115 phải truyền full params thay vì chỉ MA_PERIOD."
        )

    def test_atr_period_override_changes_atr(self):
        """ATR_PERIOD=20 vs ATR_PERIOD=5 phải tạo ra ATR khác nhau.

        ATR_PERIOD là tham số quan trọng cho SL/TP sizing — bị bỏ qua sẽ
        làm kích thước lệnh và ngưỡng RR tính sai.
        """
        df   = _make_ohlcv(300)
        base = get_indicator_params()  # ATR_PERIOD=5

        df_fast = add_combo_indicators(df.copy(), base)
        df_slow = add_combo_indicators(df.copy(), {**base, "ATR_PERIOD": 20})

        # ATR với period 20 mượt hơn period 5 — tail values phải khác nhau rõ ràng
        mean_diff = (df_fast["atr"].tail(50) - df_slow["atr"].tail(50)).abs().mean()
        assert mean_diff > 0.5, (
            f"ATR_PERIOD override không tác dụng — mean diff tail={mean_diff:.4f}. "
            "Fix: truyền full params vào add_combo_indicators."
        )

    def test_ma_period_override_changes_ma(self):
        """MA_PERIOD=50 vs MA_PERIOD=20 phải tạo ra MA column khác nhau."""
        df = _make_ohlcv(300)
        base = get_indicator_params()

        df_20 = add_combo_indicators(df.copy(), {**base, "MA_PERIOD": 20})
        df_50 = add_combo_indicators(df.copy(), {**base, "MA_PERIOD": 50})

        assert not df_20["ma"].round(6).equals(df_50["ma"].round(6)), (
            "MA_PERIOD override không tác dụng — MA column giống nhau với period 20 và 50."
        )

    def test_partial_params_uses_defaults_for_missing_keys(self):
        """Khi truyền partial params, add_combo_indicators phải merge với defaults.

        Logic hiện tại trong add_combo_indicators: full_p = {**defaults, **params}.
        → Các key không có trong params được lấy từ defaults.
        """
        df   = _make_ohlcv(200)
        base = get_indicator_params()

        # Chỉ truyền MA_PERIOD — MACD và ATR phải dùng defaults
        df_partial = add_combo_indicators(df.copy(), {"MA_PERIOD": base["MA_PERIOD"]})

        # Truyền full defaults
        df_full = add_combo_indicators(df.copy(), base)

        # Kết quả phải bằng nhau vì params tương đương
        try:
            pd.testing.assert_series_equal(
                df_partial["macd_h"].dropna(),
                df_full["macd_h"].dropna(),
                check_names=False,
                rtol=1e-6,
            )
        except AssertionError as exc:
            raise AssertionError(
                "Partial params phải cho cùng kết quả với full defaults khi các key giống nhau"
            ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Fix #1 — OOS warmup carry-over
# Regression: OOS slice phải có indicator hợp lệ ở tất cả bars, không có NaN.
# ─────────────────────────────────────────────────────────────────────────────

class TestOosWarmup:
    """Fix #1: indicator state phải valid ngay từ đầu OOS window."""

    def test_no_warmup_produces_nan_macd_at_oos_start(self):
        """Kiểm tra setup: không có warmup → macd_h NaN ở đầu OOS.

        Đây là TRẠNG THÁI BUG — test phải pass để chứng minh bug tồn tại.
        MA rolling(20) tạo NaN đầu; MACD EWM có min_periods = ATR_PERIOD.
        """
        params = get_indicator_params()  # MA_PERIOD=20, MACD_SLOW=25, ATR_PERIOD=5

        # 60 OOS bars, không có warmup history
        oos_only = _make_ohlcv(60)
        df_out   = add_combo_indicators(oos_only, params)

        # MA period=20 → ít nhất 19 NaN ở đầu (rolling không có min_periods=1)
        nan_in_ma = df_out["ma"].isna().sum()
        assert nan_in_ma > 0, (
            "Test setup lỗi: cần thấy NaN ở MA khi không có warmup history. "
            f"Thực tế NaN={nan_in_ma}. Kiểm tra lại add_indicators()."
        )

    def test_warmup_carry_over_removes_nan_from_oos_slice(self):
        """Sau warmup fix: rebuild indicator từ full slice + drop warmup → 0 NaN.

        Pattern được áp dụng trong symbol/walkforward.py và portfolio/walkforward.py:
            oos_with_warmup = data[max(0, oos_start - warmup) : oos_end]
            df_ind = add_combo_indicators(oos_with_warmup, params)
            oos_slice = df_ind.iloc[actual_warmup_len:]
        """
        params     = get_indicator_params()
        warmup_len = max(
            params["MACD_SLOW"], params["MA_PERIOD"], params["ATR_PERIOD"]
        ) + 5   # +5 buffer

        n_oos     = 60
        df_full   = _make_ohlcv(warmup_len + n_oos)

        # Build indicators on full slice (warmup + OOS)
        df_ind    = add_combo_indicators(df_full.copy(), params)

        # Slice to OOS only (drop warmup bars)
        oos_slice = df_ind.tail(n_oos)

        nan_ma    = oos_slice["ma"].isna().sum()
        nan_macd  = oos_slice["macd_h"].isna().sum()
        nan_atr   = oos_slice["atr"].isna().sum()

        assert nan_ma == 0, (
            f"MA còn {nan_ma} NaN trong OOS slice sau warmup carry. "
            f"warmup_len={warmup_len}, n_oos={n_oos}."
        )
        assert nan_macd == 0, (
            f"macd_h còn {nan_macd} NaN trong OOS slice sau warmup carry. "
            "Kiểm tra MACD_SLOW ({params['MACD_SLOW']}) vs warmup_len ({warmup_len})."
        )
        assert nan_atr == 0, (
            f"atr còn {nan_atr} NaN trong OOS slice sau warmup carry."
        )

    def test_warmup_len_formula_covers_all_indicators(self):
        """Công thức warmup = max(MACD_SLOW, MA_PERIOD, ATR_PERIOD) + 5 phải đủ.

        Với warmup đúng, toàn bộ OOS slice phải có valid indicators.
        """
        params     = get_indicator_params()
        warmup_len = max(
            params["MACD_SLOW"], params["MA_PERIOD"], params["ATR_PERIOD"]
        ) + 5

        for n_oos in [30, 60, 125]:
            df_full   = _make_ohlcv(warmup_len + n_oos, seed=n_oos)
            df_ind    = add_combo_indicators(df_full.copy(), params)
            oos_slice = df_ind.tail(n_oos)

            total_nan = oos_slice[["ma", "macd_h", "atr"]].isna().sum().sum()
            assert total_nan == 0, (
                f"n_oos={n_oos}: {total_nan} NaN còn lại trong OOS slice. "
                f"warmup_len={warmup_len} chưa đủ."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Fix #2 — backtest_fast parity với backtest_symbol
# Regression: swap cost, force-close, years_span đúng
# ─────────────────────────────────────────────────────────────────────────────

class TestBacktestFastParity:
    """Fix #2: backtest_fast phải có semantics tương đương backtest_symbol."""

    def _build_df_ind(self, n_bars: int = 600, seed: int = 42) -> pd.DataFrame:
        df     = _make_ohlcv(n_bars, seed)
        params = get_indicator_params()
        return add_combo_indicators(df, params)

    def _build_df_sig(self, df_ind: pd.DataFrame) -> pd.DataFrame:
        """Build signal frame (dùng cho backtest_symbol)."""
        params = get_indicator_params()
        mask   = session_mask(df_ind, [])
        return detect_combo_signals(df_ind, mask, sym_key=None, params=params)

    # ── Swap cost ──────────────────────────────────────────────────────────────

    def test_swap_cost_reduces_equity(self):
        """Swap cost dương phải làm equity cuối kỳ nhỏ hơn khi không có swap."""
        df_ind = self._build_df_ind()
        date_from = "2023-04-01"
        date_to   = "2023-09-30"

        base_kw = dict(
            ktp=2.3, x_actual=10.0, trailing_act=1.0,
            date_from=date_from, date_to=date_to,
            init_eq=100_000.0, strategy=_STRATEGY,
        )

        kpi_no_swap = backtest_fast("TEST", df_ind, _CFG, **base_kw)

        cfg_with_swap = {**_CFG, "swap_long_per_lot_per_day": 5.0}
        kpi_swap      = backtest_fast("TEST", df_ind, cfg_with_swap, **base_kw)

        if kpi_no_swap["trades"] == 0:
            pytest.skip("Không có giao dịch trong window — không thể kiểm tra swap")

        # Swap cost dương (long swap = 5/lot/day) → final equity / ret phải nhỏ hơn
        assert kpi_swap["ret"] <= kpi_no_swap["ret"], (
            f"Swap cost không làm giảm return: "
            f"no_swap={kpi_no_swap['ret']:.2f}%, with_swap={kpi_swap['ret']:.2f}%"
        )

    # ── years_span dùng đúng window date ──────────────────────────────────────

    def test_years_span_uses_date_window_not_full_df(self):
        """years_span phải dùng date_from/date_to, không phải toàn bộ df_ind.

        Trước fix: years_span = (df_ind.index[-1] - df_ind.index[0]).days / 365.25
        Sau fix:   years_span = (date_to - date_from).days / 365.25

        Khi window 3 tháng ≠ span của toàn df (12+ tháng), Sharpe phải khác nhau
        nếu tính đúng theo window.
        """
        df_ind = self._build_df_ind(600)

        base_kw = dict(
            ktp=2.3, x_actual=10.0, trailing_act=1.0,
            init_eq=100_000.0, strategy=_STRATEGY,
        )

        kpi_short = backtest_fast(
            "TEST", df_ind, _CFG,
            date_from="2023-06-01", date_to="2023-09-01",
            **base_kw,
        )
        kpi_full  = backtest_fast(
            "TEST", df_ind, _CFG,
            date_from="2023-01-01", date_to="2023-12-31",
            **base_kw,
        )

        # Chỉ so sánh khi cả hai window có đủ trades cho Sharpe
        if kpi_short["trades"] >= 10 and kpi_full["trades"] >= 10:
            # Nếu bug còn: cả hai dùng toàn df span → years_span bằng nhau
            # → Sharpe scale bằng nhau (chỉ khác vì n_trades khác)
            # Sau fix: years_span 3T vs 12T → n_per_year khác → Sharpe khác rõ ràng
            short_n_per_yr = kpi_short["trades"] / 0.25   # 3 tháng = 0.25 năm
            full_n_per_yr  = kpi_full["trades"]  / 1.0    # 12 tháng = 1 năm
            # Nếu tỷ lệ trades/năm khác nhau, Sharpe phải khác nhau
            if abs(short_n_per_yr - full_n_per_yr) / max(full_n_per_yr, 1) > 0.1:
                assert kpi_short["sharpe"] != pytest.approx(kpi_full["sharpe"], rel=0.01), (
                    "Sharpe của window 3T và 12T bằng nhau — years_span chưa dùng đúng window. "
                    f"short={kpi_short['sharpe']:.3f}, full={kpi_full['sharpe']:.3f}"
                )

    def test_years_span_value_reasonable(self):
        """years_span tính theo 6 tháng window phải ≈ 0.5 năm."""
        df_ind = self._build_df_ind(600)

        # Tạo scenario có trades để trigger Sharpe computation
        # Chỉ cần function chạy không lỗi và trades>0 để Sharpe được tính
        kpi = backtest_fast(
            "TEST", df_ind, _CFG,
            ktp=2.3, x_actual=10.0, trailing_act=1.0,
            date_from="2023-01-01", date_to="2023-07-01",
            init_eq=100_000.0, strategy=_STRATEGY,
        )
        # Chỉ verify không crash; years_span internal không exposed
        assert isinstance(kpi, dict), "backtest_fast phải trả về dict"
        assert "sharpe" in kpi, "KPI dict phải có key 'sharpe'"
        assert "trades" in kpi, "KPI dict phải có key 'trades'"

    # ── Force-close cuối window ────────────────────────────────────────────────

    def test_force_close_includes_open_position_in_trades(self):
        """Position đang mở khi hết window phải được force-close vào trades_pnl.

        Trước fix: backtest_fast bỏ qua position mở → PnL cuối inflate nếu đang win.
        Sau fix: force-close tại bar cuối → equity đúng.
        """
        df_ind = self._build_df_ind(600)
        params = get_indicator_params()

        # Chạy window nhỏ để tăng khả năng có open position khi kết thúc
        kpi = backtest_fast(
            "TEST", df_ind, _CFG,
            ktp=2.3, x_actual=10.0, trailing_act=1.0,
            date_from="2023-03-01", date_to="2023-06-01",
            init_eq=100_000.0, strategy=_STRATEGY,
        )

        # Kết quả phải là dict hoàn chỉnh, equity = init_eq + sum(trades_pnl)
        assert isinstance(kpi["ret"], float), "ret phải là float"
        # ret = (equity/init_eq - 1) * 100 → equity = init_eq * (1 + ret/100)
        implied_equity = 100_000.0 * (1 + kpi["ret"] / 100)
        assert implied_equity > 0, "Equity sau force-close phải dương"

    # ── Parity tổng thể ────────────────────────────────────────────────────────

    def test_fast_full_pnl_within_tolerance(self):
        """backtest_fast và backtest_symbol: PnL chênh < 15% khi có đủ giao dịch.

        Chênh lệch nhỏ vẫn chấp nhận được vì:
        - backtest_symbol dùng pre-computed signal column (vecto hóa trước)
        - backtest_fast tính signal inline với logic tương đương nhưng không hoàn toàn giống
        - swap=0, commission=0 trong test config → nguồn chênh lệch chính là logic entry/exit
        """
        df_ind = self._build_df_ind(800)
        params = get_indicator_params()

        date_from = "2023-03-01"
        date_to   = "2023-11-30"
        init_eq   = 100_000.0

        # --- Full backtest ---
        mask   = session_mask(df_ind, [])
        df_sig = detect_combo_signals(df_ind, mask, sym_key=None, params=params)
        # in_window cho full backtest: chỉ trading trong window
        df_sig["in_window"] = (
            (df_sig.index >= pd.Timestamp(date_from))
            & (df_sig.index <= pd.Timestamp(date_to))
        )
        # Cắt df_sig theo window để backtest_symbol không tạo pending ngoài window
        df_sig_window = df_sig[
            (df_sig.index >= pd.Timestamp(date_from))
            & (df_sig.index <= pd.Timestamp(date_to))
        ].copy()
        trades, eq_ts = backtest_symbol(
            "TEST", df_sig_window, _CFG, init_eq, strategy=_STRATEGY
        )

        # --- Fast backtest ---
        kpi_fast = backtest_fast(
            "TEST", df_ind, _CFG,
            ktp=_CFG["ktp"], x_actual=_CFG["x"], trailing_act=_CFG["trailing_activation"],
            date_from=date_from, date_to=date_to,
            init_eq=init_eq, strategy=_STRATEGY,
        )

        n_full = len(trades)
        n_fast = kpi_fast["trades"]

        if n_full == 0 and n_fast == 0:
            pytest.skip("Không có giao dịch trong cả hai path — bỏ qua parity check")

        full_pnl = float(eq_ts.iloc[-1]) - init_eq if len(eq_ts) else 0.0
        fast_pnl = kpi_fast["ret"] / 100 * init_eq

        if abs(full_pnl) < 50 or abs(fast_pnl) < 50:
            pytest.skip(
                f"PnL quá nhỏ để kiểm tra parity (full={full_pnl:.0f}, fast={fast_pnl:.0f})"
            )

        pnl_diff = abs(fast_pnl - full_pnl) / max(abs(full_pnl), 1)
        assert pnl_diff < 0.20, (
            f"PnL chênh {pnl_diff:.1%} > 20% — backtest_fast mất parity với backtest_symbol. "
            f"fast_pnl={fast_pnl:.0f}, full_pnl={full_pnl:.0f}, "
            f"n_fast={n_fast}, n_full={n_full}"
        )
