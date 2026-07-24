from types import SimpleNamespace

import pytest

from tick_engine.data_storage.symbols import RemoteSymbol, TargetSymbol
from tick_engine.data_storage.ticks import DecodedHistoricalTick
from tick_engine.historical_pulling import (
    _next_history_page_to_timestamp,
    merge_historical_quote_ticks,
)
from tick_engine.utils_support.runtime import validate_configured_account


TARGET = TargetSymbol(1, "TEST", "INDEX", "tick.TEST")
REMOTE = RemoteSymbol(101, "TEST", digits=2)


def tick(timestamp_ms: int, raw_price: int, side: str) -> DecodedHistoricalTick:
    return DecodedHistoricalTick(timestamp_ms, raw_price, side)


def test_same_millisecond_updates_are_not_overwritten() -> None:
    records, stats = merge_historical_quote_ticks(
        TARGET,
        REMOTE,
        [tick(1_000, 10_002_000, "BID"), tick(1_000, 10_000_000, "BID")],
        [tick(1_000, 10_010_000, "ASK")],
        ingest_run_id="run",
    )

    assert [(record.bid_raw, record.ask_raw) for record in records] == [
        (10_000_000, 10_010_000),
        (10_002_000, 10_010_000),
    ]
    assert stats.dropped_duplicate_quote == 0


def test_repeated_same_price_update_can_still_change_quote_sequence() -> None:
    records, _stats = merge_historical_quote_ticks(
        TARGET,
        REMOTE,
        [
            tick(1_000, 10_000_000, "BID"),
            tick(1_000, 10_002_000, "BID"),
            tick(1_000, 10_000_000, "BID"),
        ],
        [tick(1_000, 10_012_000, "ASK"), tick(1_000, 10_010_000, "ASK")],
        ingest_run_id="run",
    )

    assert [(item.bid_raw, item.ask_raw) for item in records] == [
        (10_000_000, 10_010_000),
        (10_002_000, 10_012_000),
        (10_000_000, 10_012_000),
    ]


def test_stale_opposite_side_is_not_forward_filled_forever() -> None:
    records, stats = merge_historical_quote_ticks(
        TARGET,
        REMOTE,
        [tick(901_001, 10_002_000, "BID"), tick(1_000, 10_000_000, "BID")],
        [tick(1_000, 10_010_000, "ASK")],
        ingest_run_id="run",
        max_side_age_seconds=900,
    )

    assert len(records) == 1
    assert stats.dropped_stale_side == 1


def test_pagination_keeps_boundary_inclusive_and_rejects_no_progress() -> None:
    page = [tick(9_000, 1, "BID"), tick(5_000, 2, "BID")]
    assert _next_history_page_to_timestamp(
        symbol="TEST",
        quote_type="BID",
        from_timestamp_ms=1_000,
        current_to_timestamp_ms=10_000,
        page_ticks=page,
        unique_page_tick_count=2,
        has_more=True,
    ) == 5_000

    with pytest.raises(RuntimeError, match="empty"):
        _next_history_page_to_timestamp(
            symbol="TEST",
            quote_type="BID",
            from_timestamp_ms=1_000,
            current_to_timestamp_ms=10_000,
            page_ticks=[],
            unique_page_tick_count=0,
            has_more=True,
        )


def test_configured_account_must_match_token_environment_and_login() -> None:
    settings = SimpleNamespace(account_id=123, env="demo", trader_login="456")
    account = {"ctidTraderAccountId": 123, "isLive": False, "traderLogin": "456"}
    assert validate_configured_account(settings, [account]) == account

    with pytest.raises(RuntimeError, match="expected demo"):
        validate_configured_account(settings, [{**account, "isLive": True}])
    with pytest.raises(RuntimeError, match="does not match"):
        validate_configured_account(settings, [{**account, "traderLogin": "999"}])
