"""Cấu hình: file mẫu hợp lệ, chặn khóa lạ/giá trị sai/db không được phép, ghi đè dòng lệnh, không lộ bí mật."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from configuration import ConfigError, load_config, with_overrides

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


def load_variant(tmp_path, old: str, new: str):
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, old
    path = tmp_path / "config.yaml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    return load_config(path)


def test_example_config_loads_with_safe_defaults():
    config = load_config(EXAMPLE)
    assert config.redis.db == 15 and config.redis.allowed_dbs == (15,)
    assert (config.redis.key_prefix, config.redis.bars_per_snapshot, config.redis.hash_ttl_seconds) == ("L_CANDLE", 1200, 604800)
    assert config.replay.symbols == ("GOLD",) and config.replay.timeframes == ("M5", "M15")
    assert config.replay.start == datetime(2026, 9, 30, 13) and config.replay.end == datetime(2026, 9, 30, 15)
    assert config.runtime_dir.is_absolute() and config.runtime_dir.parent == EXAMPLE.parent
    assert (config.pacing.mode, config.pacing.delay_seconds) == ("delay", 1.0)


def test_secrets_never_appear_in_repr_or_summary():
    config = load_config(EXAMPLE)
    assert "<redis-password>" not in repr(config) and "<redis-password>" not in str(config.summary())


def test_a_db_outside_allowed_dbs_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="allowed_dbs"):
        load_variant(tmp_path, "db: 15 ", "db: 0  ")


@pytest.mark.parametrize("old,new,message", [
    ("mode: delay", "mode: warp", "pacing.mode"),
    ("delay_seconds: 1.0", "delay_seconds: -1", "pacing.delay_seconds"),
    ("speed: 300.0", "speed: 0", "pacing.speed"),
    ('key_prefix: "L_CANDLE"', 'key_prefix: "L:CANDLE"', "redis.key_prefix"),
    ('end_utc: "2026-09-30 15:00:00"', 'end_utc: "2026-09-30 12:00:00"', "earlier than"),
    ("symbols: [GOLD]", "symbols: [GOLD, gold]", "duplicates"),
    ("trusted_connection: true", "trusted_connection: yes_please", "trusted_connection"),
])
def test_invalid_values_are_rejected_with_the_field_name(tmp_path, old, new, message):
    with pytest.raises(ConfigError, match=message):
        load_variant(tmp_path, old, new)


def test_unknown_and_missing_keys_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown key"):
        load_variant(tmp_path, "  speed: 300.0 ", "  speeed: 300.0 ")
    with pytest.raises(ConfigError, match="exactly these sections"):
        load_variant(tmp_path, "pacing:", "pacing_x:")


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_overrides_are_validated_and_exclusive():
    base = load_config(EXAMPLE)
    changed = with_overrides(base, symbols="gold,de40", timeframes="m5", delay=0.2, start="2026-09-30 13:00", end="2026-09-30T14:00:00+00:00")
    assert changed.replay.symbols == ("GOLD", "DE40") and changed.replay.timeframes == ("M5",)
    assert (changed.pacing.mode, changed.pacing.delay_seconds) == ("delay", 0.2)
    assert changed.replay.end == datetime(2026, 9, 30, 14)
    assert with_overrides(base, speed=600).pacing.mode == "speed"
    with pytest.raises(ConfigError, match="either"):
        with_overrides(base, delay=1, speed=2)
    with pytest.raises(ConfigError, match="earlier than"):
        with_overrides(base, start="2026-09-30 16:00")
    with pytest.raises(ConfigError, match="override"):
        with_overrides(base, symbols="")
