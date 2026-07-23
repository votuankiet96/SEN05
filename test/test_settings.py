"""Tests for core_engine.settings: the env coercion helpers in
settings/operational.py, and that the settings/__init__.py facade still
re-exports everything call sites across the codebase rely on (a regression
check for the operational/system split done in an earlier refactor pass).
"""

from __future__ import annotations

import os

import pytest

from core_engine.settings.operational import env_bool, env_csv, env_float, env_int, env_str


@pytest.fixture
def clean_env():
    key = "DP_TEST_ENV_VAR_XYZ"
    yield key
    os.environ.pop(key, None)


def test_env_str_default_when_unset(clean_env):
    assert env_str(clean_env, "fallback") == "fallback"


def test_env_str_strips_whitespace(clean_env):
    os.environ[clean_env] = "  value  "
    assert env_str(clean_env) == "value"


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("false", False), ("no", False)])
def test_env_bool_recognized_values(clean_env, raw, expected):
    os.environ[clean_env] = raw
    assert env_bool(clean_env, default=not expected) is expected


def test_env_bool_empty_string_uses_default(clean_env):
    os.environ[clean_env] = ""
    assert env_bool(clean_env, default=True) is True
    assert env_bool(clean_env, default=False) is False


def test_env_bool_default_when_unset(clean_env):
    assert env_bool(clean_env, default=True) is True
    assert env_bool(clean_env, default=False) is False


def test_env_int_parses_and_clamps(clean_env):
    os.environ[clean_env] = "500"
    assert env_int(clean_env, 0, minimum=0, maximum=100) == 100
    os.environ[clean_env] = "-5"
    assert env_int(clean_env, 0, minimum=0, maximum=100) == 0


def test_env_int_falls_back_to_default_on_bad_value(clean_env):
    os.environ[clean_env] = "not_a_number"
    assert env_int(clean_env, 42) == 42


def test_env_float_parses_and_clamps(clean_env):
    os.environ[clean_env] = "3.7"
    assert env_float(clean_env, 0.0, minimum=0.0, maximum=3.0) == 3.0


def test_env_csv_splits_and_strips(clean_env):
    os.environ[clean_env] = " a, b ,c ,, "
    assert env_csv(clean_env) == ["a", "b", "c"]


def test_env_csv_default_when_unset(clean_env):
    assert env_csv(clean_env, "x,y") == ["x", "y"]


def test_settings_facade_reexports_typed_groups_and_paths():
    from core_engine import settings

    for name in (
        "DB", "TRADINGVIEW", "HISTORICAL", "LIVE", "NOTIFICATION",
        "CANDLE_SNAPSHOT", "STORAGE", "LOGGING", "BACKEND",
        "APP_ROOT", "ENV_FILE", "RUNTIME_DIR",
        "SYMBOLS", "TF_DISPLAY_ORDER", "TF_MINUTES", "DIRECT_TFS", "WEEKEND_CLOSED",
        "TF_STAGING", "DEFAULT_N_BARS",
        "build_conn_str", "ensure_runtime_dirs", "get_historical_timeframes",
    ):
        assert hasattr(settings, name), f"settings facade is missing {name}"


def test_storage_and_live_scope_are_reviewed_code_contracts():
    from core_engine.settings import CANDLE_SNAPSHOT, LIVE, STORAGE

    assert STORAGE.mode == "sql"
    assert CANDLE_SNAPSHOT.enabled is False
    assert CANDLE_SNAPSHOT.pubsub_enabled is False
    assert LIVE.asset_types == ("Indice", "Metal", "Crypto")
    assert LIVE.expected_symbol_count == 11


def test_operator_example_is_valid_and_minimal():
    from pathlib import Path

    from core_engine.settings.operational import inspect_operator_config

    example = Path(__file__).resolve().parents[1] / "config" / "dp_provider.env.example"
    report = inspect_operator_config(example)

    assert report["ok"] is True
    assert report["key_count"] == 25
    assert report["issues"] == []


def test_operator_config_rejects_unknown_duplicate_and_bad_values(tmp_path):
    from core_engine.settings.operational import inspect_operator_config

    path = tmp_path / "dp_provider.env"
    path.write_text(
        "\n".join(
            [
                "LOG_LEVEL=VERBOSE",
                "LOG_LEVEL=INFO",
                "HISTORICAL_BACKFILL_UTC=25:00",
                "SQL_PORT=not-a-port",
                "WS_LIVE_BATCH_INTERVAL_MIN=1",
            ]
        ),
        encoding="utf-8",
    )

    report = inspect_operator_config(path)

    assert report["ok"] is False
    assert any("LOG_LEVEL appears more than once" in issue for issue in report["issues"])
    assert any("LOG_LEVEL must be" in issue for issue in report["issues"])
    assert any("HISTORICAL_BACKFILL_UTC must" in issue for issue in report["issues"])
    assert any("SQL_PORT must be an integer" in issue for issue in report["issues"])
    assert any("WS_LIVE_BATCH_INTERVAL_MIN is not operator-editable" in issue for issue in report["issues"])


def test_odbc_connection_string_uses_optional_port():
    from core_engine.settings.operational import DatabaseSettings, build_conn_str

    db = DatabaseSettings(server="sql-host", database="db", port="1444", uid="", pwd="")
    value = build_conn_str(db=db)

    assert "DRIVER={ODBC Driver 18 for SQL Server};" in value
    assert "SERVER=sql-host,1444;" in value
    assert "TDS_Version" not in value


def test_database_settings_default_driver():
    from core_engine.settings.operational import DatabaseSettings

    db = DatabaseSettings()
    assert db.driver  # non-empty; exact value is operator-configurable
    assert db.retry_count >= 1


def test_get_historical_timeframes_covers_all_15_timeframes():
    from core_engine.settings import get_historical_timeframes

    rows = get_historical_timeframes()
    tf_codes = {tf for _interval, tf, _staging, _n_bars in rows}
    assert tf_codes == {
        "M5", "M10", "M15", "M20", "M30", "M45", "H1", "M90",
        "H2", "H3", "H4", "H6", "H8", "D1", "W",
    }
