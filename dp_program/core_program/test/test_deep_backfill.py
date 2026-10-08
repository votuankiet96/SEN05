from __future__ import annotations

import ast
import json
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import websocket
import yaml

from dp_program import configuration
from dp_program.engine.pipeline import CandleValidationError
from dp_program.engine.sql_connector import candle_signature
from dp_program.engine.websocket import IncompleteFetchError, ProviderRequestError, split_messages
from dp_program.util import deep_backfill

UTC = timezone.utc
FIXED_NOW = datetime(2026, 10, 7, 4, 3, 20, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(deep_backfill, "_PAUSE_SECONDS", 0.0)


def _symbol(name: str = "GOLD", symbol_id: int = 56) -> dict:
    return {"symbol_id": symbol_id, "exchange": "CAPITALCOM", "symbol": name, "asset_type": "Metal", "enabled": True}


def _timeframe(code: str = "M5", minutes: int = 5) -> dict:
    return {"code": code, "interval": str(minutes), "minutes": minutes, "staging_table": f"SEN.TF_{code}"}


def _candle(stamp: datetime, close: str = "101", symbol: str = "GOLD") -> dict:
    return {
        "symbol_id": 56, "exchange": "CAPITALCOM", "symbol": symbol, "timeframe": "M5", "timestamp": stamp,
        "open": Decimal("100"), "high": Decimal("102"), "low": Decimal("99"), "close": Decimal(close),
        "volume": Decimal("12.5"),
    }


def _config(tmp_path: Path, **deep) -> dict:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"]["retry_count"] = 1
    config["deep_backfill"].update(
        enabled=True, symbols=["GOLD"], timeframes=[], start_utc=datetime(2026, 1, 1, tzinfo=UTC), **deep
    )
    return config


def _packet(message: dict) -> str:
    payload = json.dumps(message, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"


class _Socket:
    def __init__(self, chunks: list[str]) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._chunks = list(chunks)

    def settimeout(self, _seconds) -> None:
        pass

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self) -> str:
        if self._chunks:
            return self._chunks.pop(0)
        raise websocket.WebSocketTimeoutException()

    def close(self) -> None:
        self.closed = True


def _tv() -> dict:
    return {"websocket_url": "wss://example.invalid/socket", "auth_token": "token", "cookie": "", "timezone": "Etc/UTC"}


def _install_socket(monkeypatch, socket: _Socket) -> None:
    monkeypatch.setattr(deep_backfill, "_session_id", lambda prefix: {"cs": "cs_t", "rs": "rs_t"}[prefix])
    monkeypatch.setattr(deep_backfill.websocket, "create_connection", lambda *_a, **_k: socket)


def _source() -> dict:
    return {
        "app": {"log_level": "INFO", "runtime_dir": "runtime"},
        "discord": {"enabled": False, "webhook_url": ""},
        "redis": {"enabled": True, "host": "localhost"},
        "tradingview": {"auth_token": "", "cookie": "", "username": "", "password": "", "two_factor_secret": ""},
        "backfill": {"enabled": True, "lookback_days": 60, "run_on_start": True, "schedule_utc": ["03:03"]},
        "live": {
            "enabled": True, "interval_minutes": 5, "bars_per_request": 3, "closed_candles_only": True,
            "symbols": ["GOLD"], "timeframes": ["M5"],
        },
        "service": {},
        "sql_server": {
            "server": "localhost", "database": "SEN05_AutoTrading", "port": "", "username": "", "password": "",
            "trusted_connection": True, "encrypt": "no", "trust_server_certificate": True,
        },
    }


def _load(tmp_path: Path, source: dict) -> dict:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    return configuration.load_config(path)


def test_config_keeps_deep_backfill_off_by_default_and_validates_it(tmp_path: Path) -> None:
    source = _source()
    deep = _load(tmp_path, source)["deep_backfill"]
    assert deep == {"enabled": False, "symbols": [], "timeframes": [], "start_utc": datetime(2022, 1, 1, tzinfo=UTC)}

    source["deep_backfill"] = {"enabled": True}
    with pytest.raises(configuration.ConfigError, match="deep_backfill.symbols"):
        _load(tmp_path, source)

    source["deep_backfill"] = {"enabled": "yes", "symbols": ["gold", " btcusd"], "timeframes": ["m5"],
                               "start_utc": "2022-03-05"}
    deep = _load(tmp_path, source)["deep_backfill"]
    assert (deep["enabled"], deep["symbols"], deep["timeframes"]) == (True, ["GOLD", "BTCUSD"], ["M5"])
    assert deep["start_utc"] == datetime(2022, 3, 5, tzinfo=UTC)

    # YAML đọc ngày không có nháy thành kiểu date: vẫn phải ra đúng 00:00 UTC.
    path = tmp_path / "config.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("start_utc: '2022-03-05'", "start_utc: 2022-03-05"),
                    encoding="utf-8")
    assert configuration.load_config(path)["deep_backfill"]["start_utc"] == datetime(2022, 3, 5, tzinfo=UTC)

    source["deep_backfill"] = {"start_utc": "not-a-date"}
    with pytest.raises(configuration.ConfigError, match="deep_backfill.start_utc"):
        _load(tmp_path, source)
    source["deep_backfill"] = {"symbols": ["GOLD", "GOLD"]}
    with pytest.raises(configuration.ConfigError, match="duplicate"):
        _load(tmp_path, source)


