"""BacktestBot — bot DUY NHẤT chạy nhiều chiến lược × symbol × khung (thay ComboMulti/MACrossMulti/MACDBreakMulti).

Máy này không biên dịch được C#, nên phần lớn test ở đây là LƯỚI AN TOÀN cho hợp đồng giữa bot và Python: tên/mặc định
tham số, thứ tự enum, bảng chiến lược trong .cs khớp bảng Python, các chuỗi log mà `evidence` đếm. Phần Python (chọn chiến
lược, ghép file tín hiệu, tên lượt, tra kho SQL trước khi chạy) được test thật. Không test nào chạm SQL Server hay Redis."""
from __future__ import annotations

import dataclasses
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import facilitator, signal_trans, store
from core_engine.configuration import (
    SL_FIB_LEVELS, STRATEGY_CODES, TP_FIB_LEVELS, MasterDbProfile, load_engine_profile, load_strategy_profile,
    resolve_backtest_config, with_signal_overrides,
)
from core_engine.models import FailureCode, RunResult, RunSpec, RunStatus
from core_engine.output_util import run_export
from core_engine.planner import build_experiment, expand_grid, research_windows, run_name

ROBOTS = Path(__file__).resolve().parents[2]
BOT_SOURCE = ROBOTS / "BacktestBot" / "BacktestBot" / "BacktestBot.cs"
PROFILE_ID = "backtestbot-v1"
PARAM_NAMES = [
    "KslLevel", "KtpLevel", "LegCount", "Leg2Ktp", "Leg3Ktp", "RiskPercent", "EnableDailyLossLimit",
    "MaxDailyLossPercent", "EnableMaxDrawdown", "MaxTotalDrawdownPercent", "EnableMaxConsecutiveLosses",
    "MaxConsecutiveLosses",
]


def _bot_text() -> str:
    return BOT_SOURCE.read_text(encoding="utf-8")


