"""Redis connections and candle snapshot reads for the live signal path."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd
import redis

LOGGER = logging.getLogger("candle_reader")
OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
REDIS_DEFAULTS: dict[str, Any] = {
    "port": 6379,
    "socket_timeout_seconds": 5,
    "socket_connect_timeout_seconds": 5,
    "healthcheck_interval_seconds": 30,
    "reconnect_delay_seconds": 3,
}
# reconcile_interval_seconds is the one input-side value that stays a code
# default: a resync cadence tuning knob, never actually changed operator-side
# the way the required keys below have been (see CLAUDE.md muc 5).
REDIS_INPUT_DEFAULTS: dict[str, Any] = {
    "reconcile_interval_seconds": 1800,
}
# retention_seconds/retention_max_entries stay code defaults for the same
# reason -- TTL/list-bound tuning, never actually changed operator-side.
REDIS_OUTPUT_DEFAULTS: dict[str, Any] = {
    "retention_seconds": 604800,
    "retention_max_entries": 500,
}
# input.* moved out of code defaults into required og_config.yaml entries
# (2026-09-17): each has actually been changed multiple times from the
# operator's perspective -- a config.yaml edit + service restart, not a code
# change -- unlike the technical parameters above. db/key_prefix/
# snapshot_bars/process_on_startup track DP's own DB0 contract (already
# revised 4 times in one week historically). output.db/key_prefix are the
# DB1 mirror of the same idea (L_OG); output.channel_prefix (L_CHANNEL) names
# OG's per-(strategy, symbol, timeframe) Pub/Sub channels toward OF (see
# signal_publisher.py) -- a transport only, nothing is stored under it. OG
# controls that one, so it stays a config value.
#
# input.event_channel ("dp:events:candles") is DELIBERATELY NOT here anymore
# (removed 2026-09-23): it was DP's own custom Pub/Sub message -- a contract
# entirely internal to DP's own implementation choices. OG and DP are two
# independent systems that only meet at Redis DB0; OG now verifies "what
# changed" itself via Redis's own keyspace notification on DB0 (see
# event_listener.py) instead of trusting a hand-crafted message DP chooses
# to publish, on whatever schedule/shape it wants. DP is free to change or
# remove its own channel without ever affecting OG's correctness.
_REQUIRED_INPUT_KEYS = ("db", "key_prefix", "snapshot_bars", "process_on_startup")
_REQUIRED_OUTPUT_KEYS = ("db", "key_prefix", "channel_prefix")
# Only the four OHLC fields are ever read by order_gateway's indicators/
# strategies/levels. The AEN HASH carries nine fields (timestamp, open, high,
# low, close, volume, status, hisid, tmu); HMGET also asks for `volume` only
# to keep the frame shape identical to strategy_lab's SQL-backed OHLCV frame.
# No strategy reads it, so it is deliberately absent from the finite check.
# There is no `bartime` field: the bar's timestamp is the LIST entry that
# names the HASH key.
_REQUIRED_FINITE_COLUMNS = ("open", "high", "low", "close")
_HASH_FIELDS = ("open", "high", "low", "close", "volume")
# Stamp format: fixed width, UTC, no offset suffix, a space between
# date and time, colons inside the time (2026-09-14 03:00:00). Fixed width
# means plain string comparison already sorts chronologically -- the colons
# inside don't change that, they just mean the HASH key ends up with several
# colons instead of exactly one (see parse_pair_key below).
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# SCAN with TYPE=list filters server-side, so pair discovery returns only the
# pair LIST keys instead of walking every per-bar HASH as well.
_SCAN_COUNT = 1000


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Merge code-level Redis defaults into a full app config copy.
    """
    out = dict(config)
    out["redis"] = normalize_redis_config(out.get("redis") or {})
    return out