def test_replay_window_speaks_the_replay_protocol_and_returns_sorted_candles(monkeypatch) -> None:
    anchor = datetime(2022, 1, 1, tzinfo=UTC)
    bars = [{"v": [anchor.timestamp() - 600 + 300 * k, 100 + k, 102, 99, 101, 5]} for k in (1, 0, 2)]
    socket = _Socket([
        "~h~7",
        _packet({"m": "timescale_update", "p": ["cs_t", {"$prices": {"s": bars}}]}),
        _packet({"m": "series_completed", "p": ["cs_t", "s1"]}),
    ])
    _install_socket(monkeypatch, socket)

    candles = deep_backfill._replay_window(_tv(), _symbol(), _timeframe(), anchor, 3)

    messages = [json.loads(split_messages(raw)[0]) for raw in socket.sent if not raw.startswith("~m~4~m~~h~")]
    assert [m["m"] for m in messages] == [
        "set_auth_token", "chart_create_session", "switch_timezone", "replay_create_session",
        "replay_add_series", "replay_reset", "resolve_symbol", "create_series",
    ]
    assert messages[5]["p"] == ["rs_t", "req_reset", int(anchor.timestamp())]
    assert '"replay":"rs_t"' in messages[6]["p"][2] and messages[6]["p"][2].startswith("=")
    assert messages[7]["p"] == ["cs_t", "$prices", "s1", "ser_1", "5", 3]
    assert "~m~4~m~~h~7" in socket.sent  # heartbeat được echo lại
    assert [c["timestamp"] for c in candles] == sorted(c["timestamp"] for c in candles) and len(candles) == 3
    assert candles[0]["close"] == Decimal("101") and candles[0]["symbol"] == "GOLD"
    assert socket.closed


def test_replay_window_never_accepts_an_incomplete_or_rejected_window(monkeypatch) -> None:
    monkeypatch.setattr(deep_backfill, "_TIMEOUT_SECONDS", 0.05)
    data = _packet({"m": "timescale_update", "p": ["cs_t", {"$prices": {"s": [{"v": [1, 1, 1, 1, 1, 1]}]}}]})
    socket = _Socket([data])
    _install_socket(monkeypatch, socket)
    with pytest.raises(IncompleteFetchError):
        deep_backfill._replay_window(_tv(), _symbol(), _timeframe(), FIXED_NOW, 1)
    assert socket.closed

    socket = _Socket([_packet({"m": "series_error", "p": ["cs_t", "s1", "boom"]})])
    _install_socket(monkeypatch, socket)
    with pytest.raises(ProviderRequestError):
        deep_backfill._replay_window(_tv(), _symbol(), _timeframe(), FIXED_NOW, 1)
    assert socket.closed


