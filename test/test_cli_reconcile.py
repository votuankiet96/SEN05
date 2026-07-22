from __future__ import annotations

import json
from types import SimpleNamespace

from core_engine.util import cli
from core_engine.shared.warehouse import reconcile


def test_reconcile_json_exposes_go_gate_buckets(monkeypatch, capsys):
    result = reconcile.TimeframeReconcileResult(
        tf_code="D1",
        staging_table="SEN.TF_D1",
        missing_before=5,
        repaired=0,
        missing_after=5,
        supported_missing_before=2,
        supported_mismatched_before=3,
        supported_missing_after=2,
        supported_mismatched_after=3,
        unsupported_calendar_count=7,
    )
    monkeypatch.setattr(reconcile, "reconcile_all", lambda **_kwargs: [result])

    exit_code = cli._run_reconcile_fact(
        SimpleNamespace(
            apply=False,
            timeframes=None,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["supported_missing_fact_rows"] == 2
    assert payload["supported_mismatched_fact_rows"] == 3
    assert payload["unsupported_calendar_rows"] == 7


def test_unsupported_rows_alone_do_not_fail_reconcile_by_default(monkeypatch, capsys):
    result = reconcile.TimeframeReconcileResult(
        tf_code="D1",
        staging_table="SEN.TF_D1",
        missing_before=0,
        repaired=0,
        missing_after=0,
        unsupported_calendar_count=2231,
    )
    monkeypatch.setattr(reconcile, "reconcile_all", lambda **_kwargs: [result])

    exit_code = cli._run_reconcile_fact(
        SimpleNamespace(
            apply=False,
            timeframes=None,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["supported_missing_fact_rows"] == 0
    assert payload["supported_mismatched_fact_rows"] == 0
    assert payload["unsupported_calendar_rows"] == 2231
