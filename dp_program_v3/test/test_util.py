from __future__ import annotations

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


def _config() -> dict:
    return {
        "data": {
            "symbols": [
                {"exchange": "CAPITALCOM", "symbol": "GOLD"},
                {"exchange": "CAPITALCOM", "symbol": "BTCUSD"},
            ],
            "timeframes": [{"code": "M5"}, {"code": "H1"}],
        },
        "tables": {"fact_table": "DWH.Fact_OHLCV"},
    }


def test_discord_config_gate_requires_webhook_only_when_enabled(tmp_path: Path) -> None:
    import yaml

    from dp_program.configuration import ConfigError, load_config

    example = Path(__file__).resolve().parents[1] / "Config.example.yaml"
    source = yaml.safe_load(example.read_text(encoding="utf-8"))
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
    candles = server._load_candles(
        _config(), "CAPITALCOM:GOLD", "M5", 50, row_loader=lambda *_args: rows
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

    with pytest.raises(ValueError):
        server._load_candles(
            _config(),
            symbol,
            timeframe,
            bars,
            row_loader=lambda *_args: pytest.fail("SQL must not be opened"),
        )


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
        assert len((root / relative).read_text(encoding="utf-8").splitlines()) <= 300

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


def test_run_batch_has_only_portable_check_start_stop_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    batch = (root / "run_dp.bat").read_text(encoding="ascii")
    lowered = batch.lower()
    assert "%~dp0" in lowered
    assert ".venv\\scripts\\python.exe" in lowered
    assert "-m dp_program settings" in lowered
    assert "-m dp_program status" in lowered
    assert "-m dp_program doctor" in lowered
    assert "start-scheduledtask" in lowered
    assert "get-scheduledtask" in lowered
    assert "-m dp_program stop" in lowered
    assert "taskkill" not in lowered
    assert 'start "dp program"' not in lowered
    assert "c:\\users\\" not in lowered


def test_task_installer_verifies_the_complete_24x7_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "windows" / "install_task.ps1").read_text(
        encoding="utf-8"
    )
    for expected in (
        "MSFT_TaskBootTrigger",
        "S4U",
        "Highest",
        "IgnoreNew",
        "PT0S",
        "RestartCount",
        "PT1M",
    ):
        assert expected in source