def _history(anchor: datetime, count: int, minutes: int = 5) -> list[dict]:
    step = minutes * 60
    last = datetime.fromtimestamp(anchor.timestamp() // step * step, tz=UTC)
    return [_candle(last - timedelta(minutes=minutes) * k) for k in range(count - 1, -1, -1)]


def _fixed_now(monkeypatch) -> None:
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    monkeypatch.setattr(deep_backfill, "datetime", Fixed)


def test_walk_chains_windows_backward_without_gaps_or_double_counting(monkeypatch, tmp_path: Path) -> None:
    _fixed_now(monkeypatch)
    anchors: list[datetime] = []

    def fake_fetch(_config, _symbol, _timeframe, anchor, count):
        anchors.append(anchor)
        return _history(anchor, count)

    monkeypatch.setattr(deep_backfill, "_fetch_window", fake_fetch)
    start = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)

    windows = list(deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), start, 4))

    stamps = [c["timestamp"] for window in windows for c in window]
    assert len(stamps) == len(set(stamps))  # mỗi nến đúng một lần, không đếm trùng nến nối giữa hai cửa sổ
    assert sorted(stamps) == [start + timedelta(minutes=5 * k) for k in range(12)]  # liền mạch 03:00 .. 03:55
    assert [min(c["timestamp"] for c in w) for w in windows] == [datetime(2026, 10, 7, 3, m, tzinfo=UTC) for m in (45, 30, 15, 0)]
    assert anchors == [FIXED_NOW] + [datetime(2026, 10, 7, 3, m, tzinfo=UTC) for m in (45, 30, 15)]
    assert max(stamps) == datetime(2026, 10, 7, 3, 55, tzinfo=UTC)  # nến 04:00 chưa đóng nên bị loại


def test_walk_stops_when_the_history_is_shorter_than_a_window(monkeypatch, tmp_path: Path) -> None:
    _fixed_now(monkeypatch)
    calls = []
    monkeypatch.setattr(deep_backfill, "_fetch_window",
                        lambda *a: calls.append(a) or _history(a[3], 2))
    windows = list(deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), datetime(2022, 1, 1, tzinfo=UTC), 4))
    assert len(windows) == 1 and len(calls) == 1


def test_walk_only_validates_candles_inside_the_requested_range(monkeypatch, tmp_path: Path) -> None:
    _fixed_now(monkeypatch)
    start = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
    broken_old = _candle(datetime(2001, 10, 16, tzinfo=UTC))
    broken_old["high"] = Decimal("90")  # high < open: lỗi nhà cung cấp từ 25 năm trước, nằm ngoài phạm vi
    history = [broken_old] + _history(datetime(2026, 10, 7, 3, 30, tzinfo=UTC), 4)
    monkeypatch.setattr(deep_backfill, "_fetch_window", lambda *_a: history)
    windows = list(deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), start, 10))  # < 10 nến: lịch sử hết
    assert [c["timestamp"] for c in windows[0]] == [h["timestamp"] for h in history[1:]]

    broken_new = _candle(datetime(2026, 10, 7, 3, 10, tzinfo=UTC))
    broken_new["low"] = Decimal("500")  # low > open: nến lỗi nằm TRONG phạm vi phải làm hỏng cặp (fail-closed)
    monkeypatch.setattr(deep_backfill, "_fetch_window", lambda *_a: [broken_new])
    with pytest.raises(CandleValidationError, match="violates OHLC bounds"):
        list(deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), start, 10))


def test_walk_ends_quietly_when_only_the_boundary_candle_comes_back(monkeypatch, tmp_path: Path) -> None:
    _fixed_now(monkeypatch)
    answers = iter((_history(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), 4),
                    [_candle(datetime(2026, 10, 7, 2, 45, tzinfo=UTC))]))  # lịch sử hết đúng tại nến nối
    monkeypatch.setattr(deep_backfill, "_fetch_window", lambda *_a: next(answers))
    windows = list(deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), datetime(2022, 1, 1, tzinfo=UTC), 4))
    assert len(windows) == 1


def test_walk_refuses_a_replay_that_makes_no_older_progress(monkeypatch, tmp_path: Path) -> None:
    _fixed_now(monkeypatch)
    monkeypatch.setattr(deep_backfill, "_fetch_window",
                        lambda _c, _s, _t, anchor, count: _history(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), count))
    walker = deep_backfill._walk(_config(tmp_path), _symbol(), _timeframe(), datetime(2022, 1, 1, tzinfo=UTC), 4)
    next(walker)
    with pytest.raises(IncompleteFetchError, match="no older progress"):
        next(walker)


