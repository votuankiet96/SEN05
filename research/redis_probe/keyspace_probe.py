"""Probe doc lap: quan sat Redis keyspace notification cho key CANDLE:*.

Xac nhan lenh Redis (HSET/HDEL/RPUSH/LPOP/DEL) THAT SU chay tren
server -- tin hieu nay do chinh Redis phat ra khi thuc thi lenh, hoan
toan tach khoi logic Python cua redis_publisher.py. Day la tang thap
nhat trong 3 probe quan sat: neu redis_publisher tuong publish thanh
cong nhung script Lua loi/khong chay, day la noi bat duoc, khong bi
"tu xac nhan dung" nhu khi tai dung code ghi.

Tien de ha tang (viec cua Redis server, KHONG phai code nay):
    CONFIG SET notify-keyspace-events Kghl
    (K=kenh keyspace, g=lenh generic nhu DEL, h=lenh hash, l=lenh list)
Neu chua bat, probe van chay nhung se khong thay gi -- co log canh bao
ngay luc khoi dong de khong nham la "moi thu im lang vi khong co ghi".

Chay: python keyspace_probe.py   (Ctrl+C de dung khi chay tay;
redis_probe.bat quan ly start/stop khi chay nen).
"""
from __future__ import annotations

import sys
import time

from _probe_common import load_config, log_event, redis_client, remove_pidfile, run_with_reconnect, safe_error, setup_probe_logging, write_pidfile

NAME = "keyspace_probe"
_SUMMARY_INTERVAL_SECONDS = 60


def main() -> int:
    config = load_config()
    logger = setup_probe_logging(NAME, config)
    write_pidfile(NAME)
    try:
        return run_with_reconnect(lambda: _run(config, logger), logger, NAME)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 -- probe phai song, khong duoc crash im lang
        log_event(logger, "ERROR", "PROBE_CRASHED", "HIGH", component=NAME, error=exc)
        return 1
    finally:
        remove_pidfile(NAME)


def _run(config: dict, logger) -> int:
    client = redis_client(config)
    db = int(config["redis"]["db"])
    prefix = str(config["redis"]["key_prefix"])
    current = client.config_get("notify-keyspace-events")
    flags = current.get("notify-keyspace-events", "")
    if not flags:
        log_event(
            logger, "WARNING", "KEYSPACE_NOTIFY_DISABLED", "MEDIUM", component=NAME,
            action="chua bat -- chay CONFIG SET notify-keyspace-events Kghl tren Redis server",
        )
    else:
        log_event(logger, "INFO", "KEYSPACE_NOTIFY_ENABLED", "NONE", component=NAME, flags=flags)

    pattern = f"__keyspace@{db}__:{prefix}:*"
    pubsub = client.pubsub()
    pubsub.psubscribe(pattern)
    log_event(logger, "INFO", "PROBE_STARTED", "NONE", component=NAME, channel_pattern=pattern)

    counts: dict[str, int] = {}
    last_summary = time.monotonic()
    for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        channel = str(message["channel"])
        key = channel.split("__:", 1)[1] if "__:" in channel else channel
        op = str(message["data"])
        counts[op] = counts.get(op, 0) + 1
        log_event(logger, "INFO", "WRITE_OBSERVED", "NONE", component=NAME, key=key, op=op)
        now = time.monotonic()
        if now - last_summary >= _SUMMARY_INTERVAL_SECONDS:
            _flush_summary(logger, counts)
            last_summary = now
    return 0


def _flush_summary(logger, counts: dict[str, int]) -> None:
    fields = {f"op_{op}": total for op, total in counts.items()}
    log_event(logger, "INFO", "PROBE_SCAN_SUMMARY", "NONE", component=NAME, total=sum(counts.values()), **fields)
    counts.clear()


if __name__ == "__main__":
    sys.exit(main())
