from __future__ import annotations

import unittest

import pandas as pd

from backtest_optimize.analysis.metrics import summarize
from backtest_optimize.contracts import ClusterStatus, Direction, ExitReason, MarketSpec
from backtest_optimize.execution.engine import run_component_backtest
from backtest_optimize.execution.sl_calculator import CBOT_V0_KSL_LEVELS
from backtest_optimize.execution.tp_calculator import CBOT_V0_KTP_LEVELS
from backtest_optimize.io.signal_loader import normalize_signal_frame


def _market() -> MarketSpec:
    return MarketSpec(symbol="US30", pip_size=1.0, pip_value_per_lot=1.0)


def _run(signals: pd.DataFrame, bars: pd.DataFrame, **kwargs):
    order_params = {"cancel_after_bars": 3}
    order_params.update(kwargs.pop("order_params", {}))
    return run_component_backtest(
        signals=normalize_signal_frame(signals, symbol="US30", timeframe="H1"),
        bars=bars,
        symbol="US30",
        timeframe="H1",
        market_spec=_market(),
        account_size=10_000,
        risk_per_cluster=0.01,
        entry_model="stop_breakout_signal_bar",
        entry_params={"x_offset": 1.0},
        sl_method="signal_bar_atr_buffer",
        sl_params={"ksl_level": 2},
        tp_method="fib_atr_ctrader_v0",
        tp_params={"ktp_level": 1},
        order_model="stop_order",
        order_params=order_params,
        **kwargs,
    )


class ComponentExecutionEngineTests(unittest.TestCase):
    def test_stop_breakout_fill_uses_signal_bar_entry_sl_tp(self) -> None:
        bars = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "open": 95, "high": 100, "low": 90, "close": 98},
                {"bartime": "2026-01-01 01:00", "open": 98, "high": 115, "low": 97, "close": 114},
                {"bartime": "2026-01-01 02:00", "open": 114, "high": 116, "low": 112, "close": 115},
            ]
        )
        signals = pd.DataFrame([{"bartime": "2026-01-01 00:00", "side": "BUY", "atr": 10.0}])

        result = _run(signals, bars)
        cluster = result.clusters[0]

        self.assertEqual(cluster.status, ClusterStatus.CLOSED)
        self.assertEqual(cluster.entry_price, 101.0)
        self.assertAlmostEqual(cluster.sl_price, 90.0 - CBOT_V0_KSL_LEVELS[2] * 10.0)
        self.assertAlmostEqual(cluster.tp_levels[0].price, 101.0 + CBOT_V0_KTP_LEVELS[1] * 10.0)
        self.assertEqual(cluster.legs[0].exit_reason, ExitReason.TP)

    def test_pending_expires_when_stop_entry_is_not_touched(self) -> None:
        bars = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "open": 95, "high": 100, "low": 90, "close": 98},
                {"bartime": "2026-01-01 01:00", "open": 98, "high": 100, "low": 96, "close": 99},
                {"bartime": "2026-01-01 02:00", "open": 99, "high": 100, "low": 97, "close": 98},
                {"bartime": "2026-01-01 03:00", "open": 98, "high": 100, "low": 97, "close": 99},
            ]
        )
        signals = pd.DataFrame([{"bartime": "2026-01-01 00:00", "side": "BUY", "atr": 10.0}])

        result = _run(signals, bars, order_params={"cancel_after_bars": 2})
        summary = summarize(result)

        self.assertEqual(result.clusters[0].status, ClusterStatus.PENDING_EXPIRED)
        self.assertEqual(summary["pending_expired_count"], 1)
        self.assertEqual(summary["filled_count"], 0)
        self.assertEqual(summary["expectancy_r"], None)

    def test_same_direction_signal_skips_while_pending_exists(self) -> None:
        bars = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "open": 95, "high": 100, "low": 90, "close": 98},
                {"bartime": "2026-01-01 01:00", "open": 98, "high": 100, "low": 96, "close": 99},
                {"bartime": "2026-01-01 02:00", "open": 99, "high": 100, "low": 97, "close": 98},
                {"bartime": "2026-01-01 03:00", "open": 98, "high": 100, "low": 97, "close": 99},
            ]
        )
        signals = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "side": "BUY", "atr": 10.0},
                {"bartime": "2026-01-01 01:00", "side": "BUY", "atr": 10.0},
            ]
        )

        result = _run(signals, bars)

        self.assertEqual(result.clusters[1].status, ClusterStatus.SKIPPED)
        self.assertEqual(result.clusters[1].skip_reason, "same_direction_open")

    def test_opposite_signal_cancels_pending_before_new_order(self) -> None:
        bars = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "open": 95, "high": 100, "low": 90, "close": 98},
                {"bartime": "2026-01-01 01:00", "open": 98, "high": 100, "low": 96, "close": 99},
                {"bartime": "2026-01-01 02:00", "open": 99, "high": 100, "low": 97, "close": 98},
            ]
        )
        signals = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "side": "BUY", "atr": 10.0},
                {"bartime": "2026-01-01 01:00", "side": "SELL", "atr": 10.0},
            ]
        )

        result = _run(signals, bars)

        self.assertEqual(result.clusters[0].status, ClusterStatus.PENDING_CANCELLED)
        self.assertEqual(result.clusters[1].status, ClusterStatus.PENDING_UNFILLED_AT_END)

    def test_signal_inside_bar_interval_uses_containing_bar_for_levels(self) -> None:
        bars = pd.DataFrame(
            [
                {"bartime": "2026-01-01 00:00", "open": 95, "high": 100, "low": 90, "close": 98},
                {"bartime": "2026-01-01 01:00", "open": 98, "high": 115, "low": 97, "close": 114},
                {"bartime": "2026-01-01 02:00", "open": 114, "high": 116, "low": 112, "close": 115},
            ]
        )
        signals = pd.DataFrame([{"bartime": "2026-01-01 00:30", "side": "BUY", "atr": 10.0}])

        result = _run(signals, bars)

        self.assertEqual(result.clusters[0].signal.bartime, pd.Timestamp("2026-01-01 00:30"))
        self.assertEqual(result.clusters[0].entry_price, 101.0)


if __name__ == "__main__":
    unittest.main()
