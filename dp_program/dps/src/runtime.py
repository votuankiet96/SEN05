"""Nền tảng dùng chung của DPS: kiểu dữ liệu, log, điều tốc (pacing) và vòng đời tiến trình.

Bốn phần độc lập trong một file, mỗi phần có tiêu đề riêng bên dưới:
  1. Kiểu dữ liệu dùng chung (DpsError, Pair, Candle, Tick): cách biểu diễn nến DUY NHẤT xuyên suốt chương trình
     (SQL -> lịch phát -> Redis). Mọi thời điểm là datetime naive theo UTC, khớp kiểu datetime2 của SQL.
  2. Log có cấu trúc: một dòng cho mỗi sự kiện (`EVENT key=value ...`), che trường bí mật, xoay vòng file.
  3. Điều tốc replay: quyết định KHI NÀO (giờ thật) được phát mỗi mốc. Cả hai chế độ dùng deadline TUYỆT ĐỐI trên
     đồng hồ đơn điệu (không cộng dồn sleep nên không trôi): delay (mốc thứ k lúc t0 + k * delay) và speed (lúc
     t0 + (giờ_ảo - gốc) / speed, giữ nguyên tỷ lệ thời gian). delay = 0 là tốc độ tối đa; đồng hồ và sleep được inject.
  4. Vòng đời tiến trình: khóa một-instance, state.json + heartbeat, yêu cầu dừng, manifest. Mọi file nằm dưới
     `<runtime_dir>/run/`; quy ước giống dp_program.
Module thuần `schedule` chỉ được import phần 1 (kiểu dữ liệu) từ file này; test kiến trúc kiểm điều đó.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol


# ---------------------------------------------------------------------------------------------
# 1. Kiểu dữ liệu dùng chung
# ---------------------------------------------------------------------------------------------
class DpsError(Exception):
    """Lỗi gốc của DPS: mọi lỗi nghiệp vụ kế thừa lớp này để CLI bắt gọn."""


@dataclass(frozen=True, slots=True)
class Pair:
    """Một chuỗi nến: (symbol, khung). `minutes` là độ dài khung theo DWH.Dim_Timeframe."""

    symbol: str
    symbol_id: int
    timeframe: str
    timeframe_id: int
    minutes: int


@dataclass(frozen=True, slots=True)
class Candle:
    """Một nến đã đóng. `bar_time` là giờ MỞ nến (UTC), đúng như cột BarTime của Fact_OHLCV."""

    pair: Pair
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class Tick:
    """Một mốc phát: mọi nến có cùng giờ phát `release`, theo thứ tự tất định. `seq` bắt đầu từ 1."""

    seq: int
    release: datetime
    candles: tuple[Candle, ...]


# ---------------------------------------------------------------------------------------------
# 2. Log có cấu trúc: trường có tên chứa password/secret/token luôn bị che
# ---------------------------------------------------------------------------------------------
_MASKED_WORDS = ("password", "secret", "token")
_LOG_FILE_BYTES = 5 * 1024 * 1024
_LOG_FILE_BACKUPS = 5
_logging_configured = False


def setup_logging(runtime_dir: Path, *, level: int = logging.INFO) -> None:
    """Gắn handler file (xoay vòng) và stderr vào root logger; gọi nhiều lần vẫn chỉ gắn một lần."""
    global _logging_configured
    if _logging_configured:
        return
    log_dir = runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s")
    formatter.converter = time.gmtime
    root = logging.getLogger()
    for handler in (
        RotatingFileHandler(log_dir / "dps.log", maxBytes=_LOG_FILE_BYTES, backupCount=_LOG_FILE_BACKUPS, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
    _logging_configured = True


def log_event(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    parts = [event]
    for key, value in fields.items():
        masked = any(word in key.lower() for word in _MASKED_WORDS)
        parts.append(f"{key}={'***' if masked else value}")
    logger.log(level, " ".join(parts))


# ---------------------------------------------------------------------------------------------
# 3. Điều tốc replay: khi nào (giờ thật) được phát mỗi mốc
# ---------------------------------------------------------------------------------------------
_SLICE_SECONDS = 0.25      # chờ được cắt thành lát ngắn để phản ứng ngay với yêu cầu dừng
_RESYNC_LAG_SECONDS = 5.0  # trễ quá mức này (đứng hình, Redis/SQL chậm) thì đặt lại gốc thời gian, không phát bù dồn dập
PACING_LOGGER = logging.getLogger("dps.pacing")


class Pacer(Protocol):
    def begin(self, origin: datetime) -> None:
        """Đặt gốc thời gian: giờ thật hiện tại ứng với giờ ảo `origin`."""

    def wait(self, release: datetime, stop: Callable[[], bool]) -> float | None:
        """Chờ tới lúc phát mốc `release`. Trả độ trễ (giây, >= 0) hoặc None nếu bị yêu cầu dừng."""


class _DeadlinePacer:
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._clock, self._sleep = clock, sleep
        self._t0, self._origin = 0.0, datetime.min

    def begin(self, origin: datetime) -> None:
        self._t0, self._origin = self._clock(), origin

    def _deadline(self, release: datetime) -> float:
        raise NotImplementedError

    def _resync(self, release: datetime) -> None:
        """Đặt lại gốc thời gian để mốc `release` rơi vào BÂY GIỜ; các mốc sau tiếp tục đúng nhịp từ đó."""
        raise NotImplementedError

    def wait(self, release: datetime, stop: Callable[[], bool]) -> float | None:
        deadline = self._deadline(release)
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                if -remaining > _RESYNC_LAG_SECONDS:
                    self._resync(release)
                    log_event(PACING_LOGGER, logging.WARNING, "PACER_RESYNC", lag_s=round(-remaining, 1),
                              action="no catch-up burst; the next ticks keep the normal pace")
                return -remaining
            if stop():
                return None
            self._sleep(min(remaining, _SLICE_SECONDS))


class DelayPacer(_DeadlinePacer):
    def __init__(self, delay_seconds: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        super().__init__(clock, sleep)
        self._delay, self._count = delay_seconds, 0

    def begin(self, origin: datetime) -> None:
        super().begin(origin)
        self._count = 0

    def _deadline(self, release: datetime) -> float:
        self._count += 1
        return self._t0 + self._count * self._delay

    def _resync(self, release: datetime) -> None:
        self._t0 = self._clock() - self._count * self._delay


class SpeedPacer(_DeadlinePacer):
    def __init__(self, speed: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        super().__init__(clock, sleep)
        self._speed = speed

    def _deadline(self, release: datetime) -> float:
        return self._t0 + (release - self._origin).total_seconds() / self._speed

    def _resync(self, release: datetime) -> None:
        self._t0 = self._clock() - (release - self._origin).total_seconds() / self._speed


def make_pacer(
    mode: str, delay_seconds: float, speed: float, *,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> Pacer:
    if mode == "delay":
        return DelayPacer(delay_seconds, clock, sleep)
    if mode == "speed":
        return SpeedPacer(speed, clock, sleep)
    raise DpsError(f"unknown pacing mode: {mode}")


def estimate_seconds(mode: str, delay_seconds: float, speed: float, ticks: int, start: datetime, last: datetime | None) -> float:
    """Ước lượng thời gian chạy (giây thật) của một lịch có `ticks` mốc, mốc cuối ở `last`."""
    if mode == "delay" or last is None:
        return ticks * delay_seconds
    return (last - start).total_seconds() / speed


# ---------------------------------------------------------------------------------------------
# 4. Vòng đời tiến trình: khóa một-instance, state.json + heartbeat, yêu cầu dừng, manifest
# ---------------------------------------------------------------------------------------------
VERSION = "0.1.0"
_STATE_FILE, _STOP_FILE, _MANIFEST_FILE, _LOCK_FILE = "state.json", "stop.request", "manifest.json", "dps.lock"
_HEARTBEAT_MIN_INTERVAL_SECONDS = 2.0
_ALIVE_HEARTBEAT_SECONDS = 60.0
_ACTIVE_STATUSES = ("running", "waiting_redis")      # tiến trình còn làm việc (đang phát, hoặc đang chờ Redis quay lại)


class InstanceLockError(DpsError):
    """Đã có một tiến trình DPS khác đang chạy trên cùng runtime_dir."""


def _run_dir(runtime_dir: Path) -> Path:
    path = runtime_dir / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


if sys.platform == "win32":
    import msvcrt

    def _lock(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def instance_lock(runtime_dir: Path) -> Iterator[None]:
    """Giữ một khóa OS không chặn: chạy lần hai trên cùng runtime_dir sẽ báo lỗi ngay."""
    handle = open(_run_dir(runtime_dir) / _LOCK_FILE, "a+b")
    try:
        try:
            _lock(handle)
        except OSError as exc:
            raise InstanceLockError("another DPS process is already running") from exc
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259                   # STILL_ACTIVE: tiến trình đã thoát nhưng còn handle thì không tính là sống
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_state(runtime_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((_run_dir(runtime_dir) / _STATE_FILE).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def service_status(runtime_dir: Path) -> dict[str, Any]:
    """state.json kèm PID còn sống không và tuổi heartbeat (cho lệnh `status`)."""
    state = read_state(runtime_dir)
    alive = process_alive(int(state.get("pid") or 0))
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(state["heartbeat_at"]))).total_seconds()
    except (KeyError, ValueError):
        age = None
    return {**state, "process_alive": alive, "heartbeat_age_seconds": None if age is None else round(age, 1),
            "healthy": bool(state.get("status") in _ACTIVE_STATUSES and alive and age is not None and age < _ALIVE_HEARTBEAT_SECONDS)}


def request_stop(runtime_dir: Path, *, wait_seconds: int = 60) -> dict[str, Any]:
    """Tạo file yêu cầu dừng rồi chờ tiến trình thoát (nếu còn chạy)."""
    (_run_dir(runtime_dir) / _STOP_FILE).write_text(_now_iso(), encoding="ascii")
    pid = int(read_state(runtime_dir).get("pid") or 0)
    deadline = time.monotonic() + max(0, wait_seconds)
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.5)
    return {"stopped": not process_alive(pid), "pid": pid}


class Runtime:
    """Trạng thái tiến trình đang chạy: cờ dừng, state.json (ghi giãn nhịp) và manifest."""

    def __init__(self, runtime_dir: Path) -> None:
        self.run_dir = _run_dir(runtime_dir)
        self._signalled = False
        self._state: dict[str, Any] = {}
        self._last_write = float("-inf")

    def install_signal_handlers(self) -> None:
        def handler(_number: int, _frame: Any) -> None:
            self._signalled = True
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def stop_requested(self) -> bool:
        return self._signalled or (self.run_dir / _STOP_FILE).exists()

    def clear_stop_request(self) -> None:
        (self.run_dir / _STOP_FILE).unlink(missing_ok=True)

    def write_state(self, *, force: bool = False, **fields: Any) -> bool:
        """Gộp `fields` vào state; ghi file nếu `force` hoặc đã đủ khoảng giãn nhịp. Trả True nếu đã ghi."""
        self._state.update(fields)
        now = time.monotonic()
        if not force and now - self._last_write < _HEARTBEAT_MIN_INTERVAL_SECONDS:
            return False
        payload = {**self._state, "pid": os.getpid(), "version": VERSION, "heartbeat_at": _now_iso()}
        _atomic_write(self.run_dir / _STATE_FILE, json.dumps(payload, ensure_ascii=True, default=str))
        self._last_write = now
        return True

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        payload = {"version": VERSION, "written_at": _now_iso(), **manifest}
        _atomic_write(self.run_dir / _MANIFEST_FILE, json.dumps(payload, ensure_ascii=True, indent=2, default=str))
