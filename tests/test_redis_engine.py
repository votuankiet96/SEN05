"""Unit tests for redis_engine's pure helper functions (no Redis, no DB)."""

from __future__ import annotations

import pandas as pd

from redis_engine.delivery.signal_id import build_signal_id


def test_signal_id_is_deterministic():
    args = ("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    assert build_signal_id(*args) == build_signal_id(*args)


def test_signal_id_accepts_equivalent_timestamp_forms():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "US30", "H1", pd.Timestamp("2026-01-05 10:00:00"), 1)
    assert a == b


def test_signal_id_differs_by_direction():
    buy = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    sell = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", -1)
    assert buy != sell


def test_signal_id_differs_by_symbol():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "DE40", "H1", "2026-01-05 10:00:00", 1)
    assert a != b


def test_signal_id_differs_by_bar_time():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "US30", "H1", "2026-01-05 11:00:00", 1)
    assert a != b
