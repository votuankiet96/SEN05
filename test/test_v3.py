from __future__ import annotations

import base64
import json
import logging
import os
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
    pipeline,
    runtime,
    spool,
    sql_connector,
    websocket,
)
from dp_program.engine import sql_connector as engine_pkg
from dp_program.engine.sql_connector import select_pairs

def _code_line_count(path: Path) -> int:
    """Count lines that carry logic, excluding blank lines and whole-line comments."""
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


_FAKE_SYMBOLS = (
    (2, "FR40", "Indice"), (3, "DE40", "Indice"), (4, "HK50", "Indice"),
    (5, "J225", "Indice"), (6, "SP35", "Indice"), (7, "UK100", "Indice"),
    (8, "US500", "Indice"), (9, "US100", "Indice"), (10, "US30", "Indice"),
    (11, "AUDCAD", "FOREX"), (12, "AUDJPY", "FOREX"), (13, "AUDNZD", "FOREX"),
    (14, "AUDCHF", "FOREX"), (15, "AUDUSD", "FOREX"), (16, "GBPAUD", "FOREX"),
    (17, "GBPCAD", "FOREX"), (18, "GBPJPY", "FOREX"), (19, "GBPNZD", "FOREX"),
    (20, "GBPCHF", "FOREX"), (21, "GBPUSD", "FOREX"), (22, "CADJPY", "FOREX"),
    (23, "CADCHF", "FOREX"), (24, "EURAUD", "FOREX"), (25, "EURGBP", "FOREX"),
    (26, "EURCAD", "FOREX"), (27, "EURJPY", "FOREX"), (28, "EURNZD", "FOREX"),
    (32, "EURCHF", "FOREX"), (33, "EURUSD", "FOREX"), (34, "NZDCAD", "FOREX"),
    (35, "NZDJPY", "FOREX"), (36, "NZDUSD", "FOREX"), (37, "USDCAD", "FOREX"),
    (41, "USDJPY", "FOREX"), (48, "USDCHF", "FOREX"), (56, "GOLD", "Metal"),
    (81, "BTCUSD", "Crypto"),
)
_FAKE_TIMEFRAMES = (
    ("M5", "5", 5), ("M10", "10", 10), ("M15", "15", 15),
    ("M20", "20", 20), ("M30", "30", 30), ("M45", "45", 45),
    ("H1", "60", 60), ("M90", "90", 90), ("H2", "120", 120),
    ("H3", "180", 180), ("H4", "240", 240), ("H6", "360", 360),
    ("H8", "480", 480), ("D1", "1D", 1440), ("W", "1W", 10080),
)

def _fake_symbol_universe() -> list[dict]:
    return [
        {"symbol_id": sid, "exchange": "CAPITALCOM", "symbol": sym,
         "asset_type": asset, "enabled": True}
        for sid, sym, asset in _FAKE_SYMBOLS
    ]


def _fake_timeframe_universe() -> list[dict]:
    return [
        {"code": code, "interval": interval, "minutes": minutes, "staging_table": f"SEN.TF_{code}"}
        for code, interval, minutes in _FAKE_TIMEFRAMES
    ]


def _mock_sql_universe(monkeypatch) -> None:
    """Stand in for the SQL symbol/timeframe reference tables (DWH.Dim_*)."""
    monkeypatch.setattr(
        engine_pkg,
        "fetch_universe",
        lambda _config: (_fake_symbol_universe(), _fake_timeframe_universe()),
    )


