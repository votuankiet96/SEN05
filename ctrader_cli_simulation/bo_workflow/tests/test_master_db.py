"""Kho SQL master: phần trích xuất thuần (evidence.trades/events..., models.daily_equity_rows,
cli_runner.find_native_output, store.record chép events.json, run_export.run_payload).
Các test chạm SQL Server nằm ở lớp riêng cuối file và tự bỏ qua nếu không kết nối được."""
from __future__ import annotations

import copy
import gzip
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import cli_runner, signal_trans, store
from core_engine.models import FailureCode, RunResult, RunSpec, RunStatus, daily_equity_rows
from core_engine.output_util import evidence, run_export


def ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def make_report() -> dict:
    """Report tối thiểu có cấu trúc thật của cTrader: 2 lệnh (1 chốt lời, 1 cắt lỗ) và 4 điểm equity."""
    return {
        "main": {
            "netProfit": 120.0, "startingCapital": 1000, "endingBalance": 1120.0, "endingEquity": 1120.0,
            "depositAsset": "USD", "accountType": "hedging", "accountLeverage": 30,
            "testingPeriod": {"startDate": ms(2026, 1, 1), "endDate": ms(2026, 1, 3), "formatted": "x"},
        },
        "equity": {
            "points": [
                {"balance": 1000, "minEquity": 1000, "maxEquity": 1000, "timestamp": ms(2026, 1, 1, 1)},
                {"balance": 1150, "minEquity": 990, "maxEquity": 1160, "timestamp": ms(2026, 1, 1, 12)},
                # 23:30 UTC = 00:30 giờ Prague (mùa đông UTC+1) → thuộc NGÀY SAU theo giờ Prague.
                {"balance": 1150, "minEquity": 1100, "maxEquity": 1150, "timestamp": ms(2026, 1, 1, 23, 30)},
                {"balance": 1120, "minEquity": 1115, "maxEquity": 1150, "timestamp": ms(2026, 1, 2, 12)},
            ],
            "maxEquityDrawdownPercent": 3.0, "maxBalanceDrawdownPercent": 2.0,
        },
        "tradeStatistics": {
            "profitFactor": {"all": 5.0}, "totalTrades": {"all": 2}, "winningTrades": {"all": 1}, "losingTrades": {"all": 1},
        },
        "history": {"items": [
            {"id": 2, "direction": "buy", "entryTime": ms(2026, 1, 1, 2), "closeTime": ms(2026, 1, 1, 12),
             "entryPrice": 100.0, "closePrice": 110.0, "volume": 15.0, "gross": 150.0, "commissions": 0, "swaps": 0,
             "net": 150.0, "balance": 1150.0, "pips": 10.0, "label": "ComboCsvPending_L1", "comment": None},
            {"id": 4, "direction": "sell", "entryTime": ms(2026, 1, 2, 2), "closeTime": ms(2026, 1, 2, 12),
             "entryPrice": 110.0, "closePrice": 112.0, "volume": 15.0, "gross": -30.0, "commissions": 0, "swaps": 0,
             "net": -30.0, "balance": 1120.0, "pips": -2.0, "label": "ComboCsvPending_L1", "comment": None},
        ]},
        "positions": {"items": []},
        "usedSymbols": [{"symbol": "US30.cash", "quoteAsset": "USD", "swapLong": -11.7}],
    }


def make_events() -> list[dict]:
    return [
        {"event": "Create Position", "positionId": 1},
        {"event": "Take Profit Hit", "time": ms(2026, 1, 1, 12), "closePrice": 110.0, "volume": 15.0},
        {"event": "Create Position", "positionId": 2},
        {"event": "Stop Loss Hit", "time": ms(2026, 1, 2, 12), "closePrice": 112.0, "volume": 15.0},
    ]


