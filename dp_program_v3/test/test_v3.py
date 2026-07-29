from __future__ import annotations

import base64
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from dp_program import __main__ as cli
from dp_program import configuration, log
from dp_program.engine import (
    auth,
    backfill,
    live,
    select_pairs,
    pipeline,
    runtime,
    spool,
    sql_connector,
    websocket,
)


def _symbol() -> dict:
    return {"symbol_id": 56, "exchange": "CAPITALCOM", "symbol": "GOLD"}


def _timeframe() -> dict:
    return {
        "code": "M5",
        "interval": "5",
        "minutes": 5,
        "staging_table": "SEN.TF_M5",
        "default_bars": 100,
    }


def _empty_fetch_results(
    requests: list[websocket.FetchRequest],
) -> dict[tuple[int, str], websocket.FetchResult]:
    return {
        websocket.request_key(request): websocket.FetchResult(
            [], request.bars, 0
        )
        for request in requests
    }


class _FakeConnection:
    """Stand in for a pooled SQL connection without touching real SQL Server."""

    def close(self) -> None:
        pass


def _candle(timestamp: datetime, close: str = "101") -> dict:
    return {
        "symbol_id": 56,
        "exchange": "CAPITALCOM",
        "symbol": "GOLD",
        "timeframe": "M5",
        "timestamp": timestamp,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal(close),
        "volume": Decimal("12.5"),
    }


def test_config_loads_full_reviewed_universe() -> None:
    config = configuration.load_config()
    assert len(config["data"]["symbols"]) == 37
    assert sum(symbol["live"] for symbol in config["data"]["symbols"]) == 11
    assert len(config["data"]["timeframes"]) == 15
    assert config["sql_server"]["contract_version"] == "4"
    tv = config["tradingview"]
    worst_tv_retry = tv["timeout_seconds"] * tv["retry_count"]
    worst_tv_retry += tv["retry_delay_seconds"] * (tv["retry_count"] - 1)
    assert config["service"]["backfill_guard_seconds"] > worst_tv_retry
    live_period = config["live"]["interval_minutes"] * 60
    assert live_period - config["service"]["backfill_guard_seconds"] >= worst_tv_retry


def test_config_rejects_static_contract_override(tmp_path) -> None:
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "Config.example.yaml"
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source["data"] = {"timeframes": []}
    path = tmp_path / "Config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(configuration.ConfigError, match="owned by configuration.py"):
        configuration.load_config(path)


def test_config_rejects_technical_override(tmp_path) -> None:
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "Config.example.yaml"
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source["live"]["bars_per_request"] = 99
    path = tmp_path / "Config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(configuration.ConfigError, match="owned by configuration.py"):
        configuration.load_config(path)


def test_config_rejects_unbounded_rolling_window(tmp_path) -> None:
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "Config.example.yaml"
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source["backfill"]["lookback_days"] = 70
    path = tmp_path / "Config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(configuration.ConfigError, match="cannot cover lookback_days"):
        configuration.load_config(path)


def test_framing_handles_heartbeat_between_messages() -> None:
    first = websocket.frame_message("one", [1])
    second = websocket.frame_message("two", [2])
    packets = websocket.split_messages(first + "~h~123" + second)
    assert json.loads(packets[0])["m"] == "one"
    assert packets[1] == "~h~123"
    assert json.loads(packets[2])["m"] == "two"


def test_auth_requires_account_identity_and_valid_expiry() -> None:
    assert auth.token_seconds_remaining(_jwt(600)) > 590
    assert auth._authenticated(_jwt(600), 60)
    assert not auth._authenticated(_jwt(600, user_id=0), 60)
    assert not auth._authenticated("unauthorized_user_token", 60)


