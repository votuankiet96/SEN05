"""Tests for the signal_display HTML chart CLI without touching SQL Server."""

from __future__ import annotations

import pandas as pd
import pytest

from core_python.configuration import run_strategy
from core_python.signal_display import cli, payload, renderer
from tests.fixtures import make_ohlcv


def test_candlestick_points_skips_nan_bartime():
    df = pd.DataFrame(
        {
            "bartime": [pd.Timestamp("2026-01-05 00:00"), pd.NaT],
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
        }
    )

    points = payload.candlestick_points(df)

    assert len(points) == 1
    assert points[0]["open"] == 1.0
    assert points[0]["close"] == 1.2
    assert isinstance(points[0]["time"], int)


def test_signal_markers_returns_empty_without_signal_column():
    df = pd.DataFrame({"bartime": [pd.Timestamp("2026-01-05 00:00")]})

    assert payload.signal_markers(df) == []


def test_signal_markers_builds_buy_and_sell_arrows():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 00:00", "2026-01-05 00:05", "2026-01-05 00:10"]),
            "signal": [1, 0, -1],
        }
    )

    markers = payload.signal_markers(df)

    assert len(markers) == 2
    assert markers[0]["text"] == "BUY"
    assert markers[0]["position"] == "belowBar"
    assert markers[1]["text"] == "SELL"
    assert markers[1]["position"] == "aboveBar"


def test_render_chart_html_embeds_candles_markers_and_metadata():
    candles = payload.candlestick_points(make_ohlcv(5))
    markers = [
        {"time": 1, "position": "belowBar", "color": "#16a34a", "shape": "arrowUp", "text": "BUY"},
    ]

    html = renderer.render_chart_html(
        candles,
        markers,
        symbol="US30",
        tf="H1",
        strategy_label="Combo",
    )

    assert "Combo · US30 H1" in html
    assert "1 BUY / 0 SELL" in html
    assert "LightweightCharts" in html
    assert "arrowUp" in html


def test_default_timeframe_uses_strategy_spec():
    assert cli.default_timeframe("ma_cross") == "M30"
    assert cli.default_timeframe("combo") == "H4"


def test_resolve_symbol_tf_normalizes_and_fills_default_tf():
    assert cli.resolve_symbol_tf("combo", " us30 ", "") == ("US30", "H4")
    assert cli.resolve_symbol_tf("combo", "us30", "h4") == ("US30", "H4")


def test_resolve_symbol_tf_rejects_unknown_symbol():
    with pytest.raises(KeyError):
        cli.resolve_symbol_tf("combo", "NOPE", "H1")


def test_clamp_bars_stays_within_bounds():
    assert cli.clamp_bars(10) == cli.BARS_MIN
    assert cli.clamp_bars(999999) == cli.BARS_MAX
    assert cli.clamp_bars(500) == 500


def test_render_signal_chart_writes_html_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load", lambda symbol, tf, bars: make_ohlcv(200))

    output = cli.render_signal_chart(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=200,
        output=tmp_path / "chart.html",
    )

    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "Combo · US30 · H1" in text


def test_render_signal_chart_rejects_unknown_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load", lambda symbol, tf, bars: make_ohlcv(50))

    with pytest.raises(KeyError):
        cli.render_signal_chart(
            strategy="combo",
            symbol="NOPE",
            tf="H1",
            bars=50,
            output=tmp_path / "chart.html",
        )


def test_render_signal_chart_rejects_timeframe_outside_strategy_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load", lambda symbol, tf, bars: make_ohlcv(50))

    with pytest.raises(ValueError, match="supports only"):
        cli.render_signal_chart(
            strategy="ma_cross",
            symbol="US30",
            tf="H1",
            bars=50,
            output=tmp_path / "chart.html",
        )


def test_main_writes_html_and_prints_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load", lambda symbol, tf, bars: make_ohlcv(200))

    output_path = tmp_path / "out.html"
    rc = cli.main(
        [
            "--strategy",
            "combo",
            "--symbol",
            "US30",
            "--tf",
            "H1",
            "--bars",
            "200",
            "--output",
            str(output_path),
        ]
    )

    assert rc == 0
    assert output_path.exists()
    assert f"Chart written: {output_path}" in capsys.readouterr().out


def test_main_reports_errors_without_raising(capsys):
    rc = cli.main(["--strategy", "unknown"])

    assert rc == 1
    assert "ERROR" in capsys.readouterr().out


def test_line_points_skips_nan_and_applies_color():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 00:00", "2026-01-05 00:05"]),
            "ma": [1.5, float("nan")],
        }
    )

    points = payload.line_points(df, "ma", color="#f59e0b")

    assert len(points) == 1
    assert points[0]["value"] == 1.5
    assert points[0]["color"] == "#f59e0b"


def test_histogram_points_colors_by_sign():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 00:00", "2026-01-05 00:05"]),
            "macd_h": [0.5, -0.5],
        }
    )

    points = payload.histogram_points(df, "macd_h")

    assert points[0]["color"] == "#22c55e"
    assert points[1]["color"] == "#ef4444"


def test_trend_line_points_colors_by_bias():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 00:00", "2026-01-05 04:00", "2026-01-05 08:00"]),
            "trend_ai_knn": [100.0, 101.0, 102.0],
            "trend_bias": [1, -1, 0],
        }
    )

    points = payload.trend_line_points(df, "trend_ai_knn")

    assert [point["color"] for point in points] == ["#22c55e", "#ef4444", "#94a3b8"]


def test_trend_bias_markers_only_use_trend_bias_changes():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                ["2026-01-05 00:00", "2026-01-05 04:00", "2026-01-05 08:00", "2026-01-05 12:00"]
            ),
            "trend_bias": [0, 1, 1, -1],
            "signal": [1, -1, 0, 1],
        }
    )

    markers = payload.trend_bias_markers(df)

    assert [marker["text"] for marker in markers] == ["NEUTRAL", "TREND UP", "TREND DOWN"]
    assert markers[1]["shape"] == "arrowUp"
    assert markers[2]["shape"] == "arrowDown"


def _combo_enriched():
    return run_strategy("combo", symbol="US30", tf="H1", bars=make_ohlcv(300))


def test_signal_table_rows_has_required_columns():
    enriched = _combo_enriched()

    rows = payload.signal_table_rows(enriched)

    assert rows
    assert set(rows[0]) == {"time", "bartime", "side", "entry", "sl", "tp", "rr", "sl_dow", "reason"}
    assert rows[0]["time"] in {point["time"] for point in payload.candlestick_points(enriched)}
    assert rows[0]["side"] in {"BUY", "SELL"}


def test_stats_summary_counts_buy_sell_and_last():
    enriched = _combo_enriched()

    stats = payload.stats_summary(enriched)

    assert stats == {"total": 31, "buy": 15, "sell": 16, "last": "SELL"}


def test_stats_summary_empty_without_signals():
    df = make_ohlcv(5)
    df["signal"] = 0

    assert payload.stats_summary(df) == {"total": 0, "buy": 0, "sell": 0, "last": "-"}
