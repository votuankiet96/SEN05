from src.notify import update_incident_state


def test_recovery_is_emitted_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.configuration.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.configuration.INCIDENT_STATE", tmp_path / "tick_health_incident_state.json")

    assert update_incident_state("ERROR", "2026-07-19T00:00:00+00:00", active=True) is None
    recovery = update_incident_state("OK", "2026-07-19T00:05:00+00:00", active=False)
    assert recovery is not None
    assert recovery["recovered_at_utc"] == "2026-07-19T00:05:00+00:00"
    assert update_incident_state("OK", "2026-07-19T00:10:00+00:00", active=False) is None
