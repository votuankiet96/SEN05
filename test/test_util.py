from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest


class _Response:
    def __init__(self, status_code: int, retry_after: float = 0) -> None:
        self.status_code = status_code
        self._retry_after = retry_after

    def json(self) -> dict[str, float]:
        return {"retry_after": self._retry_after}


class _Cursor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.statement = ""
        self.params: tuple = ()

    def execute(self, statement: str, *params: object) -> "_Cursor":
        self.statement = statement
        self.params = params
        return self

    def fetchall(self) -> list[tuple]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[tuple]) -> None:
        self._cursor = _Cursor(rows)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


class _FakeRedisClient:
    def __init__(self) -> None:
        self.evals: list[tuple[str, list[str], list[str]]] = []
        self.strings: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.strings[key] = value

    def register_script(self, script: str):
        def execute(*, keys: list[str], args: list[object], client: object = None) -> int:
            del client
            self.evals.append((script, list(keys), [str(value) for value in args]))
            return 1

        return execute


def _code_line_count(path: Path) -> int:
    """Count lines that carry logic, excluding blank lines and whole-line comments."""
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _config() -> dict:
    return {"tables": {"fact_table": "DWH.Fact_OHLCV"}}


def _universe() -> tuple[list[dict], list[dict]]:
    symbols = [
        {"exchange": "CAPITALCOM", "symbol": "GOLD", "asset_type": "METAL", "enabled": True},
        {"exchange": "CAPITALCOM", "symbol": "BTCUSD", "asset_type": "CRYPTO", "enabled": True},
    ]
    timeframes = [{"code": "M5"}, {"code": "H1"}]
    return symbols, timeframes


def test_discord_config_gate_requires_webhook_only_when_enabled(tmp_path: Path) -> None:
    import yaml

    from dp_program.configuration import ConfigError, load_config

    source = {
        "app": {"log_level": "INFO", "runtime_dir": "runtime"},
        "discord": {"enabled": False, "webhook_url": ""},
        "redis": {"enabled": False},
        "tradingview": {
            "auth_token": "",
            "cookie": "",
            "username": "",
            "password": "",
            "two_factor_secret": "",
        },
        "backfill": {
            "enabled": True,
            "lookback_days": 60,
            "run_on_start": True,
            "schedule_utc": ["11:11", "15:15", "19:19", "23:23", "03:03", "07:07"],
        },
        "live": {
            "enabled": True,
            "interval_minutes": 5,
            "bars_per_request": 3,
            "closed_candles_only": True,
            "symbols": [
                "FR40", "DE40", "HK50", "J225", "SP35", "UK100",
                "US500", "US100", "US30", "GOLD", "BTCUSD",
            ],
            "timeframes": [
                "M5", "M10", "M15", "M20", "M30", "M45", "H1", "M90",
                "H2", "H3", "H4", "H6", "H8", "D1", "W",
            ],
        },
        "service": {},
        "sql_server": {
            "server": "localhost",
            "database": "SEN05_AutoTrading",
            "port": "",
            "username": "",
            "password": "",
            "trusted_connection": True,
            "encrypt": "no",
            "trust_server_certificate": True,
        },
    }
    disabled = tmp_path / "disabled.yaml"
    disabled.write_text(yaml.safe_dump(source), encoding="utf-8")
    config = load_config(disabled)
    assert config["discord"] == {"enabled": False, "webhook_url": ""}

    source["discord"]["enabled"] = True
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ConfigError, match="discord.webhook_url"):
        load_config(invalid)


def test_discord_report_redacts_secrets_and_builds_operator_payload() -> None:
    from dp_program.util import discord_report

    secret = "https://discord.com/api/webhooks/123456/super-secret"
    text = discord_report._redact(
        f"password=hunter2 cookie=session-value auth_token=abc {secret}"
    )
    assert "hunter2" not in text
    assert "session-value" not in text
    assert "super-secret" not in text

    snapshot = {
        "captured_at": "2026-07-27T12:00:00Z",
        "risk": "HIGH",
        "status": "running",
        "pid": 1234,
        "heartbeat_age_seconds": 12,
        "last_live": {"pairs": 165, "ok": 164, "failed": 1, "affected": 10},
        "auth": {"ok": True, "state": "authenticated", "seconds_remaining": 1800},
        "database": {"ok": True, "database": "SEN05_AutoTrading"},
        "spool": {"pending": 2, "corrupt": 0, "oldest_age_seconds": 20},
    }
    payload = discord_report._build_payload(snapshot, "health")
    rendered = str(payload)
    assert payload["embeds"][0]["title"] == "DP Program — HIGH"
    assert "V3" not in rendered
    assert "HIGH" in rendered
    assert "164/165" in rendered
    assert secret not in rendered
    assert discord_report._risk({"last_backfill": {"failed": 1}}) == "HIGH"
    assert discord_report._risk(
        {"last_backfill": {"failed": 0}, "backfill_failed_pairs": ["GOLD/M5"]}
    ) == "HIGH"
    assert discord_report._risk(
        {"last_live": {"failed": 0, "deferred": 4}}
    ) == "MEDIUM"
    assert discord_report._risk(
        {"spool": {"pending": 2, "corrupt": 0, "oldest_age_seconds": 20}}
    ) == "NONE"
    assert discord_report._risk(
        {"spool": {"pending": 2, "corrupt": 0, "oldest_age_seconds": 121}}
    ) == "MEDIUM"
    assert discord_report._risk(
        {
            "backfill_queue_remaining": 10,
            "last_backfill_progress_at": (
                datetime.now(timezone.utc) - timedelta(hours=2)
            ).isoformat(),
        }
    ) == "HIGH"