def normalize_redis_config(redis_config: dict[str, Any]) -> dict[str, Any]:
    """
    Build the full Redis runtime config from the operator config -- raises
    KeyError naming the exact missing key if redis.input/redis.output skip
    one of _REQUIRED_INPUT_KEYS/_REQUIRED_OUTPUT_KEYS.
    """
    normalized = {**REDIS_DEFAULTS, **redis_config}
    input_cfg = redis_config.get("input") or {}
    output_cfg = redis_config.get("output") or {}
    for key in _REQUIRED_INPUT_KEYS:
        if key not in input_cfg:
            raise KeyError(f"og_config.yaml thiếu 'redis.input.{key}'")
    for key in _REQUIRED_OUTPUT_KEYS:
        if key not in output_cfg:
            raise KeyError(f"og_config.yaml thiếu 'redis.output.{key}'")
    normalized["input"] = {**REDIS_INPUT_DEFAULTS, **input_cfg}
    normalized["output"] = {**REDIS_OUTPUT_DEFAULTS, **output_cfg}
    return normalized


def create_client(redis_config: dict[str, Any], db: int) -> redis.Redis:
    """Create one decoded Redis client for a specific logical database."""
    redis_config = normalize_redis_config(redis_config)
    return redis.Redis(
        host=redis_config["host"],
        port=int(redis_config["port"]),
        db=int(db),
        password=redis_config["password"] or None,
        decode_responses=True,
        socket_timeout=float(redis_config["socket_timeout_seconds"]),
        socket_connect_timeout=float(redis_config["socket_connect_timeout_seconds"]),
        health_check_interval=int(redis_config["healthcheck_interval_seconds"]),
    )


def pair_list_key(key_prefix: str, symbol: str, timeframe: str) -> str:
    """The LIST of bar stamps for one pair, newest first (Core LPUSHes each new bar).

    ``{key_prefix}_{SYMBOL}_{TIMEFRAME}`` -- every join is an underscore, no
    colon at all until a stamp is appended (see candle_key). SYMBOL comes
    before TIMEFRAME: ``L_CANDLE_US30_M5``, never ``L_CANDLE_M5_US30``.
    Argument order here mirrors the key on purpose.
    """
    return f"{key_prefix}_{symbol.upper()}_{timeframe.upper()}"


def candle_key(list_key: str, stamp: str) -> str:
    """The HASH holding one bar: the pair's LIST key plus ``":" + stamp``.

    This is the whole naming rule -- one concatenation, no other convention.
    """
    return f"{list_key}:{stamp}"


def parse_pair_key(key: str, key_prefix: str) -> tuple[str, str] | None:
    """Return ``(symbol, timeframe)`` for a pair LIST key, else ``None``.

    A pair LIST key has zero colons (``L_CANDLE_US30_M5``); a per-bar HASH
    key is that same key plus ``":<stamp>"`` and the stamp itself now has
    colons in it too (``L_CANDLE_US30_M5:2026-09-14 03:00:00``), so a HASH
    key can carry several colons, not exactly one. The test is presence, not
    count -- any colon at all means "this is a bar, not a pair". This is the
    opposite rule from the short-lived ``CANDLE:`` contract, which is
    exactly why this is worth spelling out rather than reusing a bare
    boolean.
    """
    prefix = f"{key_prefix}_"
    if not key.startswith(prefix) or ":" in key:
        return None
    body = key[len(prefix) :]
    if "_" not in body:
        return None
    symbol, timeframe = body.rsplit("_", 1)
    if not symbol or not timeframe:
        return None
    return symbol.upper(), timeframe.upper()


