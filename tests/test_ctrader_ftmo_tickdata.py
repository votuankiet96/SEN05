from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from data_provider.tickdata_ctrader_ftmo.auth import build_authorization_url
from data_provider.tickdata_ctrader_ftmo.cli import build_parser
from data_provider.tickdata_ctrader_ftmo.history import (
    MAX_TICK_REQUEST_MS,
    decode_delta_ticks,
    iter_tick_windows,
)
from data_provider.tickdata_ctrader_ftmo.models import (
    RemoteSymbol,
    TargetSymbol,
    TickRecord,
    price_from_raw,
)
from data_provider.tickdata_ctrader_ftmo.sdk import remote_symbol_from_proto
from data_provider.tickdata_ctrader_ftmo.settings import load_settings
from data_provider.tickdata_ctrader_ftmo.spool_sqlite import TickSpool
from data_provider.tickdata_ctrader_ftmo.store_sql import qualified_tick_table, quote_ident
from data_provider.tickdata_ctrader_ftmo.symbol_matcher import build_symbol_matches
from data_provider.tickdata_ctrader_ftmo.token_store import (
    load_token_cache,
    save_token_cache,
    token_status,
    update_cached_account,
)

EXPECTED_TICK_SYMBOLS = {
    (2, "FR40"),
    (3, "DE40"),
    (4, "HK50"),
    (5, "J225"),
    (6, "SP35"),
    (7, "UK100"),
    (8, "US500"),
    (9, "US100"),
    (10, "US30"),
    (56, "GOLD"),
    (81, "BTCUSD"),
}


def _target(symbol_id: int = 10, symbol: str = "US30") -> TargetSymbol:
    return TargetSymbol(symbol_id=symbol_id, local_symbol=symbol, asset_type="Indice", table=f"tick.{symbol}")


def _remote(symbol_id: int = 1001, name: str = "US30") -> RemoteSymbol:
    return RemoteSymbol(ctrader_symbol_id=symbol_id, symbol_name=name, digits=5, pip_position=1)


def test_settings_uses_initial_11_symbol_universe_from_config():
    settings = load_settings()

    actual = {(symbol.symbol_id, symbol.local_symbol) for symbol in settings.symbols}
    assert actual == EXPECTED_TICK_SYMBOLS
    assert settings.env == "demo"
    assert settings.schema == "tick"
    assert settings.endpoint_label == "demo.ctraderapi.com:5035"


def test_price_from_raw_uses_ctrader_scale_and_optional_digits():
    assert price_from_raw(215012345) == Decimal("2150.12345")
    assert price_from_raw(215012345, digits=2) == Decimal("2150.12")
    assert price_from_raw(None) is None


def test_tick_event_hash_is_stable_and_changes_with_price():
    target = _target()
    remote = _remote()

    first = TickRecord.from_historical_tick(target, remote, "BID", 1700000000000, 400001234)
    same = TickRecord.from_historical_tick(target, remote, "BID", 1700000000000, 400001234)
    changed = TickRecord.from_historical_tick(target, remote, "BID", 1700000000000, 400001235)

    assert first.event_hash == same.event_hash
    assert len(first.event_hash) == 32
    assert first.event_hash != changed.event_hash


def test_tick_event_hash_deduplicates_live_and_historical_overlap():
    target = _target()
    remote = _remote()
    timestamp_ms = 1700000000123
    raw_bid = 400001234

    historical = TickRecord.from_historical_tick(target, remote, "BID", timestamp_ms, raw_bid)
    live = TickRecord.from_live_spot(
        target,
        remote,
        SimpleNamespace(symbolId=remote.ctrader_symbol_id, bid=raw_bid, timestamp=timestamp_ms),
    )

    assert historical.source_mode == "HISTORICAL"
    assert live.source_mode == "LIVE"
    assert historical.event_hash == live.event_hash


def test_live_spot_record_keeps_bid_ask_and_timestamp():
    event = SimpleNamespace(
        symbolId=1001,
        bid=400001234,
        ask=400001777,
        timestamp=1700000000123,
    )

    record = TickRecord.from_live_spot(_target(), _remote(), event)

    assert record.quote_type == "BOTH"
    assert record.source_mode == "LIVE"
    assert record.bid == Decimal("4000.01234")
    assert record.ask == Decimal("4000.01777")
    assert record.tick_time_utc == datetime.fromtimestamp(1700000000.123, tz=timezone.utc)


def test_tick_windows_never_exceed_one_week_limit():
    start = 1700000000000
    end = start + (15 * 24 * 60 * 60 * 1000)

    windows = list(iter_tick_windows(start, end))

    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all((window_end - window_start) <= MAX_TICK_REQUEST_MS for window_start, window_end in windows)
    assert all(windows[i][1] + 1 == windows[i + 1][0] for i in range(len(windows) - 1))


