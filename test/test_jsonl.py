"""Tests for core_engine.logkit.jsonl.append_jsonl_capped - the Medium-15
fix for live_fetching_summary.jsonl / historical_pulling_summary.jsonl
growing forever. Both files used to be appended to via a bare
open(path, "a") with no cap, and health.cleanup_old_runtime_files' age-
based retention cannot bound an actively-written file (its mtime is
refreshed on every append, so it never looks "old" regardless of how large
it grows or how old its oldest records are).
"""

from __future__ import annotations

import json

from core_engine.logkit.jsonl import append_jsonl_capped


def test_append_adds_one_line_per_call(tmp_path):
    path = tmp_path / "summary.jsonl"
    append_jsonl_capped(path, {"n": 1})
    append_jsonl_capped(path, {"n": 2})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_file_stays_under_keep_lines_once_max_bytes_is_exceeded(tmp_path):
    path = tmp_path / "summary.jsonl"
    # Small max_bytes so a handful of small rows already exceeds it -
    # keeps the test fast without needing megabytes of fixture data.
    for i in range(50):
        append_jsonl_capped(path, {"n": i, "pad": "x" * 20}, max_bytes=500, keep_lines=10)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 10
    # The most recent rows must survive the trim, not the oldest ones.
    assert json.loads(lines[-1])["n"] == 49


def test_never_trims_when_max_bytes_is_zero_or_negative(tmp_path):
    path = tmp_path / "summary.jsonl"
    for i in range(20):
        append_jsonl_capped(path, {"n": i}, max_bytes=0, keep_lines=5)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20  # no cap applied


def test_only_reads_the_last_line_pattern_still_works_after_trim(tmp_path):
    # health._read_last_jsonl only ever reads the final line - confirm
    # that line is always the most recent row after a trim, matching what
    # that reader actually depends on.
    path = tmp_path / "summary.jsonl"
    for i in range(30):
        append_jsonl_capped(path, {"n": i, "pad": "y" * 20}, max_bytes=300, keep_lines=5)

    last_line = path.read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(last_line)["n"] == 29