class ProfileAndSourceContractTests(unittest.TestCase):
    def test_exact_channels_carry_independent_group_risk_without_a_cartesian_product(self) -> None:
        config = resolve_backtest_config({
            "channels": [
                {"strategy": "combo", "symbol": "US30.cash", "timeframe": "H4", "risk_percent": 0.6},
                {"strategy": "macd_break", "symbol": "XAUUSD", "timeframe": "H3", "risk_percent": 0.9},
            ],
            "account_mode": "shared",
        })
        profile = with_signal_overrides(load_strategy_profile(PROFILE_ID), config)
        specs = expand_grid(config, profile, load_engine_profile(config["engine_profile"]))
        self.assertEqual(len(research_windows(config)), 17)
        self.assertEqual(len(specs), 17)
        self.assertEqual([(c.strategy, c.symbol, c.timeframe, c.risk_percent) for c in specs[0].channels], [
            ("combo", "US30.cash", "H4", 0.6),
            ("macd_break", "XAUUSD", "H3", 0.9),
        ])

    def test_one_job_rejects_multiple_redis_profiles(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_backtest_config({
                "redis_profile": ["sen05-signal-v1", "sen05-signal-knn-v1"],
                "channels": [{"strategy": "combo", "symbol": "US30.cash", "timeframe": "H4", "risk_percent": 0.6}],
            })

    def test_bot_reads_risk_from_each_channel(self) -> None:
        source = _bot_text()
        self.assertIn('Array.IndexOf(header, "risk_percent")', source)
        self.assertIn("double riskPercent = signal.Channel.RiskPercent;", source)
        self.assertIn("has conflicting risk values", source)

    def test_profile_is_the_only_catalog_of_three_strategies_with_their_default_frames(self) -> None:
        profile = load_strategy_profile(PROFILE_ID)
        self.assertEqual(profile.cbot_name, "BacktestBot")
        self.assertEqual(profile.algo_path.name, "BacktestBot.algo")
        self.assertEqual(profile.source_path, BOT_SOURCE)
        self.assertEqual(list(profile.strategy_frames), ["combo", "ma_cross", "macd_break"])
        self.assertEqual(profile.strategy_frames["combo"], ("H1", "H2", "H3", "H4"))
        self.assertEqual(profile.strategy_frames["ma_cross"], ("M10", "M20", "M30", "M45"))
        self.assertEqual(profile.strategy_frames["macd_break"], ("H1", "H2", "H3", "H4", "M10", "M20", "M30", "M45"))
        self.assertEqual((profile.signal_strategies, profile.signal_symbols, profile.signal_timeframes), ((), (), ()))

    def test_params_and_defaults_are_the_combomulti_ones(self) -> None:
        """15 tham số (+ SignalFilePath = 16 trong metadata), TỐI ĐA 3 chân: thứ tự và mặc định như ComboMulti trước đây, nhưng
        không còn Leg4Ktp. Enum lưu theo số thứ tự nên thứ tự các tham số/enum còn lại không được đổi."""
        profile = load_strategy_profile(PROFILE_ID)
        self.assertEqual(list(profile.params), PARAM_NAMES)
        defaults = profile.defaults()
        self.assertEqual(defaults["KslLevel"], str(SL_FIB_LEVELS.index("Fib1618")))
        self.assertEqual(defaults["KtpLevel"], str(TP_FIB_LEVELS.index("Fib2618")))
        self.assertEqual(defaults["RiskPercent"], "0.2")
        self.assertEqual(defaults["LegCount"], "1")
        self.assertEqual(profile.params["Leg2Ktp"].default, "Fib3330")
        self.assertEqual(profile.params["Leg3Ktp"].default, "Fib4236")
        self.assertNotIn("Leg4Ktp", profile.params)   # tối đa 3 chân

    def test_cs_source_declares_every_profile_param_with_the_profile_default(self) -> None:
        source = _bot_text()
        self.assertIn("public class BacktestBot : Robot", source)
        self.assertRegex(source, r"public string SignalFilePath \{")
        for name, param in load_strategy_profile(PROFILE_ID).params.items():
            found = re.search(
                r"\[Parameter\([^\]]*?DefaultValue = ([^,\)\]]+)[^\]]*\]\s*public \S+ " + re.escape(name) + r" \{", source,
            )
            self.assertIsNotNone(found, f"tham số {name} không có trong BacktestBot.cs")
            declared = found.group(1).strip()
            if re.match(r"[A-Za-z_]", declared):   # enum (SlFibLevel.Fib1618) hoặc true/false
                declared = declared.split(".")[-1]
            expected = str(param.default).lower() if isinstance(param.default, bool) else str(param.default)
            if re.fullmatch(r"-?\d+(\.\d+)?", expected):
                self.assertEqual(float(declared), float(expected), name)
            else:
                self.assertEqual(declared, expected, name)

    def test_metadata_without_default_value_means_zero_for_numbers_only(self) -> None:
        """ctrader-cli không ghi DefaultValue khi mặc định bằng 0 (ComboRiskPercent...); các mặc định khác vẫn phải khớp."""
        profile = load_strategy_profile(PROFILE_ID)
        meta = _metadata(profile)
        for row in meta["Parameters"]:
            if row["PropertyName"] == "ComboRiskPercent":
                del row["DefaultValue"]
        profile.validate_metadata(meta)
        for row in meta["Parameters"]:
            if row["PropertyName"] == "RiskPercent":
                del row["DefaultValue"]   # mặc định thật 0,2: thiếu là lệch
        with self.assertRaisesRegex(ValueError, "default drift"):
            profile.validate_metadata(meta)

    def test_enum_order_matches_the_python_levels(self) -> None:
        """cTrader lưu enum theo SỐ THỨ TỰ: lệch thứ tự = lệch nghĩa âm thầm."""
        source = _bot_text()
        for enum, levels in (("SlFibLevel", SL_FIB_LEVELS), ("TpFibLevel", TP_FIB_LEVELS)):
            body = re.search(r"public enum " + enum + r"\s*\{([^}]*)\}", source).group(1)
            self.assertEqual([item.strip() for item in body.split(",") if item.strip()], list(levels), enum)

    @unittest.skip("replaced by per-channel CSV risk contract")
    def test_per_strategy_risk_overrides_exist_for_every_strategy_and_default_to_the_common_risk(self) -> None:
        source = _bot_text()
        profile = load_strategy_profile(PROFILE_ID)
        for code, name in (("cb", "ComboRiskPercent"), ("mc", "MaCrossRiskPercent"), ("mb", "MacdBreakRiskPercent")):
            self.assertIn(f'"{code}" => {name}', source)
            self.assertEqual(profile.params[name].default, 0.0)   # 0 = dùng RiskPercent chung, hành vi cũ không đổi
        self.assertIn("return own > 0 ? own : RiskPercent;", source)
        self.assertNotIn("Account.Balance * RiskPercent", source)   # khối lượng luôn theo rủi ro của chiến lược

    @unittest.skip("replaced by per-channel CSV risk contract")
    def test_macd_h3_h4_channel_risk_overrides_precede_strategy_fallback(self) -> None:
        source = _bot_text()
        profile = load_strategy_profile(PROFILE_ID)
        for timeframe, name in (("H3", "MacdBreakH3RiskPercent"), ("H4", "MacdBreakH4RiskPercent")):
            self.assertIn(f'("mb", "{timeframe}") => {name}', source)
            self.assertEqual(profile.params[name].default, 0.0)
        self.assertIn("if (channelOwn > 0)", source)
        self.assertIn("return channelOwn;", source)

    def test_strategy_table_in_cs_matches_python(self) -> None:
        declared = re.findall(r'new StrategyDef\("([a-z_]+)", "([a-z]{2})", (true|false)\)', _bot_text())
        self.assertEqual(
            declared, [("combo", "cb", "true"), ("ma_cross", "mc", "false"), ("macd_break", "mb", "false")],
        )
        self.assertEqual({token: code for token, code, _ in declared}, STRATEGY_CODES)
        for token, _code, is_stop in declared:
            self.assertIn(token, signal_trans.STRATEGY_COLUMNS)
            # lệnh STOP cần entry; lệnh MARKET không đọc entry từ Redis
            self.assertEqual("entry" in signal_trans.STRATEGY_COLUMNS[token], is_stop == "true", token)

    def test_required_columns_in_cs_are_the_ones_python_writes(self) -> None:
        source = _bot_text()
        for column in signal_trans.SIGNAL_FILE_COLUMNS:
            self.assertIn(f'Array.IndexOf(header, "{column}")', source, column)

    def test_log_lines_never_trigger_the_margin_rejection_counter(self) -> None:
        """evidence._REJECTION_LINE đếm MỌI dòng log chứa margin / not enough money / rejected là 1 lần từ chối margin
        (làm hỏng report_ok). Chỉ 2 dòng đặt lệnh thất bại được phép chứa 'rejected'."""
        literals = re.findall(r'"((?:\\.|[^"\\])*)"', _bot_text())
        prints = [text for text in literals if text.startswith("BacktestBot:")]
        self.assertTrue(prints)
        for text in prints:
            self.assertNotRegex(text, r"(?i)margin|not enough money", text)
        rejected = [text for text in prints if "rejected" in text.lower()]
        self.assertEqual(len(rejected), 2, rejected)
        self.assertTrue(all("was rejected" in text for text in rejected))

    def test_onstop_line_keeps_the_counters_evidence_reads(self) -> None:
        text = next(
            item for item in re.findall(r'"((?:\\.|[^"\\])*)"', _bot_text()) if item.startswith("BacktestBot: loaded=")
        )
        for key in ("loaded", "processed", "before-start", "not-processed", "placed", "failed", "guard-skipped",
                    "pending-expired", "same-direction-skipped", "reversed"):
            self.assertIn(f"{key}=", text, key)
        # không có field nào khác kết thúc bằng tên counter đã có, để regex không khớp nhầm
        for suffix in ("loaded=", "processed=", "placed=", "failed=", "reversed="):
            self.assertEqual(len(re.findall(r"[\w-]*" + re.escape(suffix), text)), 1 + (suffix == "processed=") , suffix)


class StrategySelectionTests(unittest.TestCase):
    def profile(self):
        return load_strategy_profile(PROFILE_ID)

    def test_strategies_are_mandatory(self) -> None:
        for config in ({}, {"signal_strategies": []}, {"signal_symbols": ["US30.cash"]}):
            with self.assertRaisesRegex(ValueError, "signal_strategies"):
                with_signal_overrides(self.profile(), config)

    def test_unknown_strategy_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "nope"):
            with_signal_overrides(self.profile(), {"signal_strategies": ["combo", "nope"]})

    def test_selection_follows_the_catalog_order_and_names_the_run_by_its_strategies(self) -> None:
        chosen = with_signal_overrides(self.profile(), {"signal_strategies": ["MACD_Break", "combo", "combo"]})
        self.assertEqual(chosen.signal_strategies, ("combo", "macd_break"))
        self.assertEqual(chosen.strategy, "combo+macd_break")
        self.assertEqual(list(chosen.strategy_frames), ["combo", "macd_break"])
        self.assertEqual(chosen.strategy_frames["combo"], ("H1", "H2", "H3", "H4"))

    def test_timeframes_narrow_each_strategy_and_never_silently(self) -> None:
        chosen = with_signal_overrides(
            self.profile(), {"signal_strategies": ["combo", "macd_break"], "signal_timeframes": ["m10", "h1"]},
        )
        self.assertEqual(chosen.strategy_frames["combo"], ("H1",))
        self.assertEqual(chosen.strategy_frames["macd_break"], ("H1", "M10"))
        with self.assertRaisesRegex(ValueError, "combo"):   # combo không có khung phút nào
            with_signal_overrides(self.profile(), {"signal_strategies": ["combo"], "signal_timeframes": ["M10"]})
        with self.assertRaisesRegex(ValueError, "M10"):   # combo vẫn hợp lệ với H1 nhưng M10 không thuộc chiến lược nào đã chọn
            with_signal_overrides(self.profile(), {"signal_strategies": ["combo"], "signal_timeframes": ["H1", "M10"]})
        with self.assertRaisesRegex(ValueError, "D1"):   # D1 không có ở chiến lược nào
            with_signal_overrides(self.profile(), {"signal_strategies": ["combo", "macd_break"], "signal_timeframes": ["H1", "D1"]})
        self.assertEqual(self.profile().strategy_frames["combo"], ("H1", "H2", "H3", "H4"))   # profile gốc không đổi

    def test_signal_frames_give_each_strategy_its_own_frames(self) -> None:
        config = {"signal_strategies": ["combo", "ma_cross", "macd_break"],
                  "signal_frames": {"combo": ["h3", "H4"], "ma_cross": ["M30", "M45"], "macd_break": ["M45"]}}
        chosen = with_signal_overrides(self.profile(), config)
        self.assertEqual(chosen.strategy_frames, {"combo": ("H3", "H4"), "ma_cross": ("M30", "M45"), "macd_break": ("M45",)})
        self.assertEqual(chosen.signal_strategies, ("combo", "ma_cross", "macd_break"))
        for bad in ({"combo": ["M30"], "ma_cross": ["M30"], "macd_break": ["M45"]},   # combo không có M30
                    {"combo": ["H3"], "ma_cross": ["M30"]},                            # thiếu macd_break
                    {"combo": [], "ma_cross": ["M30"], "macd_break": ["M45"]}):        # combo rỗng
            with self.assertRaises(ValueError):
                with_signal_overrides(self.profile(), {**config, "signal_frames": bad})
        with self.assertRaisesRegex(ValueError, "cùng nhau"):
            with_signal_overrides(self.profile(), {**config, "signal_timeframes": ["H3"]})
        self.assertEqual(self.profile().strategy_frames["combo"], ("H1", "H2", "H3", "H4"))   # profile gốc không đổi

    def test_symbols_are_overridden_like_other_profiles(self) -> None:
        chosen = with_signal_overrides(self.profile(), {"signal_strategies": ["combo"], "signal_symbols": ["XAUUSD", "US30.cash"]})
        self.assertEqual(chosen.signal_symbols, ("XAUUSD", "US30.cash"))

    def test_single_strategy_profile_refuses_signal_strategies(self) -> None:
        single = dataclasses.replace(self.profile(), strategy_frames={})
        with self.assertRaisesRegex(ValueError, "một chiến lược"):
            with_signal_overrides(single, {"signal_strategies": ["combo"]})
        self.assertEqual(with_signal_overrides(single, {"signal_timeframes": ["h2"]}).signal_timeframes, ("H2",))


