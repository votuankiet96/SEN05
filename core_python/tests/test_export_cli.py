"""Tests for the minimal CSV export CLI without touching SQL Server."""

from __future__ import annotations

from io import StringIO

import pandas as pd
import pytest
from core_python.src.og_signal import export_cli

from tests.fixtures import make_ohlcv


def test_parse_overrides_splits_name_value_pairs():
    assert export_cli.parse_overrides(["MA_PERIOD=25", "FAST_MA=13"]) == {
        "MA_PERIOD": "25",
        "FAST_MA": "13",
    }


def test_parse_overrides_rejects_missing_equals():
    with pytest.raises(ValueError, match="NAME=VALUE"):
        export_cli.parse_overrides(["MA_PERIOD"])


def test_default_timeframe_uses_strategy_spec():
    assert export_cli.default_timeframe("ma_cross") == "M30"
    assert export_cli.default_timeframe("combo") == "H4"


def test_combo_csv_emits_pending_entry_from_strategy_levels():
    df = make_ohlcv(300)
    enriched = export_cli.run_strategy("combo", symbol="US30", tf="H1", bars=df)

    csv_text = export_cli.to_csv(enriched, strategy="combo")
    csv = pd.read_csv(StringIO(csv_text))
    expected_entries = export_cli.signal_rows(enriched)["entry_price"].round(2).tolist()

    assert list(csv.columns) == ["bartime", "atr", "entry", "signal"]
    # cTrader ProtoOATradeSide encoding in the CSV: BUY=1, SELL=2 (not OG's
    # internal 1/-1) -- see export_cli.py module docstring.
    assert set(csv["signal"].unique()) <= {1, 2}
    assert csv["atr"].notna().all()
    # entry/atr làm tròn 2 chữ số trong signal_frame() (chốt 2026-09-23).
    assert csv["entry"].tolist() == pytest.approx(expected_entries)


def test_ma_cross_csv_keeps_the_three_requested_columns():
    df = make_ohlcv(300)
    enriched = export_cli.run_strategy("ma_cross", symbol="US30", tf="M30", bars=df)

    csv = pd.read_csv(StringIO(export_cli.to_csv(enriched, strategy="ma_cross")))

    assert list(csv.columns) == ["bartime", "atr", "signal"]
    assert set(csv["signal"].unique()) <= {1, 2}



def test_to_csv_on_no_signals_returns_header_only():
    df = make_ohlcv(300)
    enriched = export_cli.run_strategy("combo", symbol="US30", tf="H1", bars=df)
    empty = enriched.iloc[0:0]

    csv_text = export_cli.to_csv(empty, strategy="combo")

    assert csv_text.strip().splitlines() == [",".join(export_cli.COMBO_CSV_COLUMNS)]


def test_load_entry_range_uses_plain_load_range_without_from(monkeypatch):
    """Full-history export (không --from) không có gì đứng "trước" bar đầu
    tiên -- giữ nguyên load_range() thường, không gọi warmup."""
    calls = []
    monkeypatch.setattr(export_cli, "load_range", lambda *a: calls.append(("load_range", *a)) or make_ohlcv(5))
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda *a: pytest.fail("full-history export must not call load_range_with_warmup"),
    )

    export_cli._load_entry_range("US30", "H1", "", "")

    assert calls == [("load_range", "US30", "H1", "", "")]


def test_load_entry_range_uses_warmup_when_from_given(monkeypatch):
    calls = []
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda *a: calls.append(a) or make_ohlcv(5),
    )

    export_cli._load_entry_range("US30", "H1", "2020-01-01", "2020-06-01")

    assert calls == [("US30", "H1", "2020-01-01", "2020-06-01", export_cli.WARMUP_BARS)]


def test_trim_to_requested_range_drops_warmup_rows():
    df = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2019-12-31", "2020-01-01", "2020-01-02"]),
            "signal": [1, -1, 1],
        }
    )

    trimmed = export_cli.trim_to_requested_range(df, "2020-01-01")

    assert list(trimmed["bartime"]) == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")]


def test_trim_to_requested_range_is_a_no_op_for_full_history():
    df = pd.DataFrame({"bartime": pd.to_datetime(["2019-12-31"]), "signal": [1]})

    assert export_cli.trim_to_requested_range(df, "") is df


