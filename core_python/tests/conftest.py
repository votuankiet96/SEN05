"""Shared pytest fixtures for core_python.

CLAUDE.md mục 6: tests không được đụng SQL Server thật. db_connector.
symbols()/tf_minutes() query DWH.Dim_Symbol/Dim_Timeframe trên DP6 — nạp
sẵn cache bằng dữ liệu tổng hợp trước mỗi test để không có test nào vô
tình kích hoạt kết nối DB thật.
"""

from __future__ import annotations

import pytest
from core_python.src import db_connector

_TEST_SYMBOLS: dict[str, dict[str, object]] = {
    "US30": {"symbol_id": 10, "label": "US30", "asset_type": "Indice"},
}

_TEST_TF_MINUTES: dict[str, int] = {
    "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
    "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "D1": 1440, "W": 10080,
}


@pytest.fixture(autouse=True)
def _no_live_reference_data(monkeypatch):
    monkeypatch.setattr(db_connector, "_symbols_cache", dict(_TEST_SYMBOLS))
    monkeypatch.setattr(db_connector, "_tf_cache", dict(_TEST_TF_MINUTES))
