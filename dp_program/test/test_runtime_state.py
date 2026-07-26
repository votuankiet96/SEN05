"""Fault-injection coverage for long-running process state files."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core_engine.util.primitives import runtime_state
from core_engine.util.primitives.runtime_state import RuntimeStateWriter, read_json_snapshot


def test_snapshot_reader_retries_windows_sharing_violation(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"status":"running","batch_completed_at":"watermark"}', encoding="utf-8")
    original_read_text = Path.read_text
    calls = 0

    def _flaky_read_text(self, *args, **kwargs):
        nonlocal calls
        if self == path:
            calls += 1
            if calls == 1:
                raise PermissionError("fault-injected sharing violation")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _flaky_read_text)

    result = read_json_snapshot(path, attempts=3, base_delay_sec=0)

    assert result.ok is True
    assert result.attempts == 2
    assert result.error is None
    assert result.data["batch_completed_at"] == "watermark"


def test_snapshot_reader_distinguishes_unreadable_from_empty_json(tmp_path, monkeypatch):
    path = tmp_path / "state.json"

    def _unreadable(*_args, **_kwargs):
        raise PermissionError("still locked")

    monkeypatch.setattr(Path, "read_text", _unreadable)

    result = read_json_snapshot(path, attempts=3, base_delay_sec=0)

    assert result.ok is False
    assert result.data is None
    assert isinstance(result.error, PermissionError)
    assert result.attempts == 3


def test_state_writer_heartbeat_preserves_semantic_fields_without_disk_merge(tmp_path, monkeypatch):
    writes: list[dict] = []
    monkeypatch.setattr(
        runtime_state,
        "atomic_write_json",
        lambda _path, payload, **_kwargs: writes.append(payload.copy()),
    )
    writer = RuntimeStateWriter(
        tmp_path / "state.json",
        SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    writer.write(
        status="waiting",
        child_started_at="child-watermark",
        batch_completed_at="batch-watermark",
    )
    writer.write(heartbeat="alive")

    assert len(writes) == 2
    assert writes[-1]["child_started_at"] == "child-watermark"
    assert writes[-1]["batch_completed_at"] == "batch-watermark"
    assert writes[-1]["heartbeat"] == "alive"


def test_state_writer_retains_full_snapshot_after_one_failed_write(tmp_path, monkeypatch):
    writes: list[dict] = []
    calls = 0

    def _fail_once(_path, payload, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("fault-injected replace failure")
        writes.append(payload.copy())

    monkeypatch.setattr(runtime_state, "atomic_write_json", _fail_once)
    writer = RuntimeStateWriter(
        tmp_path / "state.json",
        SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    writer.write(
        status="waiting",
        child_started_at="child-watermark",
        batch_completed_at="batch-watermark",
    )
    writer.write(heartbeat="alive")

    assert len(writes) == 1
    assert writes[0]["child_started_at"] == "child-watermark"
    assert writes[0]["batch_completed_at"] == "batch-watermark"
    assert writes[0]["heartbeat"] == "alive"