class EvidenceExportTests(unittest.TestCase):
    def test_trades_take_close_reason_from_events_and_none_without_them(self) -> None:
        with_events = evidence.trades(make_report(), make_events())
        self.assertEqual([row["close_reason"] for row in with_events], ["take_profit", "stop_loss"])
        self.assertEqual(with_events[0]["entry_time_utc"], "2026-01-01T02:00:00.000+00:00")
        self.assertEqual(with_events[0]["net_profit"], 150.0)
        self.assertEqual(with_events[0]["leg_label"], "ComboCsvPending_L1")
        self.assertEqual([row["close_reason"] for row in evidence.trades(make_report())], [None, None])

    def test_events_match_report_counts_created_positions(self) -> None:
        self.assertTrue(evidence.events_match_report(make_events(), make_report()))
        self.assertFalse(evidence.events_match_report(make_events()[:2], make_report()))

    def test_events_match_report_rejects_events_of_another_window_with_the_same_trade_count(self) -> None:
        other_window = make_events()
        other_window[1] = {**other_window[1], "time": ms(2026, 2, 1, 12)}   # cùng số lệnh, khác giờ đóng
        self.assertFalse(evidence.events_match_report(other_window, make_report()))

    def test_symbol_spec_hash_changes_when_swap_changes(self) -> None:
        env = evidence.environment(make_report())
        text, digest = evidence.symbol_spec_and_hash(env)
        other = make_report()
        other["usedSymbols"][0]["swapLong"] = -12.0
        self.assertEqual(json.loads(text)[0]["symbol"], "US30.cash")
        self.assertNotEqual(digest, evidence.symbol_spec_and_hash(evidence.environment(other))[1])

    def test_last_data_time_is_the_last_equity_point(self) -> None:
        self.assertEqual(evidence.last_data_time(make_report()), "2026-01-02T12:00:00.000+00:00")
        self.assertIsNone(evidence.last_data_time({}))

    def test_daily_equity_rows_split_days_by_prague_midnight(self) -> None:
        rows = daily_equity_rows(make_report()["equity"]["points"], "Europe/Prague", 1000.0)
        self.assertEqual([row["trading_day"] for row in rows], ["2026-01-01", "2026-01-02"])
        first, second = rows
        self.assertEqual((first["balance_at_close"], first["lowest_equity"]), (1150.0, 990.0))
        # điểm 23:30 UTC rơi vào ngày Prague thứ 2; balance lúc 00:00 không lưu (view tính bằng LAG).
        self.assertEqual((second["balance_at_close"], second["lowest_equity"]), (1120.0, 1100.0))
        self.assertEqual(set(first), {"trading_day", "balance_at_close", "lowest_equity"})


