"""Probe doc lap: doi chieu trang thai Hash/List voi bat bien thiet ke va voi SQL.

Trong tai cuoi cung, tham quyen cao nhat trong 3 probe quan sat: SQL la
su that, nen lech o day la bang chung chac chan nhat, khong phai nhieu
nhu keyspace/pubsub (2 cai do co the "dung tam thoi giua chung" vi
publish va reconcile chay bat dong bo).

Hai vong kiem moi lan chay:
  1. Structural scan (moi lan goi, nhanh, khong dung SQL): quet toan
     bo key dp:candles:*:*:order qua SCAN (khong dung KEYS -- tranh block
     Redis production), kiem cac bat bien thiet ke da thong nhat:
       - Moi epoch trong List co Hash nen tuong ung (khong nen thieu)
       - Moi nen du 5 field open/high/low/close/volume, dung kieu so
       - Field bartime trong Hash khop chinh epoch dat ten key
       - List khong trung bartime va sap xep tang dan
       - LLEN(order) <= bars_per_snapshot (cua so khong phinh)
       - Do tuoi: bartime moi nhat khong cu hon nguong theo khung gio
         (doc Minutes tu chinh DWH.Dim_Timeframe qua select_pairs(),
         khong hardcode)
  2. SQL cross-check (thua hon, mac dinh moi vong quet lay mau vai
     pair thay vi ca 165 -- nang cho SQL): tu query truc tiep N nen
     moi nhat cua 1 pair, diff voi Hash hien tai theo bartime + gia tri.

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
    CANDLE_FIELDS, epoch_from_bartime, load_config, log_event, redis_client, remove_pidfile,
    require_schema, run_with_reconnect, safe_error, setup_probe_logging, write_pidfile,
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
    require_schema(client, config, logger, NAME)
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


def _minutes_by_pair(pairs: list) -> dict[str, int]:
    # {"US30:H1": 60, ...} -- dung de tinh nguong do tuoi, doc that tu SQL.
    return {f"{symbol['symbol']}:{timeframe['code']}": int(timeframe["minutes"]) for symbol, timeframe in pairs}


def _structural_scan(client, logger, prefix: str, bars_per_snapshot: int, minutes_by_pair: dict[str, int]) -> tuple[int, int]:
    passed = failed = 0
    now = time.time()
    for order_key in client.scan_iter(match=f"{prefix}:*:*:order", count=200):
        pair, problems = _check_one_pair(client, str(order_key), prefix, bars_per_snapshot, minutes_by_pair, now)
        if problems:
            failed += 1
            log_event(logger, "WARNING", "STATE_INVARIANT_FAILED", "MEDIUM", component=NAME, pair=pair, problems=", ".join(problems))
        else:
            passed += 1
    return passed, failed


def _check_one_pair(client, order_key: str, prefix: str, bars_per_snapshot: int, minutes_by_pair: dict[str, int], now: float) -> tuple[str, list[str]]:
    # order_key = "{prefix}:{tf}:{symbol}:order"; key nen = cung base + epoch.
    base = order_key[:-len(":order")]
    tf, symbol = base[len(prefix) + 1:].split(":", 1)
    pair = f"{symbol}:{tf}"
    problems: list[str] = []

    order = client.lrange(order_key, 0, -1)
    order_set = set(order)
    if len(order) != len(order_set):
        problems.append("order_has_duplicates")
    if len(order) > bars_per_snapshot:
        problems.append(f"window_overflow llen={len(order)}>{bars_per_snapshot}")
    if order != sorted(order, key=int):
        problems.append("order_not_sorted")

    # Doc toan bo nen cua pair trong 1 vong pipeline, roi kiem tung cai.
    pipe = client.pipeline(transaction=False)
    for bartime in order:
        pipe.hgetall(f"{base}:{bartime}")
    for bartime, candle in zip(order, pipe.execute()):
        if not candle:
            problems.append(f"missing_candle bartime={bartime}")
            continue
        missing = [field for field in CANDLE_FIELDS if field not in candle]
        if missing:
            problems.append(f"missing_field bartime={bartime} keys={','.join(missing)}")
            continue
        # "null" la sentinel hop le rieng cho volume (xem _candle_fields()
        # trong redis_publisher.py) -- moi field con lai phai la so huu han.
        non_finite: list[str] = []
        for field in CANDLE_FIELDS:
            raw = candle[field]
            if field == "volume" and raw == "null":
                continue
            try:
                number = float(raw)
            except ValueError:
                problems.append(f"bad_number bartime={bartime} key={field} value={raw}")
                continue
            if not math.isfinite(number):
                non_finite.append(field)
        if non_finite:
            problems.append(f"non_finite_value bartime={bartime} keys={','.join(non_finite)}")
        stored = epoch_from_bartime(candle.get("bartime", ""))
        if stored is not None and str(stored) != bartime:
            problems.append(f"bartime_mismatch key={bartime} field={candle.get('bartime')}")

    minutes = minutes_by_pair.get(pair)
    if minutes and order:
        newest = max(int(bt) for bt in order)
        threshold = minutes * 60 * 3  # 3 chu ky khung gio -- ranh de tranh bao nham luc thi truong dong cua
        if now - newest > threshold:
            age_minutes = round((now - newest) / 60)
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
        base = f"{prefix}:{timeframe['code']}:{symbol['symbol']}"
        mismatches = 0
        for bartime, open_, high, low, close, _volume in rows:
            field = epoch_from_bartime(bartime.isoformat(sep=" ") if hasattr(bartime, "isoformat") else str(bartime))
            stored = client.hget(f"{base}:{field}", "close")
            if stored is None:
                mismatches += 1
                continue
            if round(float(stored), 6) != round(float(close), 6):
                mismatches += 1
                log_event(
                    logger, "WARNING", "SQL_REDIS_MISMATCH", "MEDIUM", component=NAME, pair=pair, bartime=field,
                    sql_close=float(close), redis_close=stored,
                )
        if mismatches == 0:
            log_event(logger, "INFO", "SQL_CROSSCHECK_OK", "NONE", component=NAME, pair=pair, checked=len(rows))


if __name__ == "__main__":
    sys.exit(main())
