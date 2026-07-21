from __future__ import annotations

import pytest

from core_engine.historical import pipeline
from core_engine.historical.runtime_support import HistoricalPullCancelled


def test_pair_retry_backoff_honors_shutdown_before_sleep(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "pull_and_store",
        lambda *args, **kwargs: pipeline.RESULT_TV_EMPTY,
    )
    monkeypatch.setattr(
        pipeline,
        "raise_if_cancelled",
        lambda *args, **kwargs: (_ for _ in ()).throw(HistoricalPullCancelled("stop")),
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(pipeline.time, "sleep", sleep_calls.append)

    with pytest.raises(HistoricalPullCancelled, match="stop"):
        pipeline.pull_with_retry(
            object(),
            {"tv_symbol": "AUDCAD"},
            "H8",
            13,
            max_retries=3,
            allow_replay=False,
        )

    assert sleep_calls == []


def test_pair_retry_backoff_checks_shutdown_each_second(monkeypatch):
    results = iter([pipeline.RESULT_TV_EMPTY, 1])
    monkeypatch.setattr(
        pipeline,
        "pull_and_store",
        lambda *args, **kwargs: next(results),
    )
    checks: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "raise_if_cancelled",
        lambda _logger, where="": checks.append(where),
    )

    clock = {"now": 0.0}
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        pipeline.time,
        "sleep",
        lambda seconds: clock.update(now=clock["now"] + seconds),
    )

    result = pipeline.pull_with_retry(
        object(),
        {"tv_symbol": "AUDCAD"},
        "H8",
        13,
        max_retries=1,
        allow_replay=False,
    )

    assert result == 1
    assert len(checks) == 11
    assert set(checks) == {"pair retry backoff"}