def _operator_config_template() -> dict:
    return {
        "app": {"log_level": "INFO", "runtime_dir": "runtime"},
        "discord": {"enabled": False, "webhook_url": ""},
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


def test_config_loads_operator_settings_without_static_universe() -> None:
    config = configuration.load_config()
    assert "data" not in config
    assert config["sql_server"]["contract_version"] == "4"
    tv = config["tradingview"]
    worst_tv_retry = tv["timeout_seconds"] * tv["retry_count"]
    worst_tv_retry += tv["retry_delay_seconds"] * (tv["retry_count"] - 1)
    assert config["service"]["backfill_guard_seconds"] > worst_tv_retry
    live_period = config["live"]["interval_minutes"] * 60
    assert live_period - config["service"]["backfill_guard_seconds"] >= worst_tv_retry


def test_config_rejects_static_contract_override(tmp_path) -> None:
    import yaml

    source = _operator_config_template()
    source["data"] = {"timeframes": []}
    path = tmp_path / "Config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(configuration.ConfigError, match="owned by SQL dimensions"):
        configuration.load_config(path)


def test_config_rejects_technical_override(tmp_path) -> None:
    import yaml

    source = _operator_config_template()
    source["service"]["heartbeat_seconds"] = 99
    path = tmp_path / "Config.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(configuration.ConfigError, match="owned by configuration.py"):
        configuration.load_config(path)


def test_config_rejects_unbounded_rolling_window(tmp_path) -> None:
    import yaml

    source = _operator_config_template()
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


def test_http_cookie_refresh_scopes_existing_session_across_redirects(
    monkeypatch,
) -> None:
    token = _jwt(3600)
    request_calls = []

    class Response:
        text = f'<script>window.init={{"auth_token":"{token}"}}</script>'

        def raise_for_status(self) -> None:
            pass

    class Session:
        def __init__(self):
            self.cookies = auth.requests.cookies.RequestsCookieJar()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, *, headers, timeout):
            request_calls.append((url, headers, timeout, self.cookies.get_dict()))
            self.cookies.set(
                "sessionid", "rotated", domain=".tradingview.com", path="/", secure=True
            )
            return Response()

    session = Session()
    monkeypatch.setattr(auth.requests, "Session", lambda: session)
    monkeypatch.setattr(
        auth.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("cookie jar session was not used"),
    )
    cookie = "sessionid=existing; device_t=value=with=equals"
    refreshed, retained_cookie = auth._http_cookie_refresh(cookie)

    assert refreshed == token
    assert retained_cookie == "sessionid=rotated; device_t=value=with=equals"
    assert request_calls == [(
        "https://www.tradingview.com/",
        {
            "User-Agent": auth.USER_AGENT,
            "Accept-Language": "en-US",
        },
        20,
        {"sessionid": "existing", "device_t": "value=with=equals"},
    )]


def test_cookie_header_rejects_lookalike_domains() -> None:
    cookies = [
        {"name": "root", "value": "1", "domain": ".tradingview.com"},
        {"name": "locale", "value": "2", "domain": "vn.tradingview.com"},
        {"name": "suffix", "value": "3", "domain": "tradingview.com.evil"},
        {"name": "prefix", "value": "4", "domain": "eviltradingview.com"},
    ]

    assert auth._cookie_header(cookies) == "root=1; locale=2"


def test_browser_login_accepts_current_tradingview_field_names() -> None:
    actions = []

    class Control:
        def __init__(self, selector):
            self.selector = selector

        @property
        def first(self):
            return self

        def click(self, *, timeout):
            actions.append(("click", self.selector, timeout))

        def fill(self, value, *, timeout):
            supported = {
                'input[name="id_username"]': "username",
                'input[name="id_password"], input[type="password"]': "password",
            }
            if self.selector not in supported:
                raise RuntimeError("selector not present")
            actions.append(("fill", supported[self.selector], value, timeout))

    class Keyboard:
        def press(self, key):
            actions.append(("press", key))

    class Page:
        keyboard = Keyboard()

        def get_by_text(self, text, *, exact):
            return Control(f"text={text}")

        def locator(self, selector):
            return Control(selector)

        def wait_for_timeout(self, milliseconds):
            actions.append(("wait", milliseconds))

    auth._complete_browser_login(
        Page(), {"username": "account@example.test", "password": "safe-test"}
    )

    assert ("fill", "username", "account@example.test", 5_000) in actions
    assert ("fill", "password", "safe-test", 10_000) in actions
    assert ("click", 'button[type="submit"]', 5_000) in actions


