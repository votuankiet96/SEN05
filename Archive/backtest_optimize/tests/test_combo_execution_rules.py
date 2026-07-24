from __future__ import annotations

import unittest

import pandas as pd

from backtest_optimize.analysis.metrics import summarize
from backtest_optimize.contracts import ClusterStatus, Direction, ExitReason, MarketSpec, SignalRow
from backtest_optimize.execution.engine import run_single
from backtest_optimize.execution.sl_calculator import (
    DEFAULT_COMBO_X_BUFFER,
    calculate_sl,
)
from backtest_optimize.execution.tp_calculator import (
    DEFAULT_KTP_LEVEL,
    KTP_FIB_LEVELS,
    calculate_tp_levels,
)
from backtest_optimize.io.signal_loader import normalize_signal_frame


class ComboExecutionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = pd.DataFrame(
            [
                {
                    "bartime": "2026-01-01 00:00:00",
                    "open": 95.0,
                    "high": 100.0,
                    "low": 90.0,
                    "close": 98.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 01:00:00",
                    "open": 101.0,
                    "high": 124.0,
                    "low": 100.0,
                    "close": 123.0,
                    "volume": 1,
                },
            ]
        )

    def test_combo_signal_bar_sl_uses_signal_bar_low_high(self) -> None:
        buy = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="US30",
            timeframe="H1",
            direction=Direction.BUY,
            atr=10.0,
        )
        sell = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="US30",
            timeframe="H1",
            direction=Direction.SELL,
            atr=10.0,
        )

        default_buy_sl = calculate_sl("combo_signal_bar", buy, 101.0, self.bars, {})
        buy_sl = calculate_sl("combo_signal_bar", buy, 101.0, self.bars, {"x_buffer": 1.0})
        sell_sl = calculate_sl("combo_signal_bar", sell, 89.0, self.bars, {"x_buffer": 1.0})

        self.assertEqual(DEFAULT_COMBO_X_BUFFER, 10.0)
        self.assertEqual(default_buy_sl.price, 80.0)
        self.assertEqual(buy_sl.price, 89.0)
        self.assertEqual(sell_sl.price, 101.0)
        self.assertEqual(buy_sl.source, "signal_bar")

    def test_combo_fib_atr_tp_uses_fibonacci_level_and_atr(self) -> None:
        buy = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="US30",
            timeframe="H1",
            direction=Direction.BUY,
            atr=10.0,
        )
        sell = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="US30",
            timeframe="H1",
            direction=Direction.SELL,
            atr=10.0,
        )

        buy_tp = calculate_tp_levels("combo_fib_atr", buy, 101.0, 89.0, self.bars, {"ktp_level": 4})[0]
        sell_tp = calculate_tp_levels("combo_fib_atr", sell, 89.0, 101.0, self.bars, {"ktp_level": 6})[0]

        self.assertEqual(DEFAULT_KTP_LEVEL, 4)
        self.assertEqual(KTP_FIB_LEVELS[4], 2.272)
        self.assertAlmostEqual(buy_tp.price, 123.72)
        self.assertAlmostEqual(sell_tp.price, 52.82)
        self.assertEqual(buy_tp.label, "KTP_L4_2.272")

    def test_combo_fib_atr_rejects_non_fibonacci_ktp(self) -> None:
        signal = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="US30",
            timeframe="H1",
            direction=Direction.BUY,
            atr=10.0,
        )

        with self.assertRaisesRegex(ValueError, "ktp must be one of"):
            calculate_tp_levels("combo_fib_atr", signal, 101.0, 89.0, self.bars, {"ktp": 2.5})

    def test_run_single_can_use_combo_sl_and_combo_tp(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "bartime": "2026-01-01 00:00:00",
                    "side": "BUY",
                    "atr": 10.0,
                }
            ]
        )
        signals = normalize_signal_frame(raw, symbol="US30", timeframe="H1")

        result = run_single(
            signals=signals,
            bars=self.bars,
            symbol="US30",
            timeframe="H1",
            market_spec=MarketSpec(symbol="US30", pip_size=1.0, pip_value_per_lot=1.0),
            sl_method="combo_signal_bar",
            sl_params={"x_buffer": 1.0},
            tp_method="combo_fib_atr",
            tp_params={"ktp_level": 4},
        )

        cluster = result.clusters[0]
        self.assertEqual(cluster.entry_price, 101.0)
        self.assertEqual(cluster.sl_price, 89.0)
        self.assertAlmostEqual(cluster.tp_levels[0].price, 123.72)
        self.assertEqual(cluster.legs[0].exit_reason.value, "tp")

    def test_opposite_combo_signal_reverses_open_cluster(self) -> None:
        bars = pd.DataFrame(
            [
                {
                    "bartime": "2026-01-01 00:00:00",
                    "open": 95.0,
                    "high": 99.0,
                    "low": 90.0,
                    "close": 98.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 01:00:00",
                    "open": 100.0,
                    "high": 104.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 02:00:00",
                    "open": 103.0,
                    "high": 105.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 03:00:00",
                    "open": 104.0,
                    "high": 106.0,
                    "low": 102.0,
                    "close": 103.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 04:00:00",
                    "open": 105.0,
                    "high": 106.0,
                    "low": 104.0,
                    "close": 104.5,
                    "volume": 1,
                },
            ]
        )
        signals = normalize_signal_frame(
            pd.DataFrame(
                [
                    {"bartime": "2026-01-01 00:00:00", "side": "BUY", "atr": 100.0},
                    {"bartime": "2026-01-01 03:00:00", "side": "SELL", "atr": 100.0},
                ]
            ),
            symbol="US30",
            timeframe="H1",
        )

        result = run_single(
            signals=signals,
            bars=bars,
            symbol="US30",
            timeframe="H1",
            market_spec=MarketSpec(symbol="US30", pip_size=1.0, pip_value_per_lot=1.0),
            sl_method="combo_signal_bar",
            sl_params={"x_buffer": 1.0},
            tp_method="combo_fib_atr",
            tp_params={"ktp_level": 4},
            position_policy={
                "same_direction": "skip",
                "opposite_direction": "reverse",
                "reverse_exit_model": "next_open",
            },
        )

        first, second = result.clusters
        summary = summarize(result)

        self.assertEqual(first.status, ClusterStatus.REVERSED)
        self.assertEqual(first.exit_time, pd.Timestamp("2026-01-01 04:00:00"))
        self.assertEqual(first.legs[0].exit_reason, ExitReason.REVERSE_SIGNAL)
        self.assertAlmostEqual(first.r_result, (105.0 - 100.0) / (100.0 - 89.0))
        self.assertNotEqual(second.status, ClusterStatus.SKIPPED)
        self.assertEqual(summary["skipped_count"], 0)
        self.assertEqual(summary["reversed_count"], 1)
        self.assertEqual(summary["reversed_profit_count"], 1)
        self.assertEqual(summary["reversed_loss_count"], 0)
        self.assertEqual(summary["reversed_flat_count"], 0)
        self.assertEqual(summary["reversed_win_rate"], 1.0)
        self.assertAlmostEqual(summary["reversed_avg_r"], first.r_result)


if __name__ == "__main__":
    unittest.main()