def test_export_signals_excludes_warmup_only_signals_from_csv(monkeypatch, tmp_path):
    """Bug thật đã fix: chạy strategy trên khung đã nới rộng (warmup +
    range) có thể ra tín hiệu thật NẰM TRONG đoạn warmup (trước date_from)
    -- CSV cuối cùng không được chứa dòng đó, chỉ đúng khoảng đã yêu cầu."""
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda symbol, tf, date_from, date_to, warmup_bars: make_ohlcv(10),
    )

    def fake_run_strategy(strategy, *, symbol, tf, bars, overrides):
        result = bars.copy()
        result["entry_price"] = 100.0
        result["signal"] = 0
        # 1 tín hiệu thật TRƯỚC date_from (trong đoạn warmup) + 1 tín hiệu
        # thật SAU date_from (trong khoảng yêu cầu thật).
        result.loc[result.index[0], "signal"] = 1  # bartime nằm trong warmup
        result.loc[result.index[-1], "signal"] = -1  # bartime nằm trong range
        return result

    monkeypatch.setattr(export_cli, "run_strategy", fake_run_strategy)

    date_from = make_ohlcv(10)["bartime"].iloc[5]
    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from=str(date_from),
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
        output_override=str(tmp_path / "signals.csv"),
    )

    csv = pd.read_csv(output, parse_dates=["bartime"])
    assert len(csv) == 1
    # SELL row: internal signal=-1 becomes cTrader's SELL=2 in the CSV.
    assert csv.iloc[0]["signal"] == 2
    assert pd.Timestamp(csv.iloc[0]["bartime"]) >= date_from


def test_no_trend_export_stays_at_the_root_of_output_dir(monkeypatch, tmp_path):
    """no_trend (mặc định) không được rơi vào runtime/exports/trend_filter/ —
    thư mục con đó chỉ dành cho bản đã lọc trend."""
    monkeypatch.setattr(export_cli, "get_symbol", lambda symbol: {"symbol_id": 1})
    monkeypatch.setattr(export_cli, "load_range", lambda symbol, tf, date_from, date_to: make_ohlcv(200))

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="",
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
    )

    assert output.parent == tmp_path
    assert output.name == "combo_US30_H1_full_history_nt.csv"


def test_export_signals_writes_csv_file(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cli, "load_range", lambda symbol, tf, date_from, date_to: make_ohlcv(200))

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="",
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
        output_override=str(tmp_path / "signals.csv"),
    )

    assert output.exists()
    csv = pd.read_csv(output)
    assert list(csv.columns) == list(export_cli.COMBO_CSV_COLUMNS)


def test_export_signals_rejects_unknown_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cli, "load_range", lambda symbol, tf, date_from, date_to: make_ohlcv(50))

    with pytest.raises(KeyError):
        export_cli.export_signals(
            strategy="combo",
            symbol="NOPE",
            tf="H1",
            date_from="",
            date_to="",
            overrides={},
            output_dir=str(tmp_path),
            output_override=str(tmp_path / "signals.csv"),
        )


def test_export_signals_rejects_timeframe_outside_strategy_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cli, "load_range", lambda symbol, tf, date_from, date_to: make_ohlcv(50))

    with pytest.raises(ValueError, match="supports only"):
        export_cli.export_signals(
            strategy="ma_cross",
            symbol="US30",
            tf="H1",
            date_from="",
            date_to="",
            overrides={},
            output_dir=str(tmp_path),
            output_override=str(tmp_path / "signals.csv"),
        )


def test_export_signals_passes_date_range_through_to_load_range_with_warmup(monkeypatch, tmp_path):
    """Có --from: đọc phải đi qua load_range_with_warmup (kèm WARMUP_BARS),
    không phải load_range trần -- xem _load_entry_range."""
    seen = {}

    def fake_load_range_with_warmup(symbol, tf, date_from, date_to, warmup_bars):
        seen["symbol"] = symbol
        seen["tf"] = tf
        seen["date_from"] = date_from
        seen["date_to"] = date_to
        seen["warmup_bars"] = warmup_bars
        return make_ohlcv(50)

    monkeypatch.setattr(export_cli, "load_range_with_warmup", fake_load_range_with_warmup)

    export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="2020-01-01",
        date_to="2021-01-01",
        overrides={},
        output_dir=str(tmp_path),
        output_override=str(tmp_path / "signals.csv"),
    )

    assert seen == {
        "symbol": "US30",
        "tf": "H1",
        "date_from": "2020-01-01",
        "date_to": "2021-01-01",
        "warmup_bars": export_cli.WARMUP_BARS,
    }


def test_main_writes_csv_and_prints_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda symbol, tf, date_from, date_to, warmup_bars: make_ohlcv(200),
    )

    output_path = tmp_path / "out.csv"
    rc = export_cli.main(
        [
            "--strategy",
            "combo",
            "--symbol",
            "US30",
            "--tf",
            "H1",
            "--from",
            "2020-01-01",
            "--output",
            str(output_path),
        ]
    )

    assert rc == 0
    assert output_path.exists()
    assert f"CSV exported: {output_path}" in capsys.readouterr().out