@pytest.mark.parametrize(
    ("successful_source", "expected_calls"),
    [
        ("session_cookie", ["session_cookie"]),
        ("browser_profile", ["session_cookie", "browser_profile"]),
        (
            "password_login",
            ["session_cookie", "browser_profile", "password_login"],
        ),
        (
            "headless_fresh_login",
            [
                "session_cookie",
                "browser_profile",
                "password_login",
                "headless_fresh_login",
            ],
        ),
    ],
)
def test_auth_refresh_uses_each_path_in_risk_order(
    tmp_path, monkeypatch, successful_source, expected_calls
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(
        cookie="sessionid=existing",
        username="account@example.test",
        password="safe-test",
        headless_fresh_login=True,
    )
    token = _jwt(3600)
    calls = []

    def outcome(source):
        calls.append(source)
        return (token, "sessionid=renewed") if source == successful_source else ("", "")

    monkeypatch.setattr(auth, "_http_cookie_refresh", lambda _cookie: outcome("session_cookie"))
    monkeypatch.setattr(
        auth,
        "_browser_refresh",
        lambda _config, _cookie, *, fresh_login: outcome(
            "headless_fresh_login" if fresh_login else "browser_profile"
        ),
    )
    monkeypatch.setattr(
        auth, "_http_login", lambda _username, _password: outcome("password_login")
    )
    monkeypatch.setattr(auth, "log_event", lambda *_args, **_kwargs: None)

    result = auth._refresh(config)

    assert result["source"] == successful_source
    assert calls == expected_calls


def test_auth_refresh_fails_closed_when_last_browser_path_is_challenged(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(
        cookie="sessionid=expired",
        username="account@example.test",
        password="safe-test",
        headless_fresh_login=True,
    )
    monkeypatch.setattr(auth, "_http_cookie_refresh", lambda _cookie: ("", ""))
    monkeypatch.setattr(auth, "_http_login", lambda _username, _password: ("", ""))

    def browser(_config, _cookie, *, fresh_login):
        if fresh_login:
            raise auth.AuthError("interactive challenge was not completed")
        return "", ""

    monkeypatch.setattr(auth, "_browser_refresh", browser)
    monkeypatch.setattr(auth, "log_event", lambda *_args, **_kwargs: None)

    with pytest.raises(auth.AuthError, match="headless_fresh_login: AuthError"):
        auth._refresh(config)

    assert not (tmp_path / "cache" / "tradingview_auth.json").exists()


def test_auth_refresh_logs_safe_no_token_fallback_metadata(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"]["cookie"] = "sessionid=existing"
    token = _jwt(3600)
    events = []
    browser_cookies = []
    clock = iter((10.0, 10.2, 20.0, 21.25))

    monkeypatch.setattr(auth.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        auth, "_http_cookie_refresh", lambda _cookie: ("", "sessionid=rotated")
    )

    def browser(_config, cookie, *, fresh_login):
        browser_cookies.append(cookie)
        return token, "sessionid=renewed"

    monkeypatch.setattr(
        auth,
        "_browser_refresh",
        browser,
    )
    monkeypatch.setattr(
        auth,
        "_http_login",
        lambda *_args: pytest.fail("password fallback should not be reached"),
    )

    def record(_logger, _level, event, risk, *, component, **fields):
        events.append({
            "event": event, "risk": risk, "component": component, **fields
        })

    monkeypatch.setattr(auth, "log_event", record)
    result = auth._refresh(config)

    assert result["source"] == "browser_profile"
    assert browser_cookies == ["sessionid=rotated"]
    assert events == [
        {
            "event": "AUTH_PATH_NO_TOKEN",
            "risk": "LOW",
            "component": "auth",
            "source": "session_cookie",
            "duration_seconds": 0.2,
            "action": "trying next authentication path",
        },
        {
            "event": "AUTH_REFRESHED",
            "risk": "NONE",
            "component": "auth",
            "source": "browser_profile",
            "duration_seconds": 1.25,
            "session_material_changed": True,
        },
    ]
    assert not any(
        key in {"token", "cookie", "username", "password"}
        for event in events for key in event
    )


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
    row = engine_pkg.prepare_warehouse_rows([_candle(timestamp)])[0]
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
    row = engine_pkg.prepare_warehouse_rows([candle])[0]

    assert row[5] == "101.12345679"
    assert row[6] == "12.3457"


def test_prepare_rows_reuses_a_precomputed_signature_without_recomputing(
    monkeypatch,
) -> None:
    candle = _candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))
    candle["_signature"] = ("stub-open", "stub-high", "stub-low", "stub-close", "stub-vol")
    monkeypatch.setattr(
        engine_pkg,
        "candle_signature",
        lambda _c: pytest.fail("signature must not be recomputed when already present"),
    )

    row = engine_pkg.prepare_warehouse_rows([candle])[0]

    assert row[2:] == ("stub-open", "stub-high", "stub-low", "stub-close", "stub-vol")


def test_sql_connection_retry_and_recovery_are_timed(monkeypatch, caplog) -> None:
    config = configuration.load_config()
    config["sql_server"]["retry_delay_seconds"] = 0
    attempts = []

    class Connection:
        timeout = 0

    def connect(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise sql_connector.pyodbc.Error(
                "Login failed for user 'DOMAIN\\private_service_account'"
            )
        return Connection()

    monkeypatch.setattr(sql_connector.pyodbc, "connect", connect)
    with caplog.at_level(logging.INFO, logger=sql_connector.LOGGER.name):
        connection = sql_connector.get_connection(config)

    assert isinstance(connection, Connection)
    assert len(attempts) == 2
    retry = next(
        record.message
        for record in caplog.records
        if "event=SQL_CONNECTION_RETRY" in record.message
    )
    recovered = next(
        record.message
        for record in caplog.records
        if "event=SQL_CONNECTION_RECOVERED" in record.message
    )
    assert "attempt=1 max_attempts=3 duration_seconds=" in retry
    assert "attempts=2 duration_seconds=" in recovered
    assert "private_service_account" not in caplog.text


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


def test_sql_upsert_no_longer_writes_a_separate_bootstrap_state_table(monkeypatch) -> None:
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
    )

    sql = "\n".join(statement for statement, _params in statements)
    assert "DP_BackfillState" not in sql
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert result["fact_inserted"] == 1


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


def test_fetch_universe_reads_sql_dimensions_and_derives_interval(monkeypatch) -> None:
    config = configuration.load_config()
    statements = []
    result_sets = iter((
        [(56, "GOLD", "CAPITALCOM", "Metal", True), (33, "EURUSD", "CAPITALCOM", "FOREX", False)],
        [("M5", 5, "TF_M5"), ("D1", 1440, "TF_D1"), ("W", 10080, "TF_W")],
    ))

    class Cursor:
        def execute(self, statement, *params):
            statements.append(statement)
            return self

        def fetchall(self):
            return next(result_sets)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: Connection())
    symbols, timeframes = sql_connector.fetch_universe(config)
    assert "DWH.Dim_Symbol" in statements[0]
    assert "BrokerChannel" in statements[0]
    assert symbols == [
        {"symbol_id": 56, "exchange": "CAPITALCOM", "symbol": "GOLD",
         "asset_type": "Metal", "enabled": True},
        {"symbol_id": 33, "exchange": "CAPITALCOM", "symbol": "EURUSD",
         "asset_type": "FOREX", "enabled": False},
    ]
    assert "DWH.Dim_Timeframe" in statements[1]
    assert timeframes == [
        {"code": "M5", "interval": "5", "minutes": 5, "staging_table": "SEN.TF_M5"},
        {"code": "D1", "interval": "1D", "minutes": 1440, "staging_table": "SEN.TF_D1"},
        {"code": "W", "interval": "1W", "minutes": 10080, "staging_table": "SEN.TF_W"},
    ]


