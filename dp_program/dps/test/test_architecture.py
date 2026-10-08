"""Kiến trúc: ranh giới thuần/I-O, mỗi tài nguyên ngoài một owner, giới hạn kích thước, bộ module cố định."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = {"__main__", "configuration", "player", "redis_writer", "runtime", "schedule", "sql_source"}
PURE = ("schedule",)                      # lõi thuần: không I/O, chỉ được lấy KIỂU DỮ LIỆU từ runtime
# Phần 1 của redis_writer (hợp đồng live): hàm thuần dù nằm chung file với writer.
CONTRACT_FUNCTIONS = {"stamp", "parse_stamp", "round_price", "list_key", "hash_prefix", "script_args"}
DATA_TYPES = {"Candle", "Pair", "Tick", "DpsError"}
PURE_STDLIB = {"__future__", "dataclasses", "datetime", "decimal", "hashlib", "itertools", "typing"}
OWNERS = {"yaml": "configuration", "pyodbc": "sql_source", "redis": "redis_writer"}
MAIN_MAY_IMPORT = {"configuration", "player", "redis_writer", "runtime"}
MAX_CODE_LINES = 300


def parse(name: str) -> ast.Module:
    return ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))


def imports(name: str) -> tuple[set[str], set[str]]:
    """(thư viện ngoài/stdlib, module anh em trong src) mà file import; cấu trúc phẳng nên import tuyệt đối."""
    names: set[str] = set()
    for node in ast.walk(parse(name)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names - MODULES, names & MODULES


def code_lines(name: str) -> int:
    lines = (SRC / f"{name}.py").read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def test_the_module_set_is_exactly_the_documented_one_and_the_layout_is_flat():
    assert {path.stem for path in SRC.glob("*.py")} == MODULES
    assert not (SRC / "__init__.py").exists()
    assert [path.name for path in SRC.iterdir() if path.is_dir() and path.name != "__pycache__"] == []   # không còn gói con


@pytest.mark.parametrize("name", sorted(MODULES))
def test_modules_import_each_other_absolutely(name):
    relative = [node.lineno for node in ast.walk(parse(name)) if isinstance(node, ast.ImportFrom) and node.level]
    assert relative == [], f"{name} uses relative imports at lines {relative}"


@pytest.mark.parametrize("name", PURE)
def test_pure_modules_have_no_io_and_take_only_data_types_from_runtime(name):
    external, siblings = imports(name)
    assert external <= PURE_STDLIB, external - PURE_STDLIB
    assert siblings <= {"runtime"}, siblings
    taken = {alias.name for node in ast.walk(parse(name)) if isinstance(node, ast.ImportFrom) and node.module == "runtime"
             for alias in node.names}
    assert taken <= DATA_TYPES, f"{name} imports lifecycle/logging/pacing code from runtime: {taken - DATA_TYPES}"


def test_the_live_contract_part_of_redis_writer_stays_pure():
    functions = {node.name: node for node in parse("redis_writer").body
                 if isinstance(node, ast.FunctionDef) and node.name in CONTRACT_FUNCTIONS}
    assert set(functions) == CONTRACT_FUNCTIONS
    for name, node in functions.items():
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        assert "redis" not in names and not attributes & {"_client", "pipeline", "execute"}, f"{name} touches Redis"


@pytest.mark.parametrize("name", sorted(MODULES))
def test_each_external_resource_has_exactly_one_owner(name):
    external, _ = imports(name)
    for library, owner in OWNERS.items():
        assert (library in external) == (name == owner), f"{library} must be imported only by {owner} (found in {name})"


def test_main_only_orchestrates():
    _, siblings = imports("__main__")
    assert siblings <= MAIN_MAY_IMPORT, siblings - MAIN_MAY_IMPORT


@pytest.mark.parametrize("name", sorted(MODULES))
def test_every_module_states_its_responsibility_and_stays_small(name):
    assert ast.get_docstring(parse(name)), f"{name} needs a module docstring"
    assert code_lines(name) <= MAX_CODE_LINES, f"{name} has {code_lines(name)} code lines"


def test_redis_writer_never_flushes_everything_or_lists_keys():
    tree = parse("redis_writer")
    attributes = {node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"flushall", "keys", "scan", "scan_iter"}
    flushes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "flushdb"]
    assert len(flushes) == 1
    assert [(k.arg, getattr(k.value, "value", None)) for k in flushes[0].keywords] == [("asynchronous", True)]


def test_sql_source_has_no_write_statement_anywhere_in_its_source():
    text = (SRC / "sql_source.py").read_text(encoding="utf-8")
    for word in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "TRUNCATE ", "EXEC "):
        assert word not in text, word
