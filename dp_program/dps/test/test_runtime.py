"""Vòng đời: khóa một-instance, state ghi giãn nhịp, yêu cầu dừng, manifest, nhận biết tiến trình còn sống."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import pytest

import runtime
from runtime import InstanceLockError, Runtime, instance_lock, process_alive, read_state, request_stop, service_status


def test_a_second_instance_on_the_same_runtime_dir_is_refused_and_the_lock_is_released(tmp_path):
    with instance_lock(tmp_path):
        with pytest.raises(InstanceLockError):
            with instance_lock(tmp_path):
                pass
    with instance_lock(tmp_path):        # khóa đã được nhả
        pass


def test_state_is_merged_throttled_and_forced(tmp_path):
    rt = Runtime(tmp_path)
    assert rt.write_state(status="running", tick_seq=1) is True              # lần đầu luôn ghi
    assert rt.write_state(tick_seq=2) is False                               # trong khoảng giãn nhịp: chỉ gộp, chưa ghi
    assert read_state(tmp_path)["tick_seq"] == 1
    assert rt.write_state(force=True) is True
    state = read_state(tmp_path)
    assert (state["status"], state["tick_seq"], state["pid"], state["version"]) == ("running", 2, os.getpid(), runtime.VERSION)
    assert "heartbeat_at" in state


def test_stop_request_file_is_seen_and_can_be_cleared(tmp_path):
    rt = Runtime(tmp_path)
    assert not rt.stop_requested()
    request_stop(tmp_path, wait_seconds=0)
    assert rt.stop_requested()
    rt.clear_stop_request()
    assert not rt.stop_requested()


def test_manifest_is_written_atomically_as_json(tmp_path):
    Runtime(tmp_path).write_manifest({"run_id": "abc", "ticks": 3})
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["run_id"], manifest["ticks"], manifest["version"]) == ("abc", 3, runtime.VERSION)
    assert not list((tmp_path / "run").glob("*.tmp"))


def test_process_alive_distinguishes_running_and_exited_processes():
    assert process_alive(os.getpid())
    assert not process_alive(0)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not process_alive(child.pid)         # đã thoát (dù handle Popen còn mở)


def test_log_event_is_one_line_and_masks_secret_fields(caplog):
    logger = logging.getLogger("dps.test")
    with caplog.at_level(logging.INFO, logger="dps.test"):
        runtime.log_event(logger, logging.INFO, "SOMETHING", ticks=3, db_password="hunter2", api_token="t0k3n")
    assert caplog.messages == ["SOMETHING ticks=3 db_password=*** api_token=***"]


def test_setup_logging_attaches_handlers_once_and_writes_under_the_runtime_dir(tmp_path, monkeypatch):
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    monkeypatch.setattr(runtime, "_logging_configured", False)
    try:
        runtime.setup_logging(tmp_path)
        runtime.setup_logging(tmp_path)                       # lần hai không gắn thêm handler
        assert len(root.handlers) == len(before) + 2          # file xoay vòng + stderr
        logging.getLogger("dps.test").info("hello")
        assert (tmp_path / "logs" / "dps.log").exists()
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            handler.close()
            root.removeHandler(handler)
        root.setLevel(level)


def test_service_status_reports_health_from_state_and_pid(tmp_path):
    rt = Runtime(tmp_path)
    rt.write_state(status="running", force=True)
    status = service_status(tmp_path)
    assert status["process_alive"] and status["healthy"] and status["heartbeat_age_seconds"] < 60
    rt.write_state(status="finished", force=True)
    assert not service_status(tmp_path)["healthy"]
    assert service_status(tmp_path / "empty")["healthy"] is False


def test_a_process_waiting_for_redis_is_still_healthy_but_a_failed_or_finished_one_is_not(tmp_path):
    rt = Runtime(tmp_path)
    rt.write_state(status="waiting_redis", force=True)
    assert service_status(tmp_path)["healthy"] is True
    for status in ("failed", "stopped", "finished", "starting"):
        rt.write_state(status=status, force=True)
        assert service_status(tmp_path)["healthy"] is False, status