class StrategyFileTests(unittest.TestCase):
    SYMBOLS = ("XAUUSD", "US30.cash")   # Redis: GOLD, US30

    @staticmethod
    def _rows(symbol_group: str, timeframe: str, token: str) -> list[dict[str, str]]:
        if token == "combo":
            return [{"bartime": "2026-01-05 06:00", "atr": "12.5", "entry": "4400.5", "signal": "1"},
                    {"bartime": "2026-01-05 07:00", "atr": "12.5", "signal": "2"}]   # dòng thứ 2 thiếu entry -> bị bỏ
        return [{"bartime": "2026-01-05 06:00", "atr": "7.5", "signal": "2", "entry": "999"}]   # entry của Redis KHÔNG được dùng

    def _write(self, frames: dict[str, tuple[str, ...]], dest: Path, symbols=SYMBOLS) -> tuple[int, list[str], list[tuple]]:
        seen: list[tuple] = []

        def fetch(client, symbol_group, timeframe, token, *, key_prefix):
            seen.append((symbol_group, timeframe, token))
            return self._rows(symbol_group, timeframe, token)

        with patch.object(signal_trans, "redis_client", return_value=MagicMock()), \
                patch.object(signal_trans, "fetch_signal_rows", side_effect=fetch):
            written = signal_trans.materialize_strategy_csv(MagicMock(key_prefix="L_PastSignal"), symbols, frames, dest)
        return written, dest.read_text(encoding="utf-8").splitlines(), seen

    def test_file_has_the_seven_columns_blank_entry_for_market_strategies_and_strategy_in_every_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written, lines, seen = self._write({"combo": ("H1", "H2"), "ma_cross": ("M10",)}, Path(tmp) / "f.csv")
        self.assertEqual(lines[0], "bartime,atr,entry,signal,tf,symbol,strategy,risk_percent")
        # combo: 2 symbol x 2 khung x 1 dòng hợp lệ = 4; ma_cross: 2 symbol x 1 khung = 2
        self.assertEqual(written, 6)
        self.assertEqual(len(lines), 7)
        self.assertEqual(len(seen), 6)   # mỗi (chiến lược, symbol, khung) đúng 1 lần đọc Redis
        by_strategy = {}
        for line in lines[1:]:
            cells = line.split(",")
            self.assertEqual(len(cells), 8, line)
            self.assertEqual(cells[7], "0.2")
            by_strategy.setdefault(cells[6], []).append(cells)
        self.assertTrue(all(cells[2] == "4400.5" for cells in by_strategy["combo"]))
        self.assertTrue(all(cells[2] == "" for cells in by_strategy["ma_cross"]), "entry của chiến lược MARKET phải để trống")
        self.assertEqual({(cells[4], cells[5]) for cells in by_strategy["combo"]},
                         {("H1", "XAUUSD"), ("H2", "XAUUSD"), ("H1", "US30.cash"), ("H2", "US30.cash")})

    def test_rows_sort_by_bartime_then_strategy_frame_symbol_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _written, lines, _seen = self._write({"combo": ("H1", "H2"), "ma_cross": ("M10",)}, Path(tmp) / "f.csv")
        keys = [tuple(line.split(",")[i] for i in (6, 4, 5)) for line in lines[1:]]
        self.assertEqual(keys, [("combo", "H1", "XAUUSD"), ("combo", "H1", "US30.cash"), ("combo", "H2", "XAUUSD"),
                                ("combo", "H2", "US30.cash"), ("ma_cross", "M10", "XAUUSD"), ("ma_cross", "M10", "US30.cash")])

    def test_a_channel_without_signals_is_an_error_not_a_silent_gap(self) -> None:
        def fetch(client, symbol_group, timeframe, token, *, key_prefix):
            return [] if (symbol_group, timeframe) == ("US30", "H2") else self._rows(symbol_group, timeframe, token)

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(signal_trans, "redis_client", return_value=MagicMock()), \
                patch.object(signal_trans, "fetch_signal_rows", side_effect=fetch):
            with self.assertRaisesRegex(RuntimeError, "US30.cash.*H2"):
                signal_trans.materialize_strategy_csv(
                    MagicMock(key_prefix="L_PastSignal"), self.SYMBOLS, {"combo": ("H1", "H2")}, Path(tmp) / "f.csv",
                )

    def test_unknown_strategy_token_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(signal_trans, "redis_client", return_value=MagicMock()):
            with self.assertRaisesRegex(ValueError, "nope"):
                signal_trans.materialize_strategy_csv(MagicMock(), self.SYMBOLS, {"nope": ("H1",)}, Path(tmp) / "f.csv")

    def test_redis_channel_follows_the_profile_key_prefix(self) -> None:
        """Đổi kênh gốc (db0) sang kênh trend (db1) chỉ bằng `redis_profile`: key đọc và key nêu trong lỗi theo key_prefix."""
        fake_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(signal_trans, "redis_client", return_value=fake_client),                 patch.object(signal_trans, "fetch_signal_rows", return_value=self._rows("GOLD", "H1", "combo")) as fetch:
            signal_trans.materialize_strategy_csv(
                MagicMock(key_prefix="L_PastSignal_Knn"), ("XAUUSD",), {"combo": ("H1",)}, Path(tmp) / "f.csv",
            )
        fetch.assert_called_once_with(fake_client, "GOLD", "H1", "combo", key_prefix="L_PastSignal_Knn")
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(signal_trans, "redis_client", return_value=MagicMock()),                 patch.object(signal_trans, "fetch_signal_rows", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "L_PastSignal_Knn_GOLD_H1_MA_CROSS"):
                signal_trans.materialize_strategy_csv(
                    MagicMock(key_prefix="L_PastSignal_Knn"), ("XAUUSD",), {"ma_cross": ("H1",)}, Path(tmp) / "f.csv",
                )


