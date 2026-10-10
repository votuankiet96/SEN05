"""Hợp đồng Redis (phần 1 của redis_writer.py): ví dụ cố định + đối chiếu từng phần với live thật (core_program/src/engine/live.py) để chống lệch."""
from __future__ import annotations

import importlib
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import redis_writer
from support import M5, at, candle

CORE_SRC = Path(__file__).resolve().parents[2] / "core_program" / "src"
# src/ của core_program đã phẳng nên module cấp cao nhất của nó (configuration, log, engine, util) dùng chung
# không gian tên với DFS: riêng `configuration` TRÙNG tên configuration.py của DFS.
_CORE_TOP_LEVEL = ("configuration", "log", "engine", "util")


def _is_core_module(name: str) -> bool:
    return name in _CORE_TOP_LEVEL or name.startswith(("engine.", "util."))


@pytest.fixture(scope="module")
def live():
    """Nạp engine/live.py của core_program tách biệt, rồi trả lại đúng các module của DFS."""
    saved = {name: module for name, module in sys.modules.items() if _is_core_module(name)}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(CORE_SRC))
    try:
        return importlib.import_module("engine.live")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"core_program live module is not importable ({type(exc).__name__})")
    finally:
        sys.path.remove(str(CORE_SRC))
        for name in [name for name in sys.modules if _is_core_module(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_round_price_examples():
    cases = {"7649.90": "7649.9", "25653.00": "25653", "52020": "52020", "0.005": "0.01", "0.004": "0",
             "1234.5678": "1234.57", "-3.10": "-3.1", "1E+3": "1000", "4137.44000000": "4137.44"}
    for raw, expected in cases.items():
        assert redis_writer.round_price(Decimal(raw)) == expected, raw


def test_stamp_is_fixed_width_utc_and_round_trips():
    assert redis_writer.stamp(at("2026-09-30 13:05")) == "2026-09-30 13:05:00"
    aware = datetime(2026, 9, 30, 15, 5, 7, 999, tzinfo=timezone.utc)
    assert redis_writer.stamp(aware) == "2026-09-30 15:05:07"
    assert redis_writer.parse_stamp("2026-09-30 13:05:00") == at("2026-09-30 13:05")


def test_keys_follow_the_list_and_hash_rule():
    key = redis_writer.list_key("L_CANDLE", M5)
    assert key == "L_CANDLE_GOLD_M5" and ":" not in key
    assert redis_writer.hash_prefix(key) == "L_CANDLE_GOLD_M5:"


def test_script_args_layout():
    first, second = candle(M5, "2026-09-30 13:00", "4137.50"), candle(M5, "2026-09-30 13:05", "4138.00")
    args = redis_writer.script_args(1200, "L_CANDLE_GOLD_M5", [first, second], "2026-09-30 13:10:00")
    assert args[:2] == [1200, "L_CANDLE_GOLD_M5:"] and len(args) == 2 + 6 * 2           # không có ô TTL
    assert args[2:8] == ["2026-09-30 13:00:00", "4137.5", "4137.5", "4137.5", "4137.5", "2026-09-30 13:10:00"]
    assert args[8:14] == ["2026-09-30 13:05:00", "4138", "4138", "4138", "4138", "2026-09-30 13:10:00"]
    # Lua đánh số từ 1: nến đầu tiên nằm ở ARGV[3] = args[2], đúng điểm bắt đầu vòng lặp của script
    assert redis_writer.INCREMENTAL_SCRIPT.count("for i = 3, #ARGV, 6 do") == 1 and args[3 - 1] == "2026-09-30 13:00:00"


# ---------------------------------------------------------------- parity với live thật
def _without_ttl(script: str) -> str:
    # Đúng ba chỗ khác nhau có chủ ý giữa script của live và của DFS (DFS không đặt TTL cho Hash);
    # mọi dòng còn lại phải giống HỆT, nên live đổi logic ở chỗ khác là test này báo lệch.
    edits = (
        ("local max_size, candle_prefix, ttl = tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3])",
         "local max_size, candle_prefix = tonumber(ARGV[1]), ARGV[2]"),
        ("for i = 4, #ARGV, 6 do", "for i = 3, #ARGV, 6 do"),
        ("        redis.call('EXPIRE', key, ttl)\n", ""),
    )
    for old, new in edits:
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    return script


def test_lua_script_is_the_live_script_without_the_ttl(live):
    assert redis_writer.INCREMENTAL_SCRIPT == _without_ttl(live._INCREMENTAL_SCRIPT)
    assert "EXPIRE" not in redis_writer.INCREMENTAL_SCRIPT and "ttl" not in redis_writer.INCREMENTAL_SCRIPT
    assert redis_writer.HASH_FIELDS == live.HASH_FIELDS


def test_round_price_matches_live_on_random_values(live):
    rng = random.Random(20261004)
    for _ in range(3000):
        value = Decimal(rng.randint(-10**9, 10**9)) / Decimal(10 ** rng.randint(0, 8))
        assert redis_writer.round_price(value) == live._round_price(value), value


def test_stamp_and_keys_match_live(live):
    rng = random.Random(7)
    for _ in range(500):
        moment = datetime(2020 + rng.randint(0, 6), rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        assert redis_writer.stamp(moment) == live._stamp(moment)
        assert redis_writer.stamp(moment.replace(tzinfo=timezone.utc)) == live._stamp(moment.replace(tzinfo=timezone.utc))
    live_key, live_hash_prefix = live._keys({"key_prefix": "L_CANDLE"}, "M5", "GOLD")
    assert (live_key, live_hash_prefix) == (redis_writer.list_key("L_CANDLE", M5), redis_writer.hash_prefix(live_key))


def test_script_args_match_live_candle_args(live):
    candle_a = candle(M5, "2026-09-30 13:00", "4137.456")
    live_dict = {"timestamp": candle_a.bar_time.replace(tzinfo=timezone.utc), "open": candle_a.open, "high": candle_a.high,
                 "low": candle_a.low, "close": candle_a.close}
    live_args = live._candles_to_args([live_dict])           # [stamp, o, h, l, c, time_update(giờ máy)]
    ours = redis_writer.script_args(1200, "L_CANDLE_GOLD_M5", [candle_a], "X")[2:]
    assert ours[:5] == live_args[:5]
