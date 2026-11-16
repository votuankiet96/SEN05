"""Test kiến trúc: vai trò từng file của DE được khoá bằng mã, không chỉ bằng lời.

  * NỐI Redis (tạo kết nối ``redis.Redis``, nghe keyspace, kiểm cờ, đăng ký, lấy message), GHI nguyên tử (lệnh ``eval``) và đọc/ghi config có phiên bản: CHỈ ``redis_connector.py``;
  * ``data_engine.py`` chỉ ĐỌC dữ liệu vào (nến và kết quả module), không tự nối;
  * mọi file còn lại KHÔNG tự gọi lệnh Redis nào: quyết định ghi gì rồi giao cho redis_connector;
  * chiều phụ thuộc giữa các file một chiều như mô tả ở docstring của chúng, và không file nào phụ thuộc ``archived/``.
Kiểm bằng cây cú pháp: tên lệnh Redis được gọi, nơi tạo ``redis.Redis(...)`` và các import ``core_engine.src.*`` của mỗi file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
CONNECTION_COMMANDS = {"psubscribe", "pubsub", "get_message", "config_get", "scan_iter"}  # (ping không tính: các lớp bọc cũng có hàm tên ping)
DATA_COMMANDS = {"lrange", "hmget", "hgetall"}
CONFIG_COMMANDS = {"lindex", "hget"}
OUTPUT_COMMANDS = {"eval"}  # (PUBLISH chạy trong script Lua của redis_connector; ``SignalWriter.publish`` của util là tên hàm, không phải lệnh Redis)
STATE_COMMANDS = {"hset", "expire", "delete"}
ALL_COMMANDS = CONNECTION_COMMANDS | DATA_COMMANDS | CONFIG_COMMANDS | OUTPUT_COMMANDS | STATE_COMMANDS | {"pipeline"}
# Mỗi file chỉ được import các file này trong src/ (một chiều, không vòng). Khoá là đường dẫn trong src/ không có đuôi .py.
ALLOWED_IMPORTS = {
    "indicator": set(),
    "log": set(),
    "redis_connector": set(),
    "data_engine": {"redis_connector"},
    "util": {"data_engine", "redis_connector"},
    "decision_engine": {"indicator"},
    "de_module/combo_strategy": {"indicator"},
    "de_module/mac_strategy": {"indicator"},
    "de_module/trend": {"indicator"},
    "configuration": {"data_engine", "de_module", "decision_engine", "indicator", "redis_connector", "util", "log"},
    "main": {"configuration"},
}


def _tree(name: str) -> ast.Module:
    return ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))


def _redis_usage(name: str) -> tuple[bool, set[str]]:
    """(file có tạo ``redis.Redis(...)`` không, các tên lệnh Redis được gọi trong file)."""
    tree = _tree(name)
    creates = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Redis" and isinstance(node.func.value, ast.Name) and node.func.value.id == "redis"
        for node in ast.walk(tree)
    )
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ALL_COMMANDS
    }
    return creates, called


def _imports(name: str) -> set[str]:
    """Các file trong src/ mà ``name`` import (``core_engine.src.<file>`` hoặc ``core_engine.src.de_module.<file>``), kể cả import trong hàm."""
    found: set[str] = set()
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("core_engine.src"):
            parts = node.module.split(".")
            if len(parts) >= 3:  # from core_engine.src.<file>[.<con>] import ...: phụ thuộc vào <file> (hoặc thư mục de_module, archived)
                found.add(parts[2])
            else:  # from core_engine.src import <file>
                found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name.split(".")[2] for alias in node.names if alias.name.startswith("core_engine.src."))
    return found


def test_chi_redis_connector_noi_redis_nghe_keyspace_ghi_va_doc_config() -> None:
    creates, called = _redis_usage("redis_connector")
    assert creates and {"psubscribe", "pubsub", "get_message", "config_get", "eval", "pipeline", "lindex", "hget"} <= called
    assert not called & DATA_COMMANDS  # không đọc nến hay kết quả module


def test_data_engine_chi_doc_du_lieu_vao() -> None:
    """data_engine là nơi DUY NHẤT đọc dữ liệu vào tiến trình: nến của nguồn (LRANGE + HMGET) và kết quả module ở db0 của DE (LRANGE + HGETALL), cả hai đều là List–Hash."""
    creates, called = _redis_usage("data_engine")
    assert not creates and {"lrange", "hmget", "hgetall"} <= called
    assert not called & (CONNECTION_COMMANDS | CONFIG_COMMANDS | OUTPUT_COMMANDS | STATE_COMMANDS)  # không tự nối/nghe/kiểm cờ, không ghi gì ra Redis


@pytest.mark.parametrize("name", ["indicator", "util", "decision_engine", "de_module/combo_strategy", "de_module/mac_strategy", "de_module/trend", "configuration", "main", "log"])
def test_cac_file_con_lai_khong_dung_redis(name: str) -> None:
    creates, called = _redis_usage(name)
    assert not creates and not called


def test_chi_redis_connector_tao_ket_noi_redis() -> None:
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).with_suffix("").as_posix()
        if name == "redis_connector" or name.startswith("archived/"):
            continue
        creates, _ = _redis_usage(name)
        assert not creates, f"{name}.py tự nối Redis: mọi việc nối chỉ ở redis_connector"


@pytest.mark.parametrize("name", sorted(ALLOWED_IMPORTS))
def test_chieu_phu_thuoc_mot_chieu_va_khong_dung_archived(name: str) -> None:
    imported = _imports(name)
    assert "archived" not in imported, f"{name}.py import archived/"
    assert imported <= ALLOWED_IMPORTS[name], f"{name}.py import {sorted(imported - ALLOWED_IMPORTS[name])}, ngoài các file được phép {sorted(ALLOWED_IMPORTS[name])}"


def test_moi_file_trong_src_ngoai_archived_co_vai_tro_da_khai() -> None:
    """Thêm file mới vào src/ thì phải khai vai trò (chiều phụ thuộc) ở ``ALLOWED_IMPORTS``: không có file nào nằm ngoài sơ đồ."""
    on_disk = {path.relative_to(SRC).with_suffix("").as_posix() for path in SRC.rglob("*.py") if "archived" not in path.parts}
    assert on_disk == set(ALLOWED_IMPORTS), on_disk ^ set(ALLOWED_IMPORTS)