class ResolveSignalPathTests(unittest.TestCase):
    SYMBOLS = ("XAUUSD", "US30.cash")

    def _spec(self, symbol: str = "XAUUSD") -> RunSpec:
        return RunSpec("combo", PROFILE_ID, "engine", symbol, "h1", date(2026, 1, 1), date(2026, 9, 30), 100000, "ticks", {})

    def test_one_file_per_batch_named_by_the_selection(self) -> None:
        profile = with_signal_overrides(
            load_strategy_profile(PROFILE_ID),
            {"signal_strategies": ["ma_cross", "combo"], "signal_symbols": list(self.SYMBOLS), "signal_timeframes": ["H1", "M10"]},
        )
        redis_profile = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            materialized: set[Path] = set()
            with patch.object(signal_trans, "materialize_strategy_csv") as write:
                first = signal_trans.resolve_signal_path(redis_profile, profile, self._spec(), Path(tmp), materialized=materialized)
                second = signal_trans.resolve_signal_path(redis_profile, profile, self._spec(), Path(tmp), materialized=materialized)
        self.assertEqual(first, second)
        self.assertTrue(first.name.startswith("MULTI_2sym_combo-ma_cross_"), first.name)
        write.assert_called_once_with(
            redis_profile, self.SYMBOLS, {"combo": ("H1",), "ma_cross": ("M10",)}, first, risk_percent=0.2,
        )

    def test_file_name_depends_on_the_selection(self) -> None:
        names = set()
        for strategies in (["combo"], ["ma_cross"], ["combo", "ma_cross"]):
            profile = with_signal_overrides(load_strategy_profile(PROFILE_ID), {"signal_strategies": strategies})
            with tempfile.TemporaryDirectory() as tmp, patch.object(signal_trans, "materialize_strategy_csv"):
                names.add(signal_trans.resolve_signal_path(MagicMock(), profile, self._spec(), Path(tmp)).name)
        self.assertEqual(len(names), 3)

    def test_unselected_profile_chart_symbol_and_unknown_symbol_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(signal_trans, "materialize_strategy_csv") as write:
            profile_ok = with_signal_overrides(load_strategy_profile(PROFILE_ID), {"signal_strategies": ["combo"]})
            with self.assertRaisesRegex(KeyError, "NOTASYMBOL"):   # symbol biểu đồ không có trong SYMBOL_GROUPS
                signal_trans.resolve_signal_path(MagicMock(), profile_ok, self._spec("NOTASYMBOL"), Path(tmp))
            with self.assertRaisesRegex(ValueError, "chưa chọn chiến lược"):
                signal_trans.resolve_signal_path(MagicMock(), load_strategy_profile(PROFILE_ID), self._spec(), Path(tmp))
            profile = with_signal_overrides(
                load_strategy_profile(PROFILE_ID), {"signal_strategies": ["combo"], "signal_symbols": ["US30.cash"]},
            )
            with self.assertRaisesRegex(ValueError, "chart symbol"):
                signal_trans.resolve_signal_path(MagicMock(), profile, self._spec("XAUUSD"), Path(tmp))
            bad = with_signal_overrides(
                load_strategy_profile(PROFILE_ID), {"signal_strategies": ["combo"], "signal_symbols": ["XAUUSD", "NOPE"]},
            )
            with self.assertRaisesRegex(KeyError, "NOPE"):
                signal_trans.resolve_signal_path(MagicMock(), bad, self._spec(), Path(tmp))
        write.assert_not_called()