def test_main_reports_errors_without_raising(capsys):
    rc = export_cli.main(["--strategy", "unknown"])

    assert rc == 1
    assert "ERROR" in capsys.readouterr().out


def test_range_label_defaults_to_full_history_when_no_range_given():
    assert export_cli._range_label("", "") == "full_history"


def test_range_label_reflects_from_only():
    assert export_cli._range_label("2020-01-01", "") == "from_20200101"


def test_range_label_reflects_to_only():
    assert export_cli._range_label("", "2021-01-01") == "to_20210101"


def test_range_label_reflects_from_and_to():
    assert export_cli._range_label("2020-01-01", "2021-01-01") == "20200101_20210101"


def test_export_signals_names_file_full_history_when_no_range_given(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cli, "get_symbol", lambda symbol: {"symbol_id": 1})
    monkeypatch.setattr(export_cli, "load_range", lambda symbol, tf, date_from, date_to: make_ohlcv(400))

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="",
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
    )

    assert output.name == "combo_US30_H1_full_history_nt.csv"


def test_export_signals_names_file_from_requested_range_not_actual_data(monkeypatch, tmp_path):
    # bartime span of the loaded data is irrelevant to the filename now —
    # only the requested --from/--to matters, so it stays consistent even
    # when the real last bar lands on a different day than --to (e.g. a
    # half-open upper bound resolving to the prior day).
    monkeypatch.setattr(export_cli, "get_symbol", lambda symbol: {"symbol_id": 1})
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda symbol, tf, date_from, date_to, warmup_bars: make_ohlcv(400),
    )

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="2020-01-01",
        date_to="2021-01-01",
        overrides={},
        output_dir=str(tmp_path),
    )

    assert output.name == "combo_US30_H1_20200101_20210101_nt.csv"


def test_export_signals_names_file_with_trend_filter_defaults(monkeypatch, tmp_path):
    calls = []
    seen = {}

    monkeypatch.setattr(export_cli, "get_symbol", lambda symbol: {"symbol_id": 1})

    def fake_load_range(symbol, tf, date_from, date_to):
        calls.append((symbol, tf, date_from, date_to))
        return make_ohlcv(50)

    def fake_run_with_trend(strategy, *, symbol, entry_tf, entry_bars, trend_tf, trend_bars, overrides):
        seen.update(
            {
                "strategy": strategy,
                "symbol": symbol,
                "entry_tf": entry_tf,
                "entry_rows": len(entry_bars),
                "trend_tf": trend_tf,
                "trend_rows": len(trend_bars),
                "overrides": dict(overrides),
            }
        )
        return pd.DataFrame(
            {
                "bartime": pd.to_datetime(["2026-01-01 00:00"]),
                "atr": [1.5],
                "entry_price": [100.0],
                "signal": [1],
            }
        )

    monkeypatch.setattr(export_cli, "load_range", fake_load_range)
    monkeypatch.setattr(export_cli, "run_strategy_with_trend_reference", fake_run_with_trend)

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="",
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
        trend_mode="trend_filter",
    )

    assert output.name == "combo_US30_H1_full_history_tf_knn_H4.csv"
    assert output.parent == tmp_path / "trend_filter"
    assert calls == [("US30", "H1", "", ""), ("US30", "H4", "", "")]
    assert seen["strategy"] == "combo"
    assert seen["trend_tf"] == "H4"
    assert seen["overrides"]["TREND_TYPE"] == "knn"


def test_trend_flag_is_a_shortcut_for_trend_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(export_cli, "get_symbol", lambda symbol: {"symbol_id": 1})
    monkeypatch.setattr(
        export_cli,
        "load_range_with_warmup",
        lambda symbol, tf, date_from, date_to, warmup_bars: make_ohlcv(50),
    )
    monkeypatch.setattr(
        export_cli,
        "run_strategy_with_trend_reference",
        lambda *args, **kwargs: pd.DataFrame(
            {
                "bartime": pd.to_datetime(["2026-01-01 00:00"]),
                "atr": [1.5],
                "entry_price": [100.0],
                "signal": [1],
            }
        ),
    )

    output = export_cli.export_signals(
        strategy="combo",
        symbol="US30",
        tf="H1",
        date_from="2020-01-01",
        date_to="",
        overrides={},
        output_dir=str(tmp_path),
        use_trend=True,
        trend_tf="H1",
    )

    assert output.name == "combo_US30_H1_from_20200101_tf_knn_H1.csv"
    assert output.parent == tmp_path / "trend_filter"
