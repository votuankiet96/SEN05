"""SQL: chỉ SELECT, dịch giờ phát -> BarTime đúng, seed đảo thứ tự, chọn pair, chuỗi kết nối."""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest

import sql_source
from configuration import SqlConfig
from sql_source import SourceError, SqlSource
from support import M15, at

FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|GRANT|INTO)\b", re.IGNORECASE)


class FakeCursor:
    def __init__(self, tables):
        self.tables, self.executed, self._rows = tables, [], []

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        self._rows = self.tables.get(sql, [])

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, tables):
        self.cursor_obj, self.closed = FakeCursor(tables), False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def sql_config(**changes):
    values = dict(server="host", port="", database="DB", username="", password="", trusted_connection=True,
                  encrypt="no", trust_server_certificate=True)
    values.update(changes)
    return SqlConfig(**values)


def test_every_statement_is_a_read_only_select():
    assert sql_source.STATEMENTS
    for statement in sql_source.STATEMENTS:
        assert statement.lstrip().upper().startswith("SELECT"), statement
        assert not FORBIDDEN.search(statement), statement


def test_window_converts_release_time_bounds_into_bar_time_bounds():
    rows = [(at("2026-09-30 13:00"), Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal("1.5"))]
    connection = FakeConnection({sql_source._SQL_WINDOW: rows})
    source = SqlSource(sql_config(), connection=connection)
    (found,) = source.window([M15], at("2026-09-30 13:15"), at("2026-09-30 13:30"))
    _sql, params = connection.cursor_obj.executed[0]
    shift = timedelta(minutes=15)
    assert params == (M15.symbol_id, M15.timeframe_id, at("2026-09-30 13:15") - shift, at("2026-09-30 13:30") - shift)
    assert (found.pair, found.bar_time, found.close) == (M15, at("2026-09-30 13:00"), Decimal("1.5"))


def test_seed_returns_ascending_candles_and_bounds_by_closed_time():
    newest_first = [(at("2026-09-30 12:45"), *[Decimal(3)] * 4), (at("2026-09-30 12:30"), *[Decimal(2)] * 4), (at("2026-09-30 12:15"), *[Decimal(1)] * 4)]
    connection = FakeConnection({sql_source._SQL_SEED: newest_first})
    source = SqlSource(sql_config(), connection=connection)
    seeded = source.seed([M15], at("2026-09-30 13:00"), 1200)
    assert [c.bar_time.strftime("%H:%M") for c in seeded] == ["12:15", "12:30", "12:45"]
    _sql, params = connection.cursor_obj.executed[0]
    assert params == (1200, M15.symbol_id, M15.timeframe_id, at("2026-09-30 12:45"))      # chỉ nến mở <= T0 - 15 phút


def test_pairs_are_sorted_and_unknown_names_are_rejected():
    tables = {
        sql_source._SQL_SYMBOLS: [(56, "GOLD"), (3, "DE40")],
        sql_source._SQL_TIMEFRAMES: [(1, "M5", 5), (3, "M15", 15)],
    }
    source = SqlSource(sql_config(), connection=FakeConnection(tables))
    pairs = source.pairs(["GOLD", "DE40"], ["M15", "M5"])
    assert [(p.symbol, p.timeframe) for p in pairs] == [("DE40", "M5"), ("DE40", "M15"), ("GOLD", "M5"), ("GOLD", "M15")]
    with pytest.raises(SourceError, match="NOPE"):
        source.pairs(["NOPE"], ["M5"])
    with pytest.raises(SourceError, match="H9"):
        source.pairs(["GOLD"], ["H9"])


def test_context_manager_closes_the_connection():
    connection = FakeConnection({})
    with SqlSource(sql_config(), connection=connection):
        pass
    assert connection.closed


def test_connection_string_variants():
    trusted = sql_source._connection_string(sql_config(port="1433"))
    assert "SERVER=host,1433" in trusted and "Trusted_Connection=yes" in trusted and "UID=" not in trusted
    login = sql_source._connection_string(sql_config(username="user", password="p;w}", trusted_connection=False))
    assert "UID={user}" in login and "PWD={p;w}}}" in login
    with pytest.raises(SourceError):
        sql_source._connection_string(sql_config(trusted_connection=False))
