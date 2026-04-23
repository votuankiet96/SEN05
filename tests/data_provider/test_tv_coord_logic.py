from __future__ import annotations


class _DummyLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


def test_wait_for_live_batch_clear_returns_true_after_lock_clears(load_module, monkeypatch):
    tv_coord = load_module("data_provider/_tv_coord.py", "tv_coord")
    logger = _DummyLogger()

    lock_states = iter([True, False])
    monotonic_values = iter([0.0, 10.0])
    slept: list[float] = []

    monkeypatch.setattr(tv_coord, "is_locked", lambda name: next(lock_states))
    monkeypatch.setattr(tv_coord.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(tv_coord.time, "sleep", slept.append)

    cleared = tv_coord.wait_for_live_batch_clear(
        "checker",
        logger,
        max_wait_sec=30.0,
        poll_sec=5.0,
    )

    assert cleared is True
    assert slept == [5.0]


def test_wait_for_live_batch_clear_times_out(load_module, monkeypatch):
    tv_coord = load_module("data_provider/_tv_coord.py", "tv_coord")
    logger = _DummyLogger()

    monotonic_values = iter([0.0, 31.0])

    monkeypatch.setattr(tv_coord, "is_locked", lambda name: True)
    monkeypatch.setattr(tv_coord.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(tv_coord.time, "sleep", lambda seconds: None)

    cleared = tv_coord.wait_for_live_batch_clear(
        "checker",
        logger,
        max_wait_sec=30.0,
        poll_sec=5.0,
    )

    assert cleared is False


def test_wait_for_historical_slot_returns_after_current_batch_when_ws_live_idle(load_module, monkeypatch):
    tv_coord = load_module("data_provider/_tv_coord.py", "tv_coord")
    logger = _DummyLogger()

    wait_calls: list[str] = []
    slept: list[float] = []

    monkeypatch.setattr(
        tv_coord,
        "wait_for_live_batch_clear",
        lambda owner, logger, **kwargs: wait_calls.append(owner) or True,
    )
    monkeypatch.setattr(tv_coord, "is_locked", lambda name: False)
    monkeypatch.setattr(tv_coord.time, "sleep", slept.append)

    cleared = tv_coord.wait_for_historical_slot("pipeline", logger)

    assert cleared is True
    assert wait_calls == ["pipeline"]
    assert slept == []


def test_wait_for_historical_slot_yields_before_upcoming_live_boundary(load_module, monkeypatch):
    tv_coord = load_module("data_provider/_tv_coord.py", "tv_coord")
    logger = _DummyLogger()

    wait_calls: list[str] = []
    slept: list[float] = []

    monkeypatch.setattr(
        tv_coord,
        "wait_for_live_batch_clear",
        lambda owner, logger, **kwargs: wait_calls.append(owner) or True,
    )
    monkeypatch.setattr(
        tv_coord,
        "is_locked",
        lambda name: name == tv_coord.WS_LIVE_RUNTIME_LOCK,
    )
    monkeypatch.setattr(tv_coord, "_seconds_until_next_live_boundary", lambda: 9.0)
    monkeypatch.setattr(tv_coord.time, "sleep", slept.append)

    cleared = tv_coord.wait_for_historical_slot(
        "checker",
        logger,
        yield_before_batch_sec=20.0,
        handoff_grace_sec=6.0,
    )

    assert cleared is True
    assert wait_calls == ["checker", "checker"]
    assert slept == [15.0]


def test_sleep_between_historical_requests_yields_to_ws_live(load_module, monkeypatch):
    tv_coord = load_module("data_provider/_tv_coord.py", "tv_coord")

    slept: list[float] = []

    monkeypatch.setattr(tv_coord, "is_locked", lambda name: True)
    monkeypatch.setattr(tv_coord.time, "sleep", slept.append)

    delay = tv_coord.sleep_between_historical_requests("GOLD")

    assert delay == 12.0
    assert slept == [12.0]
