"""Tests for CSV export launcher helpers without touching SQL Server."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from types import SimpleNamespace

import pandas as pd

from og_past import export_cli
from og_past.export import service as export_service


def test_export_cli_request_args_includes_dates_cols_and_extra_params():
    args = Namespace(
        cols="bartime,side",
        start_date="2026-07-01",
        end_date="2026-07-11",
        param=["HTF_TREND_ENABLED=true", "HTF_TF=H4"],
    )

    request_args = export_cli._request_args(args)

    assert request_args == {
        "cols": "bartime,side",
        "start_date": "2026-07-01",
        "end_date": "2026-07-11",
        "HTF_TREND_ENABLED": "true",
        "HTF_TF": "H4",
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
