"""Thread-safe accounting for one live-fetch batch and its DB delivery."""

from __future__ import annotations

import time

from core_engine.core.live.state import (
    BATCH_DB_REPORT_WAIT_SEC,
    MAX_BATCH_METRIC_HISTORY,
    _batch_metrics,
    _batch_metrics_lock,
    _hourly_lock,
    _hourly_stats,
    _shutdown,
    _state_lock,
    _stats,
)


def _empty_metrics() -> dict:
    return {
        "accepted": 0,
        "db_processed": 0,
        "staging_rows": 0,
        "fact_inserted": 0,
    }


def init_batch(batch_id: int) -> None:
    with _batch_metrics_lock:
        _batch_metrics[batch_id] = _empty_metrics()
        if len(_batch_metrics) > MAX_BATCH_METRIC_HISTORY:
            for old_batch_id in sorted(_batch_metrics)[:-MAX_BATCH_METRIC_HISTORY]:
                if old_batch_id != 0:
                    _batch_metrics.pop(old_batch_id, None)


def record_accepted(batch_id: int, key: tuple[int, str], count: int) -> None:
    if count <= 0:
        return
    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(batch_id, _empty_metrics())
        metrics["accepted"] += count
    with _state_lock:
        _stats["accepted_bars"] += count


def record_db_result(
    batch_id: int,
    key: tuple[int, str],
    accepted_count: int,
    staging_rows: int,
    fact_inserted: int,
) -> None:
    staging_rows = max(0, int(staging_rows or 0))
    fact_inserted = max(0, int(fact_inserted or 0))
    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(batch_id, _empty_metrics())
        metrics["db_processed"] += max(0, accepted_count)
        metrics["staging_rows"] += staging_rows
        metrics["fact_inserted"] += fact_inserted

    if fact_inserted:
        with _hourly_lock:
            _hourly_stats["fact_bars"] += fact_inserted
            _hourly_stats["pair_bars"][key] = _hourly_stats["pair_bars"].get(key, 0) + fact_inserted

    if staging_rows or fact_inserted:
        with _state_lock:
            _stats["staging_rows"] += staging_rows
            _stats["fact_inserted"] += fact_inserted
        if staging_rows:
            with _hourly_lock:
                _hourly_stats["staging_rows"] += staging_rows
                _hourly_stats["pair_staging"][key] = (
                    _hourly_stats["pair_staging"].get(key, 0) + staging_rows
                )


def snapshot(batch_id: int) -> dict:
    with _batch_metrics_lock:
        metrics = dict(_batch_metrics.get(batch_id, {}))
        return metrics


def wait_for_db(batch_id: int, timeout_sec: float = BATCH_DB_REPORT_WAIT_SEC) -> dict:
    deadline = time.monotonic() + timeout_sec
    while True:
        metrics = snapshot(batch_id)
        accepted = int(metrics.get("accepted", 0))
        processed = int(metrics.get("db_processed", 0))
        if accepted == 0 or processed >= accepted or time.monotonic() >= deadline:
            return metrics
        _shutdown.wait(0.25)
        if _shutdown.is_set():
            return snapshot(batch_id)
