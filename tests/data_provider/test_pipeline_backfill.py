from __future__ import annotations

import logging


def test_run_backfill_refreshes_auth_after_three_consecutive_failures(load_module, monkeypatch):
    pipeline = load_module("data_provider/01_data_pipeline.py", "pipeline")

    stale_pairs = [
        {
            "sym": {"symbol_id": 10, "tv_symbol": "US30"},
            "tf_code": "M5",
            "n_bars": 30,
            "reason": "STALE",
            "gap_hours": 1.0,
        },
        {
            "sym": {"symbol_id": 10, "tv_symbol": "US30"},
            "tf_code": "M15",
            "n_bars": 30,
            "reason": "STALE",
            "gap_hours": 1.0,
        },
        {
            "sym": {"symbol_id": 81, "tv_symbol": "BTCUSD"},
            "tf_code": "M5",
            "n_bars": 30,
            "reason": "STALE",
            "gap_hours": 1.0,
        },
        {
            "sym": {"symbol_id": 81, "tv_symbol": "BTCUSD"},
            "tf_code": "M15",
            "n_bars": 30,
            "reason": "STALE",
            "gap_hours": 1.0,
        },
    ]

    pull_results = iter([-1, -1, -1, 2])
    refresh_calls: list[object] = []
    recompute_calls: list[set[int]] = []
    fake_tv = object()

    monkeypatch.setattr(pipeline, "get_latest_bars", lambda: {})
    monkeypatch.setattr(pipeline, "find_stale_pairs", lambda latest: stale_pairs)
    monkeypatch.setattr(pipeline, "sleep_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_FORCE_RESET_REPULL", False)
    monkeypatch.setattr(
        pipeline,
        "pull_with_retry",
        lambda *args, **kwargs: next(pull_results),
    )
    monkeypatch.setattr(
        pipeline,
        "refresh_mid_run",
        lambda tv, logger: refresh_calls.append(tv) or True,
    )
    monkeypatch.setattr(
        pipeline,
        "recompute_derived",
        lambda updated_sym_ids, logger: recompute_calls.append(set(updated_sym_ids)) or (1, 0),
    )

    stats = pipeline.run_backfill(fake_tv, dry_run=False)

    assert stats["fail"] == 3
    assert stats["ok"] == 1
    assert stats["bars_inserted"] == 2
    assert refresh_calls == [fake_tv]
    assert recompute_calls == [{81}]


def test_run_backfill_uses_short_write_lock_for_gap_mode(load_module, monkeypatch):
    pipeline = load_module("data_provider/01_data_pipeline.py", "pipeline")

    stale_pairs = [
        {
            "sym": {"symbol_id": 10, "tv_symbol": "US30"},
            "tf_code": "M5",
            "n_bars": 30,
            "reason": "STALE",
            "gap_hours": 1.0,
        }
    ]
    events: list[str] = []

    monkeypatch.setattr(pipeline, "get_latest_bars", lambda: {})
    monkeypatch.setattr(pipeline, "find_stale_pairs", lambda latest: stale_pairs)
    monkeypatch.setattr(pipeline, "sleep_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_FORCE_RESET_REPULL", False)
    monkeypatch.setattr(
        pipeline,
        "wait_for_historical_slot",
        lambda owner, logger, **kwargs: events.append(f"wait:{owner}") or True,
    )
    monkeypatch.setattr(
        pipeline,
        "_acquire_short_write_lock",
        lambda task_name, logger, **kwargs: events.append(f"lock:{kwargs['action']}") or True,
    )
    monkeypatch.setattr(
        pipeline,
        "release",
        lambda task_name: events.append(f"release:{task_name}"),
    )
    monkeypatch.setattr(
        pipeline,
        "pull_with_retry",
        lambda *args, **kwargs: events.append("pull") or 2,
    )
    monkeypatch.setattr(
        pipeline,
        "recompute_derived",
        lambda updated_sym_ids, logger: events.append(f"derived:{sorted(updated_sym_ids)}") or (1, 0),
    )

    stats = pipeline.run_backfill(object(), dry_run=False, write_lock_name="warehouse_maintenance")

    assert stats["fail"] == 0
    assert stats["ok"] == 1
    assert stats["bars_inserted"] == 2
    assert events == [
        "wait:pipeline-maint",
        "lock:backfill US30 M5",
        "pull",
        "release:warehouse_maintenance",
        "wait:pipeline-maint",
        "lock:recompute derived batch",
        "derived:[10]",
        "release:warehouse_maintenance",
    ]