def test_auth_fails_closed_when_no_account_session(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(auth_token="", cookie="", username="", password="")

    def fail(_config):
        raise auth.AuthError("no authenticated session")

    monkeypatch.setattr(auth, "_refresh", fail)
    with pytest.raises(auth.AuthError, match="no authenticated session"):
        auth.ensure_authenticated(config, force=True)


def test_forced_auth_refresh_does_not_hide_failure_behind_old_token(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"]["auth_token"] = _jwt(3600)

    def fail(_config):
        raise auth.AuthError("forced refresh failed")

    monkeypatch.setattr(auth, "_refresh", fail)
    with pytest.raises(auth.AuthError, match="forced refresh failed"):
        auth.ensure_authenticated(config, force=True)


def test_fetch_authenticates_before_opening_socket(monkeypatch) -> None:
    config = configuration.load_config()
    token = _jwt(3600)

    def authenticated(target, force=False):
        target["tradingview"]["auth_token"] = token
        return {"token": token, "cookie": "sessionid=test", "source": "test"}

    monkeypatch.setattr(websocket, "ensure_authenticated", authenticated)
    monkeypatch.setattr(
        websocket,
        "_fetch_batch_once",
        lambda _tv, requests, _cap: (
            _empty_fetch_results(requests),
            {"connect_seconds": 0.0, "received_bytes": 0},
        ),
    )
    assert websocket.fetch_candles(config, _symbol(), _timeframe(), 3) == []
    assert config["tradingview"]["auth_token"] == token


def test_websocket_collects_closed_socket_cycles(monkeypatch) -> None:
    config = configuration.load_config()
    token = _jwt(3600)
    calls = []
    monkeypatch.setattr(
        websocket,
        "ensure_authenticated",
        lambda target: {"token": token, "cookie": "", "source": "test"},
    )
    monkeypatch.setattr(
        websocket,
        "_fetch_batch_once",
        lambda _tv, requests, _cap: (
            _empty_fetch_results(requests),
            {"connect_seconds": 0.0, "received_bytes": 0},
        ),
    )
    monkeypatch.setattr(websocket.gc, "collect", lambda: calls.append(True))
    websocket.fetch_candles(config, _symbol(), _timeframe(), 3)
    assert calls == [True]


def test_parser_normalizes_tradingview_bar_to_utc() -> None:
    values = [1_700_000_000, 100, 102, 99, 101, 12.5]
    message = {
        "m": "timescale_update",
        "p": ["cs_test", {"s1": {"s": [{"v": values}]}}],
    }
    candles = websocket.parse_series_message(message, _symbol(), _timeframe())
    assert len(candles) == 1
    assert candles[0]["timestamp"].tzinfo == timezone.utc
    assert candles[0]["close"] == Decimal("101")


def test_validation_deduplicates_and_drops_only_open_candles() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    closed = _candle(now - timedelta(minutes=10))
    corrected = _candle(now - timedelta(minutes=10), close="101.5")
    open_candle = _candle(now - timedelta(minutes=2))
    result = pipeline.validate_candles(
        [closed, corrected, open_candle],
        _timeframe(),
        closed_only=True,
        now=now,
    )
    assert len(result) == 1
    assert result[0]["close"] == Decimal("101.5")


def test_validation_rejects_invalid_candle_instead_of_advancing() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    invalid = {**_candle(now - timedelta(minutes=20)), "high": Decimal("98")}
    with pytest.raises(pipeline.CandleValidationError, match="OHLC bounds"):
        pipeline.validate_candles(
            [invalid],
            _timeframe(),
            closed_only=True,
            now=now,
        )


def test_sql_parameter_rows_use_utc_naive_datetime_and_fixed_scale_text() -> None:
    timestamp = datetime(2026, 7, 27, 10, 5, 22, 123, tzinfo=timezone.utc)
    row = sql_connector.prepare_rows([_candle(timestamp)])[0]
    assert row[0] == 56
    assert row[1] == datetime(2026, 7, 27, 10, 5, 22)
    assert row[2:6] == (
        "100.00000000",
        "102.00000000",
        "99.00000000",
        "101.00000000",
    )
    assert row[6] == "12.5000"


def test_sql_parameter_rows_round_to_declared_sql_scale() -> None:
    candle = {
        **_candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc)),
        "close": Decimal("101.123456789"),
        "volume": Decimal("12.34567"),
    }
    row = sql_connector.prepare_rows([candle])[0]

    assert row[5] == "101.12345679"
    assert row[6] == "12.3457"


def test_prepare_rows_reuses_a_precomputed_signature_without_recomputing(
    monkeypatch,
) -> None:
    candle = _candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))
    candle["_signature"] = ("stub-open", "stub-high", "stub-low", "stub-close", "stub-vol")
    monkeypatch.setattr(
        sql_connector,
        "candle_signature",
        lambda _c: pytest.fail("signature must not be recomputed when already present"),
    )

    row = sql_connector.prepare_rows([candle])[0]

    assert row[2:] == ("stub-open", "stub-high", "stub-low", "stub-close", "stub-vol")


def _jwt(ttl: int, user_id: int = 123) -> str:
    def encoded(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encoded({'alg': 'none'})}.{encoded({'exp': time.time() + ttl, 'user_id': user_id})}."


def test_sql_result_reader_skips_non_query_result_sets() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.index = 0
            self.descriptions = [None, [("UpdatedRows",), ("InsertedRows",), ("AffectedRows",)]]

        @property
        def description(self):
            return self.descriptions[self.index]

        def fetchone(self):
            if self.description is None:
                raise RuntimeError("No results. Previous SQL was not a query.")
            return (1, 2, 3)

        def nextset(self):
            self.index += 1
            return self.index < len(self.descriptions)

    assert sql_connector._fetch_result_row(Cursor(), "test operation") == (1, 2, 3)


def test_sql_commits_bootstrap_state_atomically_with_fact(monkeypatch) -> None:
    config = configuration.load_config()
    statements = []

    class Cursor:
        fast_executemany = False

        def execute(self, statement, *params):
            statements.append((statement, params))
            return self

        def executemany(self, statement, rows):
            statements.append((statement, list(rows)))

    class Connection:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    connection = Connection()
    results = iter(((1, 0), (0, 1, 1)))
    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: connection)
    monkeypatch.setattr(sql_connector, "_require_contract", lambda *_args: None)
    monkeypatch.setattr(
        sql_connector, "_fetch_result_row", lambda *_args: next(results)
    )

    result = sql_connector.bulk_upsert_candles(
        config,
        _timeframe(),
        [_candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))],
        complete_bootstrap=True,
    )

    sql = "\n".join(statement for statement, _params in statements)
    assert "MERGE [SEN].[DP_BackfillState]" in sql
    assert "BootstrapCompletedAt=SYSUTCDATETIME()" in sql
    assert sql.index("Fact verification failed") < sql.index(
        "MERGE [SEN].[DP_BackfillState]"
    )
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert result["fact_inserted"] == 1