def test_discord_post_retries_rate_limit_and_accepts_204() -> None:
    from dp_program.util import discord_report

    responses = iter((_Response(429, 0.01), _Response(204)))
    sleeps: list[float] = []
    calls: list[tuple[str, dict, float]] = []

    def post(url: str, *, json: dict, timeout: float) -> _Response:
        calls.append((url, json, timeout))
        return next(responses)

    status = discord_report._post_payload(
        "https://discord.com/api/webhooks/123/secret",
        {"embeds": []},
        post=post,
        sleep=sleeps.append,
    )
    assert status == 204
    assert len(calls) == 2
    assert sleeps == [0.1]


def test_discord_reporter_is_inert_when_disabled() -> None:
    from dp_program.util.discord_report import DiscordReporter

    reporter = DiscordReporter(
        {"discord": {"enabled": False, "webhook_url": ""}},
        post=lambda *_args, **_kwargs: pytest.fail("Discord must remain disabled"),
    )
    with reporter:
        reporter.publish("health", {"status": "running"})
    assert reporter.thread is None


def test_send_watchdog_alert_posts_a_critical_embed_without_a_reporter_thread() -> None:
    from dp_program.util.discord_report import send_watchdog_alert

    payloads: list[dict] = []

    def post(_url: str, *, json: dict, timeout: float) -> _Response:
        payloads.append(json)
        return _Response(204)

    status = send_watchdog_alert(
        {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/123/secret",
            }
        },
        "live_service_down",
        {"status": "stopped", "pid": 6112, "process_alive": False, "risk": "CRITICAL"},
        post=post,
    )
    assert status == 204
    assert len(payloads) == 1
    assert "CRITICAL" in str(payloads[0])
    assert "LIVE_SERVICE_DOWN" in str(payloads[0])


def test_send_watchdog_alert_is_inert_when_discord_disabled() -> None:
    from dp_program.util.discord_report import send_watchdog_alert

    status = send_watchdog_alert(
        {"discord": {"enabled": False, "webhook_url": ""}},
        "live_service_down",
        {"status": "stopped", "risk": "CRITICAL"},
        post=lambda *_args, **_kwargs: pytest.fail("Discord must remain disabled"),
    )
    assert status == 0


def test_watchdog_alerts_once_per_outage_and_clears_marker_on_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Watchdog giờ chỉ còn trong dp_program_entry.py (bản chạy .bat cũ đã bỏ).
    from dp_program.engine import runtime
    from dp_program.util import discord_report

    entry = _load_exe_entry()
    config = {"app": {"runtime_dir": str(tmp_path)}}
    statuses = {"live": {"ok": False}, "backfill": {"ok": True}}
    monkeypatch.setattr(runtime, "service_status", lambda _config, role: statuses[role])
    sent: list[tuple] = []
    monkeypatch.setattr(
        discord_report, "send_watchdog_alert", lambda *args, **kwargs: sent.append((args, kwargs))
    )
    marker = tmp_path / "run" / "watchdog_alerted_live"

    assert entry.watchdog_once(config) == 1
    assert len(sent) == 1 and marker.exists()

    # Still unhealthy on the next run: stays silent (edge-triggered, not per-poll).
    assert entry.watchdog_once(config) == 1
    assert len(sent) == 1

    # Recovery clears the marker so a future outage can alert again.
    statuses["live"] = {"ok": True}
    assert entry.watchdog_once(config) == 0
    assert not marker.exists()


