"""portfolio_composition: suy nguồn tín hiệu và gom thành phần danh mục trên dữ liệu tổng hợp (không chạm SQL)."""
from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path

import pandas as pd

# tests/ nằm ngang hàng core_engine/ dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine.output_util import portfolio_composition as pc


def frame(*rows):
    """rows: (bartime, signal, tf, symbol, strategy, risk_percent)."""
    return pd.DataFrame(rows, columns=["bartime", "signal", "tf", "symbol", "strategy", "risk_percent"]).assign(atr=1.0, entry=1.0)


class SmallHelperTests(unittest.TestCase):
    def test_fib_value_reads_the_multiplier(self):
        self.assertEqual(pc.fib_value("Fib3330"), 3.33)
        self.assertEqual(pc.fib_value("Fib2618"), 2.618)
        self.assertIsNone(pc.fib_value(None))
        self.assertIsNone(pc.fib_value("abc"))

    def test_channel_name_matches_the_trade_table(self):
        self.assertEqual(pc.channel_name("combo", "HK50.cash", "h3"), "Combo · HK50 · H3")
        self.assertEqual(pc.channel_name("macd_break", "USDJPY", "M20"), "MACD Break · USDJPY · M20")
        self.assertEqual(pc.channel_name("ma_cross", "XAUUSD", "m45"), "MA Cross · XAUUSD · M45")

    def test_decode_signals_round_trip(self):
        csv = "bartime,atr,entry,signal,tf,symbol,strategy,risk_percent\n2022-01-03 10:00:00,1.5,100,1,H3,HK50.cash,combo,0.2\n"
        out = pc.decode_signals(gzip.compress(csv.encode()))
        self.assertEqual(out.iloc[0]["symbol"], "HK50.cash")
        self.assertEqual(out.iloc[0]["risk_percent"], 0.2)

    def test_jaccard(self):
        self.assertEqual(pc.jaccard(frozenset({1, 2}), frozenset({1, 2})), 1.0)
        self.assertEqual(pc.jaccard(frozenset({1, 2}), frozenset({2, 3})), 1 / 3)
        self.assertEqual(pc.jaccard(frozenset(), frozenset()), 0.0)


class ChannelSignalTests(unittest.TestCase):
    def test_groups_by_channel_clips_the_window_and_normalises_the_side(self):
        data = frame(
            ("2021-12-31 10:00:00", 1, "H3", "HK50.cash", "combo", 0.2),      # trước cửa sổ
            ("2022-01-03 10:00:00", 1, "H3", "HK50.cash", "combo", 0.2),
            ("2022-01-04 10:00:00", 2, "H3", "HK50.cash", "combo", 0.2),
            ("2022-01-05 10:00:00", -1, "H3", "HK50.cash", "combo", 0.2),
            ("2026-06-30 23:00:00", 1, "M20", "USDJPY", "macd_break", 0.5),
            ("2026-07-01 00:00:00", 1, "M20", "USDJPY", "macd_break", 0.5),   # sau cửa sổ
        )
        out = pc.channel_signals(data, "2022-01-01", "2026-06-30")
        self.assertEqual(set(out), {("combo", "HK50.cash", "h3"), ("macd_break", "USDJPY", "m20")})
        combo = out[("combo", "HK50.cash", "h3")]
        self.assertEqual(combo["risk"], 0.2)
        self.assertEqual(combo["signals"], frozenset({("2022-01-03 10:00:00", 1), ("2022-01-04 10:00:00", -1), ("2022-01-05 10:00:00", -1)}))
        self.assertEqual(out[("macd_break", "USDJPY", "m20")]["signals"], frozenset({("2026-06-30 23:00:00", 1)}))

    def test_single_snapshot_without_risk_column_still_groups(self):
        data = frame(("2022-01-03 10:00:00", 1, "H3", "HK50.cash", "combo", 0.2)).drop(columns=["risk_percent"])
        info = pc.channel_signals(data, "2022-01-01", "2026-06-30")[("combo", "HK50.cash", "h3")]
        self.assertTrue(pd.isna(info["risk"]))
        self.assertEqual(info["signals"], frozenset({("2022-01-03 10:00:00", 1)}))

    def test_single_channel_signals_filters_when_columns_exist_and_takes_all_when_not(self):
        data = frame(("2022-01-03 10:00:00", 1, "H3", "HK50.cash", "combo", 0.2), ("2022-01-04 10:00:00", 2, "H4", "US30.cash", "combo", 0.2))
        key = ("combo", "HK50.cash", "h3")
        self.assertEqual(pc.single_channel_signals(data, key, "2022-01-01", "2026-06-30"), frozenset({("2022-01-03 10:00:00", 1)}))
        bare = data[["bartime", "signal"]]
        self.assertEqual(len(pc.single_channel_signals(bare, key, "2022-01-01", "2026-06-30")), 2)

    def test_risk_range_is_reported(self):
        data = frame(("2022-01-03 10:00:00", 1, "H3", "HK50.cash", "combo", 0.2), ("2022-01-04 10:00:00", 1, "H3", "HK50.cash", "combo", 0.4))
        info = pc.channel_signals(data, "2022-01-01", "2026-06-30")[("combo", "HK50.cash", "h3")]
        self.assertEqual((info["risk_min"], info["risk"]), (0.2, 0.4))


class DecideSourceTests(unittest.TestCase):
    def test_one_exact_match_wins(self):
        self.assertEqual(pc.decide_source({"original": 1.0, "knn": 0.19, "breakout": 0.41, "zigzag": 0.47}, None), ("original", 1.0, "trùng 100%"))

    def test_two_identical_sources_are_not_guessed(self):
        source, score, note = pc.decide_source({"original": 1.0, "knn": 1.0, "breakout": 0.3}, None)
        self.assertEqual((source, score), ("original|knn", 1.0))
        self.assertIn("không phân biệt được", note)

    def test_declared_source_of_a_single_source_run_is_trusted(self):
        source, score, note = pc.decide_source({"original": 1.0, "knn": 1.0}, "knn")
        self.assertEqual((source, score), ("knn", 1.0))
        self.assertIn("theo bản chụp của lượt", note)

    def test_near_match_is_flagged_and_far_match_is_unknown(self):
        source, score, note = pc.decide_source({"original": 0.985, "knn": 0.4}, None)
        self.assertEqual((source, round(score, 3)), ("original", 0.985))
        self.assertIn("gần khớp", note)
        source, score, note = pc.decide_source({"original": 0.7, "knn": 0.4}, None)
        self.assertEqual(source, "unknown")
        self.assertIn("không nguồn nào trùng", note)

    def test_no_snapshot_at_all_is_unknown(self):
        self.assertEqual(pc.decide_source({}, None)[0], "unknown")


if __name__ == "__main__":
    unittest.main()