def test_sql_zero_delta_bootstrap_still_updates_policy_state(monkeypatch) -> None:
    config = configuration.load_config()
    statements = []

    class Cursor:
        def execute(self, statement, *params):
            statements.append((statement, params))
            return self

    class Connection:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: connection)
    monkeypatch.setattr(sql_connector, "_require_contract", lambda *_args: None)
    result = sql_connector.bulk_upsert_candles(
        config,
        _timeframe(),
        [],
        complete_bootstrap=True,
        symbol_id=56,
    )

    sql = "\n".join(statement for statement, _params in statements)
    assert "MERGE [SEN].[DP_BackfillState]" in sql
    assert "CREATE TABLE #V3Candles" not in sql
    assert result["input"] == 0
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_bulk_upsert_reuses_a_shared_connection_without_temp_table_collision(
    monkeypatch,
) -> None:
    """A reused connection keeps #V3Candles from a prior call in the same SQL
    session; the CREATE must drop any leftover copy first or the second
    delivery in a group fails with 'already an object named #V3Candles'."""
    config = configuration.load_config()
    session = {"v3candles_exists": False}

    class Cursor:
        fast_executemany = False

        def execute(self, statement, *_params):
            if "DROP TABLE #V3Candles" in statement:
                session["v3candles_exists"] = False
            if "CREATE TABLE #V3Candles" in statement:
                if session["v3candles_exists"]:
                    raise RuntimeError(
                        "There is already an object named '#V3Candles'"
                    )
                session["v3candles_exists"] = True
            return self

        def executemany(self, _statement, _rows):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    connection = Connection()
    results = iter(((1, 0), (0, 1, 1), (1, 0), (0, 1, 1)))
    monkeypatch.setattr(sql_connector, "_require_contract", lambda *_args: None)
    monkeypatch.setattr(
        sql_connector, "_fetch_result_row", lambda *_args: next(results)
    )

    for _ in range(2):
        sql_connector.bulk_upsert_candles(
            config,
            _timeframe(),
            [_candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))],
            connection=connection,
        )


def test_pair_state_temp_table_uses_database_collation(monkeypatch) -> None:
    config = configuration.load_config()
    statements = []

    class Cursor:
        fast_executemany = False

        def execute(self, statement, *params):
            statements.append(statement)
            return self

        def executemany(self, statement, rows):
            statements.append(statement)
            assert list(rows) == [(56, "M5")]

        def fetchall(self):
            return [(56, "M5", None, datetime(2026, 7, 28, 3, 55))]

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(
        sql_connector, "get_connection", lambda _config: Connection()
    )
    states = sql_connector.get_pair_states(
        config,
        [(_symbol(), _timeframe())],
    )
    assert "COLLATE DATABASE_DEFAULT" in statements[0]
    assert states[(56, "M5")]["latest"] == datetime(2026, 7, 28, 3, 55)


def test_bootstrap_state_failure_rolls_back_fact_transaction(monkeypatch) -> None:
    config = configuration.load_config()

    class Cursor:
        fast_executemany = False

        def execute(self, statement, *params):
            if "MERGE [SEN].[DP_BackfillState]" in statement:
                raise RuntimeError("state write failed")
            return self

        def executemany(self, _statement, _rows):
            pass

    class Connection:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    connection = Connection()
    results = iter(((1, 0), (0, 1, 1)))
    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: connection)
    monkeypatch.setattr(sql_connector, "_require_contract", lambda *_args: None)
    monkeypatch.setattr(
        sql_connector, "_fetch_result_row", lambda *_args: next(results)
    )

    with pytest.raises(RuntimeError, match="state write failed"):
        sql_connector.bulk_upsert_candles(
            config,
            _timeframe(),
            [_candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))],
            complete_bootstrap=True,
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_spool_replays_and_acks_only_after_sql_commit(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    candle = _candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))
    spool.enqueue(config, [candle])
    assert spool.pending_status(config)["pending"] == 1
    delivered = []

    def write(_config, timeframe, candles):
        delivered.extend(candles)
        return {"affected": len(candles)}

    monkeypatch.setattr(spool, "bulk_upsert_candles", write)
    assert spool.drain(config) == {"examined": 1, "delivered": 1, "failed": 0}
    assert delivered[0]["timestamp"] == candle["timestamp"]
    assert spool.pending_status(config)["pending"] == 0


def test_spool_replays_each_symbol_as_an_independent_sql_batch(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    timestamp = datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc)
    candles = [
        _candle(timestamp),
        {**_candle(timestamp), "symbol_id": 2, "symbol": "FR40"},
    ]
    spool.enqueue(config, candles)
    batches = []

    def write(_config, _timeframe, values):
        batches.append({item["symbol_id"] for item in values})
        return {"affected": len(values)}

    monkeypatch.setattr(spool, "bulk_upsert_candles", write)
    assert spool.drain(config)["delivered"] == 2
    assert batches == [{2}, {56}]


def test_spool_ack_retries_a_transient_windows_file_lock(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    candle = _candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))
    spool.enqueue(config, [candle])
    original = Path.unlink
    attempts = []

    def transient(path, *args, **kwargs):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("temporarily locked")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient)
    assert spool.ack(config, [candle]) == 1
    assert len(attempts) == 2


