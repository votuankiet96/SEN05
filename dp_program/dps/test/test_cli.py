"""CLI: cú pháp lệnh, loại trừ --delay/--speed, các lệnh không cần kết nối vẫn chạy đúng."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# `__main__` đã bị pytest chiếm tên, nên nạp src/__main__.py theo đường dẫn với tên khác.
_SPEC = importlib.util.spec_from_file_location("dps_cli", Path(__file__).resolve().parents[1] / "src" / "__main__.py")
cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


def test_plan_and_run_accept_overrides():
    args = cli._parser().parse_args(["plan", "--show", "5", "--delay", "0.2", "--symbols", "GOLD,DE40", "--export", "x.csv"])
    assert (args.command, args.show, args.delay, args.speed, args.symbols, args.export) == ("plan", 5, 0.2, None, "GOLD,DE40", "x.csv")
    run = cli._parser().parse_args(["run", "--speed", "300"])
    assert (run.command, run.speed, run.delay) == ("run", 300.0, None)


def test_there_is_no_resume_command_or_flag_every_run_starts_over():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["run", "--resume"])
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["resume"])


def test_delay_and_speed_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["run", "--delay", "1", "--speed", "300"])


def test_a_command_is_required_and_clean_defaults_to_report_only():
    with pytest.raises(SystemExit):
        cli._parser().parse_args([])
    assert cli._parser().parse_args(["clean"]).yes is False


def test_config_errors_exit_with_a_message_and_code_1(tmp_path, capsys):
    assert cli.main(["plan", "--config", str(tmp_path / "missing.yaml")]) == 1
    assert "configuration file not found" in capsys.readouterr().err
