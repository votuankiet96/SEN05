from __future__ import annotations

import logging

from core_engine.core.live.telemetry import LiveReporter


def _classify(*, spool_count: int, spool_oldest_age_seconds: float | None):
    reporter = LiveReporter(logging.getLogger("test_live_health"), {})
    return reporter.classify_health(
        recent_errors=0,
        total_errors=0,
        last_hour_accepted=10,
        last_hour_saved=10,
        stale_count=0,
        source_lag_count=0,
        source_lag_entries=[],
        spool_count=spool_count,
        spool_oldest_age_seconds=spool_oldest_age_seconds,
        recent_ws_errors=0,
        total_ws_errors=0,
        n_miss_active=0,
        is_guest=False,
        consecutive_guest_batches=0,
    )


def test_fresh_in_flight_outbox_row_does_not_degrade_live_health():
    status, level, issues = _classify(
        spool_count=1,
        spool_oldest_age_seconds=2.0,
    )

    assert status == "HEALTHY"
    assert level == "SUCCESS"
    assert issues == []


def test_outbox_row_waiting_over_15_minutes_is_critical():
    status, level, issues = _classify(
        spool_count=2,
        spool_oldest_age_seconds=16 * 60,
    )

    assert status == "CRITICAL"
    assert level == "ERROR"
    assert any("Fact" in issue and "16" in issue for issue in issues)
