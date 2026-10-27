"""trade_excursion: ghép nhóm lệnh ↔ tín hiệu và đo MFE trên tick tổng hợp (không chạm SQL, không đọc cache tick thật)."""
from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

# tests/ nằm ngang hàng core_engine/ dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine.output_util import trade_excursion as te

T0 = 1_700_000_000_000
STEP = 1_000
KSL = 2.618


def make_ticks(mids, skip=()):
    """Tick mỗi giây; ask = bid + 1. `skip` = các chỉ số tick bị bỏ (thị trường đóng)."""
    keep = [i for i in range(len(mids)) if i not in set(skip)]
    t = np.array([T0 + i * STEP for i in keep], dtype=np.int64)
    bid = np.array([mids[i] for i in keep], dtype=float)
    return t, bid, bid + 1.0


def signals(*rows):
    """rows: (avail_idx_ms_offset, direction, atr, entry) → bảng như parse_signals."""
    frame = pd.DataFrame(rows, columns=["avail_ms", "direction", "atr", "entry"])
    frame["avail_ms"] = frame["avail_ms"] + T0
    frame["bar_ms"] = frame["avail_ms"] - 60_000
    return frame.sort_values("avail_ms", kind="stable").reset_index(drop=True)


def groups(*rows):
    """rows: (direction, entry_idx, fill_price)."""
    return pd.DataFrame({"direction": [r[0] for r in rows], "entry_ms": [T0 + r[1] * STEP for r in rows], "fill_price": [r[2] for r in rows]})


def measure(t, bid, ask, sig, grp, *, stop=False, bar_ms=60_000):
    return te.measure_run(t, bid, ask, sig, grp, is_stop_order=stop, bar_ms=bar_ms, ksl=KSL, last_ms=int(t[-1]))


class MeasureMarketTests(unittest.TestCase):
    def test_reversal_ends_the_walk_and_mfe_uses_bid_for_buys(self):
        mids = [1000.0] * 10 + [1000.0 + 3 * k for k in range(11)] + [1030.0] * 10 + [1030.0 - 2 * k for k in range(11)] + [1010.0] * 60
        t, bid, ask = make_ticks(mids)
        sig = signals((10 * STEP, 1, 10.0, 0.0), (50 * STEP, -1, 10.0, 0.0))
        out = measure(t, bid, ask, sig, groups((1, 10, 1001.0)))
        row = out.iloc[0]
        self.assertTrue(row["matched"])
        self.assertEqual(row["end_kind"], te.END_REVERSAL)
        self.assertAlmostEqual(row["mfe_atr"], (1030.0 - 1001.0) / 10.0)
        self.assertAlmostEqual(row["end_exc_atr"], (mids[50] - 1001.0) / 10.0)
        self.assertEqual(row["end_ms"], T0 + 50 * STEP)
        self.assertAlmostEqual(row["slip_atr"], 0.0)

    def test_stop_loss_ends_the_walk_before_the_reversal_and_excludes_the_stop_tick(self):
        mids = [1000.0] * 10 + [1000.0 + 2 * k for k in range(6)] + [1010.0 - 3 * k for k in range(25)] + [930.0] * 50
        t, bid, ask = make_ticks(mids)
        sig = signals((10 * STEP, 1, 10.0, 0.0), (80 * STEP, -1, 10.0, 0.0))
        out = measure(t, bid, ask, sig, groups((1, 10, 1001.0)))
        row = out.iloc[0]
        sl_price = 1001.0 - KSL * 10.0
        first_hit = int(np.argmax(bid[10:] <= sl_price)) + 10
        self.assertEqual(row["end_kind"], te.END_SL)
        self.assertEqual(row["end_ms"], T0 + first_hit * STEP)
        self.assertLessEqual(row["end_exc_atr"], -KSL)
        self.assertAlmostEqual(row["mfe_atr"], (bid[10:first_hit].max() - 1001.0) / 10.0)

    def test_sells_use_ask_and_favourable_is_the_lowest_ask(self):
        mids = [1000.0] * 10 + [1000.0 - 2 * k for k in range(10)] + [982.0] * 40
        t, bid, ask = make_ticks(mids)
        sig = signals((10 * STEP, -1, 10.0, 0.0))
        out = measure(t, bid, ask, sig, groups((-1, 10, 1000.0)))
        row = out.iloc[0]
        self.assertEqual(row["end_kind"], te.END_DATA)
        self.assertAlmostEqual(row["mfe_atr"], (1000.0 - ask.min()) / 10.0)

    def test_two_signals_queued_for_the_same_tick_the_earlier_one_owns_the_group(self):
        mids = [1000.0] * 40
        t, bid, ask = make_ticks(mids, skip=range(5, 10))
        sig = signals((4_500, 1, 10.0, 0.0), (8_000, 1, 20.0, 0.0))
        out = measure(t, bid, ask, sig, groups((1, 10, 1001.0)))
        self.assertTrue(out.iloc[0]["matched"])
        self.assertAlmostEqual(out.iloc[0]["atr"], 10.0)

    def test_group_without_signal_stays_unmatched(self):
        t, bid, ask = make_ticks([1000.0] * 30)
        out = measure(t, bid, ask, signals((20 * STEP, 1, 10.0, 0.0)), groups((1, 10, 1001.0)))
        self.assertFalse(out.iloc[0]["matched"])