def test_runtime_lock_rejects_a_second_writer(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    lock_path = tmp_path / "run" / "engine.lock"
    with runtime.instance_lock(config):
        with pytest.raises(RuntimeError, match="already running"):
            with runtime.instance_lock(config):
                pass
        assert lock_path.stat().st_size == 32
    with runtime.instance_lock(config):
        pass
    assert lock_path.stat().st_size == 32


def test_service_failure_state_is_durable_and_secret_free(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    runtime.record_service_failure(config, RuntimeError("password=must-not-persist"))
    state = json.loads(
        (tmp_path / "run" / "state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert state["error_type"] == "RuntimeError"
    assert "error" not in state
    assert "must-not-persist" not in json.dumps(state)
    assert runtime.service_status(config)["ok"] is False


def test_runtime_state_write_retries_a_transient_windows_file_lock(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    original = Path.replace
    attempts = []

    def transient(path, target):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("state reader temporarily holds the destination")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", transient)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    runtime._write_state(config, {"status": "running", "pid": 123})
    state = json.loads(
        (tmp_path / "run" / "state.json").read_text(encoding="utf-8")
    )
    assert len(attempts) == 3
    assert state["status"] == "running"
    assert state["pid"] == 123
    assert state["heartbeat_at"]


def test_runtime_state_write_fails_closed_after_bounded_retries(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    runtime._write_state(config, {"status": "old"})
    attempts = []

    def locked(_path, _target):
        attempts.append(1)
        raise PermissionError("destination remains locked")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(runtime, "_STATE_WRITE_ATTEMPTS", 3)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="remains locked"):
        runtime._write_state(config, {"status": "new"})
    persisted = json.loads(
        (tmp_path / "run" / "state.json").read_text(encoding="utf-8")
    )
    assert len(attempts) == 3
    assert persisted["status"] == "old"


@pytest.mark.skipif(runtime.sys.platform != "win32", reason="Windows file sharing")
def test_runtime_state_write_survives_a_real_open_reader(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    runtime._write_state(config, {"status": "old"})
    path = tmp_path / "run" / "state.json"
    reader = path.open("r", encoding="utf-8")
    release = threading.Timer(0.15, reader.close)
    release.start()
    try:
        runtime._write_state(config, {"status": "new"})
    finally:
        reader.close()
        release.join()
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "new"


def test_startup_database_readiness_retries_until_sql_is_available(
    monkeypatch,
) -> None:
    config = configuration.load_config()
    attempts = []
    expected = {"ok": True, "database": "SEN", "bootstrap_remaining_pairs": 0}

    def check(_config):
        attempts.append(True)
        if len(attempts) == 1:
            raise ConnectionError("SQL is still starting")
        return expected

    monkeypatch.setattr(runtime, "check_connection", check)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    assert runtime._wait_for_database(config) == expected
    assert len(attempts) == 2


def test_starting_state_replaces_stale_generation_before_readiness(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["discord"]["enabled"] = False
    runtime._write_state(
        config,
        {
            "status": "running",
            "pid": 999,
            "started_at": "2026-07-28T05:00:00+00:00",
            "last_live": {"ok": 165},
        },
    )
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(
        runtime,
        "_service_pairs",
        lambda _config: ([(_symbol(), _timeframe())], []),
    )
    monkeypatch.setattr(
        runtime,
        "ensure_authenticated",
        lambda _config: (_ for _ in ()).throw(auth.AuthError("unavailable")),
    )

    with pytest.raises(auth.AuthError, match="unavailable"):
        runtime.run_service(config)
    runtime.record_service_failure(config, auth.AuthError("unavailable"))
    state = json.loads(
        (tmp_path / "run" / "state.json").read_text(encoding="utf-8")
    )

    assert state["status"] == "failed"
    assert state["pid"] != 999
    assert state["started_at"] != "2026-07-28T05:00:00+00:00"
    assert "last_live" not in state


def test_auth_retry_cooldown_survives_a_process_restart(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(
        auth_token="", cookie="", username="", password="", two_factor_secret=""
    )
    auth._write_cache(config, {"retry_after": time.time() + 300})
    monkeypatch.setattr(auth, "_NEXT_REFRESH_ATTEMPT", 0.0)
    monkeypatch.setattr(
        auth, "_refresh", lambda *_a: pytest.fail("persistent cooldown was bypassed")
    )
    with pytest.raises(auth.AuthError, match="retry cooldown"):
        auth.ensure_authenticated(config)


def test_auth_failure_persists_the_next_retry_deadline(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(
        auth_token="", cookie="", username="", password="", two_factor_secret=""
    )
    monkeypatch.setattr(auth, "_NEXT_REFRESH_ATTEMPT", 0.0)
    monkeypatch.setattr(
        auth,
        "_refresh",
        lambda *_a: (_ for _ in ()).throw(auth.AuthError("unavailable")),
    )
    before = time.time()
    with pytest.raises(auth.AuthError, match="unavailable"):
        auth.ensure_authenticated(config)
    cache = json.loads(
        (tmp_path / "cache" / "tradingview_auth.json").read_text(encoding="utf-8")
    )
    assert cache["retry_after"] >= before + 590


def test_backfill_request_uses_durable_bootstrap_then_rolling_window() -> None:
    config = configuration.load_config()
    timeframe = _timeframe()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    latest = now - timedelta(minutes=30)
    bootstrap = backfill.plan_backfill(
        config,
        _symbol(),
        timeframe,
        {"bootstrap_complete": False, "latest": latest},
        now=now,
    )
    rolling = backfill.plan_backfill(
        config,
        _symbol(),
        timeframe,
        {"bootstrap_complete": True, "latest": latest},
        now=now,
    )
    exact_bootstrap_bars = (
        60 * 24 * 12 + config["backfill"]["overlap_bars"]
    )
    assert bootstrap.window_start == now - timedelta(days=60)
    assert bootstrap.bars == (exact_bootstrap_bars * 3 + 3) // 4
    assert bootstrap.max_bars == exact_bootstrap_bars
    assert rolling.window_start == latest - timedelta(minutes=10)
    assert rolling.bars < 20
    assert rolling.max_bars == rolling.bars


def test_live_tail_cannot_skip_full_bootstrap(monkeypatch) -> None:
    config = configuration.load_config()
    latest = datetime(2026, 7, 27, 11, 55, tzinfo=timezone.utc)
    observed = {}
    requests = []

    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_args: {
            (56, "M5"): {"bootstrap_complete": False, "latest": latest}
        },
    )

    def fetch(_config, _symbol, timeframe, **kwargs):
        observed.update(timeframe=timeframe, **kwargs)
        return {"affected": 0}

    def fetch_batch(_config, planned):
        requests.extend(planned)
        return _empty_fetch_results(planned)

    monkeypatch.setattr(backfill, "fetch_candles_batch", fetch_batch)
    monkeypatch.setattr(backfill, "get_connection", lambda _config: _FakeConnection())
    monkeypatch.setattr(backfill, "fetch_and_store", fetch)
    result = backfill.run_backfill_pairs(
        config,
        [(_symbol(), _timeframe())],
        now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
    )

    assert result["failed"] == 0
    exact_bootstrap_bars = 60 * 24 * 12 + 3
    assert len(requests) == 1
    assert requests[0].bars == (exact_bootstrap_bars * 3 + 3) // 4
    assert requests[0].max_bars == exact_bootstrap_bars
    assert observed["bars"] == requests[0].bars
    assert observed["complete_bootstrap"] is True


def test_manual_bar_override_does_not_complete_bootstrap(monkeypatch) -> None:
    config = configuration.load_config()
    observed = {}
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_args: {(56, "M5"): {"bootstrap_complete": False, "latest": None}},
    )

    def fetch(_config, _symbol, _timeframe, **kwargs):
        observed.update(kwargs)
        return {"affected": 0}

    monkeypatch.setattr(
        backfill,
        "fetch_candles_batch",
        lambda _config, requests: _empty_fetch_results(requests),
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _config: _FakeConnection())
    monkeypatch.setattr(backfill, "fetch_and_store", fetch)
    backfill.run_backfill_pairs(
        config,
        [(_symbol(), _timeframe())],
        bars_override=20,
    )

    assert observed["bars"] == 20
    assert observed["complete_bootstrap"] is False


def test_pair_selection_keeps_forex_historical_only() -> None:
    config = configuration.load_config()
    live_pairs = select_pairs(config, live=True, timeframe_filter="M5")
    historical_pairs = select_pairs(
        config, live=False, timeframe_filter="M5"
    )
    assert len(live_pairs) == 11
    assert len(historical_pairs) == 37
    assert all(symbol["asset_type"] != "FOREX" for symbol, _ in live_pairs)


def test_service_pair_plan_respects_workflow_enabled_flags() -> None:
    config = configuration.load_config()
    config["live"]["enabled"] = False
    live_pairs, backfill_pairs = runtime._service_pairs(config)
    assert live_pairs == []
    assert len(backfill_pairs) == 37 * 15

    config["live"]["enabled"] = True
    config["backfill"]["enabled"] = False
    live_pairs, backfill_pairs = runtime._service_pairs(config)
    assert len(live_pairs) == 11 * 15
    assert backfill_pairs == []

    config["live"]["enabled"] = False
    with pytest.raises(RuntimeError, match="both live and backfill"):
        runtime._service_pairs(config)


def test_manual_live_command_runs_one_finite_pipeline_cycle(monkeypatch) -> None:
    config = configuration.load_config()
    observed = {}

    def run(target, pairs, **kwargs):
        observed.update(target=target, pairs=len(pairs), pending=kwargs["pending_pairs"])
        return {"pairs": len(pairs), "ok": len(pairs), "failed": 0, "affected": 0}

    monkeypatch.setattr(live, "run_live_pairs", run)
    result = live.run_live_cycle(config, timeframe="M5")
    assert result == {"pairs": 11, "ok": 11, "failed": 0, "affected": 0}
    assert observed["target"] == config
    assert observed["pairs"] == 11
    assert len(observed["pending"]) == 11

    config["live"]["enabled"] = False
    with pytest.raises(RuntimeError, match="live fetching is disabled"):
        live.run_live_cycle(config)


def test_log_event_is_queryable_and_masks_secrets(caplog, monkeypatch) -> None:
    monkeypatch.setattr(log.os, "getpid", lambda: 4321)
    logger = logging.getLogger("dp_program.test")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log.log_event(
            logger,
            logging.ERROR,
            "PAIR_FAILED",
            "HIGH",
            component="pipeline",
            workflow="live",
            stage="sql_delivery",
            error=RuntimeError(
                "password=hunter2 Authorization: Bearer eyJabc.def.ghi\nconnection lost"
            ),
            auth_token="must-not-appear",
            action="transaction rolled back; candles retained in spool",
        )

    record = caplog.records[-1]
    assert record.levelno == logging.ERROR
    assert "component=pipeline event=PAIR_FAILED risk=HIGH pid=4321" in record.message
    assert "workflow=live stage=sql_delivery" in record.message
    assert 'action="transaction rolled back; candles retained in spool"' in record.message
    assert "\n" not in record.message
    assert "hunter2" not in record.message
    assert "eyJabc" not in record.message
    assert "must-not-appear" not in record.message
    assert "[REDACTED]" in record.message


def test_pair_logging_uses_debug_for_live_success_and_reports_failure_stage(
    tmp_path, monkeypatch, caplog
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    result = {
        "affected": 0,
        "fact_inserted": 0,
        "fact_updated": 0,
        "skipped": 0,
    }
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_args: [])
    monkeypatch.setattr(pipeline, "enqueue", lambda *_args: 0)
    monkeypatch.setattr(pipeline, "ack", lambda *_args: 0)
    monkeypatch.setattr(pipeline, "bulk_upsert_candles", lambda *_args, **_kwargs: result)

    with caplog.at_level(logging.DEBUG, logger=pipeline.LOGGER.name):
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="live",
            bars=3,
        )
    success = caplog.records[-1]
    assert success.levelno == logging.DEBUG
    assert "event=PAIR_COMPLETED" in success.message
    assert "workflow=live" in success.message

    caplog.clear()
    monkeypatch.setattr(
        live,
        "get_pair_states",
        lambda *_args: {
            (56, "M5"): {
                "latest": datetime(2026, 7, 27, 11, 55, tzinfo=timezone.utc)
            }
        },
    )
    monkeypatch.setattr(
        live,
        "fetch_and_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            pipeline.PipelineError("sql_delivery", RuntimeError("SQL unavailable"))
        ),
    )
    monkeypatch.setattr(
        live,
        "fetch_candles_batch",
        lambda _config, requests: _empty_fetch_results(requests),
    )
    monkeypatch.setattr(live, "get_connection", lambda _config: _FakeConnection())
    with caplog.at_level(logging.ERROR, logger=pipeline.LOGGER.name):
        summary = live.run_live_pairs(config, [(_symbol(), _timeframe())])
    assert summary["failed"] == 1
    failure = caplog.records[-1]
    assert "event=PAIR_FAILED" in failure.message
    assert "risk=HIGH" in failure.message
    assert "stage=sql_delivery" in failure.message
    assert 'action="transaction rolled back; candles retained in spool"' in failure.message


def test_runtime_logs_one_info_summary_for_a_live_cycle(
    tmp_path, monkeypatch, caplog
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["discord"]["enabled"] = False
    pair = (_symbol(), _timeframe())
    stops = iter((False, False, True))
    monkeypatch.setattr(runtime.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(runtime, "_service_pairs", lambda _config: ([pair], []))
    monkeypatch.setattr(
        runtime, "ensure_authenticated", lambda _config: {"source": "runtime_cache"}
    )
    monkeypatch.setattr(
        runtime, "auth_status", lambda _config: {"ok": True, "state": "authenticated"}
    )
    monkeypatch.setattr(
        runtime,
        "check_connection",
        lambda _config: {
            "ok": True,
            "database": "test",
            "bootstrap_remaining_pairs": 0,
        },
    )
    monkeypatch.setattr(
        runtime, "drain", lambda _config: {"examined": 0, "delivered": 0, "failed": 0}
    )
    monkeypatch.setattr(
        runtime,
        "pending_status",
        lambda _config: {
            "pending": 0,
            "corrupt": 0,
            "bytes": 0,
            "oldest_age_seconds": None,
        },
    )
    monkeypatch.setattr(
        runtime,
        "run_live_pairs",
        lambda *_args, **_kwargs: {
            "pairs": 165,
            "ok": 165,
            "failed": 0,
            "deferred": 0,
            "affected": 12,
            "pending_pairs": [],
            "recovered_pairs": [],
        },
    )
    monkeypatch.setattr(runtime, "_stop_requested", lambda _config: next(stops))

    with caplog.at_level(logging.INFO, logger=runtime.LOGGER.name):
        assert runtime.run_service(config)["ok"] is True
    messages = [record.message for record in caplog.records]
    assert sum("event=SERVICE_STARTED" in message for message in messages) == 1
    assert sum("event=LIVE_CYCLE_COMPLETED" in message for message in messages) == 1
    assert "pairs=165 ok=165 failed=0 deferred=0 affected=12 spool_pending=0" in next(
        message for message in messages if "event=LIVE_CYCLE_COMPLETED" in message
    )
    assert sum("event=SERVICE_STOPPED" in message for message in messages) == 1


def test_runtime_dequeues_and_counts_a_multi_pair_backfill_group(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["discord"]["enabled"] = False
    second_timeframe = {
        **_timeframe(),
        "code": "M10",
        "interval": "10",
        "minutes": 10,
        "staging_table": "SEN.TF_M10",
    }
    pairs = [(_symbol(), _timeframe()), (_symbol(), second_timeframe)]
    slot = "2026-07-28T11:00"
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(runtime, "_service_pairs", lambda _config: ([], pairs))
    monkeypatch.setattr(
        runtime, "ensure_authenticated", lambda _config: {"source": "runtime_cache"}
    )
    monkeypatch.setattr(
        runtime, "auth_status", lambda _config: {"ok": True, "state": "authenticated"}
    )
    monkeypatch.setattr(
        runtime,
        "check_connection",
        lambda _config: {
            "ok": True,
            "database": "test",
            "bootstrap_remaining_pairs": 2,
        },
    )
    monkeypatch.setattr(
        runtime, "drain", lambda _config: {"examined": 0, "delivered": 0, "failed": 0}
    )
    monkeypatch.setattr(
        runtime,
        "pending_status",
        lambda _config: {
            "pending": 0,
            "corrupt": 0,
            "bytes": 0,
            "oldest_age_seconds": None,
        },
    )
    monkeypatch.setattr(
        runtime, "prioritize_backfill_pairs", lambda _config, pairs: pairs
    )
    monkeypatch.setattr(
        runtime, "_due_slot", lambda _slots, previous: "" if previous == slot else slot
    )

    selected_groups = []
    monkeypatch.setattr(
        runtime,
        "next_backfill_group",
        lambda _config, candidates: (
            selected_groups.append(list(candidates)) or list(candidates)
        ),
    )
    processed_groups = []

    def run_group(_config, group):
        processed_groups.append(list(group))
        return {
            "pairs": len(group),
            "ok": len(group),
            "failed": 0,
            "affected": 0,
            "completed_bootstraps": len(group),
            "failed_pairs": [],
            "group_failures": 0,
        }

    monkeypatch.setattr(
        runtime,
        "run_backfill_pairs",
        run_group,
    )
    stops = iter((False, False, True))
    monkeypatch.setattr(runtime, "_stop_requested", lambda _config: next(stops))

    assert runtime.run_service(config)["ok"] is True
    state = json.loads((tmp_path / "run" / "state.json").read_text(encoding="utf-8"))
    assert selected_groups == [pairs]
    assert processed_groups == [pairs]
    assert state["last_backfill_slot"] == slot
    assert state["last_backfill"]["pairs"] == 2
    assert state["backfill_generation_total"] == 2
    assert state["backfill_generation_processed"] == 2
    assert state["backfill_queue_remaining"] == 0
    assert state["bootstrap_remaining_pairs"] == 0


def test_runtime_reports_circuit_deferred_pairs_separately_from_failures(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["discord"]["enabled"] = False
    pairs = [
        ({**_symbol(), "symbol_id": symbol_id, "symbol": name}, _timeframe())
        for symbol_id, name in ((56, "GOLD"), (81, "BTCUSD"), (8, "US500"))
    ]
    processed = []
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(runtime, "_service_pairs", lambda _config: ([], pairs))
    monkeypatch.setattr(
        runtime, "ensure_authenticated", lambda _config: {"source": "runtime_cache"}
    )
    monkeypatch.setattr(
        runtime, "auth_status", lambda _config: {"ok": True, "state": "authenticated"}
    )
    monkeypatch.setattr(
        runtime,
        "check_connection",
        lambda _config: {
            "ok": True,
            "database": "test",
            "bootstrap_remaining_pairs": 3,
        },
    )
    monkeypatch.setattr(
        runtime, "drain", lambda _config: {"examined": 0, "delivered": 0, "failed": 0}
    )
    monkeypatch.setattr(
        runtime,
        "pending_status",
        lambda _config: {
            "pending": 0,
            "corrupt": 0,
            "bytes": 0,
            "oldest_age_seconds": None,
        },
    )
    monkeypatch.setattr(runtime, "prioritize_backfill_pairs", lambda _c, value: value)
    monkeypatch.setattr(runtime, "_due_slot", lambda *_a: "")
    monkeypatch.setattr(
        runtime, "next_backfill_group", lambda _config, candidates: [candidates[0]]
    )

    def fail_group(_config, group):
        key = f"CAPITALCOM:{group[0][0]['symbol']}/M5"
        processed.append(key)
        return {
            "pairs": 1,
            "ok": 0,
            "failed": 1,
            "affected": 0,
            "completed_bootstraps": 0,
            "failed_pairs": [key],
            "group_failures": 1,
        }

    monkeypatch.setattr(runtime, "run_backfill_pairs", fail_group)
    monkeypatch.setattr(
        runtime, "_stop_requested", lambda _config: len(processed) >= 2
    )

    assert runtime.run_service(config)["ok"] is True
    state = json.loads((tmp_path / "run" / "state.json").read_text(encoding="utf-8"))
    assert set(state["backfill_failed_pairs"]) == set(processed)
    assert state["backfill_deferred_pairs"] == ["CAPITALCOM:US500/M5"]
    assert state["backfill_generation_processed"] == 2
    assert state["backfill_generation_deferred"] == 1
    assert state["backfill_circuit_open"] is True


def test_runtime_honors_a_durable_stop_before_authentication(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["discord"]["enabled"] = False
    stop_path = tmp_path / "run" / "stop.request"
    stop_path.parent.mkdir(parents=True)
    stop_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(
        runtime,
        "_service_pairs",
        lambda _config: pytest.fail("stop marker must be honored before startup work"),
    )
    result = runtime.run_service(config)
    state = json.loads((tmp_path / "run" / "state.json").read_text(encoding="utf-8"))
    assert result["status"] == state["status"] == "stopped"
    assert stop_path.exists()


def test_cli_records_a_terminal_service_failure(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    observed: list[str] = []
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "configure_logging", lambda _config: None)
    monkeypatch.setattr(
        cli,
        "run_service",
        lambda _config: (_ for _ in ()).throw(ValueError("fatal")),
    )
    monkeypatch.setattr(
        cli,
        "record_service_failure",
        lambda _config, error: observed.append(type(error).__name__),
    )
    monkeypatch.setattr(cli, "log_event", lambda *_a, **_k: None)
    assert cli.main(["run"]) == 1
    assert observed == ["RuntimeError"]


def test_cli_parses_expected_commands() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["backfill", "--symbol", "CAPITALCOM:GOLD", "--timeframe", "M5", "--bars", "20"]
    )
    assert args.command == "backfill"
    assert args.bars == 20
    args = parser.parse_args(["live"])
    assert args.command == "live"
    assert not hasattr(args, "once")


def test_v3_modules_do_not_import_legacy_or_read_environment() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in (root / "src" / "dp_program").rglob("*.py")
    }
    for name, source in sources.items():
        assert "core_engine" not in source
        assert "os.environ" not in source
        assert "getenv(" not in source
        assert "load_dotenv" not in source
    assert "dotenv" not in sources["src/dp_program/configuration.py"]


def test_engine_uses_src_package_layout() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "src" / "dp_program"
    assert {path.name for path in package.glob("*.py")} == {
        "__init__.py",
        "__main__.py",
        "configuration.py",
        "log.py",
    }
    engine = package / "engine"
    assert {path.name for path in engine.glob("*.py")} == {
        "__init__.py",
        "auth.py",
        "backfill.py",
        "live.py",
        "pipeline.py",
        "spool.py",
        "runtime.py",
        "sql_connector.py",
        "websocket.py",
    }
    assert len(list(engine.glob("*.py"))) == 9
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 300
        for path in package.rglob("*.py")
    )
    assert not list(root.glob("*.py"))


def test_continuous_runtime_and_version_each_have_one_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "src" / "dp_program"
    engine = package / "engine"
    pipeline_source = (engine / "pipeline.py").read_text(encoding="utf-8")
    live_source = (engine / "live.py").read_text(encoding="utf-8")
    backfill_source = (engine / "backfill.py").read_text(encoding="utf-8")
    runtime_source = (engine / "runtime.py").read_text(encoding="utf-8")
    log_source = (package / "log.py").read_text(encoding="utf-8")
    main_source = (package / "__main__.py").read_text(encoding="utf-8")
    init_source = (package / "__init__.py").read_text(encoding="utf-8")
    project_source = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert "def run_live(" not in pipeline_source
    assert "def run_live_cycle(" not in pipeline_source
    assert "def run_live_cycle(" in live_source
    assert "def run_backfill(" in backfill_source
    assert "run_service" not in live_source + backfill_source + pipeline_source
    assert "sql_connector" not in (
        engine / "websocket.py"
    ).read_text(encoding="utf-8")
    assert "spool" not in (engine / "websocket.py").read_text(encoding="utf-8")
    assert all(
        "import pyodbc" not in path.read_text(encoding="utf-8")
        for path in engine.glob("*.py")
        if path.name != "sql_connector.py"
    )
    assert "def run_service(" in runtime_source
    assert "def configure_logging(" not in runtime_source
    assert "def configure_logging(" in log_source
    assert "contract == {" not in main_source
    assert "__version__" not in init_source
    assert project_source.count("\nversion = ") == 1
    package_source = "\n".join(
        path.read_text(encoding="utf-8") for path in package.rglob("*.py")
    )
    assert package_source.count("unauthorized_user_token") == 1
    assert package_source.count("Mozilla/5.0") == 1


def test_runtime_uses_one_private_yaml_configuration() -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    template_path = root / "Config.example.yaml"
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    assert not (root / ".env.example").exists()
    assert "python-dotenv" not in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "Config.yaml" in (root / ".gitignore").read_text(encoding="utf-8")
    assert "data" not in template
    assert "tables" not in template
    assert "contract_version" not in template["sql_server"]
    assert template["backfill"]["lookback_days"] == 60
    assert "scan_bars" not in template["backfill"]
    assert set(template["live"]) == {"enabled"}
    assert set(template["service"]) == {
        "backfill_on_start",
        "backfill_schedule_utc",
    }
    assert set(template["tradingview"]) == {
        "auth_token",
        "cookie",
        "username",
        "password",
        "two_factor_secret",
    }
    for key in ("driver", "timeout_seconds", "retry_count", "batch_size"):
        assert key not in template["sql_server"]
    assert not any(
        str(key).endswith("_env")
        for section in template.values()
        if isinstance(section, dict)
        for key in section
    )
    for key in ("auth_token", "cookie", "username", "password", "two_factor_secret"):
        assert template["tradingview"][key] == ""
    for key in ("username", "password"):
        assert template["sql_server"][key] == ""
    loaded = configuration.load_config(template_path)
    assert loaded["app"]["config_path"] == str(template_path.resolve())
    assert len(loaded["data"]["symbols"]) == 37
    assert len(loaded["data"]["timeframes"]) == 15
    assert loaded["tables"]["load_procedure"] == "DWH.usp_LoadDirect"
    assert loaded["sql_server"]["contract_version"] == "4"


def test_sql_directory_contains_only_the_canonical_v3_installer() -> None:
    root = Path(__file__).resolve().parents[1]
    sql_dir = root / "scripts" / "sql"
    expected = {
        "00_run_all.sql",
        "01_setup_database.sql",
        "02_core_tables.sql",
        "03_staging_tables.sql",
        "04_business_objects.sql",
        "05_seed_symbols.sql",
        "06_verify.sql",
    }
    assert {path.name for path in sql_dir.glob("*.sql")} == expected

    runner = (sql_dir / "00_run_all.sql").read_text(encoding="utf-8")
    for name in sorted(expected - {"00_run_all.sql"}):
        assert f':r "{name}"' in runner

    deploy_sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sql_dir.glob("*.sql")
    )
    assert "DPContractVersion', @value = N'4'" in deploy_sql
    assert "DPContractVersion', @value = N'2'" not in deploy_sql
    assert "DPContractVersion', @value = N'3'" not in deploy_sql
    assert "SEN.DP_BackfillState" in deploy_sql
    assert "SEN.ActiveTask" not in deploy_sql
    assert "SEN.OHLCV_UnsupportedCalendar" not in deploy_sql
