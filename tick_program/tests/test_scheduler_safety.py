import time
from types import SimpleNamespace

from tick_engine.scheduler import Job, TickScheduler


class FakeHandle:
    def __init__(self, returncode=None) -> None:
        self.returncode = returncode
        self.pid = 1234
        self.started_mono = time.monotonic()
        self.last_output_mono = self.started_mono
        self.cancel_file = None

    def poll(self):
        return self.returncode

    def idle_seconds(self):
        return 0

    def close(self):
        pass


def make_scheduler(monkeypatch):
    daily = Job("daily-deep-repair", args=["daily"], interval_seconds=60, run_at_startup=True)
    frequent = Job("frequent-backfill", args=["frequent"], interval_seconds=60, run_at_startup=True)
    monkeypatch.setattr("tick_engine.scheduler.build_jobs", lambda _cfg: [daily, frequent])
    handles = []

    def fake_spawn(*_args, **_kwargs):
        handle = FakeHandle()
        handles.append(handle)
        return handle

    monkeypatch.setattr("tick_engine.scheduler.spawn_job", fake_spawn)
    scheduler = TickScheduler(SimpleNamespace(child_idle_timeout_seconds=1800))
    now = time.monotonic()
    scheduler.init_timers(now)
    return scheduler, daily, frequent, handles, now


def test_lower_priority_job_remains_due_when_selected_job_fails(monkeypatch) -> None:
    scheduler, _daily, _frequent, handles, now = make_scheduler(monkeypatch)
    scheduler.tick(now, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert "daily-deep-repair" in scheduler._active

    handles[0].returncode = 1
    scheduler.tick(now + 1, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))

    assert "frequent-backfill" in scheduler._active


def test_lower_priority_job_is_covered_only_after_success(monkeypatch) -> None:
    scheduler, _daily, frequent, handles, now = make_scheduler(monkeypatch)
    scheduler.tick(now, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    handles[0].returncode = 0
    scheduler.tick(now + 1, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))

    assert scheduler._active == {}
    assert frequent._next_run > now + 1