class FindNativeOutputTests(unittest.TestCase):
    CBOTSET = {"Chart": {"Symbol": "US30.cash", "Period": "h1"}, "Parameters": {"KslLevel": "4", "KtpLevel": "4"}}

    def _setup(self, td: str) -> tuple[Path, Path, Path, datetime, datetime]:
        algo = Path(td) / "bot.algo"
        algo.write_bytes(b"algo-bytes")
        cbotset = Path(td) / "params.cbotset"
        cbotset.write_text(json.dumps(self.CBOTSET, indent=4), encoding="utf-8")
        data_dir = Path(td) / "cBots"
        started = datetime(2026, 10, 14, 4, 38, 14, tzinfo=timezone.utc)
        return algo, cbotset, data_dir, started, started + timedelta(seconds=74)

    def _native(self, data_dir: Path, algo: Path, guid: str, params: dict, mtime: datetime) -> Path:
        import hashlib

        folder = data_dir / hashlib.sha256(algo.read_bytes()).hexdigest() / guid / "Backtesting"
        folder.mkdir(parents=True)
        (folder / "parameters.cbotset").write_text(json.dumps(params, indent=2), encoding="utf-8")
        events = folder / "events.json"
        events.write_text("[]", encoding="utf-8")
        os.utime(events, (mtime.timestamp(), mtime.timestamp()))
        return folder

    def test_matches_the_single_folder_with_same_params_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            algo, cbotset, data_dir, started, ended = self._setup(td)
            wanted = self._native(data_dir, algo, "guid-ok", self.CBOTSET, ended - timedelta(seconds=2))
            self._native(data_dir, algo, "guid-other-params", {**self.CBOTSET, "Parameters": {"KslLevel": "5"}}, ended - timedelta(seconds=2))
            self._native(data_dir, algo, "guid-old", self.CBOTSET, started - timedelta(hours=1))
            self.assertEqual(cli_runner.find_native_output(algo, cbotset, started, ended, data_dir), wanted)

    def test_previous_run_with_the_same_params_that_just_finished_is_not_mistaken_for_this_run(self) -> None:
        """Lỗi thật 2026-10-14: lượt trước cùng KSL/KTP (khác cửa sổ ngày) xong <5 s trước khi lượt này bắt đầu → 2 ứng viên → mất events."""
        with tempfile.TemporaryDirectory() as td:
            algo, cbotset, data_dir, started, ended = self._setup(td)
            self._native(data_dir, algo, "guid-previous-window", self.CBOTSET, started - timedelta(seconds=1))
            wanted = self._native(data_dir, algo, "guid-this-run", self.CBOTSET, ended - timedelta(seconds=2))
            self.assertEqual(cli_runner.find_native_output(algo, cbotset, started, ended, data_dir), wanted)

    def test_accept_breaks_the_tie_between_concurrent_runs_with_the_same_params(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            algo, cbotset, data_dir, started, ended = self._setup(td)
            a = self._native(data_dir, algo, "guid-a", self.CBOTSET, ended - timedelta(seconds=1))
            self._native(data_dir, algo, "guid-b", self.CBOTSET, ended - timedelta(seconds=3))
            find = lambda accept: cli_runner.find_native_output(algo, cbotset, started, ended, data_dir, accept=accept)  # noqa: E731
            self.assertEqual(find(lambda events: events.parent.parent.name == "guid-a"), a)
            self.assertIsNone(find(lambda events: True))     # vẫn nhập nhằng → không đoán
            self.assertIsNone(find(lambda events: False))    # không ứng viên nào đúng → không nhận bừa

    def test_returns_none_when_nothing_or_more_than_one_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            algo, cbotset, data_dir, started, ended = self._setup(td)
            self.assertIsNone(cli_runner.find_native_output(algo, cbotset, started, ended, data_dir))
            self.assertIsNone(cli_runner.find_native_output(algo, cbotset, started, ended, None))
            self._native(data_dir, algo, "guid-a", self.CBOTSET, ended - timedelta(seconds=1))
            self._native(data_dir, algo, "guid-b", self.CBOTSET, ended - timedelta(seconds=3))
            self.assertIsNone(cli_runner.find_native_output(algo, cbotset, started, ended, data_dir))


class StoreRecordsEventsTests(unittest.TestCase):
    SPEC = RunSpec("combo", "backtestbot-v1", "engine", "US30.cash", "h1", date(2026, 1, 1), date(2026, 1, 2), 1000, "ticks", {})

    def _record(self, st: "store.ExperimentStore", tmp: Path, **extra) -> Path:
        param_hash = st.param_hash(self.SPEC, "x")
        claim = st.claim(self.SPEC, param_hash)
        report = tmp / "report.json"
        report.write_text(json.dumps(make_report()), encoding="utf-8")
        result = RunResult(
            status=RunStatus.OK, failure_code=FailureCode.NONE, reason="", exit_code=0, timed_out=False, wall_seconds=1.0,
            started_utc="2026-01-01T00:00:00+00:00", ended_utc="2026-01-01T00:00:01+00:00", cli_path="fake", cli_version="fake",
            report_json=report, cli_log=report, bot_log=report, cbotset=report, **extra,
        )
        st.record(
            claim=claim, spec=self.SPEC, param_hash=param_hash, run_result=result, metrics={}, flags={"period_ok": True, "signal_ok": True, "margin_rejections": 0},
            bot_summary={}, environment={}, inputs={"algo_sha256": "a" * 64, "signal_sha256": "s" * 64, "source_sha256": "c" * 64, "source_newer_than_algo": False},
        )
        return st.run_dir(claim.run_id, self.SPEC.label())

    def test_events_are_gzipped_into_the_run_dir_and_verified_against_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            st = store.ExperimentStore("grid", "exp")
            st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
            events = Path(td) / "events.json"
            events.write_text(json.dumps(make_events()), encoding="utf-8")
            run_dir = self._record(st, Path(td), events_json=events, native_dir=r"C:\cBots\x\Backtesting")
            self.assertEqual(json.loads(gzip.open(run_dir / "events.json.gz", "rt", encoding="utf-8").read()), make_events())
            execution = json.loads((run_dir / "execution.json").read_text(encoding="utf-8"))
            self.assertEqual(execution["native"]["directory"], r"C:\cBots\x\Backtesting")
            self.assertTrue(execution["native"]["events_verified"])
            self.assertEqual(len(execution["native"]["events_sha256"]), 64)
            self.assertEqual(execution["inputs"]["source_sha256"], "c" * 64)
            self.assertFalse(execution["inputs"]["source_newer_than_algo"])

    def test_without_events_the_run_is_still_recorded_with_empty_native_info(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            st = store.ExperimentStore("grid", "exp")
            st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
            run_dir = self._record(st, Path(td))
            self.assertFalse((run_dir / "events.json.gz").exists())
            native = json.loads((run_dir / "execution.json").read_text(encoding="utf-8"))["native"]
            self.assertEqual(native, {"directory": None, "events_sha256": None, "events_verified": None})


def fake_algo_bytes(manifest: dict) -> bytes:
    body = gzip.compress(json.dumps(manifest).encode("utf-8"), mtime=0)   # mtime cố định: gzip mặc định ghi giờ hiện tại → hash .algo giả đổi theo giây
    return b"algo" + b"\x02" + len(body).to_bytes(4, "big") + body


MANIFEST = {
    "Timestamp": "2026-10-13T11:08:54.8903457Z", "Store": {"SourceIncluded": False},   # 7 chữ số thập phân như manifest MACD Break
    "Types": [{"Parameters": [
        {"PropertyName": "KslLevel", "EnumValues": {"Fib1000": 2, "Fib1618": 4}},
        {"PropertyName": "KtpLevel", "EnumValues": {"Fib2058": 3, "Fib2618": 4}},
        {"PropertyName": "RiskPercent"},
    ]}],
}
CSV = "bartime,atr,entry,signal\n2026-01-01 10:00:00,1.0,100.0,1\n2026-01-02 10:00:00,1.0,101.0,2\n2026-01-03 10:00:00,1.0,102.0,1\n"
SOURCE = b"class Combo {\r\n}"   # file .cs mau co CRLF: ban luu phai giu nguyen tung byte


def build_experiment(
    td: str, *, with_source: bool = True, param_hash: str = "h" * 64, run_id: str = "0001",
    window: str = "single", zone: str = "single", tags: dict | None = None, root: Path | None = None, method: str = "grid",
) -> tuple[Path, Path]:
    """Dựng 1 experiment tối thiểu trên đĩa (hoặc thêm 1 lượt vào experiment có sẵn qua `root`)."""
    import hashlib

    root = root or Path(td) / "exp"
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    algo = fake_algo_bytes(MANIFEST)
    algo_sha = hashlib.sha256(algo).hexdigest()
    (inputs / f"{algo_sha}.algo").write_bytes(algo)
    csv_sha = hashlib.sha256(CSV.encode()).hexdigest()
    (inputs / f"{csv_sha}.csv").write_bytes(CSV.encode("utf-8"))   # bytes: write_text đổi \n thành \r\n trên Windows
    source_sha = None
    if with_source:
        source_sha = "d" * 64
        (inputs / f"{source_sha}.cs").write_bytes(SOURCE)   # CRLF: bản lưu phải giữ nguyên từng byte
    if not (root / "experiment.json").exists():
        (root / "experiment.json").write_text(
            json.dumps({"name": "exp", "method": method, "config": {"redis_profile": "sen05-signal-knn-v1"}}), encoding="utf-8")
    run_dir = root / "runs" / f"{run_id}__combo"
    run_dir.mkdir(parents=True)
    spec = {
        "strategy": "combo", "strategy_profile": "backtestbot-v1", "engine_profile": "engine", "symbol": "US30.cash", "timeframe": "h1",
        "start": "2026-01-01", "end": "2026-01-02", "balance": 1000.0, "data_mode": "ticks", "precise_conversion": False,
        "commission": None, "commission_type": None, "commission_auto": False, "spread": None, "zone": zone, "window": window,
        "params": {"KslLevel": "4", "KtpLevel": "4", "RiskPercent": "1", "LegCount": "1"}, "tags": tags or {},
    }
    execution = {
        "run_id": run_id, "param_hash": param_hash, "spec": spec, "status": "ok", "failure_code": "", "reason": "",
        "timing": {"started_utc": "2026-10-14T04:38:14+00:00", "ended_utc": "2026-10-14T04:39:28+00:00", "wall_seconds": 73.7},
        "inputs": {"algo_sha256": algo_sha, "signal_sha256": csv_sha, "source_sha256": source_sha},
        "native": {"directory": r"C:\cBots\x\Backtesting", "events_sha256": "e" * 64, "events_verified": True},
        "cli": {"version": "5.10.19"}, "bot_summary": {"placed": 2},
        "validity_flags": {"period_ok": True, "signal_ok": True, "approx_fx": False, "margin_rejections": 0},
    }
    (run_dir / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
    with gzip.open(run_dir / "report.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(make_report(), handle)
    with gzip.open(run_dir / "events.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(make_events(), handle)
    return root, run_dir


def walkforward_experiment(td: str) -> Path:
    """1 lượt học (wf01_is) + 2 lượt kiểm (wf01_oos) do 2 luật chọn ra, kèm selection.json."""
    root, _ = build_experiment(td, param_hash="1" * 64, run_id="0001", window="wf01_is", zone="train", method="walkforward")
    build_experiment(td, root=root, param_hash="2" * 64, run_id="0002", window="wf01_oos", zone="oos", tags={"rule": "plateau"})
    build_experiment(td, root=root, param_hash="3" * 64, run_id="0003", window="wf01_oos", zone="oos", tags={"rule": "best"})
    decision = {
        "candidates": 100, "eligible": 94, "symbol": "US30.cash", "timeframe": "h1", "test_window": "wf01_oos",
        "train_window": "wf01_is", "train_run_id": "0001", "selected_params": {"KslLevel": "4", "KtpLevel": "4"},
    }
    (root / "selection.json").write_text(json.dumps({"objective": "net_profit", "decisions": [
        {**decision, "rule": "plateau", "plateau_score": 12.5}, {**decision, "rule": "best", "plateau_score": None},
    ]}), encoding="utf-8")
    return root


class RunPayloadTests(unittest.TestCase):
    def test_payload_carries_versions_parameters_trades_and_daily_equity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            run, bot, snapshot = payload["run"], payload["bot_build"], payload["signal_snapshot"]
            self.assertEqual(run["run_key"], "h" * 64)
            self.assertEqual((run["ksl_atr_multiple"], run["ktp_atr_multiple"]), (1.618, 2.618))   # từ EnumValues của manifest
            self.assertEqual(json.loads(run["parameters_json"])["KslLevel"], "Fib1618")
            self.assertEqual((run["net_profit"], run["total_trades"], run["leg_count"]), (120.0, 2, 1))
            self.assertEqual(run["last_data_time"], "2026-01-02T12:00:00.000+00:00")
            self.assertNotIn("native_directory", run)
            self.assertEqual(run["signal_window_sha256"], signal_trans.signal_window_sha256(
                next((root / "inputs").glob("*.csv")), date(2026, 1, 2)))
            self.assertEqual(bot["source_code"], SOURCE.decode("utf-8"))
            self.assertEqual(bot["build_time_utc"], "2026-10-13T11:08:54.8903457Z")
            self.assertEqual(snapshot["channel"], "knn")
            self.assertEqual(snapshot["redis_key"], "L_PastSignal_Knn_US30_H1_COMBO")
            self.assertEqual(snapshot["last_bar_time"], "2026-01-03 10:00:00")
            self.assertEqual(gzip.decompress(snapshot["rows_compressed"]).decode("utf-8"), CSV)
            self.assertEqual([row["close_reason"] for row in payload["trades"]], ["take_profit", "stop_loss"])
            self.assertEqual(round(sum(row["net_profit"] for row in payload["trades"]), 2), run["net_profit"])
            self.assertEqual(len(payload["daily_equity"]), 2)

    def test_payload_without_source_or_report_still_builds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td, with_source=False)
            (run_dir / "report.json.gz").unlink()
            (run_dir / "events.json.gz").unlink()
            payload = run_export.run_payload(root, run_dir)
            self.assertIsNone(payload["bot_build"]["source_code"])
            self.assertIsNone(payload["run"]["net_profit"])
            self.assertEqual((payload["trades"], payload["daily_equity"]), ([], []))

    def test_run_payload_labels_the_method_and_the_selection_of_walkforward_test_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = walkforward_experiment(td)
            keys = ("method", "selection_rule", "selection_objective", "train_run_key")
            train, plateau, best = [
                {key: run_export.run_payload(root, run_dir)["run"][key] for key in keys} for run_dir in sorted((root / "runs").iterdir())
            ]
            self.assertEqual(train, {"method": "walkforward", "selection_rule": None, "selection_objective": None, "train_run_key": None})
            self.assertEqual(plateau, {"method": "walkforward", "selection_rule": "plateau", "selection_objective": "net_profit", "train_run_key": "1" * 64})
            self.assertEqual((best["selection_rule"], best["train_run_key"]), ("best", "1" * 64))

    def test_run_payload_selection_labels_degrade_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = walkforward_experiment(td)
            test_dir = next(p for p in sorted((root / "runs").iterdir()) if p.name.startswith("0002"))
            selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
            for decision in selection["decisions"]:
                decision["train_run_id"] = "9999"   # lượt học không còn trong kho file
            (root / "selection.json").write_text(json.dumps(selection), encoding="utf-8")
            run = run_export.run_payload(root, test_dir)["run"]
            self.assertEqual((run["selection_rule"], run["selection_objective"], run["train_run_key"]), ("plateau", "net_profit", None))
            (root / "selection.json").unlink()
            run = run_export.run_payload(root, test_dir)["run"]
            self.assertEqual((run["selection_rule"], run["selection_objective"], run["train_run_key"]), (None, None, None))


class MasterDbPureTests(unittest.TestCase):
    def test_datetime_parsing_handles_manifest_and_report_formats(self) -> None:
        from core_engine import master_db

        self.assertEqual(master_db._datetime("2026-10-13T11:08:54.8903457Z"), datetime(2026, 10, 13, 11, 8, 54, 890345))
        self.assertEqual(master_db._datetime("2026-01-06T15:02:28.904+00:00"), datetime(2026, 1, 6, 15, 2, 28, 904000))
        self.assertEqual(master_db._datetime("2026-01-06T17:02:28+02:00"), datetime(2026, 1, 6, 15, 2, 28))
        self.assertEqual(master_db._datetime("2022-01-03 13:00:00"), datetime(2022, 1, 3, 13, 0))
        self.assertIsNone(master_db._datetime(None))

    def test_convert_makes_sql_safe_values(self) -> None:
        from core_engine import master_db

        self.assertIsNone(master_db._convert("margin_rejections", "unknown"))
        self.assertIsNone(master_db._convert("profit_factor", float("inf")))
        self.assertEqual(master_db._convert("win_rate", "0.41"), 0.41)
        self.assertEqual(master_db._convert("start_date", "2026-01-01"), date(2026, 1, 1))


class MasterDbTests(unittest.TestCase):
    """Chạm SQL Server thật nhưng CHỈ database thử `bo_research_test` (tạo mới đầu lớp, xoá cuối lớp);
    tự bỏ qua nếu không có pyodbc/driver/SQL Server."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from core_engine import master_db
            from core_engine.configuration import load_master_db_profile

            cls.master_db = master_db
            cls.profile = master_db.with_database(load_master_db_profile(), "bo_research_test")
            master_db.drop_database(cls.profile)
            master_db.ensure_database(cls.profile)
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"SQL Server không dùng được: {exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.master_db.drop_database(cls.profile)

    def setUp(self) -> None:
        self.conn = self.master_db.connect(self.profile)
        self.master_db.ensure_schema(self.conn)
        for table in ("trade", "daily_equity", "backtest_run", "signal_snapshot", "bot_build"):
            self.conn.execute(f"DELETE FROM dbo.{table}")
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def _count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM dbo.{table}").fetchval()

    def test_schema_is_idempotent_and_has_the_five_tables(self) -> None:
        self.master_db.ensure_schema(self.conn)
        names = {row[0] for row in self.conn.execute("SELECT name FROM sys.tables").fetchall()}
        self.assertEqual(names, {"bot_build", "signal_snapshot", "backtest_run", "trade", "daily_equity"})

    def test_save_run_writes_everything_once_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            run_id, created = self.master_db.save_run(self.conn, payload)
            self.assertTrue(created)
            self.assertEqual(self.master_db.find_run(self.conn, "h" * 64), run_id)
            self.assertEqual((self._count("backtest_run"), self._count("trade"), self._count("daily_equity")), (1, 2, 2))
            self.assertEqual(self._count("bot_build"), 1)
            self.assertEqual(self._count("signal_snapshot"), 1)
            row = self.conn.execute("SELECT ksl_atr_multiple, net_profit, last_data_time, margin_rejections FROM dbo.backtest_run").fetchone()
            self.assertEqual((float(row[0]), float(row[1]), row[3]), (1.618, 120.0, 0))
            again_id, again_created = self.master_db.save_run(self.conn, payload)
            self.assertEqual((again_id, again_created), (run_id, False))
            self.assertEqual((self._count("backtest_run"), self._count("trade")), (1, 2))

    def test_same_bot_and_signal_are_shared_between_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, first = build_experiment(td, param_hash="1" * 64, run_id="0001")
            _, second = build_experiment(td, root=root, param_hash="2" * 64, run_id="0002")
            for run_dir in (first, second):
                self.master_db.save_run(self.conn, run_export.run_payload(root, run_dir))
            self.assertEqual((self._count("backtest_run"), self._count("bot_build"), self._count("signal_snapshot")), (2, 1, 1))

    def test_parallel_runs_sharing_a_new_bot_and_snapshot_are_all_saved(self) -> None:
        """Lỗi thật 2026-10-14: 8 lượt walk-forward chạy song song cùng ghi bản chụp tín hiệu MỚI, 1 lượt bị mất khỏi SQL
        (SELECT-rồi-INSERT không khoá → vi phạm UNIQUE). Mỗi vòng dùng bot + bản chụp khác để cuộc đua xảy ra lại."""
        import threading

        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            base = run_export.run_payload(root, run_dir)
        workers, rounds, errors = 8, 4, []
        for round_no in range(rounds):
            payloads = []
            for index in range(workers):
                payload = copy.deepcopy(base)
                payload["run"]["run_key"] = f"{round_no}{index}".ljust(64, "a")
                payload["signal_snapshot"]["content_sha256"] = f"{round_no}".ljust(64, "b")
                payload["bot_build"]["algo_sha256"] = f"{round_no}".ljust(64, "c")
                payloads.append(payload)
            barrier = threading.Barrier(workers)

            def work(payload: dict) -> None:
                conn = self.master_db.connect(self.profile)
                try:
                    barrier.wait()
                    self.master_db.save_run(conn, payload)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                finally:
                    conn.close()

            threads = [threading.Thread(target=work, args=(payload,)) for payload in payloads]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual([self._count(t) for t in ("backtest_run", "signal_snapshot", "bot_build")], [workers * rounds, rounds, rounds])

    def test_failed_save_rolls_back_everything(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            payload["run"]["parameters_json"] = "not json"   # vi phạm CHECK ISJSON sau khi bot_build/signal_snapshot đã được thêm
            with self.assertRaises(Exception):
                self.master_db.save_run(self.conn, payload)
            self.assertEqual([self._count(t) for t in ("backtest_run", "bot_build", "signal_snapshot", "trade")], [0, 0, 0, 0])

    def test_source_code_is_filled_in_later_but_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, first = build_experiment(td, with_source=False, param_hash="1" * 64, run_id="0001")
            self.master_db.save_run(self.conn, run_export.run_payload(root, first))
            self.assertIsNone(self.conn.execute("SELECT source_sha256 FROM dbo.bot_build").fetchval())
            # cùng bản build, lượt sau có chụp .cs → bổ sung chỗ trống
            _, second = build_experiment(td, root=root, with_source=True, param_hash="2" * 64, run_id="0002")
            self.master_db.save_run(self.conn, run_export.run_payload(root, second))
            self.assertEqual(self.conn.execute("SELECT source_sha256 FROM dbo.bot_build").fetchval().strip(), "d" * 64)
            self.assertEqual(self.conn.execute("SELECT source_code FROM dbo.bot_build").fetchval(), SOURCE.decode("utf-8"))
            # lượt thứ ba KHÔNG có .cs không được xoá mã nguồn đã có
            _, third = build_experiment(td, root=root, with_source=False, param_hash="3" * 64, run_id="0003")
            self.master_db.save_run(self.conn, run_export.run_payload(root, third))
            self.assertEqual(self.conn.execute("SELECT source_code FROM dbo.bot_build").fetchval(), SOURCE.decode("utf-8"))

    def test_views_sum_half_years_to_net_profit_and_flag_which_version_differs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            first_id, _ = self.master_db.save_run(self.conn, payload)
            # cùng ô, cùng bot, cùng tín hiệu; chỉ khác bản CLI → versions_differing = 1 và là cli_differs
            other = copy.deepcopy(payload)
            other["run"]["run_key"] = "x" * 64
            other["run"]["cli_version"] = "5.10.20"
            second_id, _ = self.master_db.save_run(self.conn, other)
            half = self.conn.execute(
                "SELECT SUM(net_profit), SUM(trade_count) FROM dbo.v_cell_half_year_profit WHERE backtest_run_id = ?", first_id).fetchone()
            self.assertEqual((float(half[0]), half[1]), (120.0, 2))
            pair = self.conn.execute(
                "SELECT run_a, run_b, versions_differing, cli_differs, bot_differs, signal_differs, net_profit_change "
                "FROM dbo.v_compare_same_cell").fetchone()
            self.assertEqual((pair[0], pair[1], pair[2], pair[3], pair[4], pair[5]), (first_id, second_id, 1, 1, 0, 0))
            self.assertEqual(float(pair[6]), 0.0)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dbo.v_run_overview").fetchval(), 2)
            ftmo = self.conn.execute(
                "SELECT COUNT(*), MAX(day_drop_percent_of_initial) FROM dbo.v_ftmo_daily_loss WHERE backtest_run_id = ?", first_id).fetchone()
            self.assertEqual(ftmo[0], 2)   # 2 ngày giờ Prague trong report mẫu
            # ngày 1: 1000 → đáy 990 (1%); ngày 2 mở 1150 → đáy 1100: sụt 50 trên vốn gốc 1000 = 5% (lớn nhất)
            self.assertAlmostEqual(float(ftmo[1]), 5.0, places=3)

    def test_durable_cells_view_ignores_walkforward_windows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            self.master_db.save_run(self.conn, payload)
            for index, zone in enumerate(("train", "oos")):
                window = copy.deepcopy(payload)
                window["run"].update(run_key=f"{index}" * 64, zone=zone, window_name=f"wf01_{zone}")
                self.master_db.save_run(self.conn, window)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dbo.v_run_overview").fetchval(), 3)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dbo.v_durable_cells").fetchval(), 1)

    def test_only_completed_runs_enter_the_database(self) -> None:
        """Người dùng chốt 2026-10-14: lượt lỗi / huỷ / treo / đang chạy dở KHÔNG vào kho — không để lại dòng nào, kể cả bot_build."""
        with tempfile.TemporaryDirectory() as td:
            root, run_dir = build_experiment(td)
            payload = run_export.run_payload(root, run_dir)
            for index, status in enumerate(("cancelled", "cli_error", "timeout", "stalled", "no_report", "period_mismatch", "running")):
                unfinished = copy.deepcopy(payload)
                unfinished["run"]["status"] = status
                unfinished["run"]["run_key"] = f"{index}" * 64
                self.assertEqual(self.master_db.save_run(self.conn, unfinished), (None, False), status)
            self.assertEqual([self._count(t) for t in ("backtest_run", "bot_build", "signal_snapshot", "trade", "daily_equity")], [0] * 5)
            self.assertEqual(self.master_db.save_run(self.conn, payload)[1], True)   # lượt hoàn tất vẫn vào bình thường

    def test_import_experiment_skips_unfinished_runs_and_reports_them(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, _ = build_experiment(td, param_hash="1" * 64, run_id="0001")
            _, broken = build_experiment(td, root=root, param_hash="2" * 64, run_id="0002")
            execution = json.loads((broken / "execution.json").read_text(encoding="utf-8"))
            execution["status"] = "cancelled"
            (broken / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
            result = self.master_db.import_experiment(self.conn, root)
            self.assertEqual((result["saved"], result["not_completed"], result["errors"]), (1, 1, []))
            self.assertEqual(self._count("backtest_run"), 1)

    def test_import_experiment_is_idempotent_and_links_test_runs_to_their_train_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = walkforward_experiment(td)
            first = self.master_db.import_experiment(self.conn, root)
            self.assertEqual((first["saved"], first["already_present"], first["errors"]), (3, 0, []))
            second = self.master_db.import_experiment(self.conn, root)
            self.assertEqual((second["saved"], second["already_present"]), (0, 3))
            rows = self.conn.execute(
                "SELECT te.selection_rule, te.selection_objective, te.method, tr.window_name, te.window_name, tr.ksl_atr_multiple "
                "FROM dbo.backtest_run te JOIN dbo.backtest_run tr ON tr.backtest_run_id = te.train_run_id ORDER BY te.selection_rule").fetchall()
            self.assertEqual([(r[0], r[1], r[2], r[3], r[4], float(r[5])) for r in rows],
                             [("best", "net_profit", "walkforward", "wf01_is", "wf01_oos", 1.618),
                              ("plateau", "net_profit", "walkforward", "wf01_is", "wf01_oos", 1.618)])
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dbo.backtest_run WHERE train_run_id IS NULL").fetchval(), 1)   # chỉ lượt học

    def test_walkforward_views_are_computed_from_the_labels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.master_db.import_experiment(self.conn, walkforward_experiment(td))
            selected = self.conn.execute(
                "SELECT selection_rule, window_number, candidate_count, train_net_profit, test_net_profit, train_trades, test_trades "
                "FROM dbo.v_walkforward_selected ORDER BY selection_rule").fetchall()
            self.assertEqual([(r[0], r[1], r[2], float(r[3]), float(r[4]), r[5], r[6]) for r in selected],
                             [("best", 1, 1, 120.0, 120.0, 2, 2), ("plateau", 1, 1, 120.0, 120.0, 2, 2)])
            chain = self.conn.execute(
                "SELECT selection_rule, COUNT(*), MAX(cumulative_net_profit) FROM dbo.v_walkforward_oos_chain GROUP BY selection_rule ORDER BY 1").fetchall()
            # lệnh mẫu: +150 rồi −30 → luỹ kế đạt đỉnh 150, kết thúc 120
            self.assertEqual([(r[0], r[1], float(r[2])) for r in chain], [("best", 2, 150.0), ("plateau", 2, 150.0)])
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM dbo.v_durable_cells").fetchval(), 0)   # cửa sổ walk-forward không vào


if __name__ == "__main__":
    unittest.main()
