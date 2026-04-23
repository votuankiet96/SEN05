from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_compare_classifies_missing_mismatch_volume_only_and_extra(load_module):
    checker = load_module("data_provider/04_checker.py", "checker")

    t0 = datetime(2026, 4, 1, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    t2 = t1 + timedelta(minutes=5)
    t3 = t2 + timedelta(minutes=5)

    tv_dict = {
        t0: (10.0, 11.0, 9.0, 10.5, 100.0),
        t1: (20.0, 21.0, 19.0, 20.5, 200.0),
        t2: (30.0, 31.0, 29.0, 30.5, 300.0),
    }
    db_dict = {
        t1: (25.0, 26.0, 24.0, 25.5, 200.0),
        t2: (30.0, 31.0, 29.0, 30.5, 360.0),
        t3: (40.0, 41.0, 39.0, 40.5, 400.0),
    }

    missing, mismatched, volume_only, extra, rate = checker._compare(tv_dict, db_dict)

    assert missing == {t0}
    assert set(mismatched) == {t1}
    assert set(volume_only) == {t2}
    assert extra == {t3}
    assert rate == 1.0


def test_choose_repair_strategy_escalates_on_m45_anchor_drift(load_module):
    checker = load_module("data_provider/04_checker.py", "checker")

    start = datetime(2026, 1, 1, 0, 0)
    tv_times = {start + timedelta(minutes=45 * i) for i in range(100)}
    db_times = {start + timedelta(minutes=30) + timedelta(minutes=45 * i) for i in range(100)}
    missing = set(sorted(tv_times)[:50])

    strategy, reason = checker._choose_repair_strategy(
        {"tv_symbol": "GOLD"},
        "M45",
        {
            "tv_bars": {dt: (1.0, 2.0, 0.5, 1.5, 10.0) for dt in tv_times},
            "db_bars": {dt: (1.0, 2.0, 0.5, 1.5, 10.0) for dt in db_times},
            "missing": missing,
            "mismatched": {},
            "extra": set(),
            "rate": 0.50,
        },
    )

    assert strategy == "full_repull"
    assert "anchor drift" in reason


class _DummyLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None


def test_main_rebuild_computed_dry_run_skips_locks(load_module, monkeypatch):
    checker = load_module("data_provider/04_checker.py", "checker")

    sent: list[str] = []
    printed: list[str] = []

    monkeypatch.setattr(checker, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(checker, "_configure_checker_library_logs", lambda: None)
    monkeypatch.setattr(checker, "_log_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "test_connection", lambda: True)
    monkeypatch.setattr(checker, "cleanup_expired", lambda: 0)
    monkeypatch.setattr(
        checker,
        "_acquire_repair_locks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run rebuild must not acquire locks")
        ),
    )
    monkeypatch.setattr(
        checker,
        "_release_repair_locks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run rebuild must not release locks")
        ),
    )
    monkeypatch.setattr(checker, "rebuild_computed_tfs", lambda **kwargs: "REBUILD OK")
    monkeypatch.setattr(checker, "tg_send", sent.append)
    monkeypatch.setattr(checker, "tg_flush", lambda: sent.append("FLUSH"))
    monkeypatch.setattr(checker, "print", lambda value="": printed.append(value), raising=False)
    monkeypatch.setattr(
        checker.sys,
        "argv",
        ["04_checker.py", "--rebuild-computed", "--dry-run"],
    )

    with pytest.raises(SystemExit) as exc:
        checker.main()

    assert exc.value.code == 0
    assert printed == ["REBUILD OK"]
    assert sent[0].startswith("<b>[Checker] Rebuild Computed TFs</b>")
    assert sent[-1] == "FLUSH"