def test_fetch_universe_rejects_null_broker_channel_for_active_symbols(monkeypatch) -> None:
    config = configuration.load_config()
    result_sets = iter((
        [(56, "GOLD", None, "Metal", True)],
        [("M5", 5, "TF_M5")],
    ))

    class Cursor:
        def execute(self, statement, *params):
            return self

        def fetchall(self):
            return next(result_sets)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: Connection())
    with pytest.raises(ValueError, match="BrokerChannel is null/empty"):
        sql_connector.fetch_universe(config)


def test_fetch_universe_tolerates_null_broker_channel_for_inactive_symbols(monkeypatch) -> None:
    config = configuration.load_config()
    result_sets = iter((
        [(56, "GOLD", None, "Metal", False)],
        [("M5", 5, "TF_M5")],
    ))

    class Cursor:
        def execute(self, statement, *params):
            return self

        def fetchall(self):
            return next(result_sets)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(sql_connector, "get_connection", lambda _config: Connection())
    symbols, _ = sql_connector.fetch_universe(config)
    assert symbols == [
        {"symbol_id": 56, "exchange": "", "symbol": "GOLD",
         "asset_type": "Metal", "enabled": False}
    ]


def test_fact_verification_failure_rolls_back_transaction(monkeypatch) -> None:
    config = configuration.load_config()

    class Cursor:
        fast_executemany = False

        def execute(self, statement, *params):
            if "Fact verification failed" in statement:
                raise RuntimeError("fact verification failed")
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

    with pytest.raises(RuntimeError, match="fact verification failed"):
        sql_connector.bulk_upsert_candles(
            config,
            _timeframe(),
            [_candle(datetime(2026, 7, 27, 10, 5, tzinfo=timezone.utc))],
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
    monkeypatch.setattr(spool, "fetch_universe", lambda _config: ([], [_timeframe()]))
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
    monkeypatch.setattr(spool, "fetch_universe", lambda _config: ([], [_timeframe()]))
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
    lock_path = tmp_path / "run" / "engine_live.lock"
    with runtime.instance_lock(config, "live"):
        with pytest.raises(RuntimeError, match="already running"):
            with runtime.instance_lock(config, "live"):
                pass
        assert lock_path.stat().st_size >= 1
    with runtime.instance_lock(config, "live"):
        pass
    assert lock_path.stat().st_size >= 1


def test_runtime_lock_is_independent_per_mode(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    with runtime.instance_lock(config, "live"):
        with runtime.instance_lock(config, "backfill"):
            pass


def test_below_normal_priority_is_a_no_op_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    runtime._set_below_normal_priority()  # must not raise


def test_below_normal_priority_never_raises_even_if_the_api_call_fails(monkeypatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    import builtins

    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "ctypes":
            raise OSError("no kernel32 in this sandbox")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    runtime._set_below_normal_priority()  # must not raise


def test_backfill_yields_when_no_live_state_exists(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    assert runtime._live_yield_active(config) is False


def test_backfill_yields_when_live_heartbeat_is_stale(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    runtime._write_state(config, "live", {"status": "running", "pid": os.getpid(), "heartbeat_at": stale.isoformat()})
    assert runtime._live_yield_active(config) is True


def test_backfill_yields_when_live_next_cycle_is_imminent(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    now = datetime.now(timezone.utc)
    runtime._write_state(config, "live", {
        "status": "running", "pid": os.getpid(), "heartbeat_at": now.isoformat(),
        "cycle_active": False, "next_live_due_at": (now + timedelta(seconds=30)).isoformat(),
    })
    assert runtime._live_yield_active(config) is True


def test_backfill_proceeds_when_live_is_healthy_and_between_cycles(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    now = datetime.now(timezone.utc)
    runtime._write_state(config, "live", {
        "status": "running", "pid": os.getpid(), "heartbeat_at": now.isoformat(),
        "cycle_active": False, "next_live_due_at": (now + timedelta(minutes=4)).isoformat(),
    })
    assert runtime._live_yield_active(config) is False


def test_backfill_schedule_handles_unsorted_midnight_wrap() -> None:
    slots = ["11:11", "15:15", "19:19", "23:23", "03:03", "07:07"]
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    slot = runtime._due_slot(slots, "", now=now)
    assert slot == "2026-08-03T11:11:00+00:00"
    assert runtime._due_slot(slots, slot, now=now) == ""


def test_service_failure_state_is_durable_and_secret_free(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    runtime.record_service_failure(config, "live", RuntimeError("password=must-not-persist"))
    state = json.loads(
        (tmp_path / "run" / "state_live.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert state["error_type"] == "RuntimeError"
    assert "error" not in state
    assert "must-not-persist" not in json.dumps(state)
    assert runtime.service_status(config, "live")["ok"] is False


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
    runtime._write_state(config, "live", {"status": "running", "pid": 123})
    state = json.loads(
        (tmp_path / "run" / "state_live.json").read_text(encoding="utf-8")
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
    runtime._write_state(config, "live", {"status": "old"})
    attempts = []

    def locked(_path, _target):
        attempts.append(1)
        raise PermissionError("destination remains locked")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="remains locked"):
        runtime._write_state(config, "live", {"status": "new"})
    persisted = json.loads(
        (tmp_path / "run" / "state_live.json").read_text(encoding="utf-8")
    )
    assert len(attempts) == 100
    assert persisted["status"] == "old"


@pytest.mark.skipif(runtime.sys.platform != "win32", reason="Windows file sharing")
def test_runtime_state_write_survives_a_real_open_reader(tmp_path) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    runtime._write_state(config, "live", {"status": "old"})
    path = tmp_path / "run" / "state_live.json"
    reader = path.open("r", encoding="utf-8")
    release = threading.Timer(0.15, reader.close)
    release.start()
    try:
        runtime._write_state(config, "live", {"status": "new"})
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
        "live",
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
        "_workflow_pairs",
        lambda _config, **_kwargs: [(_symbol(), _timeframe())],
    )
    monkeypatch.setattr(
        runtime,
        "ensure_authenticated",
        lambda _config: (_ for _ in ()).throw(auth.AuthError("unavailable")),
    )

    with pytest.raises(auth.AuthError, match="unavailable"):
        runtime.run_live_service(config)
    runtime.record_service_failure(config, "live", auth.AuthError("unavailable"))
    state = json.loads(
        (tmp_path / "run" / "state_live.json").read_text(encoding="utf-8")
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


def test_auth_rejection_repeats_forced_refresh_after_process_restart(
    tmp_path, monkeypatch
) -> None:
    """Reproduce the forced-refresh loop without contacting the provider."""
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    config["tradingview"].update(
        auth_token="",
        cookie="",
        username="",
        password="",
        two_factor_secret="",
        retry_count=2,
        retry_delay_seconds=0,
        refresh_retry_seconds=600,
    )
    auth._write_cache(
        config,
        {
            "token": _jwt(3600),
            "cookie": "sessionid=isolated-test",
            "source": "isolated-test",
        },
    )
    refresh_attempts = []
    transport_attempts = []

    def unavailable(_config):
        refresh_attempts.append(True)
        raise auth.AuthError("simulated refresh failure")

    def rejected(*_args):
        transport_attempts.append(True)
        raise websocket.ProviderRequestError("unauthorized")

    monkeypatch.setattr(auth, "_refresh", unavailable)
    monkeypatch.setattr(websocket, "ensure_authenticated", auth.ensure_authenticated)
    monkeypatch.setattr(websocket, "_fetch_batch_once", rejected)
    request = websocket.FetchRequest(_symbol(), _timeframe(), 3, 3)

    for _process_generation in range(2):
        monkeypatch.setattr(auth, "_NEXT_REFRESH_ATTEMPT", 0.0)
        with pytest.raises(auth.AuthError, match="simulated refresh failure"):
            websocket.fetch_candles_batch(config, [request])

    cache = auth._load_cache(config)
    assert cache["retry_after"] > time.time()
    assert len(transport_attempts) == 2
    assert len(refresh_attempts) == 2


def test_backfill_request_uses_durable_bootstrap_then_rolling_window() -> None:
    config = configuration.load_config()
    timeframe = _timeframe()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    latest = now - timedelta(minutes=30)
    bootstrap = backfill.plan_backfill(
        config,
        _symbol(),
        timeframe,
        {"earliest": None, "latest": latest},
        now=now,
    )
    rolling = backfill.plan_backfill(
        config,
        _symbol(),
        timeframe,
        {"earliest": now - timedelta(days=65), "latest": latest},
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
            (56, "M5"): {"earliest": None, "latest": latest}
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
    assert result["completed_bootstraps"] == 1


def test_manual_bar_override_does_not_complete_bootstrap(monkeypatch) -> None:
    config = configuration.load_config()
    observed = {}
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_args: {(56, "M5"): {"earliest": None, "latest": None}},
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
    result = backfill.run_backfill_pairs(
        config,
        [(_symbol(), _timeframe())],
        bars_override=20,
    )

    assert observed["bars"] == 20
    assert result["completed_bootstraps"] == 0


def test_pair_selection_keeps_forex_historical_only(monkeypatch) -> None:
    _mock_sql_universe(monkeypatch)
    config = configuration.load_config()
    live_pairs = select_pairs(config, live=True, timeframe_filter="M5")
    historical_pairs = select_pairs(
        config, live=False, timeframe_filter="M5"
    )
    assert len(live_pairs) == 11
    assert len(historical_pairs) == 37
    assert all(symbol["asset_type"] != "FOREX" for symbol, _ in live_pairs)


def test_workflow_pairs_respects_workflow_enabled_flags(monkeypatch) -> None:
    _mock_sql_universe(monkeypatch)
    config = configuration.load_config()
    config["backfill"]["enabled"] = True
    backfill_pairs = runtime._workflow_pairs(config, live=False)
    assert len(backfill_pairs) == 37 * 15

    live_pairs = runtime._workflow_pairs(config, live=True)
    assert len(live_pairs) == len(config["live"]["symbols"]) * len(config["live"]["timeframes"])

    config["live"]["enabled"] = False
    with pytest.raises(RuntimeError, match="live workflow is disabled"):
        runtime._workflow_pairs(config, live=True)

    config["backfill"]["enabled"] = False
    with pytest.raises(RuntimeError, match="backfill workflow is disabled"):
        runtime._workflow_pairs(config, live=False)


def test_manual_live_command_runs_one_finite_pipeline_cycle(monkeypatch) -> None:
    _mock_sql_universe(monkeypatch)
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
    calls = {"n": 0}

    def stop_requested(_config, _name):
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(runtime.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_workflow_pairs", lambda _config, **_kwargs: [pair])
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
            "timings": {
                "planning_seconds": 0.01,
                "fetch_seconds": 0.02,
                "connection_seconds": 0.03,
                "pipeline_seconds": 0.04,
                "max_pair_seconds": 0.005,
            },
        },
    )
    monkeypatch.setattr(runtime, "_stop_requested", stop_requested)

    with caplog.at_level(logging.INFO, logger=runtime.LOGGER.name):
        assert runtime.run_live_service(config)["ok"] is True
    messages = [record.message for record in caplog.records]
    assert sum("event=SERVICE_STARTED" in message for message in messages) == 1
    assert sum("event=LIVE_CYCLE_COMPLETED" in message for message in messages) == 1
    assert "pairs=165 ok=165 failed=0 deferred=0 affected=12 spool_pending=0" in next(
        message for message in messages if "event=LIVE_CYCLE_COMPLETED" in message
    )
    cycle_message = next(
        message for message in messages if "event=LIVE_CYCLE_COMPLETED" in message
    )
    for field in (
        "planning_seconds=0.01",
        "fetch_seconds=0.02",
        "connection_seconds=0.03",
        "pipeline_seconds=0.04",
        "max_pair_seconds=0.005",
    ):
        assert field in cycle_message
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
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_workflow_pairs", lambda _config, **_kwargs: pairs)
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
    calls = {"n": 0}

    def stop_requested(_config, _name):
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(runtime, "_stop_requested", stop_requested)

    assert runtime.run_backfill_service(config)["ok"] is True
    state = json.loads((tmp_path / "run" / "state_backfill.json").read_text(encoding="utf-8"))
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
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_workflow_pairs", lambda _config, **_kwargs: pairs)
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
        runtime, "_stop_requested", lambda _config, _name: len(processed) >= 2
    )

    assert runtime.run_backfill_service(config)["ok"] is True
    state = json.loads((tmp_path / "run" / "state_backfill.json").read_text(encoding="utf-8"))
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
    stop_path = tmp_path / "run" / "stop_live.request"
    stop_path.parent.mkdir(parents=True)
    stop_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(
        runtime,
        "_workflow_pairs",
        lambda _config, **_kwargs: pytest.fail("stop marker must be honored before startup work"),
    )
    result = runtime.run_live_service(config)
    state = json.loads((tmp_path / "run" / "state_live.json").read_text(encoding="utf-8"))
    assert result["status"] == state["status"] == "stopped"
    assert stop_path.exists()


def test_cli_records_a_terminal_service_failure(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    observed: list[str] = []
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "run_live_service",
        lambda _config: (_ for _ in ()).throw(ValueError("fatal")),
    )
    monkeypatch.setattr(
        cli,
        "record_service_failure",
        lambda _config, _name, error: observed.append(type(error).__name__),
    )
    monkeypatch.setattr(cli, "log_event", lambda *_a, **_k: None)
    assert cli.main(["run-live"]) == 1
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
        "__main__.py",
        "configuration.py",
        "log.py",
    }
    engine = package / "engine"
    assert {path.name for path in engine.glob("*.py")} == {
        "auth.py",
        "backfill.py",
        "live.py",
        "pipeline.py",
        "spool.py",
        "runtime.py",
        "sql_connector.py",
        "websocket.py",
    }
    assert len(list(engine.glob("*.py"))) == 8
    for path in package.rglob("*.py"):
        lines = _code_line_count(path)
        assert lines <= (460 if path.name == "sql_connector.py" else 300), path.name
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

    assert "def run_live(" not in pipeline_source
    assert "def run_live_cycle(" not in pipeline_source
    assert "def run_live_cycle(" in live_source
    assert "def run_backfill(" in backfill_source
    assert "run_live_service" not in live_source + backfill_source + pipeline_source
    assert "run_backfill_service" not in live_source + backfill_source + pipeline_source
    assert "sql_connector" not in (
        engine / "websocket.py"
    ).read_text(encoding="utf-8")
    assert "spool" not in (engine / "websocket.py").read_text(encoding="utf-8")
    assert all(
        "import pyodbc" not in path.read_text(encoding="utf-8")
        for path in engine.glob("*.py")
        if path.name != "sql_connector.py"
    )
    assert "def run_live_service(" in runtime_source
    assert "def run_backfill_service(" in runtime_source
    assert "def configure_logging(" not in runtime_source
    assert "def configure_logging(" in log_source
    assert "contract == {" not in main_source
    assert not (package / "__init__.py").exists()
    assert not (engine / "__init__.py").exists()
    assert not (root / "pyproject.toml").exists()
    package_source = "\n".join(
        path.read_text(encoding="utf-8") for path in package.rglob("*.py")
    )
    assert package_source.count("unauthorized_user_token") == 1
    assert package_source.count("Mozilla/5.0") == 1


def test_runtime_uses_one_private_yaml_configuration(tmp_path) -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    assert not (root / ".env.example").exists()
    assert not (root / "Config.example.yaml").exists()
    assert not (root / "pyproject.toml").exists()
    assert "Config.yaml" in (root / ".gitignore").read_text(encoding="utf-8")
    config = configuration.load_config()
    assert "data" not in config
    assert "tables" in config
    assert config["sql_server"]["contract_version"] == "4"
    assert config["backfill"]["lookback_days"] == 60
    assert "scan_bars" not in config["backfill"]
    assert set(config["live"]) >= {
        "enabled", "interval_minutes", "bars_per_request",
        "closed_candles_only", "symbols", "timeframes",
    }
    assert config["live"]["interval_minutes"] == 5
    assert config["live"]["bars_per_request"] == 3
    assert config["live"]["closed_candles_only"] is True

    template = _operator_config_template()
    assert template["live"]["closed_candles_only"] is True
    assert set(template["service"]) == set()
    assert template["backfill"]["run_on_start"] is True
    assert template["backfill"]["schedule_utc"] == [
        "11:11", "15:15", "19:19", "23:23", "03:03", "07:07",
    ]
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
    template_path = tmp_path / "Config.yaml"
    template_path.write_text(yaml.safe_dump(template), encoding="utf-8")
    loaded = configuration.load_config(template_path)
    assert loaded["app"]["config_path"] == str(template_path.resolve())
    assert "data" not in loaded
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
    assert "BrokerChannel" in deploy_sql
    assert "RefName" not in deploy_sql
    assert "SEN.DP_BackfillState" not in deploy_sql
    assert "SEN.ActiveTask" not in deploy_sql
    assert "SEN.OHLCV_UnsupportedCalendar" not in deploy_sql
