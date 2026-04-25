"""Sprint 2 regression tests.

Fix #4  Portfolio FTMO risk at account level   → shared/portfolio.py + portfolio/backtest.py
Fix #5  Monte Carlo capital baseline            → shared/monte_carlo.py (initial_balance, block bootstrap)

Chạy:
    cd "d:/Auto Trading/SEN05"
    python -m pytest tests/test_sprint2_regression.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.monte_carlo import run_monte_carlo
from shared.portfolio import check_portfolio_ftmo


# ─────────────────────────────────────────────────────────────────────────────
# Fix #4 — Portfolio FTMO account-level check
# ─────────────────────────────────────────────────────────────────────────────

def _make_equity(values: list[float], freq: str = "4h") -> pd.Series:
    """Tạo equity series với DatetimeIndex chuẩn cho portfolio tests."""
    dates = pd.date_range("2023-01-02", periods=len(values), freq=freq)
    return pd.Series(values, index=dates)


class TestCheckPortfolioFtmo:
    """Fix #4: check_portfolio_ftmo() phải phát hiện vi phạm ở cấp account."""

    # ── Không vi phạm ─────────────────────────────────────────────────────────

    def test_pass_when_no_breach(self):
        """Portfolio tăng đều từ 100K → 105K: không vi phạm bất kỳ giới hạn nào."""
        equity = _make_equity([100_000, 101_000, 102_000, 103_000, 104_000, 105_000])
        result = check_portfolio_ftmo(equity, initial_balance=100_000)

        assert result["ftmo_pass"] is True
        assert result["breach_reason"] is None
        assert result["n_daily_breach_days"] == 0

    def test_pass_small_daily_loss_within_limit(self):
        """Lỗ ngày 3% < 5% daily limit, tổng 7% < 10% max DD → vẫn pass."""
        # initial=100K, daily limit=5% → limit_usd=5000; mỗi ngày lỗ 3K < 5K
        # Tổng lỗ 7K = 7% < 10% max DD limit → không vi phạm cả 2 giới hạn
        equity = _make_equity([100_000, 97_000, 95_000, 93_000], freq="D")
        result = check_portfolio_ftmo(
            equity, initial_balance=100_000, daily_loss_limit=0.05, max_dd_limit=0.10
        )
        assert result["ftmo_pass"] is True

    # ── Vi phạm Daily Loss ─────────────────────────────────────────────────────

    def test_fail_daily_loss_breach(self):
        """Lỗ ngày 6% > 5% daily limit → fail với breach_reason='daily_loss'."""
        # Ngày 2: equity giảm từ 100K → 93.5K = mất 6.5K (6.5% của 100K)
        equity = _make_equity([100_000, 93_500, 93_000, 94_000], freq="D")
        result = check_portfolio_ftmo(
            equity, initial_balance=100_000, daily_loss_limit=0.05
        )

        assert result["ftmo_pass"] is False
        assert result["breach_reason"] in ("daily_loss", "both")
        assert result["n_daily_breach_days"] >= 1
        assert result["max_daily_loss_pct"] > 5.0  # expressed as percent

    def test_breach_date_is_reported(self):
        """Ngày vi phạm đầu tiên phải được báo cáo chính xác."""
        # Vi phạm vào ngày 2023-01-03 (ngày thứ 2)
        dates  = pd.date_range("2023-01-02", periods=4, freq="D")
        equity = pd.Series([100_000, 94_000, 93_000, 94_000], index=dates)
        result = check_portfolio_ftmo(equity, initial_balance=100_000, daily_loss_limit=0.05)

        assert result["breach_date"] is not None
        assert "2023-01-03" in str(result["breach_date"])

    def test_multiple_breach_days_counted(self):
        """Nhiều ngày vi phạm phải được đếm đúng."""
        # Mỗi ngày lỗ 6K (6%) — 3 ngày liên tiếp vi phạm
        equity = _make_equity([100_000, 94_000, 88_000, 82_000], freq="D")
        result = check_portfolio_ftmo(equity, initial_balance=100_000, daily_loss_limit=0.05)

        assert result["n_daily_breach_days"] == 3

    # ── Vi phạm Max Drawdown ───────────────────────────────────────────────────

    def test_fail_max_dd_breach(self):
        """Drawdown 12% > 10% limit → fail với breach_reason='max_dd'."""
        # Equity tăng lên 110K rồi giảm xuống 96.8K = DD = (110K-96.8K)/110K ≈ 12%
        equity = _make_equity(
            [100_000, 97_000, 94_000, 91_000, 89_500],
            freq="D",
        )
        result = check_portfolio_ftmo(
            equity,
            initial_balance=100_000,
            daily_loss_limit=0.05,
            max_dd_limit=0.10,
        )

        assert result["ftmo_pass"] is False
        assert result["breach_reason"] == "max_dd"
        assert result["max_dd_pct"] > 10.0

    def test_max_dd_below_limit_passes(self):
        """Drawdown 8% < 10% limit → pass."""
        # Tăng lên 110K rồi giảm xuống 101.2K = DD = (110K-101.2K)/110K ≈ 8%
        equity = _make_equity(
            [100_000, 105_000, 110_000, 107_000, 104_000, 101_200],
            freq="4h",
        )
        result = check_portfolio_ftmo(equity, initial_balance=100_000, max_dd_limit=0.10)

        assert result["ftmo_pass"] is True
        assert result["max_dd_pct"] < 10.0

    def test_both_breach_types_reported(self):
        """Cả daily loss lẫn max DD vi phạm → breach_reason='both'."""
        # Ngày 2: mất 7K (7% daily) + DD từ 105K xuống 88K = 16%
        dates  = pd.date_range("2023-01-02", periods=4, freq="D")
        equity = pd.Series([100_000, 105_000, 98_000, 88_000], index=dates)
        result = check_portfolio_ftmo(equity, initial_balance=100_000,
                                      daily_loss_limit=0.05, max_dd_limit=0.10)

        assert result["ftmo_pass"] is False
        assert result["breach_reason"] == "both"

    # ── Edge cases ─────────────────────────────────────────────────────────────

    def test_empty_equity_returns_no_data(self):
        """Empty equity series → ftmo_pass=None, breach_reason='no_data'."""
        result = check_portfolio_ftmo(pd.Series(dtype=float), initial_balance=100_000)
        assert result["ftmo_pass"] is None
        assert result["breach_reason"] == "no_data"

    def test_zero_initial_balance_returns_no_data(self):
        """initial_balance=0 → không thể tính % → trả về no_data."""
        equity = _make_equity([0, 1000, 2000])
        result = check_portfolio_ftmo(equity, initial_balance=0)
        assert result["ftmo_pass"] is None

    def test_output_keys_complete(self):
        """Dict kết quả phải có đủ 7 keys theo contract."""
        equity  = _make_equity([100_000, 101_000, 102_000])
        result  = check_portfolio_ftmo(equity, initial_balance=100_000)
        required = {
            "ftmo_pass", "breach_reason", "breach_date",
            "max_daily_loss_pct", "n_daily_breach_days",
            "max_dd_pct", "max_dd_usd",
        }
        assert required.issubset(result.keys()), (
            f"Keys bị thiếu: {required - result.keys()}"
        )

    def test_sleeve_level_vs_account_level_different(self):
        """Minh hoạ tại sao cần account-level check.

        5 sleeves each at $20K, daily_limit 5%:
        - Sleeve level: stop khi mỗi sleeve mất $1K/day (5% of $20K)
        - Account level: stop khi tổng portfolio mất $5K/day (5% of $100K)

        Nếu 5 sleeves đều mất $1K trong cùng ngày:
        - Sleeve: mỗi cái dừng vì 5% của $20K = $1K → vừa chạm ngưỡng
        - Account: tổng mất $5K = 5% của $100K → vừa chạm ngưỡng (đúng!)

        Nhưng nếu 4 sleeves mất $900 và 1 sleeve mất $4.1K:
        - Sleeve level: 4 sleeves dưới ngưỡng; 1 sleeve mất $4.1K > $1K → mỗi ngừng riêng
        - Account: $4.1K + 4×$900 = $7.7K = 7.7% của $100K → vi phạm 5% account limit

        Check này verify rằng hàm phát hiện vi phạm account level.
        """
        # 5 sleeves: 4 × $900 loss + 1 × $4.1K loss in the same day
        # Combined: $7.7K loss on $100K = 7.7% > 5% limit
        dates  = pd.date_range("2023-01-02", periods=2, freq="D")
        combined_equity = pd.Series(
            [100_000, 100_000 - 7_700],   # portfolio loses $7.7K in one day
            index=dates,
        )
        result = check_portfolio_ftmo(
            combined_equity, initial_balance=100_000, daily_loss_limit=0.05
        )
        assert result["ftmo_pass"] is False, (
            "Account-level daily loss 7.7% > 5% phải fail, nhưng sleeve-level "
            "có thể không phát hiện nếu phân bổ lỗ không đều giữa các sleeves."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fix #5 — Monte Carlo capital baseline + block bootstrap
# ─────────────────────────────────────────────────────────────────────────────

class TestMonteCarlo:
    """Fix #5: initial_balance fix drawdown inflation; block bootstrap đủ độ dài."""

    def _make_pnls(self, n: int = 50, seed: int = 42) -> list[float]:
        """Tạo trade PnL sequence giả lập."""
        rng = np.random.default_rng(seed)
        return list(rng.normal(100, 500, n))

    # ── initial_balance fixes drawdown inflation ───────────────────────────────

    def test_drawdown_smaller_with_initial_balance(self):
        """Drawdown phải nhỏ hơn khi có initial_balance so với không có.

        Không có initial_balance (=0): equity bắt đầu từ 0, peak gần 0
        → denominator nhỏ → drawdown % bị phóng đại.
        Với initial_balance=100K: denominator luôn lớn → drawdown % đúng.
        """
        pnls = self._make_pnls(50)

        mc_no_base = run_monte_carlo(pnls, n_iter=200, initial_balance=0.0)
        mc_with_base = run_monte_carlo(pnls, n_iter=200, initial_balance=100_000.0)

        mean_dd_no_base   = float(np.mean(mc_no_base["max_drawdowns"]))
        mean_dd_with_base = float(np.mean(mc_with_base["max_drawdowns"]))

        assert mean_dd_with_base < mean_dd_no_base, (
            f"Drawdown với initial_balance ({mean_dd_with_base:.3f}) phải nhỏ hơn "
            f"không có initial_balance ({mean_dd_no_base:.3f}). "
            "Bug: equity bắt đầu từ 0 làm denominator nhỏ, phóng đại drawdown."
        )

    def test_equity_paths_start_at_initial_balance(self):
        """equity_paths phải bắt đầu từ initial_balance, không phải từ 0."""
        pnls  = self._make_pnls(30)
        init  = 50_000.0
        mc    = run_monte_carlo(pnls, n_iter=100, initial_balance=init)

        # Giá trị equity trung vị ở bar đầu tiên phải gần initial_balance + mean_pnl
        first_bar_median = float(np.percentile(mc["equity_paths"][:, 0], 50))
        # first bar = initial_balance + pnl[0] (sau 1 trade)
        # Với block bootstrap, pnl[0] là random từ array — phải gần mean pnl
        assert first_bar_median > init * 0.8, (
            f"equity_paths bar đầu tiên median={first_bar_median:.0f} quá thấp so với "
            f"initial_balance={init:.0f}. Kiểm tra initial_balance + cumsum."
        )

    def test_prob_exceed_dd_lower_with_initial_balance(self):
        """Xác suất vượt ngưỡng DD phải thấp hơn khi có initial_balance đủ lớn."""
        # Pnl nhỏ so với initial_balance → drawdown % sẽ nhỏ → prob thấp
        small_pnls = [float(x) for x in np.random.default_rng(0).normal(50, 200, 40)]

        mc_no_base   = run_monte_carlo(small_pnls, n_iter=500, initial_balance=0.0,    dd_threshold=0.20)
        mc_with_base = run_monte_carlo(small_pnls, n_iter=500, initial_balance=100_000.0, dd_threshold=0.20)

        assert mc_with_base["prob_exceed_dd"] < mc_no_base["prob_exceed_dd"], (
            "Prob exceed DD phải giảm khi initial_balance=100K so với initial_balance=0. "
            f"no_base={mc_no_base['prob_exceed_dd']:.3f}, "
            f"with_base={mc_with_base['prob_exceed_dd']:.3f}"
        )

    # ── Block bootstrap path length ────────────────────────────────────────────

    def test_equity_paths_correct_shape(self):
        """equity_paths phải có shape (n_iter, n_trades) không bị thiếu."""
        for n_trades in [5, 10, 25, 50, 100]:
            pnls = list(np.random.default_rng(n_trades).normal(0, 100, n_trades))
            mc   = run_monte_carlo(pnls, n_iter=50)

            assert mc["equity_paths"].shape == (50, n_trades), (
                f"n_trades={n_trades}: equity_paths shape {mc['equity_paths'].shape} "
                f"!= (50, {n_trades}). Block bootstrap bị cắt ngắn."
            )

    def test_small_n_trades_no_crash(self):
        """n_trades nhỏ (2–5) không được crash do block bootstrap."""
        for n in range(2, 6):
            pnls = list(np.arange(1.0, n + 1))
            mc   = run_monte_carlo(pnls, n_iter=20)
            assert mc["equity_paths"].shape[1] == n, (
                f"n_trades={n}: equity_paths shape sai sau block bootstrap"
            )

    def test_block_size_floor_is_one(self):
        """block_size = max(1, sqrt(n)) đảm bảo không bao giờ = 0."""
        pnls = [100.0]   # n_trades=1
        mc   = run_monte_carlo(pnls, n_iter=10)
        assert mc["equity_paths"].shape == (10, 1)

    # ── Output contract ────────────────────────────────────────────────────────

    def test_empty_pnls_returns_empty_arrays(self):
        """Danh sách rỗng → equity arrays rỗng, prob=0, sharpe_ci=0."""
        mc = run_monte_carlo([])
        assert mc["equity_p5"].size == 0
        assert mc["equity_p50"].size == 0
        assert mc["equity_p95"].size == 0
        assert mc["prob_exceed_dd"] == 0.0
        assert mc["sharpe_ci_low"] == 0.0

    def test_output_keys_complete(self):
        """Dict kết quả phải có đủ keys theo contract."""
        mc = run_monte_carlo([100.0, -50.0, 200.0], n_iter=10)
        required = {
            "equity_p5", "equity_p50", "equity_p95",
            "prob_exceed_dd", "sharpe_ci_low", "sharpe_ci_high",
            "max_drawdowns", "sharpe_samples", "equity_paths",
        }
        assert required.issubset(mc.keys())

    def test_percentiles_ordered(self):
        """equity_p5 ≤ equity_p50 ≤ equity_p95 tại mọi bar."""
        pnls = list(np.random.default_rng(99).normal(100, 800, 60))
        mc   = run_monte_carlo(pnls, n_iter=500, initial_balance=100_000)

        assert np.all(mc["equity_p5"] <= mc["equity_p50"] + 1e-9), \
            "P5 phải ≤ P50 tại mọi bar"
        assert np.all(mc["equity_p50"] <= mc["equity_p95"] + 1e-9), \
            "P50 phải ≤ P95 tại mọi bar"

    def test_prob_exceed_dd_in_range(self):
        """prob_exceed_dd phải trong [0, 1]."""
        pnls = self._make_pnls(40)
        mc   = run_monte_carlo(pnls, n_iter=200, dd_threshold=0.10)
        assert 0.0 <= mc["prob_exceed_dd"] <= 1.0

    def test_backward_compatible_no_initial_balance(self):
        """Gọi không có initial_balance phải hoạt động như cũ (default=0)."""
        pnls = self._make_pnls(30)
        mc_old = run_monte_carlo(pnls, n_iter=50)     # backward compat call
        mc_new = run_monte_carlo(pnls, n_iter=50, initial_balance=0.0)

        # Với seed khác nhau kết quả sẽ khác, nhưng shape và keys phải giống
        assert mc_old["equity_paths"].shape == mc_new["equity_paths"].shape
        assert set(mc_old.keys()) == set(mc_new.keys())