def test_discord_reporter_runs_only_inside_service_lifecycle() -> None:
    from dp_program.util.discord_report import DiscordReporter

    payloads: list[dict] = []

    def post(_url: str, *, json: dict, timeout: float) -> _Response:
        assert timeout == 5.0
        payloads.append(json)
        return _Response(204)

    reporter = DiscordReporter(
        {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/123/secret",
            }
        },
        post=post,
    )
    reporter.publish("started", {"status": "running"})
    assert payloads == []
    with reporter:
        reporter.publish(
            "started",
            {
                "status": "running",
                "pid": 1234,
                "auth": {"ok": True, "state": "authenticated"},
                "database": {"ok": True, "database": "warehouse"},
            },
        )
    assert len(payloads) == 1
    assert payloads[0]["username"] == "DP Program"
    assert "V3" not in str(payloads[0])


def test_discord_reporter_suppresses_duplicate_incidents_for_15_minutes() -> None:
    from dp_program.util.discord_report import DiscordReporter

    now = [0.0]
    payloads: list[dict] = []

    def post(_url: str, *, json: dict, timeout: float) -> _Response:
        payloads.append(json)
        return _Response(204)

    reporter = DiscordReporter(
        {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/123/secret",
            }
        },
        post=post,
        clock=lambda: now[0],
    )
    with reporter:
        reporter.publish("started", {"status": "running"})
        now[0] = 1
        reporter.publish("live", {"status": "running", "risk": "HIGH"})
        now[0] = 100
        reporter.publish("live", {"status": "running", "risk": "HIGH"})
        now[0] = 902
        reporter.publish("live", {"status": "running", "risk": "HIGH"})
    assert len(payloads) == 3


def test_discord_reporter_sends_periodic_health_every_three_hours() -> None:
    from dp_program.util.discord_report import DiscordReporter

    now = [0.0]
    payloads: list[dict] = []

    def post(_url: str, *, json: dict, timeout: float) -> _Response:
        payloads.append(json)
        return _Response(204)

    reporter = DiscordReporter(
        {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/123/secret",
            }
        },
        post=post,
        clock=lambda: now[0],
    )
    healthy = {
        "status": "running",
        "auth": {"ok": True},
        "database": {"ok": True},
        "spool": {"pending": 0, "corrupt": 0},
    }
    with reporter:
        reporter.publish("health", healthy)
        now[0] = 3600
        reporter.publish("health", healthy)
        now[0] = 10800
        reporter.publish("health", healthy)
    assert len(payloads) == 2


def test_discord_reporter_never_suppresses_a_completed_backfill_generation() -> None:
    from dp_program.util.discord_report import DiscordReporter

    now = [0.0]
    payloads: list[dict] = []

    def post(_url: str, *, json: dict, timeout: float) -> _Response:
        payloads.append(json)
        return _Response(204)

    reporter = DiscordReporter(
        {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/123/secret",
            }
        },
        post=post,
        clock=lambda: now[0],
    )
    summary = "schedule:11:00: 555/555 processed, 0 failed, 0 deferred, elapsed 242s"
    with reporter:
        reporter.publish("started", {"status": "running"})
        now[0] = 1
        reporter.publish("live", {"status": "running", "risk": "HIGH"})
        now[0] = 2
        reporter.publish(
            "backfill_completed",
            {"status": "running", "risk": "HIGH", "last_backfill_generation": summary},
        )
    assert len(payloads) == 3
    assert "555/555" in str(payloads[2])


def test_redis_publisher_is_inert_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from dp_program.util import redis_publisher

    monkeypatch.setattr(
        redis_publisher, "read_latest_candles",
        lambda *_a, **_k: pytest.fail("SQL must not be read when Redis is disabled"),
    )
    publisher = redis_publisher._RedisPublisher()
    publisher.enqueue({"redis": {"enabled": False}}, 1, "GOLD", "M5", "CAPITALCOM:GOLD", [])
    assert publisher._thread is None
    assert not publisher._updates and not publisher._reconciles


def _redis_config() -> dict:
    return {
        "redis": {
            "enabled": True, "bars_per_snapshot": 500, "circuit_cooldown_seconds": 30,
            "key_prefix": "CANDLE", "event_channel": "dp:events:candles",
        }
    }


