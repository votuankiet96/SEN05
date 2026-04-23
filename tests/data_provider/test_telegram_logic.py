from __future__ import annotations

import sys
import types


def test_handle_command_relays_confirmation_token(load_module, monkeypatch):
    telegram = load_module("data_provider/_telegram.py", "telegram")

    seen: dict[str, list] = {"local": [], "db": []}

    fake_task_lock = types.SimpleNamespace(
        _handle_token_command=lambda text: seen["local"].append(text)
    )
    monkeypatch.setitem(sys.modules, "_task_lock", fake_task_lock)
    monkeypatch.setattr(
        telegram,
        "_store_token_to_db",
        lambda action, token: seen["db"].append((action, token)),
    )

    telegram._handle_command("/confirm_ab12cd34")

    assert seen["local"] == ["/confirm_ab12cd34"]
    assert seen["db"] == [("confirm", "AB12CD34")]


def test_handle_command_fix_starts_checker_process(load_module, monkeypatch):
    telegram = load_module("data_provider/_telegram.py", "telegram")
    telegram._running_task = None

    replies: list[str] = []
    created_threads: list[object] = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name
            self.started = False
            created_threads.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(telegram, "_reply", replies.append)
    monkeypatch.setattr(telegram.threading, "Thread", FakeThread)

    telegram._handle_command("/fix")

    assert telegram._running_task == "Checker"
    assert len(created_threads) == 1
    thread = created_threads[0]
    assert thread.started is True
    assert thread.target is telegram._run_task
    assert thread.args[0] == "Checker"
    assert thread.args[1][0] == sys.executable
    assert thread.args[1][1].endswith("04_checker.py")
    assert any("Checker" in reply for reply in replies)
