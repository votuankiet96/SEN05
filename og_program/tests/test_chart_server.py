"""Tests for dashboard API validation that do not touch SQL Server."""

from __future__ import annotations

from core_python.chart.server import create_app


def test_api_config_exposes_only_supported_strategies():
    client = create_app().test_client()

    response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert sorted(payload["strategies"]) == ["combo", "ma_cross"]
    ma_cross = payload["strategies"]["ma_cross"]
    assert ma_cross["defaultTimeframe"] == "M30"
    assert ma_cross["supportedTimeframes"] == ["M10", "M20", "M30", "M45"]
    assert ma_cross["symbolDefaults"]["US30"]["X"] == 15.0


def test_scan_rejects_unknown_strategy_before_db_query():
    client = create_app().test_client()

    response = client.get("/api/scan?strategy=bad&symbol=US30&tf=H1&bars=50")

    assert response.status_code == 400
    assert "Unknown strategy" in response.get_json()["error"]


def test_scan_rejects_unknown_symbol_before_db_query():
    client = create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=BAD&tf=H1&bars=50")

    assert response.status_code == 400
    assert "Unknown symbol" in response.get_json()["error"]


def test_scan_rejects_unknown_timeframe_before_db_query():
    client = create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=US30&tf=BAD&bars=50")

    assert response.status_code == 400
    assert "Unsupported timeframe" in response.get_json()["error"]


def test_data_range_rejects_unknown_strategy():
    client = create_app().test_client()

    response = client.get("/api/data-range?strategy=bad&symbol=US30&tf=H1")

    assert response.status_code == 400
    assert "Unknown strategy" in response.get_json()["error"]


def test_data_range_rejects_unsupported_ma_cross_timeframe_before_db_query():
    client = create_app().test_client()

    response = client.get(
        "/api/data-range?strategy=ma_cross&symbol=US30&tf=H1"
    )

    assert response.status_code == 400
    assert "supports only these timeframes" in response.get_json()["error"]