def test_redis_publisher_writes_only_the_changed_candles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hot path re-reads the committed Fact row for exactly the bar times
    live.py just wrote -- so `inserttime` is SQL's own CreatedAt and every
    value in Redis is a copy of SQL -- then HSETs (not ZADDs) and PUBLISHes."""
    from dp_program.util import redis_publisher

    bar = datetime(2026, 7, 27, 12, 5)
    created = datetime(2026, 7, 27, 12, 6, 3)
    asked: list[list[datetime]] = []

    def _read(_config, _symbol_id, _tf_code, _limit, bartimes=None):
        asked.append(list(bartimes))
        return [(bar, Decimal("1.5"), Decimal("2.5"), Decimal("1"), Decimal("2"),
                 Decimal("12"), created)]

    monkeypatch.setattr(redis_publisher, "read_latest_candles", _read)
    client = _FakeRedisClient()
    publisher = redis_publisher._RedisPublisher()
    monkeypatch.setattr(publisher, "_get_client", lambda _settings: client)

    publisher._publish_one(_redis_config(), (9, "US30", "H1", "CAPITALCOM:US30"), [bar])
    assert asked == [[bar]]  # chi gui moc sang SQL, khong gui gia tri nao
    assert len(client.evals) == 1
    script, keys, rest = client.evals[0]
    assert "HSET" in script and "PUBLISH" in script and "ZADD" not in script
    assert keys == ["CANDLE:US30_H1", "dp:events:candles"]
    (max_size, candle_prefix, prefix, source,
     stamp, epoch, readable, o, h, low_, c, v, inserted, payload) = rest
    # Key Hash = candle_prefix + stamp, tuc chinh la list_key noi them ":" +
    # phan tu lay tu List -- dung mot phep noi, khong quy uoc nao khac.
    assert candle_prefix == "CANDLE:US30_H1:"
    assert candle_prefix == keys[0] + ":"
    # Moc dung '-' cho gio-phut-giay: ':' se bi Redis GUI hieu la phan cap.
    assert stamp == "2026-07-27_12-05-00" and ":" not in stamp
    assert epoch == str(int(bar.replace(tzinfo=timezone.utc).timestamp()))
    assert readable == "2026-07-27 12:05:00"
    assert source == "CAPITALCOM:US30"
    assert inserted == "2026-07-27 12:06:03"  # Fact.CreatedAt, khong phai gio publish
    assert (o, h, low_, c, v) == (
        "1.50000000", "2.50000000", "1.00000000", "2.00000000", "12.0000",
    )
    assert '"open":1.5' in payload  # Decimal converted to a real JSON number
    # Mock Redis o day khong thuc thi Lua, nen no khong the tu bat loi
    # neu chuoi noi trong script bi sai (vd thieu dau dong '}' -- day
    # la loi that da xay ra va duoc redis_probe/pubsub_probe.py bat qua
    # 1 event PUBLISH that khong parse duoc). Trich dung dung literal
    # duoc noi sau table.concat(...) trong script that de dung lai
    # chinh xac PUBLISH payload cuoi cung se la gi, roi parse thu --
    # neu ai sua script lam vo JSON lan nua, dong assert nay se do.
    suffix_match = re.search(r"table\.concat\(events, ','\)\s*\.\.\s*'([^']*)'", script)
    assert suffix_match, "khong tim thay doan noi chuoi PUBLISH trong script Lua"
    envelope = prefix + payload + suffix_match.group(1)
    assert json.loads(envelope) == {"symbol": "US30", "timeframe": "H1", "candles": [json.loads(payload)]}


def test_redis_publisher_reconcile_reads_sql_and_diffs_against_current_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconciliation (startup + periodic safety net) re-reads SQL truth and
    sends it to the diff-and-patch script, not the incremental HSET script."""
    from dp_program.util import redis_publisher

    bar1 = datetime(2026, 7, 27, 12, 0)
    bar2 = datetime(2026, 7, 27, 12, 5)
    made = datetime(2026, 7, 27, 12, 6, 3)
    rows = [
        (bar1, Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal("1.5"), Decimal("10"), made),
        (bar2, Decimal("1.5"), Decimal("2.5"), Decimal("1"), Decimal("2"), Decimal("12"), made),
    ]
    monkeypatch.setattr(redis_publisher, "read_latest_candles", lambda *_a, **_k: rows)
    client = _FakeRedisClient()
    publisher = redis_publisher._RedisPublisher()
    monkeypatch.setattr(publisher, "_get_client", lambda _settings: client)

    publisher._reconcile_one(_redis_config(), 9, "US30", "H1", "CAPITALCOM:US30")
    assert len(client.evals) == 1
    script, keys, rest = client.evals[0]
    assert "LRANGE" in script and "DEL" in script  # diffs and evicts, not a blind rewrite
    assert keys == ["CANDLE:US30_H1"]
    candle_prefix, source, *candle_args = rest
    assert candle_prefix == "CANDLE:US30_H1:"
    assert source == "CAPITALCOM:US30"
    stamp1, epoch1, readable1, *rest1 = candle_args[0:9]
    stamp2, _epoch2, _readable2, *rest2 = candle_args[9:18]
    assert stamp1 == bar1.strftime("%Y-%m-%d_%H-%M-%S")
    assert stamp2 == bar2.strftime("%Y-%m-%d_%H-%M-%S")
    assert epoch1 == str(int(bar1.replace(tzinfo=timezone.utc).timestamp()))
    assert readable1 == "2026-07-27 12:00:00"
    assert rest1 == ["1.00000000", "2.00000000", "0.50000000", "1.50000000", "10.0000",
                     "2026-07-27 12:06:03"]
    assert rest2 == ["1.50000000", "2.50000000", "1.00000000", "2.00000000", "12.0000",
                     "2026-07-27 12:06:03"]


