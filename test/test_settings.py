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


class TestStorageModeResolution:
    """DP_STORAGE_MODE (sql|redis|both) resolution.

    Unset must reproduce pre-existing behavior exactly, since no current
    deployment sets this variable: CANDLE_SNAPSHOT_ENABLED=0 -> "sql" (the
    historical default, SQL Server only), CANDLE_SNAPSHOT_ENABLED=1 ->
    "both" (SQL plus the existing Redis candle-snapshot handoff to OG).
    """

    @pytest.fixture(autouse=True)
    def _clean(self):
        for key in ("DP_STORAGE_MODE", "CANDLE_SNAPSHOT_ENABLED"):
            os.environ.pop(key, None)
        yield
        for key in ("DP_STORAGE_MODE", "CANDLE_SNAPSHOT_ENABLED"):
            os.environ.pop(key, None)

    def _resolve(self):
        from core_engine.settings.operational import _resolve_storage_mode

        return _resolve_storage_mode()

    def test_unset_and_candle_snapshot_disabled_defaults_to_sql(self):
        assert self._resolve() == "sql"

    def test_unset_and_candle_snapshot_enabled_falls_back_to_both(self):
        os.environ["CANDLE_SNAPSHOT_ENABLED"] = "1"
        assert self._resolve() == "both"

    @pytest.mark.parametrize("mode", ["sql", "redis", "both"])
    def test_explicit_mode_wins_regardless_of_candle_snapshot_flag(self, mode):
        os.environ["DP_STORAGE_MODE"] = mode
        os.environ["CANDLE_SNAPSHOT_ENABLED"] = "1"
        assert self._resolve() == mode

    def test_explicit_mode_is_case_insensitive(self):
        os.environ["DP_STORAGE_MODE"] = "REDIS"
        assert self._resolve() == "redis"

    def test_invalid_mode_falls_back_to_candle_snapshot_inference(self):
        os.environ["DP_STORAGE_MODE"] = "not-a-real-mode"
        assert self._resolve() == "sql"


def test_redis_init_candles_env_var_default_and_clamp():
    # StorageSettings.redis_init_candles is `env_int("REDIS_INIT_CANDLES", 500,
    # minimum=50, maximum=5000)` evaluated as a dataclass field default, which
    # (like every other settings field in this module) is fixed once when the
    # class body first executes, not re-read per instantiation - so this
    # exercises env_int with the same name/bounds directly rather than via
    # StorageSettings(), which would silently ignore a later env change.
    key = "REDIS_INIT_CANDLES"
    original = os.environ.pop(key, None)
    try:
        assert env_int(key, 500, minimum=50, maximum=5000) == 500
        os.environ[key] = "99999"
        assert env_int(key, 500, minimum=50, maximum=5000) == 5000
        os.environ[key] = "1"
        assert env_int(key, 500, minimum=50, maximum=5000) == 50
    finally:
        os.environ.pop(key, None)
        if original is not None:
            os.environ[key] = original


def test_storage_settings_post_init_rejects_invalid_mode():
    from core_engine.settings.operational import StorageSettings

    bad = StorageSettings.__new__(StorageSettings)
    object.__setattr__(bad, "mode", "bogus")
    object.__setattr__(bad, "redis_init_candles", 500)
    bad.__post_init__()
    assert bad.mode == "sql"


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