def test_decode_delta_ticks_newest_first():
    raw_ticks = [
        SimpleNamespace(timestamp=1700000000000, tick=100000001),
        SimpleNamespace(timestamp=250, tick=100000002),
        SimpleNamespace(timestamp=750, tick=100000003),
    ]

    decoded = decode_delta_ticks(raw_ticks, "ASK")

    assert [tick.timestamp_ms for tick in decoded] == [
        1700000000000,
        1699999999750,
        1699999999000,
    ]
    assert [tick.raw_price for tick in decoded] == [100000001, 100000002, 100000003]
    assert {tick.quote_type for tick in decoded} == {"ASK"}


def test_symbol_matcher_matches_aliases_but_marks_close_ties_ambiguous():
    targets = (
        TargetSymbol(56, "GOLD", "Metal", "tick.GOLD"),
        TargetSymbol(8, "US500", "Indice", "tick.US500"),
    )
    remotes = [
        RemoteSymbol(5001, "XAUUSD", digits=2),
        RemoteSymbol(5002, "SPX500", digits=2),
        RemoteSymbol(5003, "SP500", digits=2),
    ]

    matches = {match.target.local_symbol: match for match in build_symbol_matches(targets, remotes)}

    assert matches["GOLD"].status == "MATCHED"
    assert matches["GOLD"].remote.symbol_name == "XAUUSD"
    assert matches["US500"].status == "AMBIGUOUS"


def test_remote_symbol_from_proto_extracts_pip_position():
    proto = SimpleNamespace(
        symbolId=123,
        symbolName="US30",
        digits=2,
        pipPosition=1,
        description="US 30",
        enabled=True,
        baseAssetId=10,
        quoteAssetId=20,
    )

    data = remote_symbol_from_proto(proto)

    assert data["ctrader_symbol_id"] == 123
    assert data["symbol_name"] == "US30"
    assert data["digits"] == 2
    assert data["pip_position"] == 1


def test_sql_identifier_whitelist_blocks_unsafe_table_names():
    assert quote_ident("tick") == "[tick]"
    assert qualified_tick_table("tick", "US30", {"US30"}) == "[tick].[US30]"

    with pytest.raises(ValueError):
        quote_ident("tick;DROP TABLE x")
    with pytest.raises(ValueError):
        qualified_tick_table("tick", "US30;DROP", {"US30"})
    with pytest.raises(ValueError):
        qualified_tick_table("tick", "GOLD", {"US30"})


def test_spool_is_durable_and_idempotent(tmp_path):
    spool = TickSpool(tmp_path / "spool.db")
    target = _target()
    remote = _remote()
    record = TickRecord.from_historical_tick(target, remote, "BID", 1700000000000, 400001234)

    assert spool.append_many([record]) == 1
    assert spool.append_many([record]) == 0
    assert spool.count() == 1

    batch = spool.read_batch(10)
    assert len(batch) == 1
    seq, restored = batch[0]
    assert restored.event_hash == record.event_hash
    assert restored.bid == record.bid

    assert spool.delete_through(seq) == 1
    assert spool.count() == 0


def test_sql_contract_file_defines_tick_schema_and_per_symbol_tables():
    sql = (load_settings().spool_path.parents[2] / "sql" / "07_ctrader_ftmo_tick.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE SCHEMA tick" in sql
    assert "CREATE TABLE tick.SymbolMap" in sql
    assert "PipPosition          INT NULL" in sql
    assert "RowsInserted         BIGINT NULL" in sql
    assert "RowsSpooled          BIGINT NULL" in sql
    for _symbol_id, symbol in EXPECTED_TICK_SYMBOLS:
        assert f"CREATE TABLE tick.{symbol}" in sql


def test_auth_url_uses_ctrader_granting_access_endpoint_and_product_web():
    url = build_authorization_url(
        "client123",
        "http://localhost:8765/callback",
        "accounts",
    )

    assert url.startswith("https://id.ctrader.com/my/settings/openapi/grantingaccess/")
    assert "client_id=client123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback" in url
    assert "scope=accounts" in url
    assert "product=web" in url


def test_live_cli_supports_capped_smoke_mode():
    args = build_parser().parse_args(["live", "--smoke-seconds", "300"])

    assert args.command == "live"
    assert args.smoke_seconds == 300


def test_token_cache_saves_tokens_and_account_without_printing_secret(tmp_path):
    path = tmp_path / "ctrader_tokens.json"

    save_token_cache(
        {
            "accessToken": "access-secret",
            "refreshToken": "refresh-secret",
            "tokenType": "bearer",
            "expiresIn": 2628000,
        },
        path=path,
        client_id="client123",
        client_secret="client-secret",
        redirect_uri="http://localhost:8765/callback",
        scope="accounts",
    )
    update_cached_account(123456789, trader_login="7563609", path=path)

    cache = load_token_cache(path)
    status = token_status(path)

    assert cache["accessToken"] == "access-secret"
    assert cache["refreshToken"] == "refresh-secret"
    assert cache["client_secret"] == "client-secret"
    assert cache["ctidTraderAccountId"] == 123456789
    assert status["access_token_set"] is True
    assert status["refresh_token_set"] is True
    assert status["client_secret_set"] is True
    assert status["account_id"] == 123456789
    assert "access-secret" not in str(status)
    assert "client-secret" not in str(status)