def read_candles_from_redis(
    client: redis.Redis,
    *,
    symbol: str,
    timeframe: str,
    key_prefix: str,
    snapshot_bars: int,
) -> pd.DataFrame:
    """Read one pair's newest bars as ascending tz-naive UTC OHLCV data.

    Two round-trips regardless of how many bars are requested: LRANGE the
    head of the pair LIST (index 0 is the newest bar) for the newest stamps,
    then one pipeline of HMGETs against the per-bar HASHes. Values arrive as
    plain decimal strings -- there is no JSON on this path.

    ``symbol``/``timeframe`` are keyword-only: the key puts SYMBOL first and
    a silently swapped pair would read an empty list rather than fail.

    A shorter list simply yields fewer bars.
    """
    list_key = pair_list_key(key_prefix, symbol, timeframe)
    stamps = client.lrange(list_key, 0, int(snapshot_bars) - 1)
    if not stamps:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    pipe = client.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hmget(candle_key(list_key, stamp), *_HASH_FIELDS)
    values = pipe.execute()

    rows: list[dict[str, Any]] = []
    missing_stamps: list[str] = []
    for stamp, fields in zip(stamps, values, strict=True):
        if fields is None or all(field is None for field in fields):
            # The LIST names a bar whose HASH is absent. Core writes the HASH
            # and the LIST with separate commands (HMSET, LPUSH, LTRIM,
            # UNLINK -- no MULTI/Lua), so a read can race a write -- drop
            # it, do not fail the window.
            missing_stamps.append(str(stamp))
            continue
        row = dict(zip(_HASH_FIELDS, fields, strict=True))
        row["bartime"] = stamp
        rows.append(row)
    if missing_stamps:
        LOGGER.warning(
            "event=candle_stamp_missing symbol=%s timeframe=%s count=%d sample=%s",
            symbol, timeframe, len(missing_stamps), missing_stamps[:5],
        )
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    frame = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    # order_gateway uses tz-naive UTC datetimes. The explicit format rejects
    # anything that is not DP's stamp (a bad value becomes NaT and is caught
    # below) and yields datetime64[us] -- the same resolution strategy_lab's
    # SQL loader produces, so an audit comparing the two frames bar-for-bar
    # never reads a resolution mismatch as a real Redis-vs-SQL difference.
    frame["bartime"] = pd.to_datetime(
        frame["bartime"], format=STAMP_FORMAT, errors="coerce"
    ).astype("datetime64[us]")
    for column in _HASH_FIELDS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    invalid_bartime = frame["bartime"].isna()
    invalid_ohlc = ~np.isfinite(
        frame[list(_REQUIRED_FINITE_COLUMNS)].to_numpy(dtype="float64")
    ).all(axis=1)
    invalid_mask = invalid_bartime | invalid_ohlc
    if invalid_mask.any():
        # Log the exact raw (pre-coercion) rows before raising -- this is the
        # primary evidence needed to tell a malformed string apart from a real
        # null, and to cross-reference the exact stamp against the source.
        LOGGER.error(
            "event=candle_snapshot_invalid symbol=%s timeframe=%s invalid_rows=%d "
            "total_rows=%d rows=%s",
            symbol, timeframe, int(invalid_mask.sum()), len(frame),
            [rows[i] for i in frame.index[invalid_mask]],
        )
        raise ValueError(f"Candle snapshot {symbol}/{timeframe} contains invalid values")
    duplicated_mask = frame["bartime"].duplicated()
    if duplicated_mask.any():
        # Core occasionally LPUSHes the same just-closed bar's stamp twice in a
        # row (both entries point to the identical HASH -- confirmed by audit,
        # never two conflicting values under one stamp). Dropping the extra
        # copy costs at most one older bar's worth of history, which is far
        # outside the convergence window of every indicator this pipeline
        # uses -- measured identical ATR/signal on the newest bar with vs
        # without the drop. Not worth failing the whole snapshot over.
        LOGGER.warning(
            "event=candle_stamp_duplicate symbol=%s timeframe=%s count=%d stamps=%s",
            symbol, timeframe, int(duplicated_mask.sum()),
            frame.loc[duplicated_mask, "bartime"].dt.strftime(STAMP_FORMAT).tolist(),
        )
        frame = frame.drop_duplicates(subset="bartime", keep="first")

    # The LIST is newest-first; the indicators need ascending bars.
    return frame.sort_values("bartime").reset_index(drop=True)


def scan_candle_pairs(
    client: redis.Redis,
    key_prefix: str,
) -> Iterator[tuple[str, str]]:
    """Yield valid ``(symbol, timeframe)`` pairs currently stored in Redis."""
    for raw_key in client.scan_iter(
        match=f"{key_prefix}_*", count=_SCAN_COUNT, _type="list"
    ):
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        pair = parse_pair_key(key, key_prefix)
        if pair is not None:
            yield pair
