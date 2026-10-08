"""Cấu hình DPS: nơi DUY NHẤT đọc `config.yaml` (mẫu: config.example.yaml).

Luồng: đọc YAML -> chặn khóa lạ -> kiểm kiểu/khoảng -> dựng các dataclass bất biến. Giá trị kỹ thuật
(driver ODBC, timeout, retry) do code sở hữu, không cho chỉnh trong YAML. File thật chứa mật khẩu nên
không bao giờ được in ra: trường bí mật có `repr=False` và lỗi YAML chỉ báo loại lỗi, không báo nội dung.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from runtime import DpsError

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config.yaml"      # dps/config.yaml (file này nằm ở dps/src/)
PACING_MODES = ("delay", "speed")


class ConfigError(DpsError):
    """config.yaml thiếu, sai kiểu hoặc vi phạm ràng buộc an toàn."""


@dataclass(frozen=True, slots=True)
class SqlConfig:
    server: str
    port: str
    database: str
    username: str
    password: str = field(repr=False)
    trusted_connection: bool
    encrypt: str
    trust_server_certificate: bool
    driver: str = "ODBC Driver 18 for SQL Server"
    login_timeout_seconds: int = 30
    command_timeout_seconds: int = 120
    retry_count: int = 3
    retry_delay_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class RedisConfig:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    db: int
    allowed_dbs: tuple[int, ...]
    key_prefix: str
    bars_per_snapshot: int
    hash_ttl_seconds: int
    connect_timeout_seconds: float = 3.0
    socket_timeout_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    start: datetime
    end: datetime
    window_days: int = 7


@dataclass(frozen=True, slots=True)
class PacingConfig:
    mode: str
    delay_seconds: float
    speed: float


@dataclass(frozen=True, slots=True)
class Config:
    runtime_dir: Path
    sql: SqlConfig
    redis: RedisConfig
    replay: ReplayConfig
    pacing: PacingConfig

    def summary(self) -> dict[str, Any]:
        """Tóm tắt KHÔNG chứa bí mật, dùng cho manifest và log."""
        return {
            "symbols": list(self.replay.symbols), "timeframes": list(self.replay.timeframes),
            "start": f"{self.replay.start:%Y-%m-%d %H:%M:%S}", "end": f"{self.replay.end:%Y-%m-%d %H:%M:%S}",
            "pacing": {"mode": self.pacing.mode, "delay_seconds": self.pacing.delay_seconds, "speed": self.pacing.speed},
            "redis_db": self.redis.db, "bars_per_snapshot": self.redis.bars_per_snapshot,
            "hash_ttl_seconds": self.redis.hash_ttl_seconds,
        }


# ---------------------------------------------------------------------------------------------
# Bộ chuyển kiểu: mỗi hàm nhận giá trị YAML thô, trả giá trị chuẩn hóa hoặc ném ValueError/TypeError.
# ---------------------------------------------------------------------------------------------
def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bool, dict, list)):
        raise ValueError("must be a string")
    return str(value).strip()


def _required_text(value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError("must not be empty")
    return text


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("must be true or false")


def _encrypt(value: Any) -> str:
    return ("yes" if value else "no") if isinstance(value, bool) else _required_text(value).lower()


def _integer(minimum: int) -> Callable[[Any], int]:
    def convert(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        number = int(value)
        if number < minimum:
            raise ValueError(f"must be >= {minimum}")
        return number
    return convert


def _number(minimum: float, *, exclusive: bool = False) -> Callable[[Any], float]:
    def convert(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("must be a number")
        number = float(value)
        if number < minimum or (exclusive and number == minimum):
            raise ValueError(f"must be {'>' if exclusive else '>='} {minimum}")
        return number
    return convert


def _prefix(value: Any) -> str:
    text = _required_text(value)
    if ":" in text or any(ch.isspace() for ch in text):
        raise ValueError("must not contain ':' or whitespace")
    return text


def _db_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("must be a non-empty list of db numbers")
    return tuple(_integer(0)(item) for item in value)


def _mode(value: Any) -> str:
    text = _required_text(value).lower()
    if text not in PACING_MODES:
        raise ValueError(f"must be one of {', '.join(PACING_MODES)}")
    return text


def parse_names(value: Any) -> tuple[str, ...]:
    """Danh sách tên (symbol/khung): nhận list hoặc chuỗi cách nhau bằng dấu phẩy; viết hoa, không trùng."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError("must be a non-empty list")
    names = tuple(str(item).strip().upper() for item in items)
    if any(not name or ":" in name or " " in name for name in names):
        raise ValueError("contains an empty or invalid name")
    if len(set(names)) != len(names):
        raise ValueError("contains duplicates")
    return names