def test_redis_publisher_circuit_breaker_skips_after_failure_until_cooldown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from dp_program.util import redis_publisher

    calls: list[int] = []

    def _boom(*_a, **_k):
        calls.append(1)
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_publisher, "read_latest_candles", _boom)
    publisher = redis_publisher._RedisPublisher()
    config = _redis_config()

    def _run_once() -> None:
        try:
            publisher._reconcile_one(config, 1, "GOLD", "M5", "CAPITALCOM:GOLD")
        except Exception as exc:  # mirrors what _worker_loop does around _reconcile_one
            publisher._open_circuit(config, exc)

    with caplog.at_level("WARNING", logger="dp_program.util.redis_publisher"):
        _run_once()
        _run_once()

    # Cooldown duoc _worker_loop ton trong (no cho het cooldown truoc khi lay
    # job ke tiep), nen o day chi kiem dung hai dam bao cua _open_circuit:
    # cooldown thuc su duoc dat, va loi chi log mot lan chu khong spam.
    assert calls == [1, 1]
    assert publisher._circuit_open_until > time.monotonic()
    failures = [record for record in caplog.records if "REDIS_PUBLISH_FAILED" in record.message]
    assert len(failures) == 1


def test_redis_publisher_builds_client_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis as redis_module

    from dp_program.util import redis_publisher

    captured: dict = {}

    class _FakeRedis:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(redis_module, "Redis", _FakeRedis)
    publisher = redis_publisher._RedisPublisher()
    settings = {
        "host": "redis.invalid", "port": 6379, "db": 0,
        "username": "", "password": "fake-redis-password", "timeout_seconds": 0.3,
    }
    client = publisher._get_client(settings)
    assert isinstance(client, _FakeRedis)
    assert captured["host"] == "redis.invalid"
    assert captured["password"] == "fake-redis-password"
    assert captured["username"] is None  # empty string normalized to None


def test_reconcile_all_live_pairs_enqueues_every_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    from dp_program.util import redis_publisher

    calls: list[tuple] = []

    class _FakePublisher:
        def enqueue_reconcile_all(self, _config, pairs):
            for symbol, timeframe in pairs:
                calls.append((symbol["symbol_id"], symbol["symbol"], timeframe["code"], None))

    monkeypatch.setattr(redis_publisher, "_publisher", _FakePublisher())
    pairs = [
        ({"symbol_id": 1, "symbol": "GOLD"}, {"code": "M5"}),
        ({"symbol_id": 2, "symbol": "BTCUSD"}, {"code": "H1"}),
    ]
    redis_publisher.reconcile_all_live_pairs({"redis": {"enabled": True}}, pairs)
    # candles=None signals a full SQL reconciliation, not an incremental publish.
    assert calls == [(1, "GOLD", "M5", None), (2, "BTCUSD", "H1", None)]


def test_chart_query_is_read_only_parameterized_and_returns_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dp_program.engine import sql_connector
    from dp_program.util.chart import server

    newer = datetime(2026, 7, 27, 12, 5, tzinfo=timezone.utc)
    older = datetime(2026, 7, 27, 12, 0)
    connection = _Connection(
        [
            (newer, Decimal("2"), Decimal("3"), Decimal("1"), Decimal("2.5"), 20),
            (older, Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal("1.5"), 10),
        ]
    )
    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: connection)
    rows = sql_connector.read_chart_rows(_config(), "GOLD", "M5", 50)
    symbols, timeframes = _universe()
    candles = server._load_candles(
        symbols, timeframes, _config(), "CAPITALCOM:GOLD", "M5", 50,
        row_loader=lambda *_args: rows,
    )

    statement = connection._cursor.statement.upper()
    assert statement.lstrip().startswith("SELECT TOP (?)")
    assert all(word not in statement for word in ("INSERT ", "UPDATE ", "DELETE ", "MERGE "))
    assert connection._cursor.params == (50, "GOLD", "M5")
    assert connection.closed
    assert [item["time"] for item in candles] == [
        int(older.replace(tzinfo=timezone.utc).timestamp()),
        int(newer.timestamp()),
    ]
    assert candles[0]["volume"] == 10.0


