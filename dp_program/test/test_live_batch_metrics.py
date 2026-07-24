from __future__ import annotations

import threading

from core_engine.core.live import runtime


def test_batch_metrics_tracks_acceptance_and_database_delivery(monkeypatch):
    metrics = {}
    stats = {"accepted_bars": 0, "staging_rows": 0, "fact_inserted": 0}
    hourly = {
        "fact_bars": 0,
        "staging_rows": 0,
        "pair_bars": {},
        "pair_staging": {},
    }
    monkeypatch.setattr(runtime, "_batch_metrics", metrics)
    monkeypatch.setattr(runtime, "_stats", stats)
    monkeypatch.setattr(runtime, "_hourly_stats", hourly)
    monkeypatch.setattr(runtime, "_batch_metrics_lock", threading.Lock())
    monkeypatch.setattr(runtime, "_state_lock", threading.Lock())
    monkeypatch.setattr(runtime, "_hourly_lock", threading.Lock())

    key = (11, "M5")
    runtime.init_batch_metrics(7)
    runtime.record_accepted(7, key, 3)
    runtime.record_db_result(7, key, 3, 2, 2)

    snapshot = runtime.snapshot_batch_metrics(7)
    assert snapshot["accepted"] == 3
    assert snapshot["db_processed"] == 3
    assert snapshot["staging_rows"] == 2
    assert snapshot["fact_inserted"] == 2
    assert stats == {"accepted_bars": 3, "staging_rows": 2, "fact_inserted": 2}
    assert hourly["pair_bars"] == {key: 2}
    assert hourly["pair_staging"] == {key: 2}