class WalkforwardUsesTheSelectedStrategiesTests(unittest.TestCase):
    """walkforward.run_walkforward phải áp `signal_strategies` của cấu hình như run_grid, nếu không profile duy nhất sẽ báo
    "chưa chọn chiến lược" ở chặng học (profile gốc chưa chọn gì)."""

    def test_train_phase_receives_the_profile_with_the_selected_strategies(self) -> None:
        from core_engine.optimize import walkforward

        seen = {}

        def fake_run(config, doc, profile, specs, *, engine, on_progress=None, should_cancel=None):
            seen["profile"] = profile
            return {"total": len(specs), "tally": {}, "cancelled": True, "complete": False, "failed": 0,
                    "inputs": None, "wall_seconds": 0.0}

        config = {
            "experiment": "wf-bb", "strategy": "backtestbot", "strategy_profile": PROFILE_ID,
            "engine_profile": "ctrader-ticks-precisefx-v1", "symbols": ["US30.cash"], "timeframe": ["h1"],
            "start": "2024-01-01", "end": "2026-06-30", "balance": 100000,
            "signal_strategies": ["combo", "macd_break"], "signal_symbols": ["US30.cash"], "signal_timeframes": ["H1"],
            "parameter_space": {"KslLevel": ["Fib1618"]},
        }
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"),                 patch.object(walkforward, "run_experiment", side_effect=fake_run):
            walkforward.run_walkforward(config)
        self.assertEqual(seen["profile"].signal_strategies, ("combo", "macd_break"))
        self.assertEqual(seen["profile"].strategy_frames["macd_break"], ("H1",))


