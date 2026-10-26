"""Phân bổ rủi ro Multi-channel: chia đều (làm tròn xuống), kiểm tổng khi tự nhập, mặc định từ config.yaml, và rủi ro
đã chia đi qua contract `channels` hiện có mà không đổi cách sinh RunSpec. Không chạm ctrader-cli/Redis/SQL."""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# tests/ nằm ngang hàng core_engine/ dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import configuration, risk_allocation
from core_engine.configuration import (
    load_engine_profile,
    load_portfolio_risk_settings,
    load_strategy_profile,
    resolve_backtest_config,
    with_signal_overrides,
)
from core_engine.planner import expand_grid, research_windows
from core_engine.risk_allocation import allocate_equal_risk, validate_manual_risks

FIVE_CHANNELS = (
    ("combo", "HK50.cash", "H3"),
    ("macd_break", "HK50.cash", "M45"),
    ("macd_break", "HK50.cash", "H2"),
    ("combo", "US30.cash", "H4"),
    ("macd_break", "JP225.cash", "H2"),
)


class EqualSplitTests(unittest.TestCase):
    def test_exact_splits(self):
        self.assertEqual(allocate_equal_risk(2, 5), 0.4)
        self.assertEqual(allocate_equal_risk(1.5, 2), 0.75)
        self.assertEqual(allocate_equal_risk(0.3, 3), 0.1)
        self.assertEqual(allocate_equal_risk("2.0", 4), 0.5)

    def test_rounds_down_so_the_sum_never_exceeds_the_limit(self):
        each = allocate_equal_risk(2, 3)
        self.assertEqual(each, 0.6666)
        self.assertLessEqual(Decimal(str(each)) * 3, Decimal(2))
        self.assertEqual(allocate_equal_risk(2, 3, decimals=2), 0.66)
        with self.assertRaises(ValueError):   # 2/3 làm tròn xuống 0 chữ số = 0, không có channel nào chạy được
            allocate_equal_risk(2, 3, decimals=0)

    def test_every_count_stays_within_the_limit(self):
        for count in range(1, 41):
            each = allocate_equal_risk(2, count)
            self.assertGreater(each, 0)
            self.assertLessEqual(Decimal(str(each)) * count, Decimal(2), count)

    def test_rejects_bad_inputs(self):
        for total in (0, -1, float("nan"), float("inf"), True, "abc", None):
            with self.assertRaises(ValueError, msg=repr(total)):
                allocate_equal_risk(total, 2)
        for count in (0, -1, 1.5, True, "3"):
            with self.assertRaises(ValueError, msg=repr(count)):
                allocate_equal_risk(2, count)
        for decimals in (-1, 9, 2.5, True):
            with self.assertRaises(ValueError, msg=repr(decimals)):
                allocate_equal_risk(2, 2, decimals=decimals)

    def test_a_limit_too_small_for_the_channel_count_is_an_error(self):
        with self.assertRaises(ValueError):
            allocate_equal_risk(0.0001, 3)


class ManualRiskTests(unittest.TestCase):
    def test_total_at_or_below_the_limit_is_returned(self):
        self.assertEqual(validate_manual_risks([0.4] * 5, 2), 2.0)
        self.assertEqual(validate_manual_risks([0.1, 0.2], 0.3), 0.3)
        self.assertEqual(validate_manual_risks([1.0], 2), 1.0)

    def test_total_above_the_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_manual_risks([0.5, 0.5, 1.1], 2)
        with self.assertRaises(ValueError):
            validate_manual_risks([0.4] * 5 + [0.0001], 2)

    def test_each_risk_must_be_positive_and_there_must_be_one(self):
        for risks in ([], [0.5, 0], [0.5, -0.1], [0.5, float("nan")], [0.5, True]):
            with self.assertRaises(ValueError, msg=repr(risks)):
                validate_manual_risks(risks, 2)


class PortfolioSettingsTests(unittest.TestCase):
    def test_repo_config_defaults(self):
        settings = load_portfolio_risk_settings()
        self.assertEqual(settings.total_risk_percent, 2.0)
        self.assertEqual(settings.risk_decimals, risk_allocation.DEFAULT_RISK_DECIMALS)

    def _with_backtest(self, backtest):
        return patch.object(configuration, "_config", return_value={"backtest": backtest})

    def test_missing_total_is_an_error_but_missing_decimals_has_a_default(self):
        for backtest in ({}, {"portfolio": None}, {"portfolio": {"risk_decimals": 4}}):
            with self._with_backtest(backtest), self.assertRaises(ValueError, msg=repr(backtest)):
                load_portfolio_risk_settings()
        with self._with_backtest({"portfolio": {"total_risk_percent": 1.5}}):
            settings = load_portfolio_risk_settings()
        self.assertEqual((settings.total_risk_percent, settings.risk_decimals), (1.5, risk_allocation.DEFAULT_RISK_DECIMALS))

    def test_invalid_values_are_errors(self):
        for portfolio in (
            {"total_risk_percent": 0},
            {"total_risk_percent": -2},
            {"total_risk_percent": "x"},
            {"total_risk_percent": 2, "risk_decimals": 12},
            {"total_risk_percent": 2, "risk_decimals": "4"},
        ):
            with self._with_backtest({"portfolio": portfolio}), self.assertRaises(ValueError, msg=repr(portfolio)):
                load_portfolio_risk_settings()


class ChannelContractTests(unittest.TestCase):
    def test_equal_split_feeds_the_existing_channels_contract(self):
        settings = load_portfolio_risk_settings()
        each = allocate_equal_risk(settings.total_risk_percent, len(FIVE_CHANNELS), decimals=settings.risk_decimals)
        self.assertEqual(each, 0.4)
        channels = [
            {"strategy": strategy, "symbol": symbol, "timeframe": timeframe, "risk_percent": each}
            for strategy, symbol, timeframe in FIVE_CHANNELS
        ]
        config = resolve_backtest_config({
            "name": "risk_allocation_test",
            "channels": channels,
            "account_mode": "shared",
            "redis_profile": "sen05-signal-v1",
        })
        profile = with_signal_overrides(load_strategy_profile(config["strategy_profile"]), config)
        engine = load_engine_profile(config["engine_profile"])
        specs = expand_grid(config, profile, engine, windows=research_windows(config))
        self.assertEqual(len(specs), 17)
        for spec in specs:
            self.assertEqual([c.risk_percent for c in spec.channels], [0.4] * len(FIVE_CHANNELS))
            # Hạn mức tổng và chế độ KHÔNG đi vào RunSpec (run_key chỉ phụ thuộc risk_percent từng channel).
            self.assertNotIn("total_risk_percent", spec.canonical_json())
            self.assertNotIn("risk_allocation", spec.canonical_json())


if __name__ == "__main__":
    unittest.main()