def test_chart_meta_groups_enabled_symbols_by_asset_type_from_sql_universe() -> None:
    from dp_program.util.chart import server

    symbols = [
        {"exchange": "CAPITALCOM", "symbol": "GOLD", "asset_type": "METAL", "enabled": True},
        {"exchange": "CAPITALCOM", "symbol": "BTCUSD", "asset_type": "CRYPTO", "enabled": True},
        {"exchange": "CAPITALCOM", "symbol": "EURUSD", "asset_type": "FOREX", "enabled": False},
    ]
    timeframes = [{"code": "M5"}, {"code": "H1"}]

    meta = server._meta(symbols, timeframes)

    assert meta["symbols"] == [
        {"name": "CRYPTO", "values": ["CAPITALCOM:BTCUSD"]},
        {"name": "METAL", "values": ["CAPITALCOM:GOLD"]},
    ]
    assert meta["timeframes"] == ["M5", "H1"]


@pytest.mark.parametrize(
    ("symbol", "timeframe", "bars"),
    [
        ("CAPITALCOM:UNKNOWN", "M5", 50),
        ("CAPITALCOM:GOLD", "D2", 50),
        ("CAPITALCOM:GOLD", "M5", 49),
        ("CAPITALCOM:GOLD", "M5", 5001),
    ],
)
def test_chart_rejects_values_outside_reviewed_contract(
    symbol: str, timeframe: str, bars: int
) -> None:
    from dp_program.util.chart import server

    symbols, timeframes = _universe()
    with pytest.raises(ValueError):
        server._load_candles(
            symbols,
            timeframes,
            _config(),
            symbol,
            timeframe,
            bars,
            row_loader=lambda *_args: pytest.fail("SQL must not be opened"),
        )


def _load_exe_entry():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "windows" / "dp_program_entry.py"
    spec = importlib.util.spec_from_file_location("dp_program_exe_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeProcess:
    """Records the role it was asked to run instead of ever starting one."""

    exitcode = 0

    def __init__(self, *, target, args, name) -> None:
        self.role = args[0]
        self.name = name
        self.started = False

    def start(self) -> None:
        self.started = True

    def join(self) -> None:
        pass


def _enabled_config(*, live: bool, backfill: bool, runtime_dir: Path) -> dict:
    return {
        "app": {"runtime_dir": str(runtime_dir)},
        "live": {"enabled": live},
        "backfill": {"enabled": backfill},
    }


def _mark_running(runtime_dir: Path, role: str) -> None:
    """Ghi state file cho mot role dang chay that (PID cua chinh test)."""
    run_dir = runtime_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"state_{role}.json").write_text(
        json.dumps({
            "status": "running",
            "pid": os.getpid(),
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }),
        encoding="utf-8",
    )


def test_exe_entry_spawns_one_child_process_per_enabled_workflow(tmp_path: Path) -> None:
    entry = _load_exe_entry()
    spawned: list[_FakeProcess] = []

    def factory(*, target, args, name):
        process = _FakeProcess(target=target, args=args, name=name)
        spawned.append(process)
        return process

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=True, runtime_dir=tmp_path),
        process_factory=factory,
    )
    assert code == 0
    assert {process.role for process in spawned} == {"live", "backfill"}
    assert all(process.started for process in spawned)


def test_exe_entry_spawns_only_the_enabled_workflow(tmp_path: Path) -> None:
    entry = _load_exe_entry()
    spawned: list[_FakeProcess] = []

    def factory(*, target, args, name):
        process = _FakeProcess(target=target, args=args, name=name)
        spawned.append(process)
        return process

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=False, runtime_dir=tmp_path),
        process_factory=factory,
    )
    assert code == 0
    assert [process.role for process in spawned] == ["live"]


def test_exe_entry_spawns_nothing_when_both_workflows_disabled(tmp_path: Path) -> None:
    entry = _load_exe_entry()

    def factory(*_args, **_kwargs):
        pytest.fail("no process should be spawned when nothing is enabled")

    code = entry.main_entry(
        config=_enabled_config(live=False, backfill=False, runtime_dir=tmp_path),
        process_factory=factory,
    )
    assert code == 1


def test_exe_entry_skips_a_workflow_that_is_already_running(tmp_path: Path) -> None:
    """Guard van hanh: khong fork con de roi no chet tren instance lock."""
    entry = _load_exe_entry()
    _mark_running(tmp_path, "live")
    spawned: list[_FakeProcess] = []

    def factory(*, target, args, name):
        process = _FakeProcess(target=target, args=args, name=name)
        spawned.append(process)
        return process

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=True, runtime_dir=tmp_path),
        process_factory=factory,
    )
    assert code == 0
    assert [process.role for process in spawned] == ["backfill"]


