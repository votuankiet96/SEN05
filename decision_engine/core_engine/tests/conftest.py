"""Cho phép `import core_engine.src.*` khi chạy pytest từ bất kỳ thư mục nào.

core_engine là namespace package (không có __init__.py, xem CLAUDE.md mục
2), nên pytest không tự đặt gốc repo vào sys.path. Chèn thẳng ở đây thay vì
bắt người chạy phải nhớ set PYTHONPATH.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def macd_break_trend_off_h1_h4(monkeypatch):
    """
    Khai macd_break KHÔNG dùng bộ lọc trend ở H1–H4 (strategies.macd_break.trend_off_timeframes). Config thật không còn khai khoá này từ
    2026-10-28 (macd_break dùng lại lọc trend ở mọi khung, trừ khối breakout nằm trong excluded_trend_methods); cơ chế `trend_off_timeframes`
    vẫn còn (ichimoku dùng) nên các test của nó dùng fixture này để dựng lại tình huống macd_break H1–H4 tắt trend.
    """
    from core_engine.src import configuration

    spec = configuration.STRATEGIES["macd_break"]
    monkeypatch.setitem(configuration.STRATEGIES, "macd_break", dataclasses.replace(spec, trend_off_timeframes=("H1", "H2", "H3", "H4")))