def test_main_rebuild_computed_live_acquires_and_releases_locks(load_module, monkeypatch):
    checker = load_module("data_provider/04_checker.py", "checker")

    sent: list[str] = []
    printed: list[str] = []
    releases: list[tuple[object, object]] = []
    heartbeat = object()

    monkeypatch.setattr(checker, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(checker, "_configure_checker_library_logs", lambda: None)
    monkeypatch.setattr(checker, "_log_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "test_connection", lambda: True)
    monkeypatch.setattr(checker, "cleanup_expired", lambda: 1)
    monkeypatch.setattr(
        checker,
        "_acquire_repair_locks",
        lambda logger, duration_min=90: heartbeat,
    )
    monkeypatch.setattr(
        checker,
        "_release_repair_locks",
        lambda stop, logger: releases.append((stop, logger)),
    )
    monkeypatch.setattr(checker, "rebuild_computed_tfs", lambda **kwargs: "REBUILD OK")
    monkeypatch.setattr(checker, "_format_derived_tickcount_summary", lambda: "TICKS")
    monkeypatch.setattr(checker, "_format_derived_continuity_summary", lambda: "CONTINUITY")
    monkeypatch.setattr(checker, "tg_send", sent.append)
    monkeypatch.setattr(checker, "tg_flush", lambda: sent.append("FLUSH"))
    monkeypatch.setattr(checker, "print", lambda value="": printed.append(value), raising=False)
    monkeypatch.setattr(checker.sys, "argv", ["04_checker.py", "--rebuild-computed"])

    with pytest.raises(SystemExit) as exc:
        checker.main()

    assert exc.value.code == 0
    assert printed == ["REBUILD OK", "", "TICKS", "", "CONTINUITY"]
    assert releases and releases[0][0] is heartbeat
    assert sent[0].startswith("<b>[Checker] Rebuild Computed TFs</b>")
    assert sent[-1] == "FLUSH"


def test_issues_to_pending_pairs_deduplicates_and_filters_unknown_symbols(load_module):
    checker = load_module("data_provider/04_checker.py", "checker")

    symbols = [
        {"tv_symbol": "BTCUSD", "symbol_id": 1},
        {"tv_symbol": "US30", "symbol_id": 2},
    ]
    issues = [
        {"sym": "BTCUSD", "tf": "m5", "reason": "missing"},
        {"sym": "BTCUSD", "tf": "M5", "reason": "duplicate"},
        {"sym": "US30", "tf": "H1", "reason": "bad"},
        {"sym": "UNKNOWN", "tf": "M15", "reason": "skip"},
    ]

    pending = checker._issues_to_pending_pairs(issues, symbols)

    assert pending == [
        (symbols[0], "M5"),
        (symbols[1], "H1"),
    ]


def test_main_auto_mode_repairs_only_scanned_issue_pairs(load_module, monkeypatch):
    checker = load_module("data_provider/04_checker.py", "checker")

    sent: list[str] = []
    releases: list[tuple[object, object]] = []
    heartbeat = object()
    run_calls: list[dict] = []
    symbols = [{"tv_symbol": "BTCUSD", "symbol_id": 1, "tv_exchange": "X"}]

    monkeypatch.setattr(checker, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(checker, "_configure_checker_library_logs", lambda: None)
    monkeypatch.setattr(checker, "_log_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "test_connection", lambda: True)
    monkeypatch.setattr(checker, "cleanup_expired", lambda: 0)
    monkeypatch.setattr(
        checker,
        "acquire_historical_job",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(checker, "release_historical_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "get_valid_tv_connection", lambda logger: (object(), "session_refresh"))
    monkeypatch.setattr(checker, "SYMBOLS", symbols)
    monkeypatch.setattr(checker, "_build_interval_map", lambda: {"M5": object()})
    monkeypatch.setattr(checker, "tg_send", sent.append)
    monkeypatch.setattr(checker, "tg_flush", lambda: sent.append("FLUSH"))
    monkeypatch.setattr(checker, "_build_report_clean", lambda *args, **kwargs: "REPORT")
    monkeypatch.setattr(checker, "sleep_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        checker,
        "_acquire_repair_locks",
        lambda logger, duration_min=90: heartbeat,
    )
    monkeypatch.setattr(
        checker,
        "_release_repair_locks",
        lambda stop, logger: releases.append((stop, logger)),
    )

    def _fake_run_checker(tv, symbols, tfs, interval_map, dry_run, threshold, logger, pending_pairs=None):
        run_calls.append(
            {
                "dry_run": dry_run,
                "pending_pairs": pending_pairs,
                "symbols": [sym["tv_symbol"] for sym in symbols],
                "tfs": list(tfs),
            }
        )
        if dry_run:
            return {
                "total": 1,
                "ok": 0,
                "repaired": 0,
                "failed": 0,
                "failures": [],
                "dry_run": True,
                "volume_advisory_pairs": 0,
                "volume_advisory_bars": 0,
                "dry_issues": [{"sym": "BTCUSD", "tf": "M5", "reason": "missing"}],
            }
        return {
            "total": 1,
            "ok": 0,
            "repaired": 1,
            "failed": 0,
            "failures": [],
            "dry_run": False,
            "volume_advisory_pairs": 0,
            "volume_advisory_bars": 0,
            "dry_issues": [],
        }

    monkeypatch.setattr(checker, "run_checker", _fake_run_checker)
    monkeypatch.setattr(checker.sys, "argv", ["04_checker.py", "--sym", "BTCUSD", "--tf", "M5"])

    with pytest.raises(SystemExit) as exc:
        checker.main()

    assert exc.value.code == 0
    assert len(run_calls) == 2
    assert run_calls[0]["dry_run"] is True
    assert run_calls[0]["symbols"] == ["BTCUSD"]
    assert run_calls[0]["tfs"] == ["M5"]
    assert run_calls[0]["pending_pairs"] is None
    assert run_calls[1]["dry_run"] is False
    assert run_calls[1]["symbols"] == ["BTCUSD"]
    assert run_calls[1]["tfs"] == ["M5"]
    assert run_calls[1]["pending_pairs"] is None
    assert releases and releases[0][0] is heartbeat
    assert "REPORT" in sent


def test_main_auto_mode_rolls_symbol_by_symbol_and_locks_only_on_issue(load_module, monkeypatch):
    checker = load_module("data_provider/04_checker.py", "checker")

    sent: list[str] = []
    releases: list[tuple[object, object]] = []
    heartbeat = object()
    run_calls: list[dict] = []
    report_stats: list[dict] = []
    slept: list[str] = []
    symbols = [
        {"tv_symbol": "BTCUSD", "symbol_id": 1, "tv_exchange": "X"},
        {"tv_symbol": "ETHUSD", "symbol_id": 2, "tv_exchange": "X"},
    ]

    monkeypatch.setattr(checker, "setup_logger", lambda *args, **kwargs: _DummyLogger())
    monkeypatch.setattr(checker, "_configure_checker_library_logs", lambda: None)
    monkeypatch.setattr(checker, "_log_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "test_connection", lambda: True)
    monkeypatch.setattr(checker, "cleanup_expired", lambda: 0)
    monkeypatch.setattr(
        checker,
        "acquire_historical_job",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(checker, "release_historical_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(checker, "get_valid_tv_connection", lambda logger: (object(), "session_refresh"))
    monkeypatch.setattr(checker, "SYMBOLS", symbols)
    monkeypatch.setattr(checker, "_build_interval_map", lambda: {"M5": object()})
    monkeypatch.setattr(checker, "tg_send", sent.append)
    monkeypatch.setattr(checker, "tg_flush", lambda: sent.append("FLUSH"))
    monkeypatch.setattr(
        checker,
        "_build_report_clean",
        lambda stats, *args, **kwargs: report_stats.append(dict(stats)) or "REPORT",
    )
    monkeypatch.setattr(checker, "sleep_for", lambda tv_symbol: slept.append(tv_symbol))
    monkeypatch.setattr(
        checker,
        "_acquire_repair_locks",
        lambda logger, duration_min=90: heartbeat,
    )
    monkeypatch.setattr(
        checker,
        "_release_repair_locks",
        lambda stop, logger: releases.append((stop, logger)),
    )

    def _fake_run_checker(tv, symbols, tfs, interval_map, dry_run, threshold, logger, pending_pairs=None):
        sym_name = symbols[0]["tv_symbol"]
        run_calls.append(
            {
                "sym": sym_name,
                "dry_run": dry_run,
                "pending_pairs": pending_pairs,
                "tfs": list(tfs),
            }
        )
        if sym_name == "BTCUSD" and dry_run:
            return {
                "total": 1,
                "ok": 1,
                "repaired": 0,
                "failed": 0,
                "failures": [],
                "dry_run": True,
                "volume_advisory_pairs": 0,
                "volume_advisory_bars": 0,
                "dry_issues": [],
            }
        if sym_name == "ETHUSD" and dry_run:
            return {
                "total": 1,
                "ok": 0,
                "repaired": 0,
                "failed": 0,
                "failures": [],
                "dry_run": True,
                "volume_advisory_pairs": 1,
                "volume_advisory_bars": 3,
                "dry_issues": [{"sym": "ETHUSD", "tf": "M5", "reason": "missing"}],
            }
        if sym_name == "ETHUSD" and not dry_run:
            return {
                "total": 1,
                "ok": 0,
                "repaired": 1,
                "failed": 0,
                "failures": [],
                "dry_run": False,
                "volume_advisory_pairs": 99,
                "volume_advisory_bars": 99,
                "dry_issues": [],
            }
        raise AssertionError(f"Unexpected run_checker call for {sym_name} dry_run={dry_run}")

    monkeypatch.setattr(checker, "run_checker", _fake_run_checker)
    monkeypatch.setattr(checker.sys, "argv", ["04_checker.py", "--tf", "M5"])

    with pytest.raises(SystemExit) as exc:
        checker.main()

    assert exc.value.code == 0
    assert run_calls == [
        {"sym": "BTCUSD", "dry_run": True, "pending_pairs": None, "tfs": ["M5"]},
        {"sym": "ETHUSD", "dry_run": True, "pending_pairs": None, "tfs": ["M5"]},
        {"sym": "ETHUSD", "dry_run": False, "pending_pairs": None, "tfs": ["M5"]},
    ]
    assert releases and releases[0][0] is heartbeat
    assert slept == ["ETHUSD"]
    assert report_stats == [
        {
            "total": 2,
            "ok": 1,
            "repaired": 1,
            "failed": 0,
            "failures": [],
            "dry_run": False,
            "volume_advisory_pairs": 1,
            "volume_advisory_bars": 3,
            "dry_issues": [],
            "skipped_tfs": [],
        }
    ]
    assert "REPORT" in sent