class MeasureStopOrderTests(unittest.TestCase):
    def test_expired_opposite_signal_does_not_trigger_a_reversal(self):
        # tín hiệu STOP hết hạn sau 3 khung (3 × 5 s); tín hiệu ngược tới lúc thị trường mở lại (muộn 29 s) bị bỏ.
        mids = [1000.0] * 15 + [1006.0] * 25 + [1010.0] * 60
        t, bid, ask = make_ticks(mids, skip=range(41, 70))
        sig = signals((10 * STEP, 1, 10.0, 1005.0), (41 * STEP, -1, 10.0, 1002.0))
        out = measure(t, bid, ask, sig, groups((1, 15, 1007.0)), stop=True, bar_ms=5_000)
        row = out.iloc[0]
        self.assertTrue(row["matched"])
        self.assertEqual(row["end_kind"], te.END_DATA)
        self.assertAlmostEqual(row["ref_price"], 1005.0)                 # ref = entry của tín hiệu, không phải giá khớp
        self.assertAlmostEqual(row["slip_atr"], (1007.0 - 1005.0) / 10.0)

    def test_a_live_opposite_signal_reverses(self):
        mids = [1000.0] * 15 + [1006.0] * 60
        t, bid, ask = make_ticks(mids)
        sig = signals((10 * STEP, 1, 10.0, 1005.0), (30 * STEP, -1, 10.0, 1002.0))
        out = measure(t, bid, ask, sig, groups((1, 15, 1007.0)), stop=True, bar_ms=5_000)
        self.assertEqual(out.iloc[0]["end_kind"], te.END_REVERSAL)
        self.assertEqual(out.iloc[0]["end_ms"], T0 + 30 * STEP)

    def test_picks_the_signal_whose_entry_is_closest_to_the_fill(self):
        mids = [1000.0] * 15 + [1006.0] * 40
        t, bid, ask = make_ticks(mids)
        sig = signals((8 * STEP, 1, 7.0, 1040.0), (10 * STEP, 1, 10.0, 1005.0))
        out = measure(t, bid, ask, sig, groups((1, 15, 1007.0)), stop=True, bar_ms=10_000)
        self.assertAlmostEqual(out.iloc[0]["atr"], 10.0)


class SourceParsingTests(unittest.TestCase):
    def test_signals_are_normalised_like_the_bot(self):
        csv = "bartime,atr,entry,signal,tf,symbol,strategy,risk_percent\n2022-01-04 15:00:00,64.03,23265.0,2,H1,HK50.cash,combo,1\n2022-01-04 16:00:00,10,23000,1,H1,HK50.cash,combo,1\n2022-01-04 17:00:00,10,23000,-1,H1,HK50.cash,combo,1\n"
        frame = te.parse_signals(gzip.compress(csv.encode()), "H1")
        self.assertEqual(frame["direction"].tolist(), [-1, 1, -1])
        self.assertEqual(frame["avail_ms"].iloc[0] - frame["bar_ms"].iloc[0], 3_600_000)
        self.assertEqual(frame["bar_ms"].iloc[0], 1_641_308_400_000)

    def test_bar_duration(self):
        self.assertEqual(te.bar_duration_ms("M45"), 45 * 60_000)
        self.assertEqual(te.bar_duration_ms("h3"), 3 * 3_600_000)

    def test_tick_records_and_forward_fill(self):
        records = np.array([[1, 100_000, 101_000], [2, 0, 102_000], [3, 103_000, 0]], dtype="<i8")
        parsed = te.parse_ticks(records.tobytes() + b"xx")                # byte thừa cuối file bị bỏ
        self.assertEqual(parsed.shape, (3, 3))
        self.assertEqual(te._forward_fill_zero(parsed[:, 1].astype(float)).tolist(), [100_000, 100_000, 103_000])
        self.assertEqual(te._forward_fill_zero(parsed[:, 2].astype(float)).tolist(), [101_000, 102_000, 102_000])


class PendingSummaryTests(unittest.TestCase):
    def test_counts_only_runs_without_an_excursion_row_by_source(self):
        runs = pd.DataFrame({"backtest_run_id": [1, 2, 3, 4, 5], "signal_channel": ["original", "original", "tstat", "tstat", "tstat"]})
        out = te.pending_summary(runs, {1, 3})
        self.assertEqual(out, {"eligible": 5, "done": 2, "pending": 3, "pending_by_source": {"original": 1, "tstat": 2}})

    def test_nothing_pending_when_every_run_is_done(self):
        runs = pd.DataFrame({"backtest_run_id": [1, 2], "signal_channel": ["knn", "knn"]})
        self.assertEqual(te.pending_summary(runs, {1, 2, 99}), {"eligible": 2, "done": 2, "pending": 0, "pending_by_source": {}})


if __name__ == "__main__":
    unittest.main()
