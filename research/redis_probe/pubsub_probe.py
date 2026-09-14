"""Probe doc lap: xac nhan kenh Pub/Sub dp:events:candles dung thiet ke.

Day la tang "app-level event" ma thiet ke v5 them moi (khac keyspace
notification o tang duoi) -- DP tu PUBLISH dung JSON nen vua doi khi
co cap nhat incremental. Probe nay:
  1. Kiem tra tung message dung schema {"symbol","timeframe","candles":[...]}
  2. Doi chieu ngay payload cua tung nen trong event voi dung field do
     trong Hash (HGET) -- bat duoc ca truong hop event ban ra nhung
     HSET chua/khong khop (race, hoac script Lua loi giua chung).
  3. Theo doi nhip do event moi pair, canh bao pair im lang qua lau so
     voi chu ky live (bat duoc hang doi day/circuit breaker mo/backfill
     khong publish -- nhung khong the phan biet ro nguyen nhan, chi la
     tin hieu "can xem log DP that").

Chay: python pubsub_probe.py
"""
from __future__ import annotations

import json
import sys
import time

from _probe_common import (
    NUMERIC_FIELDS, load_config, log_event, redis_client, remove_pidfile, stamp_to_datetime,
    run_with_reconnect, setup_probe_logging, write_pidfile,
)

NAME = "pubsub_probe"
_SILENCE_CHECK_SECONDS = 60
# Khong biet chu ky live that cua tung pair tu ben ngoai, nen dung 1
# nguong chung du rong (gap doi chu ky live mac dinh 5 phut) -- pair
# im lang qua nguong nay la dang de xem log DP, khong chac chan la loi.
_SILENCE_THRESHOLD_SECONDS = 900


def main() -> int:
    config = load_config()
    logger = setup_probe_logging(NAME, config)
    write_pidfile(NAME)
    try:
        return run_with_reconnect(lambda: _run(config, logger), logger, NAME)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        log_event(logger, "ERROR", "PROBE_CRASHED", "HIGH", component=NAME, error=exc)
        return 1
    finally:
        remove_pidfile(NAME)


def _run(config: dict, logger) -> int:
    client = redis_client(config)
    channel = str(config["redis"]["event_channel"])
    prefix = str(config["redis"]["key_prefix"])
    pubsub = client.pubsub()
    pubsub.subscribe(channel)
    log_event(logger, "INFO", "PROBE_STARTED", "NONE", component=NAME, channel=channel)

    last_seen: dict[str, float] = {}
    event_count = 0
    mismatch_count = 0
    last_summary = time.monotonic()
    while True:
        message = pubsub.get_message(timeout=5.0)
        now = time.monotonic()
        if message and message["type"] == "message":
            event_count += 1
            mismatch_count += _handle_event(client, logger, prefix, str(message["data"]), last_seen)
        if now - last_summary >= _SILENCE_CHECK_SECONDS:
            _flush_silence_check(logger, last_seen, now)
            log_event(
                logger, "INFO", "PROBE_SCAN_SUMMARY", "NONE", component=NAME,
                events=event_count, mismatches=mismatch_count, pairs_seen=len(last_seen),
            )
            event_count = 0
            mismatch_count = 0
            last_summary = now


def _handle_event(client, logger, prefix: str, raw: str, last_seen: dict[str, float]) -> int:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        log_event(logger, "WARNING", "EVENT_MALFORMED", "MEDIUM", component=NAME, error=exc, raw=raw[:200])
        return 0
    symbol = payload.get("symbol")
    timeframe = payload.get("timeframe")
    candles = payload.get("candles")
    if not symbol or not timeframe or not isinstance(candles, list) or not candles:
        log_event(logger, "WARNING", "EVENT_SCHEMA_INVALID", "MEDIUM", component=NAME, raw=raw[:200])
        return 0
    pair = f"{symbol}:{timeframe}"
    last_seen[pair] = time.monotonic()
    list_key = f"{prefix}:{symbol}_{timeframe}"
    mismatches = 0
    for candle in candles:
        stamp = candle.get("bartime")
        if not stamp or stamp_to_datetime(stamp) is None:
            log_event(logger, "WARNING", "EVENT_CANDLE_INVALID", "MEDIUM", component=NAME, pair=pair, candle=json.dumps(candle))
            mismatches += 1
            continue
        # Moc trong event dung y het dinh dang duoi key Hash, nen noi thang
        # ra key duoc -- khong phai chuyen doi gi.
        candle_key = f"{list_key}:{stamp}"
        stored = client.hmget(candle_key, list(NUMERIC_FIELDS))
        if any(value is None for value in stored):
            log_event(logger, "WARNING", "EVENT_NOT_IN_HASH", "MEDIUM", component=NAME, pair=pair, bartime=stamp, candle_key=candle_key)
            mismatches += 1
            continue
        mismatched = False
        for name, value in zip(NUMERIC_FIELDS, stored):
            expected = candle.get(name)
            if expected is None:
                mismatched = mismatched or value != "null"
            else:
                mismatched = mismatched or float(value) != float(expected)
        if mismatched:
            log_event(
                logger, "WARNING", "EVENT_HASH_MISMATCH", "MEDIUM", component=NAME, pair=pair, bartime=stamp,
                event_payload=json.dumps(candle), hash_payload=",".join(stored),
            )
            mismatches += 1
    return mismatches


def _flush_silence_check(logger, last_seen: dict[str, float], now: float) -> None:
    for pair, seen_at in sorted(last_seen.items()):
        idle = now - seen_at
        if idle >= _SILENCE_THRESHOLD_SECONDS:
            log_event(logger, "WARNING", "PAIR_EVENT_SILENT", "LOW", component=NAME, pair=pair, idle_seconds=round(idle))


if __name__ == "__main__":
    sys.exit(main())
