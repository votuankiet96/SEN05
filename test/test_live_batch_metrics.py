from __future__ import annotations

import threading

from core_engine.core.live import batch_metrics


def test_batch_metrics_tracks_acceptance_and_database_delivery(monkeypatch):
    metrics = {}
    stats = {"accepted_bars": 0, "staging_rows": 0, "fact_inserted": 0}
    hourly = {
        "fact_bars": 0,
        "staging_rows": 0,
        "pair_bars": {},
        "pair_staging": {},
    }
    monkeypatch.setattr(batch_metrics, "_batch_metrics", metrics)
    monkeypatch.setattr(batch_metrics, "_stats", stats)
    monkeypatch.setattr(batch_metrics, "_hourly_stats", hourly)
    monkeypatch.setattr(batch_metrics, "_batch_metrics_lock", threading.Lock())
    monkeypatch.setattr(batch_metrics, "_state_lock", threading.Lock())
    monkeypatch.setattr(batch_metrics, "_hourly_lock", threading.Lock())

    key = (11, "M5")
    batch_metrics.init_batch(7)
    batch_metrics.record_accepted(7, key, 3)
    batch_metrics.record_db_result(7, key, 3, 2, 2)

    snapshot = batch_metrics.snapshot(7)
    assert snapshot["accepted"] == 3
    assert snapshot["db_processed"] == 3
    assert snapshot["staging_rows"] == 2
    assert snapshot["fact_inserted"] == 2
    assert stats == {"accepted_bars": 3, "staging_rows": 2, "fact_inserted": 2}
    assert hourly["pair_bars"] == {key: 2}
    assert hourly["pair_staging"] == {key: 2}
