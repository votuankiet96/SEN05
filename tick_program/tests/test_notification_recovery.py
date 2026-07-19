from types import SimpleNamespace

from tick_engine.reporting.notification_policy import (
    TickCheckNotificationDecision,
    update_tick_check_incident_state,
)


def decision(active: bool) -> TickCheckNotificationDecision:
    return TickCheckNotificationDecision(
        notify=active,
        level="ERROR" if active else "INFO",
        title="test",
        conclusion="test",
        action=None,
        details=[],
        technical=[],
        throttle_key="test",
        throttle_seconds=1,
        suppressed=[],
    )


def test_recovery_is_emitted_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("tick_engine.settings.CACHE_DIR", tmp_path)
    incident = SimpleNamespace(status="ERROR", generated_at_utc="2026-07-19T00:00:00+00:00")
    healthy = SimpleNamespace(status="OK", generated_at_utc="2026-07-19T00:05:00+00:00")

    assert update_tick_check_incident_state(incident, decision(True)) is None
    recovery = update_tick_check_incident_state(healthy, decision(False))
    assert recovery is not None
    assert recovery.title == "Tick data recovered"
    assert update_tick_check_incident_state(healthy, decision(False)) is None
