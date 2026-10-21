"""Tests for the signal_display HTML chart CLI without touching SQL Server."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from wisdom_engine.we_core.src.configuration import run_strategy
from wisdom_engine.we_core.src.signal_display import cli, payload, renderer, server

from tests import payload_reference
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
    assert set(rows[0]) == {"time", "bartime", "side", "entry", "reason"}
    assert rows[0]["time"] in {point["time"] for point in payload.candlestick_points(enriched)}
    assert rows[0]["side"] in {"BUY", "SELL"}


def test_stats_summary_counts_buy_sell_and_last():
    enriched = _combo_enriched()

    stats = payload.stats_summary(enriched)

    # trend filter tắt -> signal = raw_signal, nên raw* trùng total/buy/sell (stats_summary, chốt 2026-09-25).
    # 31/15/16 trước khi khôi phục lọc lặp cùng chiều của combo (2026-10-11).
    assert stats == {"total": 23, "buy": 11, "sell": 12, "last": "SELL", "rawTotal": 23, "rawBuy": 11, "rawSell": 12}


def test_stats_summary_empty_without_signals():
    df = make_ohlcv(5)
    df["signal"] = 0

    assert payload.stats_summary(df) == {
        "total": 0, "buy": 0, "sell": 0, "last": "-", "rawTotal": 0, "rawBuy": 0, "rawSell": 0,
    }


# --- payload.py xử lý theo cột (2026-10-15): kết quả phải GIỐNG HỆT bản lặp từng dòng cũ ---
# Bản cũ nằm ở tests/payload_reference.py (iterrows + to_unix_ts/_to_number từng giá trị).

_VECTORIZED = ("candlestick_points", "histogram_points", "line_points", "trend_line_points")


def _legacy_without_digits(name):
    """Bản lặp cũ không có tham số digits (luôn 2 chữ số): server nay truyền digits=price_decimals(symbol) bằng từ
    khoá; với symbol dùng 2 chữ số (US30) hai bên phải giống hệt, nên bỏ digits=2 rồi gọi bản cũ."""
    reference = getattr(payload_reference, name)

    def call(*args, digits=2, **kwargs):
        assert digits == 2
        return reference(*args, **kwargs)

    return call


@pytest.mark.parametrize("trend_mode", ["no_trend", "trend_filter"])
@pytest.mark.parametrize(("strategy", "tf"), [("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")])
def test_run_scan_giong_het_ban_lap_tung_dong_cu(monkeypatch, strategy, tf, trend_mode):
    def fake_load(symbol, tf, bars, *, before=None):
        _ = symbol, before
        minutes = {"M30": 30, "H1": 60, "H4": 240}[tf]
        return make_ohlcv(bars, freq_minutes=minutes, seed=minutes, start="2026-01-01")

    monkeypatch.setattr(server, "load", fake_load)
    request = {"strategy": strategy, "symbol": "US30", "tf": tf, "bars": 400, "trend_mode": trend_mode}
    if trend_mode == "trend_filter":
        request["trend_tf"] = "H4"

    vectorized = server.run_scan(**request)
    for name in _VECTORIZED:
        monkeypatch.setattr(server, name, _legacy_without_digits(name))
    row_by_row = server.run_scan(**request)

    assert vectorized == row_by_row
    # Không so hai rỗng với nhau: đường overlay, panel MACD và (nếu bật) chart trend đều có điểm.
    assert vectorized["candles"]
    assert all(overlay["data"] for overlay in vectorized["overlays"])
    assert vectorized["panels"][0]["data"]
    if trend_mode == "trend_filter":
        assert vectorized["trendChart"]["candles"]
        assert all(overlay["data"] for overlay in vectorized["trendChart"]["overlays"])


def _edge_frame(n: int, **columns) -> pd.DataFrame:
    bars = {"bartime": pd.date_range("2026-01-05", periods=n, freq="1h")}
    for name in ("open", "high", "low", "close"):
        bars[name] = np.linspace(100.0, 101.0, n) + {"open": 0.0, "high": 0.4, "low": -0.4, "close": 0.1}[name]
    return pd.DataFrame({**bars, **columns})


def _edge_frames() -> dict[str, pd.DataFrame]:
    nan = float("nan")
    # Giá trị sát nửa ở chữ số thứ 2 (round(2.675, 2) = 2.67 nhưng np.round ra 2.68) và quanh 0
    # (-0.004 làm tròn thành -0.0, vẫn tô màu "dương").
    halves = [2.675, 1.005, 0.125, 2.5, -2.675, 100.005, 1e-9, -0.004, 0.004, -0.0, 0.0, 123456.785, -0.005, 0.015]
    n = len(halves)
    bias = [1, -1, 0, nan, 0.999, -0.996, 0.004, 1, -1, nan, 0.5, 1.0, -1.0, 0]
    frames = {
        "rong": _edge_frame(0, ma=[], macd_h=[], trend_ai_knn=[], trend_bias=[]),
        "mot_dong": _edge_frame(1, ma=[1.234], macd_h=[-0.004], trend_ai_knn=[5.0], trend_bias=[1]),
        "lam_tron_sat_nua": _edge_frame(n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias),
        "nan_xen_ke": _edge_frame(
            n, ma=[nan, 1.5] * 7, macd_h=[0.5, nan] * 7, trend_ai_knn=[nan, nan] * 7, trend_bias=bias
        ),
        "toan_nan": _edge_frame(n, ma=[nan] * n, macd_h=[nan] * n, trend_ai_knn=[nan] * n, trend_bias=bias),
        "float32_va_int": _edge_frame(
            n,
            ma=np.array(halves, dtype="float32"),
            macd_h=np.arange(n, dtype="int64") - 7,
            trend_ai_knn=np.arange(n, dtype="int32"),
            trend_bias=np.array(bias, dtype="float32"),
        ),
        "object_float": _edge_frame(
            n, ma=pd.Series(halves, dtype=object), macd_h=pd.Series(halves, dtype=object),
            trend_ai_knn=pd.Series(halves, dtype=object), trend_bias=pd.Series(bias, dtype=object),
        ),
        "kieu_nullable": _edge_frame(
            n, ma=pd.array([nan if i % 3 == 0 else v for i, v in enumerate(halves)], dtype="Float64"),
            macd_h=pd.array(halves, dtype="Float64"), trend_ai_knn=pd.array(halves, dtype="Float64"),
            trend_bias=pd.array([1, -1, 0] * 4 + [1, -1], dtype="Int64"),
        ),
        "bartime_co_mui_gio": _edge_frame(
            n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias
        ).assign(bartime=pd.date_range("2026-01-05", periods=n, freq="1h", tz="Asia/Ho_Chi_Minh")),
        "bartime_utc": _edge_frame(
            n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias
        ).assign(bartime=pd.date_range("2026-01-05", periods=n, freq="1h", tz="UTC")),
        "bartime_do_chinh_xac_khac": _edge_frame(
            n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias
        ).assign(bartime=pd.date_range("2026-01-05", periods=n, freq="1h").astype("datetime64[s]")),
        "bartime_object": _edge_frame(
            n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias
        ).assign(bartime=pd.Series([f"2026-01-05 {i:02d}:00:00" for i in range(n)], dtype=object)),
        "bartime_le_giay": _edge_frame(
            n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias
        ).assign(bartime=pd.date_range("2026-01-05", periods=n, freq="1h") + pd.to_timedelta(np.arange(n) * 77, unit="ms")),
        "index_la": _edge_frame(n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias).set_axis(
            [f"r{i}" for i in range(n)]
        ),
        "index_trung": _edge_frame(n, ma=halves, macd_h=halves, trend_ai_knn=halves, trend_bias=bias).set_axis([0] * n),
        "thieu_cot_ma_va_bias": _edge_frame(n, macd_h=halves, trend_ai_knn=halves),
    }
    return frames


@pytest.mark.parametrize("name", list(_edge_frames()))
def test_ham_theo_cot_giong_het_ban_lap_tung_dong_cu_tren_ca_bien(name):
    df = _edge_frames()[name]

    assert payload.candlestick_points(df) == payload_reference.candlestick_points(df)
    for column in ("ma", "macd_h", "trend_ai_knn", "khong_co_cot_nay"):
        assert payload.line_points(df, column) == payload_reference.line_points(df, column)
        assert payload.line_points(df, column, color="#f59e0b") == payload_reference.line_points(
            df, column, color="#f59e0b"
        )
        assert payload.histogram_points(df, column) == payload_reference.histogram_points(df, column)
        assert payload.trend_line_points(df, column) == payload_reference.trend_line_points(df, column)
    # Cột hướng riêng của từng khối xác nhận (bias_column) và cột hướng không tồn tại.
    assert payload.trend_line_points(df, "trend_ai_knn", bias_column="macd_h") == payload_reference.trend_line_points(
        df, "trend_ai_knn", bias_column="macd_h"
    )
    assert payload.trend_line_points(df, "trend_ai_knn", bias_column="khong_co") == payload_reference.trend_line_points(
        df, "trend_ai_knn", bias_column="khong_co"
    )


def test_ham_theo_cot_giu_gia_tri_lam_tron_cua_round_python():
    # Chốt cứng giá trị đã biết (không chỉ so với bản cũ): np.round sẽ cho 2.68 ở dòng đầu.
    df = _edge_frame(3, ma=[2.675, 1.005, -0.004], macd_h=[2.675, 1.005, -0.004])

    assert [point["value"] for point in payload.line_points(df, "ma")] == [2.67, 1.0, -0.0]
    assert [point["color"] for point in payload.histogram_points(df, "macd_h")] == ["#22c55e", "#22c55e", "#22c55e"]


def test_ham_theo_cot_giong_ban_cu_tren_du_lieu_ngau_nhien():
    rng = np.random.default_rng(20261015)
    for _ in range(25):
        n = int(rng.integers(0, 40))
        values = np.round(rng.normal(0.0, 0.03, n), 3) + 0.005 * rng.integers(-3, 4, n)
        values[rng.random(n) < 0.2] = np.nan
        bias = rng.choice([-1.0, 0.0, 1.0, np.nan, 0.999, -0.996], n)
        df = _edge_frame(n, ma=values, macd_h=values, trend_ai_knn=values, trend_bias=bias)

        assert payload.candlestick_points(df) == payload_reference.candlestick_points(df)
        assert payload.line_points(df, "ma") == payload_reference.line_points(df, "ma")
        assert payload.histogram_points(df, "macd_h") == payload_reference.histogram_points(df, "macd_h")
        assert payload.trend_line_points(df, "trend_ai_knn") == payload_reference.trend_line_points(df, "trend_ai_knn")


def test_line_points_van_nem_loi_khi_bartime_nat_o_dong_co_gia_tri():
    df = _edge_frame(2, ma=[1.0, 2.0])
    df.loc[1, "bartime"] = pd.NaT

    with pytest.raises(ValueError, match="NaT"):
        payload.line_points(df, "ma")
    # Dòng NaT nhưng không có giá trị thì bị bỏ trước khi đụng tới bartime — như bản lặp cũ.
    df["ma"] = [1.0, float("nan")]
    assert len(payload.line_points(df, "ma")) == 1


def test_payload_digits_mac_dinh_2_va_digits_lon_hon_giu_so_le_cua_forex():
    df = _edge_frame(4, ma=[1.123456, 1.123449, np.nan, 1.1], macd_h=[0.000123, -0.000049, 0.0, np.nan],
                     trend_ai_knn=[1.12345, 1.12346, 1.12347, 1.12348], trend_bias=[1, -1, 0, 1])
    df["open"] = [1.12341, 1.12342, 1.12343, 1.12344]

    assert payload.candlestick_points(df)[0]["open"] == 1.12
    assert payload.candlestick_points(df, digits=5)[0]["open"] == 1.12341
    assert [p["value"] for p in payload.line_points(df, "ma")] == [1.12, 1.12, 1.1]
    assert [p["value"] for p in payload.line_points(df, "ma", digits=5)] == [1.12346, 1.12345, 1.1]
    hist = payload.histogram_points(df, "macd_h", digits=5)
    assert [p["value"] for p in hist] == [0.00012, -0.00005, 0.0]
    assert [p["color"] for p in hist][:2] == [payload.UP_COLOR, payload.DOWN_COLOR]       # màu xét trên giá trị đã làm tròn
    assert [p["value"] for p in payload.trend_line_points(df, "trend_ai_knn", digits=5)][:2] == [1.12345, 1.12346]
    assert [p["value"] for p in payload.trend_line_points(df, "trend_ai_knn")][:2] == [1.12, 1.12]
    rows = payload.signal_table_rows(df.assign(signal=[1, 0, 0, 0], entry_price=[1.123456] * 4), digits=5)
    assert rows[0]["entry"] == 1.12346 and payload.signal_table_rows(df.assign(signal=[1, 0, 0, 0], entry_price=[1.123456] * 4))[0]["entry"] == 1.12
