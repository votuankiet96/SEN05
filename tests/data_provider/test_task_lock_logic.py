from __future__ import annotations

import sys
import types


def test_request_confirm_accepts_db_relay_result(load_module, monkeypatch):
    task_lock = load_module("data_provider/_task_lock.py", "task_lock")
    task_lock._pending_tokens.clear()

    asked: list[dict] = []

    fake_telegram = types.SimpleNamespace(
        _get_updates=lambda: [],
        _is_from_our_chat=lambda update: (False, ""),
        tg_ask=lambda **kwargs: asked.append(kwargs),
        tg_flush=lambda timeout=15: None,
        tg_send=lambda message: None,
    )

    monotonic_values = iter([0.0, 0.0])

    monkeypatch.setitem(sys.modules, "_telegram", fake_telegram)
    monkeypatch.setattr(task_lock, "generate_token", lambda: "AB12CD34")
    monkeypatch.setattr(task_lock, "_read_db_relay", lambda task_name, token: "confirm")
    monkeypatch.setattr(task_lock.time, "monotonic", lambda: next(monotonic_values))

    result = task_lock.request_confirm(
        title="Checker",
        problem_desc="Need approval",
        options={"confirm": "Fix all", "skip": "Skip"},
        timeout_min=1,
        task_name="checker_repair",
    )

    assert result == "confirm"
    assert asked and asked[0]["token"] == "AB12CD34"
    assert "AB12CD34" not in task_lock._pending_tokens


def test_request_confirm_times_out_and_notifies(load_module, monkeypatch):
    task_lock = load_module("data_provider/_task_lock.py", "task_lock")
    task_lock._pending_tokens.clear()

    sent: list[str] = []
    fake_telegram = types.SimpleNamespace(
        _get_updates=lambda: [],
        _is_from_our_chat=lambda update: (False, ""),
        tg_ask=lambda **kwargs: None,
        tg_flush=lambda timeout=15: None,
        tg_send=sent.append,
    )

    monotonic_values = iter([0.0, 1.0])

    monkeypatch.setitem(sys.modules, "_telegram", fake_telegram)
    monkeypatch.setattr(task_lock, "generate_token", lambda: "ZX98CV76")
    monkeypatch.setattr(task_lock, "_read_db_relay", lambda task_name, token: None)
    monkeypatch.setattr(task_lock.time, "monotonic", lambda: next(monotonic_values))

    result = task_lock.request_confirm(
        title="Checker",
        problem_desc="Need approval",
        options={"confirm": "Fix all", "skip": "Skip"},
        timeout_min=0,
        task_name="checker_repair",
    )

    assert result == "timeout"
    assert sent and "/fix" in sent[0]
    assert "ZX98CV76" not in task_lock._pending_tokens
