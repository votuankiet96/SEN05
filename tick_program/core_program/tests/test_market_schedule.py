"""Validates is_market_closed()'s real-schedule path against DE40's actual
cTrader-fetched schedule (captured live via ProtoOASymbolByIdReq on the real
FTMO account, 2026-08-24) and cross-checked against this project's own
previously-observed real close/open timestamps recorded in memory ("DE40
stops ~19:49 Friday, resumes ~22:05 Sunday"). If cTrader ever changes how it
encodes the week start or holiday date, these are the numbers that would
need updating -- keep them as literal, real captured data, not synthesized.
"""

from datetime import date, datetime, timezone

from src.notify import SESSION_RULES, is_market_closed, run_tick_check

UTC = timezone.utc

# Real schedule fetched for DE40 (GER40.cash) on 2026-08-24. Week seconds
# start Sunday 00:00 in `tz`. Verified: the Friday interval's end (514200s ->
# day5 22:50 Moscow -> 19:50 UTC) matches the ~19:49 UTC Friday close already
# independently observed in tick.DE40 and recorded in memory.
DE40_SCHEDULE = {
    "tz": "Europe/Moscow",
    "intervals": [
        [90300, 172200],    # Monday 01:05 -> 23:50
        [176700, 258600],   # Tuesday 01:05 -> 23:50
        [263100, 345000],   # Wednesday 01:05 -> 23:50
        [349500, 431400],   # Thursday 01:05 -> 23:50
        [435900, 514200],   # Friday 01:05 -> 22:50 (early close)
    ],
    "holidays": [
        {"name": "Memorial Day - Early Close", "date_days": 20598, "recurring": False, "start": 82800, "end": 86399},
    ],
}

SCHEDULES = {"DE40": DE40_SCHEDULE}


def test_open_during_a_normal_weekday_session():
    # Thursday 2026-08-27 12:00 UTC -> 15:00 Moscow, inside 01:05-23:50.
    when = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    assert when.weekday() == 3  # Thursday
    assert is_market_closed("DE40", when, SCHEDULES) is False


def test_closed_during_the_real_daily_gap_between_sessions():
    # Thursday 2026-08-27 21:30 UTC -> Friday 00:30 Moscow: Thursday's
    # session already closed (23:50 Moscow = 20:50 UTC) and Friday's hasn't
    # opened yet (01:05 Moscow = Thursday 22:05 UTC) -- a real ~75min gap,
    # not the cTrader-API-lag theory this project spent days chasing before
    # finding this real schedule data.
    when = datetime(2026, 8, 27, 21, 30, tzinfo=UTC)
    assert is_market_closed("DE40", when, SCHEDULES) is True


def test_open_just_before_the_daily_gap_starts():
    when = datetime(2026, 8, 27, 20, 40, tzinfo=UTC)  # 20:40 UTC, gap starts 20:50
    assert is_market_closed("DE40", when, SCHEDULES) is False


def test_open_right_after_the_daily_gap_ends():
    when = datetime(2026, 8, 27, 22, 10, tzinfo=UTC)  # gap ends 22:05 UTC
    assert is_market_closed("DE40", when, SCHEDULES) is False


def test_open_during_fridays_normal_session():
    # Friday 2026-08-28 15:00 UTC -> 18:00 Moscow, inside 01:05-22:50.
    when = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
    assert when.weekday() == 4  # Friday
    assert is_market_closed("DE40", when, SCHEDULES) is False


def test_closed_after_fridays_early_close_start_of_weekend():
    # Friday's session ends 22:50 Moscow = 19:50 UTC (matches the ~19:49 UTC
    # close already observed and recorded in memory for DE40/UK100).
    when = datetime(2026, 8, 28, 20, 30, tzinfo=UTC)
    assert is_market_closed("DE40", when, SCHEDULES) is True


def test_closed_on_sunday_before_reopen():
    # Sunday 2026-08-30 21:00 UTC -> Monday 00:00 Moscow, before the 01:05 open.
    when = datetime(2026, 8, 30, 21, 0, tzinfo=UTC)
    assert when.weekday() == 6  # Sunday
    assert is_market_closed("DE40", when, SCHEDULES) is True


def test_open_after_sunday_reopen():
    # Matches memory's independently-observed "resumes ~22:05 Sunday" for
    # DE40: Monday's session opens 01:05 Moscow = Sunday 22:05 UTC.
    when = datetime(2026, 8, 30, 22, 30, tzinfo=UTC)
    assert is_market_closed("DE40", when, SCHEDULES) is False


def test_holiday_closure_overrides_an_otherwise_open_weekly_slot():
    # Build a holiday for "today" (whatever day the test runs) using the same
    # epoch-day arithmetic the implementation uses, so this doesn't depend on
    # guessing a real 2026 holiday date's weekday by hand.
    today = date(2026, 8, 27)  # Thursday -- normally open per DE40_SCHEDULE
    day_epoch = (today - date(1970, 1, 1)).days
    schedule = {
        **DE40_SCHEDULE,
        "holidays": [{"name": "Test Holiday", "date_days": day_epoch, "recurring": False, "start": 0, "end": 86399}],
    }
    when = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)  # would be open without the holiday
    assert is_market_closed("DE40", when, {"DE40": schedule}) is True


def test_holiday_on_a_different_date_does_not_affect_this_one():
    today = date(2026, 8, 27)
    other_day_epoch = (today - date(1970, 1, 1)).days + 5
    schedule = {
        **DE40_SCHEDULE,
        "holidays": [{"name": "Test Holiday", "date_days": other_day_epoch, "recurring": False, "start": 0, "end": 86399}],
    }
    when = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    assert is_market_closed("DE40", when, {"DE40": schedule}) is False


