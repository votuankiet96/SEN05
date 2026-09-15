"""Shared plumbing for the research/ Redis diagnostic probes.

Not part of the dp_program package -- these are standalone diagnostic
tools that observe Redis/SQL from the outside, kept in research/ so
they never ship inside the production .exe (PyInstaller only ever
points at scripts/windows/dp_program_entry.py). Each probe borrows
dp_program's config loading and read-only SQL helpers (single source
of truth for config.yaml / DWH schema), but never imports
redis_publisher's write logic -- reusing the code being checked would
let a bug in that code confirm itself as correct. Cả 3 probe chỉ ĐỌC;
không probe nào được ghi vào Redis production (dp_simulator.py trước
đây có ghi và đã làm hỏng dữ liệu khi chạy code cũ trong bộ nhớ, nên
đã bị xoá).

3 chuong trinh dung chung 3 thu o day: cach nap config.yaml, cach ghi
log (1 file rieng moi chuong trinh, KHONG chung voi runtime/logs/ san
xuat), va cach ghi/doc pid file cho redis_probe.bat start/stop.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_PROBE_DIR = Path(__file__).resolve().parent
_CORE_DIR = _PROBE_DIR.parents[1]  # research/redis_probe/ -> research/ -> core_program/
sys.path.insert(0, str(_CORE_DIR / "src"))

from dp_program.configuration import load_config  # noqa: E402
from dp_program.log import log_event, safe_error  # noqa: E402

__all__ = [
    "load_config", "log_event", "safe_error",
    "setup_probe_logging", "write_pidfile", "remove_pidfile",
    "redis_client", "stamp_to_datetime", "list_keys", "run_with_reconnect",
    "CANDLE_FIELDS", "NUMERIC_FIELDS", "LOG_DIR", "RUN_DIR",
]

# Dung bo field cua mot Hash nen. NUMERIC_FIELDS la phan duy nhat parse
# duoc ra so; timestamp/datetime/source/inserttime la metadata dang chuoi.
CANDLE_FIELDS = ("timestamp", "datetime", "open", "high", "low", "close",
                 "volume", "source", "inserttime")
NUMERIC_FIELDS = ("open", "high", "low", "close", "volume")

LOG_DIR = _PROBE_DIR / "probe_logs"
RUN_DIR = _PROBE_DIR / "run"
# 10MB -- rieng cho probe, KHONG dung chung nguong 20MB/30 ban cua DP
# production (config["service"]["log_max_bytes"]) vi 2 muc dich khac
# nhau: probe chi can log gan day de doi chieu, khong can giu lich su.
_MAX_LOG_BYTES = 10 * 1024 * 1024


class _ClearOnRolloverHandler(RotatingFileHandler):
    """Xoa sach log thay vi xoay vong giu ban cu (.1, .2, ...).

    RotatingFileHandler chuan cua Python COV TINH ep mode ve 'a' bat cu
    khi nao maxBytes > 0 (xem docstring goc: "if maxBytes > 0: mode =
    'a'", de log khong bi mat qua cac lan chay lai) -- nen truyen thang
    mode='w' khong co tac dung, file se chi phinh to mai khong bao gio
    duoc don. Override doRollover() de tu mo lai file bang 'w' (tu
    xoa sach), bo qua hoan toan co che rename .1/.2 cua lop cha --
    dung y "auto clear toan bo" thay vi "rotate va giu lich su".
    """

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        if not self.delay:
            self.stream = open(self.baseFilename, "w", encoding=self.encoding)


def setup_probe_logging(name: str, config: dict[str, Any]) -> logging.Logger:
    """Configure name's own log file, separate from runtime/logs/.

    Dung lai dung dinh dang dong log cua dp_program/log.py (qua
    log_event) nhung tu mo handler rieng -- configure_logging() trong
    log.py cung path vao runtime_dir va chi nhan role live/backfill.
    Khi file vuot _MAX_LOG_BYTES thi tu xoa sach (xem
    _ClearOnRolloverHandler), khong giu ban cu.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"probe.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    file_handler = _ClearOnRolloverHandler(
        LOG_DIR / f"{name}.log", maxBytes=_MAX_LOG_BYTES, backupCount=0, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


def write_pidfile(name: str) -> None:
    # redis_probe.bat doc file nay de biet PID can taskkill khi stop.
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / f"{name}.pid").write_text(str(os.getpid()), encoding="ascii")


def remove_pidfile(name: str) -> None:
    (RUN_DIR / f"{name}.pid").unlink(missing_ok=True)


def redis_client(config: dict[str, Any]):
    """Build a plain redis-py client straight from config.yaml -- no app logic."""
    import redis

    settings = config["redis"]
    return redis.Redis(
        host=settings["host"], port=settings["port"], db=settings["db"],
        username=settings["username"] or None, password=settings["password"] or None,
        socket_connect_timeout=5, socket_timeout=5, decode_responses=True,
    )


def run_with_reconnect(run_once, logger: logging.Logger, component: str, *, retry_seconds: int = 30) -> int:
    """Keep calling run_once(); on a Redis connection drop, wait and retry
    instead of letting the whole probe die.

    Ngay 2026-09-07 03:40 UTC, ca 3 probe quan sat (keyspace/pubsub/state)
    cung mat ket noi Redis (VM-OG8 tam khong toi duoc), moi cai tu bat loi
    o tang tren cung roi thoat han -- khong con gi chay de nhan ra Redis
    da song lai chi vai phut sau, dan toi ~29 gio khong ai giam sat that
    su. Ham nay them 1 lop giu-song, khong doi kien truc doc lap cua chung.
    """
    import redis

    while True:
        try:
            return run_once()
        except redis.exceptions.RedisError as exc:
            log_event(
                logger, "WARNING", "PROBE_RECONNECTING", "MEDIUM", component=component,
                error=safe_error(exc), retry_seconds=retry_seconds,
            )
            time.sleep(retry_seconds)


def stamp_to_datetime(stamp: str) -> datetime | None:
    """Doc moc thoi gian cua key/List ve datetime UTC.

    Dinh dang do redis_publisher._stamp() sinh ra: "YYYYMMDD_HHMMSS", luon
    UTC, khong hau to offset. Khong chua ':' de Redis GUI khong tach moi nen
    thanh nhieu tang thu muc; field `datetime` trong Hash moi la dang nguoi doc.
    """
    try:
        return datetime.strptime(str(stamp), "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def list_keys(client, prefix: str):
    """Duyet cac key List chi muc, bo qua key Hash cua tung nen.

    Ca hai deu bat dau bang "{prefix}_"; khac nhau o cho key Hash co them
    ":" + moc thoi gian, con ten List thi KHONG BAO GIO chua ":" (symbol va
    timeframe deu khong co dau hai cham). Loc bang ten re hon goi TYPE cho
    tung key trong hang chuc nghin key.
    """
    for key in client.scan_iter(match=f"{prefix}_*", count=500):
        if ":" not in key:
            yield key
