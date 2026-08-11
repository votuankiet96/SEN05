from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest


def _load_watchdog():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "windows" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("dp_program_watchdog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    watchdog = _load_watchdog()
    monkeypatch.setattr(watchdog, "load_config", lambda: {"app": {"runtime_dir": str(tmp_path)}})
    statuses = {"live": {"ok": False}, "backfill": {"ok": True}}
    monkeypatch.setattr(watchdog, "service_status", lambda _config, role: statuses[role])
    sent: list[tuple] = []
    monkeypatch.setattr(
        watchdog, "send_watchdog_alert", lambda *args, **kwargs: sent.append((args, kwargs))
    )
    marker = tmp_path / "run" / "watchdog_alerted_live"

    assert watchdog.main() == 1
    assert len(sent) == 1 and marker.exists()

    # Still unhealthy on the next run: stays silent (edge-triggered, not per-poll).
    assert watchdog.main() == 1
    assert len(sent) == 1

    # Recovery clears the marker so a future outage can alert again.
    statuses["live"] = {"ok": True}
    assert watchdog.main() == 0
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


def _enabled_config(*, live: bool, backfill: bool) -> dict:
    return {"live": {"enabled": live}, "backfill": {"enabled": backfill}}


def test_exe_entry_spawns_one_child_process_per_enabled_workflow() -> None:
    entry = _load_exe_entry()
    spawned: list[_FakeProcess] = []

    def factory(*, target, args, name):
        process = _FakeProcess(target=target, args=args, name=name)
        spawned.append(process)
        return process

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=True), process_factory=factory
    )
    assert code == 0
    assert {process.role for process in spawned} == {"live", "backfill"}
    assert all(process.started for process in spawned)


def test_exe_entry_spawns_only_the_enabled_workflow() -> None:
    entry = _load_exe_entry()
    spawned: list[_FakeProcess] = []

    def factory(*, target, args, name):
        process = _FakeProcess(target=target, args=args, name=name)
        spawned.append(process)
        return process

    code = entry.main_entry(
        config=_enabled_config(live=True, backfill=False), process_factory=factory
    )
    assert code == 0
    assert [process.role for process in spawned] == ["live"]


def test_exe_entry_spawns_nothing_when_both_workflows_disabled() -> None:
    entry = _load_exe_entry()

    def factory(*_args, **_kwargs):
        pytest.fail("no process should be spawned when nothing is enabled")

    code = entry.main_entry(
        config=_enabled_config(live=False, backfill=False), process_factory=factory
    )
    assert code == 1


def test_run_dp_example_config_matches_current_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    example_config = (root / "run_dp" / "Config.example.yaml").read_text(encoding="utf-8")
    assert "sql_server:" in example_config
    assert "tradingview:" in example_config
    # The example must never carry a real secret if someone edits it in
    # place and forgets to rename it away from the tracked filename.
    assert 'auth_token: ""' in example_config


def test_run_dp_deploy_package_bundles_sql_installer_and_docs() -> None:
    root = Path(__file__).resolve().parents[1]
    run_dp = root / "run_dp"
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
    assert "Config.example.yaml" in deploy_doc


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
    ]
    for relative in utility_python:
        assert _code_line_count(root / relative) <= 300

    imports = {
        path.relative_to(root).as_posix()
        for path in (root / "src" / "dp_program").rglob("*.py")
        if "util.discord_report" in path.read_text(encoding="utf-8")
    }
    assert imports == {"src/dp_program/engine/runtime.py"}

    from dp_program.util.chart import server

    asset = root / "src" / "dp_program" / "util" / "chart" / "lightweight-charts.js"
    assert asset.stat().st_size > 100_000
    assert "/assets/lightweight-charts.js" in server.PAGE
    assert "https://" not in server.PAGE
    assert "http://" not in server.PAGE


def test_run_batches_launch_only_portable_foreground_services() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "run_dp.bat").exists()
    for name, mode in (("run_live.bat", "live"), ("run_backfill.bat", "backfill")):
        batch = (root / name).read_text(encoding="ascii")
        lowered = batch.lower()
        assert "%~dp0" in lowered
        assert ".venv\\scripts\\python.exe" in lowered
        assert "-m dp_program settings" in lowered
        assert f"-m dp_program status --mode {mode}" in lowered
        assert "-m dp_program check-sql" in lowered
        assert "-m dp_program doctor" in lowered
        assert f"'stop_{mode}.request'" in lowered
        assert f"-m dp_program run-{mode}" in lowered
        assert f"-m dp_program stop --mode {mode}" in lowered
        assert "scheduledtask" not in lowered
        assert "start-process" not in lowered
        assert "start /b" not in lowered
        assert "taskkill" not in lowered
        assert 'start "dp program"' not in lowered
        assert "c:\\users\\" not in lowered


def test_top_level_launcher_dispatches_to_backfill_live_and_chart() -> None:
    root = Path(__file__).resolve().parents[1]
    batch = (root / "run.bat").read_text(encoding="ascii")
    lowered = batch.lower()
    assert "%~dp0" in lowered
    assert "c:\\users\\" not in lowered
    for mode, target in (
        ("live", "run_live.bat"),
        ("backfill", "run_backfill.bat"),
        ("chart", "run_live.bat"),
    ):
        assert f'"%~1"=="{mode}"' in lowered
        assert target.lower() in lowered
    # Extra arguments after the mode must forward through unchanged
    # (e.g. `run.bat backfill check` -> run_backfill.bat check).
    assert "%2 %3 %4" in batch


def test_scheduled_task_installer_registers_boot_and_watchdog_tasks() -> None:
    root = Path(__file__).resolve().parents[1]
    installer = (root / "scripts" / "windows" / "install_task.ps1").read_text(encoding="utf-8")
    assert "run_live.bat" in installer and "run_backfill.bat" in installer
    assert "AtStartup" in installer
    assert "RestartCount 999" in installer
    assert "watchdog.py" in installer
    assert "LogonType S4U" in installer
    watchdog = (root / "scripts" / "windows" / "watchdog.py").read_text(encoding="utf-8")
    assert "service_status" in watchdog
    assert "send_watchdog_alert" in watchdog
