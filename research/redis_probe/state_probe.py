"""Probe doc lap: doi chieu trang thai Hash/List voi bat bien thiet ke va voi SQL.

Trong tai cuoi cung, tham quyen cao nhat trong 3 probe quan sat: SQL la
su that, nen lech o day la bang chung chac chan nhat, khong phai nhieu
nhu keyspace/pubsub (2 cai do co the "dung tam thoi giua chung" vi
publish va reconcile chay bat dong bo).

Hai vong kiem moi lan chay:
  1. Structural scan (moi lan goi, nhanh, khong dung SQL): quet toan
     bo key List L_CANDLE_* qua SCAN (khong dung KEYS -- tranh block
     Redis production), kiem cac bat bien thiet ke da thong nhat:
       - Moi moc trong List co Hash nen tuong ung (khong nen thieu)
       - Moi nen du 9 field, va 5 field OHLCV dung kieu so
       - Field `datetime` la dang nguoi doc cua chinh moc trong List
       - List khong trung moc va sap xep tang dan
       - LLEN(list) <= bars_per_snapshot (cua so khong phinh)
       - Do tuoi: moc moi nhat khong cu hon nguong theo khung gio
         (doc Minutes tu chinh DWH.Dim_Timeframe qua select_pairs(),
         khong hardcode)
  2. SQL cross-check (thua hon, mac dinh moi vong quet lay mau vai
     pair thay vi ca 165 -- nang cho SQL): tu query truc tiep N nen
     moi nhat cua 1 pair, diff voi Hash hien tai theo moc + gia tri.

Chay: python state_probe.py            (vong lap lien tuc)
      python state_probe.py --once     (1 luot roi thoat, tien loi test tay)
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from itertools import islice

from _probe_common import (
    CANDLE_FIELDS, NUMERIC_FIELDS, list_keys, load_config, log_event, redis_client,
    remove_pidfile, stamp_to_datetime,
    run_with_reconnect, safe_error, setup_probe_logging, write_pidfile,
)

NAME = "state_probe"
_SCAN_INTERVAL_SECONDS = 60
_SQL_CROSSCHECK_EVERY_N_SCANS = 5
_SQL_SAMPLE_SIZE = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="chay 1 luot scan roi thoat")
    args = parser.parse_args()

    config = load_config()
    logger = setup_probe_logging(NAME, config)
    write_pidfile(NAME)
    try:
        if args.once:
            return _run(config, logger, once=True)
        return run_with_reconnect(lambda: _run(config, logger, once=False), logger, NAME)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        log_event(logger, "ERROR", "PROBE_CRASHED", "HIGH", component=NAME, error=exc)
        return 1
    finally:
        remove_pidfile(NAME)


def _run(config: dict, logger, *, once: bool) -> int:
    from dp_program.engine.sql_connector import select_pairs

    client = redis_client(config)
    prefix = str(config["redis"]["key_prefix"])
    bars_per_snapshot = int(config["redis"]["bars_per_snapshot"])
    minutes_by_pair = _minutes_by_pair(select_pairs(config, live=True))

    log_event(logger, "INFO", "PROBE_STARTED", "NONE", component=NAME, key_prefix=prefix)
    scan_number = 0
    while True:
        scan_number += 1
        passed, failed = _structural_scan(client, logger, prefix, bars_per_snapshot, minutes_by_pair)
        log_event(logger, "INFO", "PROBE_SCAN_SUMMARY", "NONE", component=NAME, pairs=passed + failed, pass_count=passed, fail_count=failed)
        if scan_number % _SQL_CROSSCHECK_EVERY_N_SCANS == 0 or once:
            _sql_crosscheck_sample(config, client, logger, prefix, scan_number)
        if once:
            return 0
        time.sleep(_SCAN_INTERVAL_SECONDS)


def _stamp_from_sql(bartime) -> str:
    """Dung lai moc thoi gian tu row SQL, doc lap voi code ghi.

    Co tinh KHONG import _stamp() cua redis_publisher: probe phai tu dung
    lai ky vong tu SQL, neu dung chung ham thi mot loi trong ham do se tu
    xac nhan la dung.
    """
    return bartime.strftime("%Y%m%d_%H%M%S")


def _minutes_by_pair(pairs: list) -> dict[str, int]:
    # {"US30:H1": 60, ...} -- dung de tinh nguong do tuoi, doc that tu SQL.
    return {f"{symbol['symbol']}:{timeframe['code']}": int(timeframe["minutes"]) for symbol, timeframe in pairs}


def _structural_scan(client, logger, prefix: str, bars_per_snapshot: int, minutes_by_pair: dict[str, int]) -> tuple[int, int]:
    passed = failed = 0
    now = time.time()
    for list_key in list_keys(client, prefix):
        pair, problems = _check_one_pair(client, str(list_key), prefix, bars_per_snapshot, minutes_by_pair, now)
        if problems:
            failed += 1
            log_event(logger, "WARNING", "STATE_INVARIANT_FAILED", "MEDIUM", component=NAME, pair=pair, problems=", ".join(problems))
        else:
            passed += 1
    return passed, failed


def _check_one_pair(client, list_key: str, prefix: str, bars_per_snapshot: int, minutes_by_pair: dict[str, int], now: float) -> tuple[str, list[str]]:
    # list_key = "{prefix}_{SYMBOL}_{TIMEFRAME}"; key nen = list_key + ":" + stamp.
    symbol, tf = list_key[len(prefix) + 1:].rsplit("_", 1)
    pair = f"{symbol}:{tf}"
    problems: list[str] = []

    order = client.lrange(list_key, 0, -1)
    order_set = set(order)
    if len(order) != len(order_set):
        problems.append("order_has_duplicates")
    if len(order) > bars_per_snapshot:
        problems.append(f"window_overflow llen={len(order)}>{bars_per_snapshot}")
    # Moc rong co dinh nen sap xep chuoi la dung thu tu thoi gian.
    if order != sorted(order):
        problems.append("order_not_sorted")

    # Doc toan bo nen cua pair trong 1 vong pipeline, roi kiem tung cai.
    pipe = client.pipeline(transaction=False)
    for stamp in order:
        pipe.hgetall(f"{list_key}:{stamp}")
    for stamp, candle in zip(order, pipe.execute()):
        if stamp_to_datetime(stamp) is None:
            problems.append(f"bad_stamp value={stamp}")
            continue
        if not candle:
            problems.append(f"missing_candle stamp={stamp}")
            continue
        extra = [field for field in candle if field not in CANDLE_FIELDS]
        if extra:
            problems.append(f"unexpected_field stamp={stamp} keys={','.join(sorted(extra))}")
        missing = [field for field in CANDLE_FIELDS if field not in candle]
        if missing:
            problems.append(f"missing_field stamp={stamp} keys={','.join(missing)}")
            continue
        # "null" la sentinel hop le rieng cho volume (xem _candle_fields()
        # trong redis_publisher.py) -- moi field con lai phai la so huu han.
        non_finite: list[str] = []
        for field in NUMERIC_FIELDS:
            raw = candle[field]
            if field == "volume" and raw == "null":
                continue
            try:
                number = float(raw)
            except ValueError:
                problems.append(f"bad_number stamp={stamp} key={field} value={raw}")
                continue
            if not math.isfinite(number):
                non_finite.append(field)
        if non_finite:
            problems.append(f"non_finite_value stamp={stamp} keys={','.join(non_finite)}")

    minutes = minutes_by_pair.get(pair)
    if minutes and order:
        newest_at = stamp_to_datetime(max(order))
        threshold = minutes * 60 * 3  # 3 chu ky khung gio -- ranh de tranh bao nham luc thi truong dong cua
        if newest_at is not None and now - newest_at.timestamp() > threshold:
            age_minutes = round((now - newest_at.timestamp()) / 60)
            problems.append(f"stale newest_age_minutes={age_minutes} threshold_minutes={threshold // 60}")

    return pair, problems


def _sql_crosscheck_sample(config: dict, client, logger, prefix: str, scan_number: int) -> None:
    from dp_program.engine.sql_connector import read_latest_candles, select_pairs

    pairs = select_pairs(config, live=True)
    # Xoay vong qua tung nhom pair theo scan_number thay vi luon lay
    # cung 5 pair dau -- de du nhieu lan quet thi moi pair deu duoc
    # doi chieu SQL it nhat 1 lan, khong bo sot pair nao vinh vien.
    offset = (scan_number // _SQL_CROSSCHECK_EVERY_N_SCANS * _SQL_SAMPLE_SIZE) % max(len(pairs), 1)
    sample = list(islice(pairs * 2, offset, offset + _SQL_SAMPLE_SIZE))
    for symbol, timeframe in sample:
        pair = f"{symbol['symbol']}:{timeframe['code']}"
        limit = int(config["redis"]["bars_per_snapshot"])
        try:
            rows = read_latest_candles(config, symbol["symbol_id"], timeframe["code"], limit)
        except Exception as exc:  # noqa: BLE001 -- SQL tam thoi khong toi khong duoc lam chet probe
            log_event(logger, "WARNING", "SQL_CROSSCHECK_FAILED", "MEDIUM", component=NAME, pair=pair, error=exc)
            continue
        list_key = f"{prefix}_{symbol['symbol']}_{timeframe['code']}"
        mismatches = 0
        for bartime, open_, high, low, close, _volume, _created in rows:
            stamp = _stamp_from_sql(bartime)
            stored = client.hget(f"{list_key}:{stamp}", "close")
            if stored is None:
                mismatches += 1
                continue
            if round(float(stored), 6) != round(float(close), 6):
                mismatches += 1
                log_event(
                    logger, "WARNING", "SQL_REDIS_MISMATCH", "MEDIUM", component=NAME, pair=pair, bartime=stamp,
                    sql_close=float(close), redis_close=stored,
                )
        if mismatches == 0:
            log_event(logger, "INFO", "SQL_CROSSCHECK_OK", "NONE", component=NAME, pair=pair, checked=len(rows))


if __name__ == "__main__":
    sys.exit(main())
