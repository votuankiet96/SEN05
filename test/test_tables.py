"""Tests for core_engine.logkit.tables - the shared cell()/kv() helpers that
replaced the byte-identical private copies live_reporter.py and
historical_reporter.py used to each define.
"""

from __future__ import annotations

from core_engine.logkit.tables import cell, kv


def test_cell_left_aligns_and_pads_by_default():
    assert cell("EURUSD", 10) == "EURUSD    "


def test_cell_right_align():
    assert cell("42", 6, align="right") == "    42"


def test_cell_center_align():
    assert cell("ok", 6, align="center") == "  ok  "


def test_cell_truncates_long_values_with_ellipsis():
    result = cell("a_very_long_symbol_name", 10)
    assert len(result) == 10
    assert result.endswith("...")


def test_cell_none_or_empty_becomes_dash():
    assert cell(None, 5) == "-    "
    assert cell("", 5) == "-    "


def test_kv_default_label_width():
    line = kv("Mode", "gap")
    assert line == "  Mode             : gap"


def test_kv_custom_label_width():
    line = kv("Mode", "gap", label_width=6)
    assert line == "  Mode  : gap"
