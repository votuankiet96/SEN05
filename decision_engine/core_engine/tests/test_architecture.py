"""Test kiến trúc: ranh giới Redis của DE được khoá bằng mã, không chỉ bằng lời.

  * NỐI Redis (tạo kết nối ``redis.Redis``, nghe keyspace, kiểm cờ, đăng ký, lấy message) và GHI nguyên tử List–Hash (lệnh ``eval``): CHỈ ``redis_connector.py``;
  * ``data_engine.py`` chỉ làm việc với dữ liệu nến (đọc LIST/HASH của db0), không tự nối;
  * ``trend_filtering.py`` giữ định dạng db trend nên được ĐỌC lại kết quả của chính nó (``lrange``, ``hmget``); nối, ghi, nghe vẫn không (ghi giao cho redis_connector);
  * strategy_runner / signal_publisher / main / configuration KHÔNG tự gọi lệnh Redis nào: quyết định ghi gì rồi giao cho redis_connector.
Kiểm bằng cây cú pháp: tên lệnh Redis được gọi và nơi tạo ``redis.Redis(...)`` ở mỗi file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
CONNECTION_COMMANDS = {"psubscribe", "pubsub", "get_message", "config_get", "scan_iter"}  # (ping không tính: các lớp bọc cũng có hàm tên ping)
DATA_COMMANDS = {"lrange", "hmget", "lindex", "hgetall"}
OUTPUT_COMMANDS = {"eval", "publish"}
STATE_COMMANDS = {"hset", "expire", "delete"}
ALL_COMMANDS = CONNECTION_COMMANDS | DATA_COMMANDS | OUTPUT_COMMANDS | STATE_COMMANDS | {"pipeline"}


def _redis_usage(name: str) -> tuple[bool, set[str]]:
    """(file có tạo ``redis.Redis(...)`` không, các tên lệnh Redis được gọi trong file)."""
    tree = ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))
    creates = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Redis" and isinstance(node.func.value, ast.Name) and node.func.value.id == "redis"
        for node in ast.walk(tree)
    )
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ALL_COMMANDS
    }
    return creates, called


def test_chi_redis_connector_noi_redis_nghe_keyspace_va_kiem_co() -> None:
    creates, called = _redis_usage("redis_connector")
    assert creates and {"psubscribe", "pubsub", "get_message", "config_get", "eval", "pipeline"} <= called
    assert not called & DATA_COMMANDS  # không đọc nến


def test_data_engine_chi_doc_du_lieu_vao() -> None:
    """data_engine là nơi DUY NHẤT đọc dữ liệu vào tiến trình: nến của nguồn (LRANGE + HMGET) và cấu phần db0 của DE (LRANGE + HGETALL) -- cả hai đều là List–Hash."""
    creates, called = _redis_usage("data_engine")
    assert not creates and {"lrange", "hmget", "hgetall"} <= called
    assert not called & (CONNECTION_COMMANDS | OUTPUT_COMMANDS | STATE_COMMANDS)  # không tự nối/nghe/kiểm cờ, không ghi gì ra Redis


def test_trend_filtering_da_nhap_vao_strategy_runner() -> None:
    """Phần tính và ghi trend nay nằm ở strategy_runner (cấu phần TREND của db0), phần đọc ở data_engine: không còn file riêng."""
    assert not (SRC / "trend_filtering.py").exists()


@pytest.mark.parametrize("name", ["strategy_runner", "signal_publisher", "main", "configuration"])
def test_cac_module_con_lai_khong_dung_redis(name: str) -> None:
    creates, called = _redis_usage(name)
    assert not creates and not called


def test_chi_redis_connector_tao_ket_noi_redis() -> None:
    for path in sorted(SRC.glob("*.py")):
        if path.stem == "redis_connector":
            continue
        creates, _ = _redis_usage(path.stem)
        assert not creates, f"{path.name} tự nối Redis: mọi việc nối chỉ ở redis_connector"


def test_ma_dung_chung_khong_chua_ten_chien_luoc_hay_khoi_trend() -> None:
    """Các module dùng chung phải chạy được cho BẤT KỲ chiến lược hay khối trend nào: thêm chiến lược (đăng ký StrategySpec, khai order_type) hay thêm khối trend
    (rules/trend_tf.METHODS + POLICY + redis.trend_outputs) không đòi sửa file nào ở đây. Quét mọi hằng chuỗi (bỏ docstring) tìm tên đã đăng ký."""
    from core_engine.src.configuration import STRATEGIES
    from core_engine.src.rules.trend_tf import method_names

    forbidden = set(STRATEGIES) | set(method_names())
    names = ("data_engine", "signal_publisher", "strategy_runner", "redis_connector", "main")
    hits: list[str] = []
    for name in names:
        tree = ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and node.value.strip().lower() in forbidden:
                hits.append(f"{name}.py:{node.lineno}: {node.value!r}")
    assert hits == [], hits