def _stub_sql(monkeypatch, existing: dict) -> tuple[list, list]:
    chunks: list = []
    locks: list = []

    @contextmanager
    def lock(_config, name, *, timeout_seconds):
        locks.append(name)
        yield

    monkeypatch.setattr(deep_backfill, "fetch_existing_candles", lambda *_a, **_k: existing)
    monkeypatch.setattr(deep_backfill, "bulk_upsert_candles",
                        lambda _config, timeframe, candles, **kw: chunks.append((timeframe["code"], list(candles), kw)))
    monkeypatch.setattr(deep_backfill, "interprocess_lock", lock)
    monkeypatch.setattr(deep_backfill, "service_status", lambda *_a: {})
    return chunks, locks


def test_store_inserts_only_missing_candles_in_chunks_under_the_delivery_lock(monkeypatch, tmp_path: Path) -> None:
    start = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
    bars = [_candle(start + timedelta(minutes=5 * k)) for k in range(-1, 5)]  # k=-1 nằm trước mốc bắt đầu
    existing = {
        # đã có, chỉ khác volume: không tính là `changed` (volume không quan trọng)
        bars[1]["timestamp"].replace(tzinfo=None): candle_signature({**bars[1], "volume": Decimal("999")}),
        bars[2]["timestamp"].replace(tzinfo=None): candle_signature(_candle(bars[2]["timestamp"], close="105")),
    }
    chunks, locks = _stub_sql(monkeypatch, existing)
    monkeypatch.setattr(deep_backfill, "_WRITE_CHUNK", 2)

    result = deep_backfill._store(_config(tmp_path), _symbol(), _timeframe(), bars, start, object(), False)

    assert [[c["timestamp"] for c in chunk] for _code, chunk, _kw in chunks] == [
        [bars[3]["timestamp"], bars[4]["timestamp"]], [bars[5]["timestamp"]],
    ]
    assert all(kw["symbol_id"] == 56 for _code, _chunk, kw in chunks)
    assert locks == ["delivery", "delivery"]
    assert dict(result) == {"fetched": 5, "inserted": 3, "present": 2, "changed": 1}


def test_store_in_dry_run_counts_but_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    start = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
    bars = [_candle(start + timedelta(minutes=5 * k)) for k in range(3)]
    chunks, locks = _stub_sql(monkeypatch, {})
    result = deep_backfill._store(_config(tmp_path), _symbol(), _timeframe(), bars, start, object(), True)
    assert dict(result) == {"fetched": 3, "inserted": 3, "present": 0} and not chunks and not locks


def test_store_stops_writing_the_moment_daily_backfill_comes_alive(monkeypatch, tmp_path: Path) -> None:
    start = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
    bars = [_candle(start + timedelta(minutes=5 * k)) for k in range(4)]
    chunks, _locks = _stub_sql(monkeypatch, {})
    monkeypatch.setattr(deep_backfill, "_WRITE_CHUNK", 2)
    states = iter(({}, {"status": "running", "process_alive": True}))
    monkeypatch.setattr(deep_backfill, "service_status", lambda *_a: next(states))
    with pytest.raises(deep_backfill.DeepBackfillError, match="daily backfill is running"):
        deep_backfill._store(_config(tmp_path), _symbol(), _timeframe(), bars, start, object(), False)
    assert len(chunks) == 1  # chunk đầu đã ghi xong, chunk thứ hai bị chặn


_PAIRS = [(_symbol("GOLD"), _timeframe("M5")), (_symbol("GOLD"), _timeframe("M10", 10)),
          (_symbol("BTCUSD", 81), _timeframe("M5")), (_symbol("BTCUSD", 81), _timeframe("H1", 60))]


