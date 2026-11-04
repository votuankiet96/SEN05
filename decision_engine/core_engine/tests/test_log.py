"""Test file log riêng cho mỗi tiến trình (core_engine/src/log.py) và quy ước logger của từng module — hermetic, ghi vào thư mục tạm."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from core_engine.src import log

SRC = Path(__file__).resolve().parents[1] / "src"


def test_log_file_mac_dinh_va_theo_chien_luoc(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log, "LOG_FILE", tmp_path / "de_run.log")
    assert log.log_file() == tmp_path / "de_run.log" and log.log_file(None) == tmp_path / "de_run.log"
    assert log.log_file("ma_cross") == tmp_path / "de_run_ma_cross.log"
    for bad in ("../x", "Ma_Cross", "a b", "ma-cross"):
        with pytest.raises(ValueError, match="Invalid log name"):
            log.log_file(bad)


def _phat_tu_strategy_runner() -> None:
    logging.getLogger("core_engine.src.strategy_runner").info("event=thu chien_luoc=combo")


def test_configure_logging_moi_dong_ghi_tien_trinh_va_noi_phat(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log, "LOG_FILE", tmp_path / "de_run.log")
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        log.configure_logging("INFO", "combo_og8")
        _phat_tu_strategy_runner()
        logging.getLogger("core_engine.src.redis_connector").error("event=thu loi")
        for handler in root.handlers:
            handler.flush()
        lines = (tmp_path / "de_run_combo_og8.log").read_text(encoding="utf-8").splitlines()
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO    combo_og8\[\d+\] strategy_runner\._phat_tu_strategy_runner:\d+: event=thu chien_luoc=combo", lines[0]
        ), lines[0]
        assert re.fullmatch(r"\S+ \S+ ERROR   combo_og8\[\d+\] redis_connector\.\w+:\d+: event=thu loi", lines[1]), lines[1]
        assert not (tmp_path / "de_run.log").exists()  # không đụng file log chung
        log.configure_logging("INFO")
        _phat_tu_strategy_runner()
        for handler in root.handlers:
            handler.flush()
        assert " de[" in (tmp_path / "de_run.log").read_text(encoding="utf-8")  # không có tên tiến trình thì nhãn là "de"
    finally:
        for handler in list(root.handlers):
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_moi_module_log_bang_logger_mang_ten_chinh_no() -> None:
    """Mỗi file có log phải khai ``LOGGER = logging.getLogger(__name__)`` (main.py chạy bằng ``-m`` nên ``__name__`` là ``__main__``: khai tên đầy đủ); không dùng tên chung nào khác,
    để dòng log luôn chỉ đúng file phụ trách."""
    expected = {"configuration", "strategy_runner", "trend_filtering", "data_engine", "redis_connector", "main"}
    seen = set()
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getLogger" and node.args):
                continue
            arg = node.args[0]
            if path.stem == "main":
                assert isinstance(arg, ast.Constant) and arg.value == "core_engine.src.main", f"{path.name}: {ast.dump(arg)}"
            else:
                assert isinstance(arg, ast.Name) and arg.id == "__name__", f"{path.name}: logger phải là getLogger(__name__)"
            seen.add(path.stem)
    assert seen == expected, seen ^ expected