def test_unknown_timezone_falls_back_to_session_rules():
    bogus = {**DE40_SCHEDULE, "tz": "Not/A_Real_Zone"}
    # SESSION_RULES' DE40 entry is "weekend_gap" -- it only knows about the
    # weekly close, not the real daily gap the schedule data revealed. This
    # demonstrates exactly why the real schedule is the better primary
    # source: at the same instant the real schedule correctly says "closed"
    # (see test_closed_during_the_real_daily_gap_between_sessions), the old
    # fallback table says "open" because it has no concept of a daily gap.
    assert SESSION_RULES["DE40"]["kind"] == "weekend_gap"
    when = datetime(2026, 8, 27, 21, 30, tzinfo=UTC)  # inside the real daily gap
    assert is_market_closed("DE40", when, {"DE40": bogus}) is False


def test_no_synced_schedule_yet_falls_back_to_session_rules():
    # FR40 *is* in SESSION_RULES (weekday_session 06:00-20:00 UTC) -- with no
    # schedules dict entry at all, behavior must match the pre-existing
    # hand-maintained table exactly (no regression for a symbol not yet synced).
    when_open = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)  # within 06:00-20:00
    when_closed = datetime(2026, 8, 27, 21, 0, tzinfo=UTC)  # after 20:00
    assert is_market_closed("FR40", when_open, {}) is False
    assert is_market_closed("FR40", when_closed, {}) is True
    assert is_market_closed("FR40", when_open, None) is False


def test_btcusd_always_open_regardless_of_schedule_dict():
    when = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
    assert is_market_closed("BTCUSD", when, {}) is False


# ---------------------------------------------------------------------------
# run_tick_check: staleness must be judged by the last error-free fetch
# attempt (tick.IngestState.LastAttemptAtUtc), not raw tick age -- added
# 2026-08-26 to stop alerting on symbols that are genuinely tick-idle
# (evening cTrader materialization lag, just-reopened session) while the
# fetch mechanism itself keeps succeeding cleanly. See notify.py::run_tick_check.
# ---------------------------------------------------------------------------


class _FakeTarget:
    def __init__(self, local_symbol: str) -> None:
        self.local_symbol = local_symbol


class _FakeStore:
    def __init__(self, *, rows, last_ticks, last_attempts, schedules=None) -> None:
        self.targets = {s: _FakeTarget(s) for s in rows}
        self._rows = rows
        self._last_ticks = last_ticks
        self._last_attempts = last_attempts
        self._schedules = schedules or {}

    def tick_row_stats_by_symbol(self):
        return {
            s: {"rows": self._rows[s], "last_tick_utc": self._last_ticks.get(s)}
            for s in self._rows
        }

    def load_symbol_schedules(self):
        return self._schedules

    def load_last_attempt_times(self):
        return {s: v for s, v in self._last_attempts.items() if v is not None}


class _FakeSettings:
    def __init__(self, spool_path, check_stale_seconds=2700):
        self.spool_path = spool_path
        self.check_stale_seconds = check_stale_seconds


def _patch_runtime(monkeypatch):
    import src.notify as notify_mod

    monkeypatch.setattr(notify_mod, "read_service_heartbeat", lambda: None)


def test_check_does_not_flag_stale_when_last_attempt_was_recent(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    store = _FakeStore(
        rows={"US500": 1000},
        last_ticks={"US500": datetime(2026, 8, 26, 8, 0, tzinfo=UTC)},  # 4h old tick
        last_attempts={"US500": datetime(2026, 8, 26, 11, 55, tzinfo=UTC)},  # 5min-old clean attempt
    )
    settings = _FakeSettings(tmp_path / "spool.db")

    import src.notify as notify_mod
    monkeypatch.setattr(notify_mod, "datetime", type("_D", (), {"now": staticmethod(lambda tz=None: now)}))

    report = run_tick_check(settings, store, stale_seconds=2700)
    assert not any(f.code == "stale_historical_tick" for f in report.findings)


def test_check_flags_stale_when_last_attempt_itself_is_old(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch)
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)  # Wednesday, normal trading hours
    store = _FakeStore(
        rows={"US500": 1000},
        last_ticks={"US500": datetime(2026, 8, 26, 8, 0, tzinfo=UTC)},
        last_attempts={"US500": datetime(2026, 8, 26, 10, 0, tzinfo=UTC)},  # 2h-old attempt, well past threshold
    )
    settings = _FakeSettings(tmp_path / "spool.db")

    import src.notify as notify_mod
    monkeypatch.setattr(notify_mod, "datetime", type("_D", (), {"now": staticmethod(lambda tz=None: now)}))

    report = run_tick_check(settings, store, stale_seconds=2700)
    matches = [f for f in report.findings if f.code == "stale_historical_tick"]
    assert len(matches) == 1
    assert "last successful fetch" in matches[0].message


def test_check_falls_back_to_tick_age_when_no_attempt_recorded_yet(monkeypatch, tmp_path):
    """Transitional case: right after this tracking was deployed, existing
    symbols have no LastAttemptAtUtc yet until their next successful fetch."""
    _patch_runtime(monkeypatch)
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    store = _FakeStore(
        rows={"US500": 1000},
        last_ticks={"US500": datetime(2026, 8, 26, 8, 0, tzinfo=UTC)},  # 4h old, past threshold
        last_attempts={"US500": None},
    )
    settings = _FakeSettings(tmp_path / "spool.db")

    import src.notify as notify_mod
    monkeypatch.setattr(notify_mod, "datetime", type("_D", (), {"now": staticmethod(lambda tz=None: now)}))

    report = run_tick_check(settings, store, stale_seconds=2700)
    matches = [f for f in report.findings if f.code == "stale_historical_tick"]
    assert len(matches) == 1
    assert "last tick" in matches[0].message