class RunNameTests(unittest.TestCase):
    BASE = {
        "strategy_profile": PROFILE_ID, "engine_profile": "ctrader-ticks-precisefx-v1", "symbols": ["US30.cash"],
        "start": "2026-01-01", "end": "2026-10-10", "balance": 100000, "fixed_params": {"LegCount": 3, "RiskPercent": 1.0},
        "signal_strategies": ["combo"], "signal_symbols": ["US30.cash", "XAUUSD", "JP225.cash", "HK50.cash", "BTCUSD"],
    }

    def test_name_has_the_readable_part_the_user_asked_for(self) -> None:
        name = run_name(self.BASE)
        self.assertRegex(name, r"^cb_S5_T4_L3_20260101_20261010_[0-9a-f]{6}$")

    def test_several_strategies_are_joined_in_catalog_order(self) -> None:
        name = run_name({**self.BASE, "signal_strategies": ["macd_break", "ma_cross", "combo"]})
        self.assertRegex(name, r"^cb-mc-mb_S5_T8_L3_20260101_20261010_[0-9a-f]{6}$")

    def test_order_the_user_lists_symbols_in_does_not_change_the_name(self) -> None:
        reordered = {**self.BASE, "signal_symbols": list(reversed(self.BASE["signal_symbols"]))}
        self.assertEqual(run_name(self.BASE), run_name(reordered))

    def test_anything_that_changes_the_result_changes_the_name(self) -> None:
        names = {run_name(self.BASE)}
        variants = [
            {"signal_symbols": ["US30.cash", "XAUUSD", "JP225.cash", "HK50.cash", "UK100.cash"]},   # cùng S5, khác symbol
            {"signal_timeframes": ["H1", "H2", "H3", "H4"], "signal_strategies": ["combo"]},   # cùng T4? (đã là 4 khung)
            {"fixed_params": {"LegCount": 3, "RiskPercent": 0.2}},
            {"fixed_params": {"LegCount": 3, "RiskPercent": 1.0, "KslLevel": "Fib2618"}},
            {"redis_profile": "sen05-signal-knn-v1"},
            {"balance": 50000},
            {"engine_profile": "ctrader-ticks-other-v1"},
            {"parameter_space": {"KslLevel": ["0", "1"]}},
        ]
        for extra in variants:
            names.add(run_name({**self.BASE, **extra}))
        # biến thể 2 giống hệt BASE về nội dung (đã là 4 khung H1-H4) nên KHÔNG đổi tên; 7 biến thể còn lại đều đổi
        self.assertEqual(len(names), 1 + len(variants) - 1)

    def test_same_readable_part_but_different_symbols_is_told_apart_by_the_hash(self) -> None:
        other = {**self.BASE, "signal_symbols": ["US30.cash", "XAUUSD", "JP225.cash", "HK50.cash", "UK100.cash"]}
        first, second = run_name(self.BASE), run_name(other)
        self.assertEqual(first.rsplit("_", 1)[0], second.rsplit("_", 1)[0])
        self.assertNotEqual(first, second)

    def test_requires_a_multi_strategy_profile_with_a_selection(self) -> None:
        with self.assertRaises(ValueError):
            run_name({k: v for k, v in self.BASE.items() if k != "signal_strategies"})


