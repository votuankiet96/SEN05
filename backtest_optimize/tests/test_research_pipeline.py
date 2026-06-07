from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from backtest_optimize.analysis.monte_carlo import eligible_r_values
from backtest_optimize.analysis.research_pipeline import (
    apply_parameter_overrides,
    rank_stability_candidates,
    run_walkforward_research,
)
from backtest_optimize.analysis.versioning import load_snapshot, save_snapshot
from backtest_optimize.analysis.walkforward import WalkForwardWindow
from backtest_optimize.contracts import (
    BacktestResult,
    ClusterResult,
    ClusterStatus,
    Direction,
    MarketSpec,
    RunAssumptions,
    SignalRow,
)
from backtest_optimize.execution.engine import run_configured_backtest


class ResearchPipelineTests(unittest.TestCase):
    def test_configured_runner_accepts_nested_component_preset(self) -> None:
        signals = pd.DataFrame(
            {
                "bartime": [pd.Timestamp("2024-01-01 00:00:00")],
                "symbol": ["TEST"],
                "timeframe": ["H1"],
                "direction": [Direction.BUY],
                "atr": [1.0],
            }
        )
        bars = pd.DataFrame(
            [
                {"bartime": "2024-01-01 00:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
                {"bartime": "2024-01-01 01:00:00", "open": 100, "high": 104, "low": 100, "close": 103, "volume": 1},
                {"bartime": "2024-01-01 02:00:00", "open": 103, "high": 104, "low": 102, "close": 103, "volume": 1},
            ]
        )
        preset = {
            "engine": "component",
            "entry": {"model": "stop_breakout_signal_bar", "params": {"x_offset": 0.0}},
            "stoploss": {"model": "signal_bar_atr_buffer", "params": {"ksl_level": 2}},
            "takeprofit": {"model": "fib_atr_ctrader_v0", "params": {"ktp_level": 6, "exit_mode": "fixed_only"}},
            "orders": {"model": "stop_order", "params": {"cancel_after_bars": 2}},
            "exits": {"model": "fixed_only", "params": {}},
            "sizing": {"params": {"account_size": 10_000, "risk_per_cluster": 0.01}},
            "run": {"ambiguity_policy": "conservative"},
        }

        result = run_configured_backtest(
            signals=signals,
            bars=bars,
            symbol="TEST",
            timeframe="H1",
            market_spec=MarketSpec(symbol="TEST", pip_size=1.0, pip_value_per_lot=1.0),
            run_config=preset,
        )

        self.assertEqual(result.config["engine"], "component")
        self.assertEqual(result.clusters[0].status, ClusterStatus.CLOSED)

    def test_monte_carlo_r_values_exclude_non_filled_statuses(self) -> None:
        clusters = pd.DataFrame(
            {
                "status": ["closed", "reversed", "skipped", "open_at_end", "pending_expired"],
                "r_result": [1.0, -0.5, 99.0, 99.0, 99.0],
            }
        )
        values = eligible_r_values(clusters)

        self.assertEqual(values.tolist(), [1.0, -0.5])

    def test_apply_parameter_overrides_routes_dotted_component_params(self) -> None:
        base = {
            "engine": "component",
            "entry_params": {"x_offset": 10.0},
            "sl_params": {"ksl_level": 2},
            "tp_params": {"ktp_level": 6, "exit_mode": "fixed_only"},
            "order_params": {"cancel_after_bars": 3},
            "risk_per_cluster": 0.01,
        }

        resolved = apply_parameter_overrides(
            base,
            {
                "entry.x_offset": 15.0,
                "stoploss.ksl_level": 4,
                "takeprofit.ktp_level": 8,
                "takeprofit.exit_mode": "two_legs",
                "orders.cancel_after_bars": 5,
                "risk.risk_per_cluster": 0.02,
            },
        )

        self.assertEqual(resolved["entry_params"]["x_offset"], 15.0)
        self.assertEqual(resolved["sl_params"]["ksl_level"], 4)
        self.assertEqual(resolved["tp_params"]["ktp_level"], 8)
        self.assertEqual(resolved["order_params"]["cancel_after_bars"], 5)
        self.assertEqual(resolved["risk_per_cluster"], 0.02)
        self.assertEqual(resolved["exit_model"], "two_legs_sma20")
        self.assertEqual(base["entry_params"]["x_offset"], 10.0)

    def test_rank_stability_candidates_applies_constraints(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "entry.x_offset": 5.0,
                    "expectancy_r": 0.40,
                    "expectancy_r_stability_score": 2.0,
                    "expectancy_r_local_count": 5,
                    "filled_count": 20,
                    "fill_rate": 0.80,
                    "pending_expired_rate": 0.10,
                    "ambiguity_rate": 0.01,
                },
                {
                    "entry.x_offset": 10.0,
                    "expectancy_r": 0.60,
                    "expectancy_r_stability_score": 4.0,
                    "expectancy_r_local_count": 1,
                    "filled_count": 3,
                    "fill_rate": 0.20,
                    "pending_expired_rate": 0.70,
                    "ambiguity_rate": 0.20,
                },
            ]
        )

        ranked = rank_stability_candidates(
            results,
            param_cols=["entry.x_offset"],
            min_filled=10,
            min_fill_rate=0.50,
            max_pending_expired_rate=0.30,
            max_ambiguity_rate=0.10,
            min_local_count=3,
        )

        self.assertTrue(ranked.iloc[0]["candidate_eligible"])
        self.assertEqual(ranked.iloc[0]["entry.x_offset"], 5.0)
        self.assertFalse(ranked.iloc[1]["candidate_eligible"])
        self.assertEqual(len(ranked.iloc[0]["candidate_id"]), 12)

    def test_walkforward_selects_on_train_then_runs_oos(self) -> None:
        signals = pd.DataFrame(
            {
                "bartime": pd.to_datetime(
                    ["2024-01-10", "2024-02-10", "2024-03-10", "2024-03-20"]
                ),
                "symbol": ["TEST"] * 4,
                "timeframe": ["D1"] * 4,
                "direction": [Direction.BUY] * 4,
                "atr": [1.0] * 4,
            }
        )
        bars = pd.DataFrame(
            {
                "bartime": pd.date_range("2024-01-01", "2024-04-01", freq="D"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1,
            }
        )
        calls: list[dict[str, object]] = []

        def fake_runner(**kwargs) -> BacktestResult:
            frame = kwargs["signals"]
            config = kwargs["run_config"]
            atr_mult = float(config["sl_params"]["atr_mult"])
            calls.append(
                {
                    "times": list(pd.to_datetime(frame["bartime"])),
                    "atr_mult": atr_mult,
                    "last_bar": pd.to_datetime(kwargs["bars"]["bartime"]).max(),
                }
            )
            clusters = []
            for row in frame.itertuples(index=False):
                signal = SignalRow(
                    bartime=pd.Timestamp(row.bartime),
                    symbol="TEST",
                    timeframe="D1",
                    direction=Direction.BUY,
                    atr=1.0,
                )
                clusters.append(
                    ClusterResult(
                        signal=signal,
                        status=ClusterStatus.CLOSED,
                        r_result=atr_mult,
                    )
                )
            return BacktestResult(
                symbol="TEST",
                timeframe="D1",
                assumptions=RunAssumptions(),
                market_spec=MarketSpec(symbol="TEST"),
                clusters=tuple(clusters),
                config=dict(config),
            )

        window = WalkForwardWindow(
            train_start=pd.Timestamp("2024-01-01"),
            train_end=pd.Timestamp("2024-03-01"),
            test_start=pd.Timestamp("2024-03-01"),
            test_end=pd.Timestamp("2024-04-01"),
            index=0,
        )
        result = run_walkforward_research(
            signals=signals,
            bars=bars,
            windows=[window],
            symbol="TEST",
            timeframe="D1",
            market_spec=MarketSpec(symbol="TEST"),
            base_config={
                "engine": "single",
                "sl_method": "atr_multiple",
                "sl_params": {"atr_mult": 1.0},
                "tp_method": "risk_multiple",
                "tp_params": {"r_multiples": [1.0]},
            },
            param_grid={"stoploss.atr_mult": [1.0, 2.0]},
            neighborhood_steps=1,
            runner=fake_runner,
        )

        self.assertEqual(result.windows.iloc[0]["status"], "tested")
        self.assertEqual(result.selected_params.iloc[0]["stoploss.atr_mult"], 2.0)
        self.assertEqual(calls[-1]["atr_mult"], 2.0)
        self.assertTrue(all(time >= window.test_start for time in calls[-1]["times"]))
        self.assertTrue(all(time < window.test_end for time in calls[-1]["times"]))
        self.assertLess(calls[-1]["last_bar"], window.test_end)
        self.assertLess(calls[0]["last_bar"], window.train_end)

    def test_snapshot_records_lineage(self) -> None:
        with TemporaryDirectory() as tmp:
            path = save_snapshot(
                name="wf_TEST_D1",
                run_id="wf_TEST_D1",
                run_type="walkforward",
                parent_run_id="stability_TEST_D1",
                config={"engine": "component"},
                result_summary={"windows": 3},
                outputs={"oos_clusters": "oos.csv"},
                output_dir=Path(tmp),
            )
            payload = load_snapshot(path)

        self.assertEqual(payload["run_id"], "wf_TEST_D1")
        self.assertEqual(payload["parent_run_id"], "stability_TEST_D1")
        self.assertEqual(payload["outputs"]["oos_clusters"], "oos.csv")


if __name__ == "__main__":
    unittest.main()