class _DummyLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None


def test_main_cleans_expired_locks_before_waiting_for_historical_slot(load_module, monkeypatch):
    pipeline = load_module("data_provider/01_data_pipeline.py", "pipeline")

    events: list[str] = []

    monkeypatch.setattr(pipeline, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(pipeline, "test_connection", lambda: True)
    monkeypatch.setattr(pipeline, "tg_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "tg_flush", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "detect_mode", lambda: "gap")
    monkeypatch.setattr(pipeline, "cleanup_expired", lambda: events.append("cleanup") or 1)
    monkeypatch.setattr(
        pipeline,
        "acquire_historical_job",
        lambda *args, **kwargs: events.append("acquire:tv_historical_job") or None,
    )

    def _fake_acquire(task_name: str, duration_min: int = 90) -> bool:
        events.append(f"acquire:{task_name}")
        return False

    monkeypatch.setattr(pipeline, "acquire", _fake_acquire)
    monkeypatch.setattr(
        pipeline.sys,
        "argv",
        ["01_data_pipeline.py", "--mode", "gap"],
    )

    exit_code = pipeline.main()

    assert exit_code == 1
    assert events == ["cleanup", "acquire:tv_historical_job"]


def test_main_gap_mode_skips_broad_maintenance_lock_and_uses_cooperative_writes(load_module, monkeypatch):
    pipeline = load_module("data_provider/01_data_pipeline.py", "pipeline")

    events: list[str] = []

    monkeypatch.setattr(pipeline, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(pipeline, "test_connection", lambda: True)
    monkeypatch.setattr(pipeline, "tg_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "tg_flush", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "detect_mode", lambda: "gap")
    monkeypatch.setattr(pipeline, "cleanup_expired", lambda: events.append("cleanup") or 1)
    monkeypatch.setattr(
        pipeline,
        "acquire_historical_job",
        lambda *args, **kwargs: events.append("acquire:tv_historical_job") or object(),
    )
    monkeypatch.setattr(
        pipeline,
        "release_historical_job",
        lambda *args, **kwargs: events.append("release:tv_historical_job"),
    )
    monkeypatch.setattr(
        pipeline,
        "get_valid_tv_connection",
        lambda *args, **kwargs: (events.append("tv_ready") or object(), "session_refresh"),
    )
    monkeypatch.setattr(
        pipeline,
        "run_backfill",
        lambda tv, dry_run=False, write_lock_name=None: (
            events.append(f"run_backfill:{write_lock_name}") or
            {"fail": 0, "ok": 1, "total": 1, "bars_inserted": 2, "miss_count": 0, "fail_pairs": []}
        ),
    )
    monkeypatch.setattr(pipeline, "purge_staging", lambda days_to_keep=7: {})
    monkeypatch.setattr(
        pipeline,
        "acquire",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gap mode must not take broad maintenance lock")),
    )
    monkeypatch.setattr(
        pipeline.sys,
        "argv",
        ["01_data_pipeline.py", "--mode", "gap"],
    )

    exit_code = pipeline.main()

    assert exit_code == 0
    assert events == [
        "cleanup",
        "acquire:tv_historical_job",
        "tv_ready",
        "run_backfill:warehouse_maintenance",
        "release:tv_historical_job",
    ]


def test_main_blocks_full_mode_when_ws_live_runtime_is_active(load_module, monkeypatch):
    pipeline = load_module("data_provider/01_data_pipeline.py", "pipeline")

    events: list[str] = []

    monkeypatch.setattr(pipeline, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(pipeline, "test_connection", lambda: True)
    monkeypatch.setattr(pipeline, "tg_alert", lambda *args, **kwargs: events.append("tg_alert"))
    monkeypatch.setattr(pipeline, "tg_flush", lambda *args, **kwargs: events.append("tg_flush"))
    monkeypatch.setattr(pipeline, "cleanup_expired", lambda: events.append("cleanup") or 1)
    monkeypatch.setattr(
        pipeline,
        "is_locked",
        lambda name: name == "ws_live_runtime",
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_historical_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full mode should abort before historical slot wait")),
    )
    monkeypatch.setattr(
        pipeline.sys,
        "argv",
        ["01_data_pipeline.py", "--mode", "full"],
    )

    exit_code = pipeline.main()

    assert exit_code == 1
    assert events == ["cleanup", "tg_alert", "tg_alert", "tg_flush"]