def test_selection_applies_cli_overrides_and_rejects_unknown_names(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(deep_backfill, "select_pairs", lambda _config, *, live: _PAIRS)
    config = _config(tmp_path)
    assert [(s["symbol"], t["code"]) for s, t in deep_backfill._select(config, None, None)] == [
        ("GOLD", "M5"), ("GOLD", "M10")]
    assert [(s["symbol"], t["code"]) for s, t in deep_backfill._select(config, "btcusd", "m5")] == [("BTCUSD", "M5")]
    with pytest.raises(ValueError, match="XAUUSD"):
        deep_backfill._select(config, "XAUUSD", None)
    with pytest.raises(ValueError, match="M7"):
        deep_backfill._select(config, None, "M7")
    config["deep_backfill"]["symbols"] = []
    with pytest.raises(ValueError, match="no symbols"):
        deep_backfill._select(config, None, None)


def _patch_run(monkeypatch, runner) -> list:
    calls: list = []
    monkeypatch.setattr(deep_backfill, "select_pairs", lambda _config, *, live: _PAIRS[:3])
    monkeypatch.setattr(deep_backfill, "service_status", lambda *_a: {})
    monkeypatch.setattr(deep_backfill, "_run_pair", lambda config, pair, start, window, dry_run: (
        calls.append((pair[1]["code"], start, dry_run)), runner(pair))[1])
    return calls


def test_run_pair_sums_windows_logs_each_one_and_always_closes_the_connection(monkeypatch, tmp_path: Path, caplog) -> None:
    class Connection:
        timeout, closed = 0, False

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    stamps = [datetime(2026, 10, 7, 3, 55, tzinfo=UTC), datetime(2026, 10, 7, 3, 40, tzinfo=UTC)]
    monkeypatch.setattr(deep_backfill, "get_connection", lambda _config: connection)
    monkeypatch.setattr(deep_backfill, "_walk", lambda *_a: iter([[_candle(stamp)] for stamp in stamps]))
    monkeypatch.setattr(deep_backfill, "_store", lambda *_a: Counter(fetched=1, inserted=1, changed=0))
    caplog.set_level("INFO", logger=deep_backfill.LOGGER.name)

    totals = deep_backfill._run_pair(_config(tmp_path), (_symbol(), _timeframe()), stamps[-1], 4, False)

    assert (totals["windows"], totals["fetched"], totals["inserted"]) == (2, 2, 2)
    assert connection.closed and connection.timeout == deep_backfill._SQL_TIMEOUT_SECONDS
    text = caplog.text
    assert text.count("DEEP_BACKFILL_WINDOW ") == 2 and text.count("DEEP_BACKFILL_PAIR_COMPLETED") == 1

    class Failing(Connection):
        pass

    failing = Failing()
    monkeypatch.setattr(deep_backfill, "get_connection", lambda _config: failing)
    monkeypatch.setattr(deep_backfill, "_walk", lambda *_a: iter([[_candle(stamps[0])]]))
    monkeypatch.setattr(deep_backfill, "_store", lambda *_a: (_ for _ in ()).throw(RuntimeError("sql down")))
    with pytest.raises(RuntimeError, match="sql down"):
        deep_backfill._run_pair(_config(tmp_path), (_symbol(), _timeframe()), stamps[-1], 4, False)
    assert failing.closed


def test_run_is_gated_by_config_and_never_runs_next_to_a_live_daily_backfill(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["deep_backfill"]["enabled"] = False
    with pytest.raises(RuntimeError, match="disabled"):
        deep_backfill.run_deep_backfill(config)

    config["deep_backfill"]["enabled"] = True
    calls = _patch_run(monkeypatch, lambda pair: Counter(windows=1))
    monkeypatch.setattr(deep_backfill, "service_status", lambda *_a: {"status": "running", "process_alive": True})
    with pytest.raises(deep_backfill.DeepBackfillError):
        deep_backfill.run_deep_backfill(config)
    assert not calls
    assert deep_backfill.run_deep_backfill(config, dry_run=True)["ok"] and len(calls) == 2  # chạy khô thì được


def test_run_checkpoints_each_pair_and_resumes_without_refetching(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    done = lambda pair: Counter(windows=2, fetched=10, inserted=4, present=6, changed=1)  # noqa: E731
    calls = _patch_run(monkeypatch, done)

    first = deep_backfill.run_deep_backfill(config, symbols="GOLD,BTCUSD")
    assert (first["completed"], first["skipped"], first["inserted"], first["windows"], first["changed"]) == (3, 0, 12, 6, 3)
    state = json.loads((tmp_path / "deep_backfill" / "state.json").read_text(encoding="utf-8"))
    assert sorted(state["pairs"]) == ["CAPITALCOM:BTCUSD/M5", "CAPITALCOM:GOLD/M10", "CAPITALCOM:GOLD/M5"]
    assert state["pairs"]["CAPITALCOM:GOLD/M5"]["inserted"] == 4

    again = deep_backfill.run_deep_backfill(config, symbols="GOLD,BTCUSD")
    assert (again["completed"], again["skipped"]) == (0, 3) and len(calls) == 3
    assert deep_backfill.run_deep_backfill(config, symbols="GOLD,BTCUSD", fresh=True)["completed"] == 3
    assert deep_backfill.run_deep_backfill(config, symbols="GOLD,BTCUSD", start="2023-01-01")["completed"] == 3
    assert calls[-1][1] == datetime(2023, 1, 1, tzinfo=UTC)


def test_run_does_not_checkpoint_a_dry_run(monkeypatch, tmp_path: Path) -> None:
    _patch_run(monkeypatch, lambda pair: Counter(windows=1, fetched=3, inserted=3))
    summary = deep_backfill.run_deep_backfill(_config(tmp_path), dry_run=True)
    assert summary["dry_run"] and summary["inserted"] == 6 and summary["completed"] == 2
    assert not (tmp_path / "deep_backfill" / "state.json").exists()


def test_run_defers_the_rest_after_three_consecutive_pair_failures(monkeypatch, tmp_path: Path) -> None:
    def boom(_pair):
        raise RuntimeError("provider down")

    monkeypatch.setattr(deep_backfill, "select_pairs", lambda _config, *, live: _PAIRS * 2)
    monkeypatch.setattr(deep_backfill, "service_status", lambda *_a: {})
    monkeypatch.setattr(deep_backfill, "_run_pair", lambda config, pair, start, window, dry_run: boom(pair))
    config = _config(tmp_path)
    config["deep_backfill"]["symbols"] = ["GOLD", "BTCUSD"]
    summary = deep_backfill.run_deep_backfill(config)
    assert (summary["failed"], summary["deferred"], summary["completed"], summary["ok"]) == (3, 5, 0, False)


def test_a_safety_stop_aborts_the_whole_run_instead_of_counting_a_pair_failure(monkeypatch, tmp_path: Path) -> None:
    def unsafe(_pair):
        raise deep_backfill.DeepBackfillError("daily backfill is running")

    _patch_run(monkeypatch, unsafe)
    with pytest.raises(deep_backfill.DeepBackfillError):
        deep_backfill.run_deep_backfill(_config(tmp_path))


def test_cli_takes_an_explicit_config_and_the_deep_backfill_options() -> None:
    from dp_program.__main__ import build_parser

    args = build_parser().parse_args([
        "--config", "C:/prod/config.yaml", "deep-backfill", "--dry-run", "--symbol", "GOLD",
        "--timeframe", "M5,M10", "--start", "2022-01-01", "--fresh",
    ])
    assert (args.config, args.command, args.symbol, args.timeframe, args.start, args.dry_run, args.fresh) == (
        "C:/prod/config.yaml", "deep-backfill", "GOLD", "M5,M10", "2022-01-01", True, True)
    assert build_parser().parse_args(["deep-backfill"]).config is None


def test_summary_names_the_config_file_that_was_used(monkeypatch, tmp_path: Path) -> None:
    _patch_run(monkeypatch, lambda pair: Counter(windows=1))
    config = _config(tmp_path)
    config["app"]["config_path"] = "C:/prod/config.yaml"
    assert deep_backfill.run_deep_backfill(config, dry_run=True)["config_path"] == "C:/prod/config.yaml"


def test_module_keeps_sql_in_sql_connector_and_never_publishes_events() -> None:
    source = Path(deep_backfill.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module.split(".")[-1] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"redis", "pyodbc", "yaml", "backfill", "live"}
    docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    assert not any(word in text.upper() for text in literals for word in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "EXEC "))
    assert not any(isinstance(n, ast.Attribute) and n.attr in {"publish", "flushdb"} for n in ast.walk(tree))