def parse_instant(value: Any) -> datetime:
    """Đổi giá trị thành datetime naive UTC, bỏ phần dưới giây."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _section(raw: dict, name: str, converters: dict[str, Callable[[Any], Any]]) -> dict[str, Any]:
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"{name} must be a mapping")
    unknown, missing = sorted(set(section) - set(converters)), sorted(set(converters) - set(section))
    if unknown:
        raise ConfigError(f"{name}: unknown key(s): {', '.join(unknown)}")
    if missing:
        raise ConfigError(f"{name}: missing key(s): {', '.join(missing)}")
    values: dict[str, Any] = {}
    for key, convert in converters.items():
        try:
            values[key] = convert(section[key])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name}.{key}: {exc}") from exc
    return values


def _check_window(start: datetime, end: datetime) -> None:
    if start >= end:
        raise ConfigError("replay.start_utc must be earlier than replay.end_utc")


def load_config(path: str | Path | None = None) -> Config:
    """Đọc và kiểm toàn bộ config.yaml; mọi vi phạm ném ConfigError (không kèm nội dung bí mật)."""
    config_path = (Path(path) if path else DEFAULT_PATH).resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration ({type(exc).__name__})") from exc
    sections = ("app", "sql_server", "redis", "replay", "pacing")
    if not isinstance(raw, dict) or set(raw) != set(sections):
        raise ConfigError(f"config.yaml must contain exactly these sections: {', '.join(sections)}")
    app = _section(raw, "app", {"runtime_dir": _required_text})
    sql = _section(raw, "sql_server", {
        "server": _required_text, "port": _text, "database": _required_text, "username": _text, "password": _text,
        "trusted_connection": _flag, "encrypt": _encrypt, "trust_server_certificate": _flag,
    })
    redis = _section(raw, "redis", {
        "host": _required_text, "port": _integer(1), "username": _text, "password": _text, "db": _integer(0),
        "allowed_dbs": _db_list, "key_prefix": _prefix, "bars_per_snapshot": _integer(1), "hash_ttl_seconds": _integer(1),
    })
    replay = _section(raw, "replay", {
        "symbols": parse_names, "timeframes": parse_names, "start_utc": parse_instant, "end_utc": parse_instant,
    })
    pacing = _section(raw, "pacing", {"mode": _mode, "delay_seconds": _number(0), "speed": _number(0, exclusive=True)})
    if redis["db"] not in redis["allowed_dbs"]:
        raise ConfigError(f"redis.db {redis['db']} is not listed in redis.allowed_dbs")
    _check_window(replay["start_utc"], replay["end_utc"])
    runtime_dir = Path(app["runtime_dir"])
    return Config(
        runtime_dir=(runtime_dir if runtime_dir.is_absolute() else config_path.parent / runtime_dir).resolve(),
        sql=SqlConfig(**sql), redis=RedisConfig(**redis),
        replay=ReplayConfig(replay["symbols"], replay["timeframes"], replay["start_utc"], replay["end_utc"]),
        pacing=PacingConfig(pacing["mode"], pacing["delay_seconds"], pacing["speed"]),
    )


def with_overrides(
    config: Config, *, start: Any = None, end: Any = None, symbols: Any = None, timeframes: Any = None,
    delay: float | None = None, speed: float | None = None,
) -> Config:
    """Áp tham số dòng lệnh lên config rồi kiểm lại; `delay` và `speed` loại trừ nhau."""
    if delay is not None and speed is not None:
        raise ConfigError("use either --delay or --speed, not both")
    try:
        replay = dataclasses.replace(
            config.replay,
            symbols=parse_names(symbols) if symbols is not None else config.replay.symbols,
            timeframes=parse_names(timeframes) if timeframes is not None else config.replay.timeframes,
            start=parse_instant(start) if start is not None else config.replay.start,
            end=parse_instant(end) if end is not None else config.replay.end,
        )
        pacing = config.pacing
        if delay is not None:
            pacing = PacingConfig("delay", _number(0)(delay), pacing.speed)
        if speed is not None:
            pacing = PacingConfig("speed", pacing.delay_seconds, _number(0, exclusive=True)(speed))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid command-line override: {exc}") from exc
    _check_window(replay.start, replay.end)
    return dataclasses.replace(config, replay=replay, pacing=pacing)