def test_exe_entry_starts_nothing_when_every_workflow_already_runs(tmp_path: Path) -> None:
    entry = _load_exe_entry()
    for role in ("live", "backfill"):
        _mark_running(tmp_path, role)

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=True, runtime_dir=tmp_path),
        process_factory=lambda **_kwargs: pytest.fail("da chay san thi khong duoc fork them"),
    )
    assert code == 0


def test_watchdog_classifies_intentional_stop_apart_from_failure() -> None:
    entry = _load_exe_entry()
    assert entry._classify({"ok": True}) == "healthy"
    assert entry._classify({"ok": False, "status": "stopped"}) == "stopped"
    # Treo = con song nhung khong tien trien: Task Scheduler khong thay duoc.
    assert entry._classify({"ok": False, "status": "running", "process_alive": True}) == "hung"
    assert entry._classify({"ok": False, "status": "running", "process_alive": False}) == "down"


def test_watchdog_stays_quiet_for_a_recent_intentional_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dp_program.engine import runtime
    from dp_program.util import discord_report

    entry = _load_exe_entry()
    stopped = {"ok": False, "status": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(runtime, "service_status", lambda _config, _role: stopped)
    monkeypatch.setattr(
        discord_report, "send_watchdog_alert",
        lambda *_a, **_k: pytest.fail("dung chu dong khong duoc sinh canh bao"),
    )
    # Dung chu dong khong tinh la unhealthy -> exit 0.
    assert entry.watchdog_once({"app": {"runtime_dir": str(tmp_path)}}) == 0


def test_watchdog_reminds_when_an_intentional_stop_is_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dp_program.engine import runtime
    from dp_program.util import discord_report

    entry = _load_exe_entry()
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    stopped = {"ok": False, "status": "stopped", "stopped_at": long_ago}
    monkeypatch.setattr(runtime, "service_status", lambda _config, _role: stopped)
    sent: list[tuple] = []
    monkeypatch.setattr(discord_report, "send_watchdog_alert", lambda *args, **_k: sent.append(args))

    assert entry.watchdog_once({"app": {"runtime_dir": str(tmp_path)}}) == 0
    assert len(sent) == 2  # live + backfill
    assert all(args[1].endswith("_stopped_reminder") for args in sent)
    assert all(args[2]["risk"] == "LOW" for args in sent)


def test_watchdog_restarts_a_hung_engine_only_after_a_second_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dp_program.engine import runtime
    from dp_program.util import discord_report

    entry = _load_exe_entry()
    hung = {"ok": False, "status": "running", "process_alive": True, "heartbeat_age_seconds": 1200}
    monkeypatch.setattr(runtime, "service_status", lambda _config, _role: hung)
    monkeypatch.setattr(discord_report, "send_watchdog_alert", lambda *_a, **_k: None)
    restarts: list[dict] = []
    monkeypatch.setattr(entry, "restart_engine", lambda **kwargs: restarts.append(kwargs) or 0)
    config = {"app": {"runtime_dir": str(tmp_path)}}

    assert entry.watchdog_once(config) == 1
    assert restarts == []  # lan dau chi canh bao, co the chi la thoang qua
    assert entry.watchdog_once(config) == 1
    assert len(restarts) == 1  # van treo o lan hai -> moi can thiep


def test_watchdog_auto_restart_stops_after_the_cap(tmp_path: Path) -> None:
    """Phanh: engine hong dai phai ket thuc bang 1 canh bao, khong phai loop."""
    entry = _load_exe_entry()
    allowed = [entry._auto_restart_allowed(tmp_path) for _ in range(entry._MAX_AUTO_RESTARTS + 1)]
    assert allowed == [True] * entry._MAX_AUTO_RESTARTS + [False]


def test_restart_refuses_to_stop_the_engine_when_config_is_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config hong khong duoc phep giet mat tien trinh tot dang chay."""
    import dp_program.__main__ as cli
    from dp_program.engine import runtime

    entry = _load_exe_entry()
    monkeypatch.setattr(cli, "main", lambda _argv: 1)  # doctor truot
    monkeypatch.setattr(
        runtime, "request_stop",
        lambda *_a, **_k: pytest.fail("khong duoc dung engine khi tien kiem chua dat"),
    )
    code = entry.restart_engine(
        config=_enabled_config(live=True, backfill=True, runtime_dir=tmp_path)
    )
    assert code == 1


def _load_ops_doctor():
    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts" / "windows"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "dp_program_ops_doctor", scripts / "dp_program_ops_doctor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ops_doctor_only_claims_tasks_that_belong_to_dp_program() -> None:
    """Thu muc Task Scheduler con chua chuong trinh khac; khong duoc vo doan."""
    ops = _load_ops_doctor()
    dp_task = {"Execute": "C:/run_dp/dp_program.exe", "Args": "--run"}
    other = {"Execute": "C:/tick_program/run_tick/tick_program.exe", "Args": "--watchdog"}
    assert ops._is_dp_task(dp_task) is True
    assert ops._is_dp_task(other) is False


def test_ops_doctor_checks_script_paths_inside_arguments(tmp_path: Path) -> None:
    """Execute hop le (python.exe) van co the tro vao script da doi cho."""
    ops = _load_ops_doctor()
    present = tmp_path / "some_job.py"
    present.write_text("", encoding="ascii")
    missing = tmp_path / "khong_ton_tai.py"

    ok_task = {"Execute": sys.executable, "Args": f'-B "{present}" --window-hours 24'}
    broken = {"Execute": sys.executable, "Args": f'-B "{missing}" --window-hours 24'}
    assert ops._missing_paths(ok_task) == []
    assert ops._missing_paths(broken) == [str(missing)]


def test_run_dp_example_config_matches_current_schema() -> None:
    run_dp = Path(__file__).resolve().parents[2] / "run_dp"
    example_config = (run_dp / "config.example.yaml").read_text(encoding="utf-8")
    assert "sql_server:" in example_config
    assert "tradingview:" in example_config
    # The example must never carry a real secret if someone edits it in
    # place and forgets to rename it away from the tracked filename.
    assert 'auth_token: ""' in example_config


def test_run_dp_deploy_package_bundles_sql_installer_and_docs() -> None:
    root = Path(__file__).resolve().parents[1]
    # run_dp/ is a sibling of the repo root (core_program/), not a subpath
    # of it -- it is an untracked, regenerated build/deploy output folder.
    run_dp = root.parent / "run_dp"
    sql_names = {path.name for path in (run_dp / "sql").glob("*.sql")}
    canonical_names = {path.name for path in (root / "scripts" / "sql").glob("*.sql")}
    assert sql_names == canonical_names
    for name in canonical_names:
        assert (run_dp / "sql" / name).read_text(encoding="utf-8") == (
            root / "scripts" / "sql" / name
        ).read_text(encoding="utf-8")

    installer = (run_dp / "install.ps1").read_text(encoding="utf-8")
    assert "dp_program.exe" in installer
    assert "sqlcmd" in installer
    assert "ms-playwright" in installer
    assert "SEN05 DP Program Engine" in installer
    # -SqlDatabase does not exist: 01_setup_database.sql hardcodes the
    # database name itself, so a parameter implying it is choosable would
    # be misleading (see the fix that removed it).
    assert "SqlDatabase" not in installer

    deploy_doc = (run_dp / "DEPLOY.md").read_text(encoding="utf-8")
    assert "install.ps1" in deploy_doc
    assert "config.example.yaml" in deploy_doc


def test_utilities_have_strict_boundaries_and_offline_chart_asset() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src" / "util").exists()
    utility_python = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "src" / "dp_program" / "util").rglob("*.py")
    )
    assert utility_python == [
        "src/dp_program/util/chart/server.py",
        "src/dp_program/util/discord_report.py",
        "src/dp_program/util/redis_publisher.py",
    ]
    for relative in utility_python:
        # redis_publisher.py nhung hai script Lua (~90 dong du lieu, khong
        # phai nhanh dieu khien) nen dung cung han muc 460 nhu sql_connector.py.
        limit = 460 if relative.endswith("redis_publisher.py") else 300
        assert _code_line_count(root / relative) <= limit, relative

    imports = {
        path.relative_to(root).as_posix()
        for path in (root / "src" / "dp_program").rglob("*.py")
        if "util.discord_report" in path.read_text(encoding="utf-8")
    }
    assert imports == {"src/dp_program/engine/runtime.py"}

    redis_imports = {
        path.relative_to(root).as_posix()
        for path in (root / "src" / "dp_program").rglob("*.py")
        if "util.redis_publisher" in path.read_text(encoding="utf-8")
    }
    assert redis_imports == {
        "src/dp_program/engine/runtime.py", "src/dp_program/engine/live.py",
    }

    from dp_program.util.chart import server

    asset = root / "src" / "dp_program" / "util" / "chart" / "lightweight-charts.js"
    assert asset.stat().st_size > 100_000
    assert "/assets/lightweight-charts.js" in server.PAGE
    assert "https://" not in server.PAGE
    assert "http://" not in server.PAGE
