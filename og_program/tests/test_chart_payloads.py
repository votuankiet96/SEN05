"""Tests for strategy-specific dashboard payload layouts."""

from __future__ import annotations

from core_python.chart.payloads.registry import build_chart_payload, get_payload_builder
from core_python.engine import run_strategy_on_bars

from tests.fixtures import make_ohlcv


def test_payload_registry_routes_supported_strategies():
    assert get_payload_builder("combo").__module__.endswith(".combo")
    assert get_payload_builder("ma_cross").__module__.endswith(".ma_cross")


def test_combo_payload_layout_contains_combo_panels_and_table_columns():
    result = run_strategy_on_bars(
        "combo",
        symbol="US30",
        tf="H1",
        bars=make_ohlcv(300),
    )

    payload = build_chart_payload(
        result.enriched,
        strategy=result.strategy,
        strategy_label=result.strategy_label,
        symbol=result.symbol,
        tf=result.tf,
        bars=result.bars,
        params=result.params,
    )

    assert [item["key"] for item in payload["overlays"]] == ["ma"]
    assert [item["key"] for item in payload["panels"]] == ["macd", "atr"]
    assert payload["signals"]
    assert {"ma", "macd_h"}.issubset(payload["signals"][0])
    assert payload["meta"]["strategy"] == "combo"


def test_ma_cross_payload_layout_contains_ma_cross_panels_and_table_columns():
    result = run_strategy_on_bars(
        "ma_cross",
        symbol="US30",
        tf="M10",
        bars=make_ohlcv(300),
    )

    payload = build_chart_payload(
        result.enriched,
        strategy=result.strategy,
        strategy_label=result.strategy_label,
        symbol=result.symbol,
        tf=result.tf,
        bars=result.bars,
        params=result.params,
    )

    assert [item["key"] for item in payload["overlays"]] == ["fast_ma", "slow_ma"]
    assert [item["key"] for item in payload["panels"]] == ["macd", "atr"]
    assert payload["signals"]
    assert {"fast_ma", "slow_ma", "macd_h", "ma_gap_atr"}.issubset(
        payload["signals"][0]
    )
    assert payload["meta"]["strategy"] == "ma_cross"