class RedisKeyForSnapshotTests(unittest.TestCase):
    EXPERIMENT = {"config": {"redis_profile": "sen05-signal-v1"}}

    def test_single_strategy_token_gives_a_key_and_other_names_give_none(self) -> None:
        spec = {"symbol": "US30.cash", "timeframe": "h1", "strategy": "combo"}
        channel, key = run_export._channel_and_key(self.EXPERIMENT, spec)
        self.assertEqual((channel, key), ("original", "L_PastSignal_US30_H1_COMBO"))
        self.assertIsNone(run_export._channel_and_key(self.EXPERIMENT, {**spec, "strategy": "combo+ma_cross"})[1])
        self.assertIsNone(run_export._channel_and_key(self.EXPERIMENT, {**spec, "strategy": "combomulti"})[1])   # tên profile cũ

    def test_label_comes_from_the_profile_not_from_the_key_prefix(self) -> None:
        spec = {"symbol": "US30.cash", "timeframe": "h1", "strategy": "combo"}
        for profile, label, prefix in (("sen05-signal-knn-v1", "knn", "L_PastSignal_Knn"),
                                       ("sen05-signal-breakout-v1", "breakout", "L_PastSignal_Breakout"),
                                       ("sen05-signal-zigzag-v1", "zigzag", "L_PastSignal_Zigzag"),
                                       ("sen05-signal-tstat-v1", "tstat", "L_PastSignal_Tstat")):
            channel, key = run_export._channel_and_key({"config": {"redis_profile": profile}}, spec)
            self.assertEqual((channel, key), (label, f"{prefix}_US30_H1_COMBO"))

    def test_a_channel_source_overrides_the_job_profile_and_different_sources_give_mixed(self) -> None:
        one = {"strategy": "combo", "symbol": "US30.cash", "timeframe": "H1", "risk_percent": 1.0}
        spec = {"symbol": "US30.cash", "timeframe": "h1", "strategy": "combo", "channels": [{**one, "source": "sen05-signal-knn-v1"}]}
        self.assertEqual(run_export._channel_and_key(self.EXPERIMENT, spec), ("knn", "L_PastSignal_Knn_US30_H1_COMBO"))
        mixed = {**spec, "strategy": "combo+macd_break", "channels": [
            one, {**one, "strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "H2", "source": "sen05-signal-knn-v1"}]}
        self.assertEqual(run_export._channel_and_key(self.EXPERIMENT, mixed), ("mixed", None))

    def test_the_retired_trend_profile_keeps_its_old_label_instead_of_failing(self) -> None:
        spec = {"symbol": "US30.cash", "timeframe": "h1", "strategy": "combo"}
        self.assertEqual(run_export._channel_and_key({"config": {"redis_profile": "sen05-signal-trend-v1"}}, spec), ("trend", None))


class ChannelSourceTests(unittest.TestCase):
    """Mỗi channel có thể lấy tín hiệu từ nguồn riêng (db0 RAW / db1 KNN / db2 BRK) trong CÙNG một lượt."""

    RAW, KNN, BRK = "sen05-signal-v1", "sen05-signal-knn-v1", "sen05-signal-breakout-v1"
    CHANNELS = [
        {"strategy": "combo", "symbol": "US30.cash", "timeframe": "H1", "risk_percent": 0.6666},
        {"strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "H2", "risk_percent": 0.6666, "source": "sen05-signal-knn-v1"},
        {"strategy": "ma_cross", "symbol": "XAUUSD", "timeframe": "M45", "risk_percent": 0.6666, "source": "sen05-signal-breakout-v1"},
    ]

    def _channels(self, **extra) -> tuple:
        config = resolve_backtest_config({"channels": self.CHANNELS, "account_mode": "shared", **extra})
        return config, with_signal_overrides(load_strategy_profile(PROFILE_ID), config).signal_channels

    def test_every_channel_gets_its_effective_source_and_only_non_raw_sources_enter_the_canonical(self) -> None:
        _config, channels = self._channels()
        self.assertEqual([c.source for c in channels], [self.RAW, self.KNN, self.BRK])
        self.assertEqual([("source" in c.canonical()) for c in channels], [False, True, True])
        self.assertEqual(channels[1].canonical()["source"], self.KNN)

    def test_single_source_jobs_keep_raw_keys_and_separate_knn_from_breakout(self) -> None:
        base = [{"strategy": "combo", "symbol": "US30.cash", "timeframe": "H1", "risk_percent": 1.0}]
        keys = {}
        for profile in (self.RAW, self.KNN, self.BRK):
            config = resolve_backtest_config({"channels": base, "redis_profile": profile})
            specs = expand_grid(config, with_signal_overrides(load_strategy_profile(PROFILE_ID), config), load_engine_profile(config["engine_profile"]))
            keys[profile] = specs[0].canonical_json()
        self.assertNotIn('"source"', keys[self.RAW])
        self.assertIn(f'"source":"{self.KNN}"', keys[self.KNN])
        self.assertIn(f'"source":"{self.BRK}"', keys[self.BRK])
        self.assertEqual(len(set(keys.values())), 3)

    def test_unknown_source_and_the_same_channel_from_two_sources_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown signal source"):
            self._channels(channels=[{**self.CHANNELS[0], "source": "sen05-signal-trend-v1"}])
        twice = [self.CHANNELS[0], {**self.CHANNELS[0], "source": self.KNN}]
        with self.assertRaisesRegex(ValueError, "duplicate channel"):
            self._channels(channels=twice)

    def test_csv_reads_each_channel_from_its_own_source_and_closes_every_connection(self) -> None:
        _config, channels = self._channels()
        profiles = {
            self.RAW: MagicMock(id=self.RAW, key_prefix="L_PastSignal"),
            self.KNN: MagicMock(id=self.KNN, key_prefix="L_PastSignal_Knn"),
            self.BRK: MagicMock(id=self.BRK, key_prefix="L_PastSignal_Breakout"),
        }
        clients = {pid: MagicMock() for pid in profiles}
        seen: list[tuple] = []

        def fetch(client, group, timeframe, token, *, key_prefix):
            seen.append((key_prefix, group, timeframe, token))
            row = {"bartime": "2026-01-05 06:00", "atr": "5", "signal": "1"}
            return [{**row, "entry": "100"}] if token == "combo" else [row]

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(signal_trans, "load_redis_profile", side_effect=profiles.__getitem__), \
                patch.object(signal_trans, "redis_client", side_effect=lambda profile: clients[profile.id]), \
                patch.object(signal_trans, "fetch_signal_rows", side_effect=fetch):
            written = signal_trans.materialize_channel_csv(profiles[self.RAW], channels, Path(tmp) / "mix.csv")
            lines = (Path(tmp) / "mix.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(written, 3)
        self.assertEqual(seen, [("L_PastSignal", "US30", "H1", "combo"), ("L_PastSignal_Knn", "HK50", "H2", "macd_break"),
                                ("L_PastSignal_Breakout", "GOLD", "M45", "ma_cross")])
        self.assertEqual({line.split(",")[6] for line in lines[1:]}, {"combo", "macd_break", "ma_cross"})
        for client in clients.values():
            client.close.assert_called_once()

    def test_a_missing_list_names_the_source_prefix_in_the_error(self) -> None:
        _config, channels = self._channels()
        profile = MagicMock(id=self.RAW, key_prefix="L_PastSignal")
        knn, brk = MagicMock(id=self.KNN, key_prefix="L_PastSignal_Knn"), MagicMock(id=self.BRK, key_prefix="L_PastSignal_Breakout")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(signal_trans, "load_redis_profile", side_effect={self.KNN: knn, self.BRK: brk}.__getitem__), \
                patch.object(signal_trans, "redis_client", return_value=MagicMock()), \
                patch.object(signal_trans, "fetch_signal_rows", side_effect=lambda c, g, t, s, *, key_prefix: [] if key_prefix.endswith("Knn") else [
                    {"bartime": "2026-01-05 06:00", "atr": "5", "signal": "1", "entry": "100"}]):
            with self.assertRaisesRegex(RuntimeError, "L_PastSignal_Knn_HK50_H2_MACD_BREAK"):
                signal_trans.materialize_channel_csv(profile, channels, Path(tmp) / "mix.csv")


def _metadata(profile) -> dict:
    types = {"enum": "Enum", "float": "Double", "int": "Integer", "bool": "Boolean"}
    rows = [{"PropertyName": "SignalFilePath", "Type": "String", "DefaultValue": ""}]
    for pdef in profile.params.values():
        row = {"PropertyName": pdef.name, "Type": types[pdef.kind], "DefaultValue": pdef.encode(pdef.default)}
        if pdef.kind == "enum":
            row["EnumValues"] = {name: i for i, name in enumerate(pdef.enum_values)}
        rows.append(row)
    return {"Name": profile.cbot_name, "Type": "Robot", "AccessRights": "FullAccess", "Parameters": rows}


def _fake_result(work_dir: Path) -> RunResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "bot.log").write_text("", encoding="utf-8")
    (work_dir / "cli.log").write_text("", encoding="utf-8")
    return RunResult(
        status=RunStatus.OK, failure_code=FailureCode.NONE, reason="", exit_code=0, timed_out=False, wall_seconds=0.1,
        started_utc="2026-01-01T00:00:00+00:00", ended_utc="2026-01-01T00:00:01+00:00", cli_path="fake", cli_version="fake",
        report_json=work_dir / "report.json", cli_log=work_dir / "cli.log", bot_log=work_dir / "bot.log",
        cbotset=work_dir / "params.cbotset",
    )


class SkipKnownInSqlTests(unittest.TestCase):
    """run_experiment THẬT với CLI/Redis/SQL giả: lượt đã có trong kho SQL không tốn lượt CLI khi bật skip_known_in_sql."""

    CSV = "bartime,atr,entry,signal,tf,symbol,strategy\n2026-01-05 10:00,50,100,1,H1,US30.cash,combo\n"

    def _run(self, td: str, *, extra=None, sql_enabled=True, known=None, name="exp"):
        engine = load_engine_profile("ctrader-ticks-precisefx-v1")
        config = {
            "experiment": name, "strategy": "backtestbot", "strategy_profile": PROFILE_ID, "engine_profile": engine.id,
            "symbols": ["US30.cash"], "timeframe": "h1", "start": "2026-01-01", "end": "2026-02-01", "balance": 100000,
            "signal_strategies": ["combo"], "signal_symbols": ["US30.cash"], "signal_timeframes": ["H1"],
            "parameter_space": {"KslLevel": ["0", "1"]}, "max_parallel": 1, **(extra or {}),
        }
        algo = Path(td) / "BacktestBot.algo"
        algo.write_bytes(b"fake algo")
        profile = dataclasses.replace(
            with_signal_overrides(load_strategy_profile(PROFILE_ID), config), algo_path=algo, source_path=None,
        )
        experiment = build_experiment(config)
        specs = expand_grid(config, profile, engine)
        cli = Path(td) / "ctrader-cli.exe"
        cli.write_bytes(b"fake cli")
        launched: list[str] = []

        def resolve(_redis, _profile, _spec, dest_dir, *, materialized=None):
            path = Path(dest_dir) / "MULTI.csv"
            path.write_text(self.CSV, encoding="utf-8")
            return path

        def run_backtest(spec, **kwargs):
            launched.append(spec.label())
            return _fake_result(kwargs["work_dir"])

        find = MagicMock(side_effect=known or (lambda _profile, keys: {}))
        master = MasterDbProfile(enabled=sql_enabled, server="x", database="y")
        with patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(facilitator, "ProtocolStore", MagicMock()), \
                patch.object(facilitator, "load_redis_profile", MagicMock()), \
                patch.object(facilitator, "load_master_db_profile", return_value=master), \
                patch.object(facilitator, "_find_known_runs", find), \
                patch.object(facilitator, "_save_to_master_db", return_value="saved"), \
                patch.object(facilitator.signal_trans, "resolve_signal_path", side_effect=resolve), \
                patch.object(facilitator.cli_runner, "select_cli", return_value=cli), \
                patch.object(facilitator.cli_runner, "read_metadata", return_value=_metadata(profile)), \
                patch.object(facilitator.cli_runner, "validate_capabilities"), \
                patch.object(facilitator.cli_runner, "auth_options", return_value={}), \
                patch.object(facilitator.cli_runner, "child_environment", return_value={}), \
                patch.object(facilitator.cli_runner, "run_backtest", side_effect=run_backtest):
            result = facilitator.run_experiment(config, experiment.canonical(), profile, specs, engine=engine)
            rows = store.ExperimentStore("grid", name).rows()
        return result, rows, launched, find

    def test_default_never_asks_the_database_and_runs_everything(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result, rows, launched, find = self._run(td)
        find.assert_not_called()
        self.assertEqual((result["tally"], len(launched), len(rows)), ({"ok": 2}, 2, 2))
        self.assertNotIn("known_in_sql", result)

    def test_a_run_already_in_sql_is_not_launched_and_is_reported_with_where_to_find_it(self) -> None:
        info = {"backtest_run_id": 77, "batch_name": "cb_S1_T1_L1_20260101_20260201_abcdef", "method": "grid"}
        seen_keys: list[list[str]] = []

        def known(_profile, keys):
            seen_keys.append(list(keys))
            return {keys[0]: info}

        with tempfile.TemporaryDirectory() as td:
            result, rows, launched, _find = self._run(td, extra={"skip_known_in_sql": True}, known=known)
        self.assertEqual(len(seen_keys[0]), 2)
        self.assertEqual(result["tally"], {"ok": 1, facilitator.KNOWN_IN_SQL: 1})
        self.assertEqual(len(launched), 1)
        self.assertEqual(len(rows), 1)   # kho cục bộ của experiment này không có dòng cho lượt đã biết
        self.assertEqual(result["known_in_sql"][0]["batch_name"], info["batch_name"])
        self.assertEqual(result["known_in_sql"][0]["backtest_run_id"], 77)
        self.assertFalse(result["complete"])   # không có kết quả cục bộ -> không được ghi vào khoá pipeline
        self.assertEqual(result["failed"], 0)

    def test_force_runs_again_without_asking_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result, _rows, launched, find = self._run(td, extra={"skip_known_in_sql": True, "force": True})
        find.assert_not_called()
        self.assertEqual((result["tally"], len(launched)), ({"ok": 2}, 2))

    def test_enabled_lookup_needs_the_database_switched_on(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "master_db.enabled"):
                self._run(td, extra={"skip_known_in_sql": True}, sql_enabled=False)

    def test_optimize_boxes_are_not_supported_yet(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "optimize_chunk_size"):
                self._run(td, extra={"skip_known_in_sql": True, "optimize_chunk_size": 2})


if __name__ == "__main__":
    unittest.main()
