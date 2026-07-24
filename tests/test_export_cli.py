"""Tests for CSV export launcher helpers without touching SQL Server."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from core_python import export_cli
from core_python.export import service as export_service


def test_export_cli_uses_ma_cross_default_timeframe():
    assert export_cli._strategy_timeframe("ma_cross", "") == "M30"
    assert export_cli._strategy_timeframe("ma_cross", "m20") == "M20"


def test_export_wizard_uses_strategy_default_and_full_data_range(monkeypatch, tmp_path):
    bounds = {
        "US30": (pd.Timestamp("2020-01-01"), pd.Timestamp("2026-07-24")),
        "US500": (pd.Timestamp("2021-02-03"), pd.Timestamp("2026-07-23")),
    }
    captured = {}

    monkeypatch.setattr(export_cli, "load_bounds", lambda symbol, _tf: bounds[symbol])

    def fake_build_bulk_export(strategy, **kwargs):
        captured["strategy"] = strategy
        captured.update(kwargs)
        return SimpleNamespace(data="bartime,symbol,signal\n", filename="signals.csv")

    monkeypatch.setattr(export_cli, "build_bulk_export", fake_build_bulk_export)
    answers = iter(["2", "", "US30,US500", "", "", ""])

    output = export_cli.export_wizard(
        Namespace(output="", output_dir=str(tmp_path)),
        input_fn=lambda _prompt: next(answers),
    )

    assert output == tmp_path / "signals.csv"
    assert Path(output).read_text(encoding="utf-8") == "bartime,symbol,signal\n"
    assert captured["strategy"] == "ma_cross"
    assert captured["tf"] == "M30"
    assert captured["args"]["symbols"] == "US30,US500"
    assert captured["date_window"].start == pd.Timestamp("2020-01-01")
    assert captured["date_window"].end == pd.Timestamp("2026-07-25")


def test_export_wizard_rejects_timeframe_outside_strategy_contract(tmp_path):
    answers = iter(["ma_cross", "H1"])

    with pytest.raises(ValueError, match="supports only"):
        export_cli.export_wizard(
            Namespace(output="", output_dir=str(tmp_path)),
            input_fn=lambda _prompt: next(answers),
        )


def test_export_cli_request_args_includes_dates_cols_and_extra_params():
    args = Namespace(
        cols="bartime,side",
        start_date="2026-07-01",
        end_date="2026-07-11",
        param=["MA_PERIOD=25", "ATR_PERIOD=10"],
    )

    request_args = export_cli._request_args(args)

    assert request_args == {
        "cols": "bartime,side",
        "start_date": "2026-07-01",
        "end_date": "2026-07-11",
        "MA_PERIOD": "25",
        "ATR_PERIOD": "10",
    }


def test_bulk_export_emits_requested_strategy_columns(monkeypatch):
    def fake_run_strategy_request(*_args, symbol: str, **_kwargs):
        return SimpleNamespace(
            enriched=pd.DataFrame(
                {
                    "bartime": pd.to_datetime(["2026-01-01 00:00:00", "2026-01-01 01:00:00"]),
                    "signal": [1, -1],
                    "entry_price": [100.0, 99.0],
                }
            ),
            symbol=symbol,
        )

    monkeypatch.setattr(export_service, "run_strategy_request", fake_run_strategy_request)

    export = export_service.build_bulk_export(
        "combo",
        tf="H1",
        bars=50,
        args={"symbols": "US30,DE40", "cols": "entry_price"},
        date_window=None,
    )
    csv = pd.read_csv(StringIO(export.data))

    assert csv.columns.tolist() == ["bartime", "symbol", "signal", "entry_price"]
    assert csv["signal"].tolist() == [1, 1, -1, -1]
