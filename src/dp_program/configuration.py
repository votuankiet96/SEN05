"""Central configuration loader for DP Program V3."""
# File này là cửa duy nhất đọc Config.yaml.
# Các module khác chỉ dùng dict config đã được chuẩn hóa ở đây.
# Luồng chính: đọc YAML -> chặn key không được phép -> thêm mặc định kỹ thuật
# -> đổi path về dạng tuyệt đối -> kiểm tra kiểu và ràng buộc vận hành.
from __future__ import annotations
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

# Token này là phiên guest của TradingView, không phải phiên đã đăng nhập.
# Nếu operator dán nhầm token này vào Config.yaml, chương trình phải dừng ngay.
GUEST_TOKEN = "unauthorized_user_token"


class ConfigError(ValueError):
    """Raised when Config.yaml is missing or invalid."""
    # CLI bắt lỗi này để in lỗi cấu hình ngắn gọn cho operator.


# Timeframe nhỏ nhất hiện dùng là M5.
# Dùng để kiểm tra request backfill có đủ phủ lookback_days không.
_MIN_TIMEFRAME_MINUTES = 5
# Hai đối tượng SQL cố định mà engine dùng.
# Không cho Config.yaml đổi để tránh ghi nhầm bảng hoặc procedure.
_TABLES = {
    "fact_table": "DWH.Fact_OHLCV",
    "load_procedure": "DWH.usp_LoadDirect",
}

# Version procedure SQL mà code này yêu cầu.
# Nếu DB khác version, runtime dừng trước khi ghi dữ liệu.
_SQL_CONTRACT_VERSION = "4"
# Các giá trị kỹ thuật do code sở hữu.
# Operator chỉ chỉnh phần vận hành trong Config.yaml, không chỉnh nhóm này.
_TECHNICAL_DEFAULTS = {
    # Cấu hình kết nối TradingView cố định của engine.
    "tradingview": {
        "websocket_url": "wss://prodata.tradingview.com/socket.io/websocket",
        "timezone": "Etc/UTC",
        "timeout_seconds": 45,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        # Nơi Chromium lưu phiên đăng nhập khi cần lấy cookie mới.
        "browser_profile_dir": "runtime/cache/tradingview-profile",
        # Khi cần login lại, mở browser ẩn và đăng nhập mới.
        "headless_fresh_login": True,
        # Refresh đăng nhập trước khi token hết hạn; nếu lỗi thì thử lại sau.
        "proactive_refresh_seconds": 900,
        "refresh_retry_seconds": 600,
    },
    # Giới hạn request backfill khi kéo dữ liệu lịch sử.
    "backfill": {
        "overlap_bars": 3,
        "max_bars_per_request": 20000,
    },
    # Nhịp service 24/7: heartbeat, thời gian chờ startup và log rotation.
    "service": {
        "backfill_guard_seconds": 150, "startup_grace_seconds": 300,
        "heartbeat_seconds": 15,
        "log_max_bytes": 20971520,
        "log_backup_count": 30,
    },
    # Driver/retry/timeout/batch size cố định cho SQL Server.
    "sql_server": {
        "driver": "ODBC Driver 18 for SQL Server",
        "timeout_seconds": 30,
        "command_timeout_seconds": 30,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "batch_size": 1000,
    },
}
# ---------------------------------------------------------------------
# Các hàm nhỏ bên dưới chỉ kiểm kiểu và chuẩn hóa giá trị config.
# Nếu sai, lỗi trả về đúng tên field để operator biết chỗ cần sửa.
# ---------------------------------------------------------------------
def _mapping(value: Any, name: str) -> dict[str, Any]:
    # Một section YAML phải là mapping/dict.
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value
def _positive_int(value: Any, name: str) -> int:
    # Ép về int và yêu cầu lớn hơn 0.
    if isinstance(value, bool):
        # Python coi True/False gần giống 1/0, nên phải chặn riêng.
        raise ConfigError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result

def _boolean(value: Any, name: str) -> bool:
    # Chấp nhận bool YAML hoặc chuỗi quen thuộc như yes/no/on/off.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be a boolean")
def _name_list(value: Any, name: str) -> list[str]:
    # Chuẩn hóa danh sách symbol/timeframe operator nhập cho live.
    # Ở đây chỉ kiểm rỗng/trùng; tồn tại trong SQL được kiểm ở sql_connector.
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list")
    normalized = [str(item).strip().upper() for item in value]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        raise ConfigError(f"{name} contains an empty or duplicate value")
    return normalized

# Trả về tên bảng/procedure cố định để gộp vào config.
def _static_contract() -> dict[str, Any]:
    return {"tables": dict(_TABLES)}

# Thêm mặc định kỹ thuật vào config.
# Nếu Config.yaml cố override các key này thì báo lỗi ngay.
def _apply_technical_defaults(config: dict[str, Any]) -> None:
    for section, defaults in _TECHNICAL_DEFAULTS.items():
        target = _mapping(config.get(section), section)
        overridden = sorted(set(target).intersection(defaults))
        if overridden:
            names = ", ".join(f"{section}.{key}" for key in overridden)
            raise ConfigError(f"technical settings are owned by configuration.py: {names}")
        target.update(deepcopy(defaults))

# Đổi path tương đối thành path tuyệt đối dựa trên thư mục chứa Config.yaml.
# Nhờ vậy service chạy từ đâu cũng dùng đúng runtime/cache/log path.
def _resolve_paths(config: dict[str, Any], root: Path) -> None:
    app = _mapping(config.get("app"), "app")
    runtime_path = Path(str(app.get("runtime_dir") or "runtime"))
    if not runtime_path.is_absolute():
        runtime_path = root / runtime_path
    app["runtime_dir"] = str(runtime_path.resolve())

    tv = _mapping(config.get("tradingview"), "tradingview")
    profile = Path(str(tv.get("browser_profile_dir") or "runtime/cache/tradingview-profile"))
    if not profile.is_absolute():
        profile = root / profile
    tv["browser_profile_dir"] = str(profile.resolve())
# Chuẩn hóa và kiểm tra toàn bộ config operator được phép chỉnh.
# Hàm này sửa dict tại chỗ để code phía sau nhận đúng kiểu cuối cùng.
def _validate(config: dict[str, Any]) -> None:
    # Mọi section chính phải tồn tại trước khi đọc key bên trong.
    for section in (
        "app", "tradingview", "discord", "redis", "backfill", "live", "service", "sql_server", "tables"
    ):
        _mapping(config.get(section), section)

    # Discord: báo cáo trạng thái/sự cố nếu operator bật webhook.
    discord = config["discord"]
    discord["enabled"] = _boolean(discord.get("enabled", False), "discord.enabled")
    discord["webhook_url"] = str(discord.get("webhook_url") or "").strip()
    if discord["enabled"] and not discord["webhook_url"].startswith(
        ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
    ):
        raise ConfigError("discord.webhook_url must be configured when Discord is enabled")

    # Redis: đẩy nến live cho OG đọc; tuỳ chọn bật/tắt, mặc định tắt.
    # Operator không bắt buộc phải khai đủ mọi key khi Redis còn tắt — thiếu
    # key nào thì dùng mặc định dưới đây, khai rồi thì vẫn bị kiểm kiểu chặt.
    redis_cfg = config["redis"]
    redis_cfg.setdefault("enabled", False)
    redis_cfg.setdefault("host", "")
    redis_cfg.setdefault("username", "")
    redis_cfg.setdefault("password", "")
    redis_cfg.setdefault("key_prefix", "dp:candles")
    redis_cfg.setdefault("port", 6379)
    redis_cfg.setdefault("db", 0)
    redis_cfg.setdefault("bars_per_snapshot", 500)
    redis_cfg.setdefault("circuit_cooldown_seconds", 30)
    redis_cfg.setdefault("timeout_seconds", 0.3)
    redis_cfg["enabled"] = _boolean(redis_cfg["enabled"], "redis.enabled")
    redis_cfg["host"] = str(redis_cfg["host"] or "").strip()
    redis_cfg["username"] = str(redis_cfg["username"] or "").strip()
    redis_cfg["password"] = str(redis_cfg["password"] or "").strip()
    redis_cfg["key_prefix"] = str(redis_cfg["key_prefix"] or "dp:candles").strip()
    for key in ("port", "bars_per_snapshot", "circuit_cooldown_seconds"):
        redis_cfg[key] = _positive_int(redis_cfg[key], f"redis.{key}")
    try:
        redis_cfg["db"] = int(redis_cfg["db"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("redis.db must be an integer") from exc
    if redis_cfg["db"] < 0:
        raise ConfigError("redis.db must be zero or a positive integer")
    try:
        redis_cfg["timeout_seconds"] = float(redis_cfg["timeout_seconds"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("redis.timeout_seconds must be a number") from exc
    if redis_cfg["timeout_seconds"] <= 0:
        raise ConfigError("redis.timeout_seconds must be greater than zero")
    if redis_cfg["enabled"] and not redis_cfg["host"]:
        raise ConfigError("redis.host must be configured when Redis is enabled")

    # TradingView: token/cookie và tài khoản dùng khi cần đăng nhập lại.
    tv = config["tradingview"]
    for key in ("auth_token", "cookie", "username", "password", "two_factor_secret"):
        tv[key] = str(tv.get(key) or "").strip()
    tv["headless_fresh_login"] = _boolean(
        tv.get("headless_fresh_login", True), "tradingview.headless_fresh_login"
    )
    if not str(tv.get("websocket_url") or "").startswith("wss://"):
        raise ConfigError("tradingview.websocket_url must start with wss://")
    tv["timeout_seconds"] = _positive_int(tv.get("timeout_seconds"), "tradingview.timeout_seconds")
    tv["retry_count"] = _positive_int(tv.get("retry_count"), "tradingview.retry_count")
    for key in ("proactive_refresh_seconds", "refresh_retry_seconds"):
        tv[key] = _positive_int(tv.get(key), f"tradingview.{key}")
    # Không cho chạy bằng token guest.
    if tv.get("auth_token") == GUEST_TOKEN:
        raise ConfigError("guest TradingView token is not permitted")

    # Live: operator chọn symbol/timeframe và chu kỳ lấy nến mới.
    # Tên symbol/timeframe có hợp lệ trong SQL hay không được kiểm sau.
    live = config["live"]
    live["enabled"] = _boolean(live.get("enabled", True), "live.enabled")
    live["closed_candles_only"] = _boolean(
        live.get("closed_candles_only", True), "live.closed_candles_only"
    )
    live["interval_minutes"] = _positive_int(live.get("interval_minutes"), "live.interval_minutes")
    live["bars_per_request"] = _positive_int(live.get("bars_per_request"), "live.bars_per_request")
    live["symbols"] = _name_list(live.get("symbols"), "live.symbols")
    live["timeframes"] = _name_list(live.get("timeframes"), "live.timeframes")
    # Production chỉ ghi nến đã đóng; nến đang chạy còn có thể đổi giá.
    if not live["closed_candles_only"]:
        raise ConfigError("live.closed_candles_only must remain true for production delivery")

    # Backfill: workflow theo lịch để kiểm và sửa cửa sổ dữ liệu lịch sử.
    backfill = config["backfill"]
    backfill["enabled"] = _boolean(backfill.get("enabled", True), "backfill.enabled")
    backfill["run_on_start"] = _boolean(
        backfill.get("run_on_start", True), "backfill.run_on_start"
    )
    # Key cũ scan_bars bị loại bỏ; báo lỗi để operator sửa Config.yaml.
    if "scan_bars" in backfill:
        raise ConfigError("backfill.scan_bars was replaced by backfill.lookback_days")
    for key in ("lookback_days", "overlap_bars", "max_bars_per_request"):
        backfill[key] = _positive_int(backfill.get(key), f"backfill.{key}")
    # Kiểm max_bars_per_request đủ phủ lookback trên timeframe nhỏ nhất.
    required_bars = (
        backfill["lookback_days"] * 24 * 60 + _MIN_TIMEFRAME_MINUTES - 1
    ) // _MIN_TIMEFRAME_MINUTES + backfill["overlap_bars"]
    if required_bars > backfill["max_bars_per_request"]:
        raise ConfigError(
            "backfill.max_bars_per_request cannot cover lookback_days "
            "for the smallest timeframe"
        )
    # Lịch backfill theo UTC, dạng HH:MM.
    slots = backfill.get("schedule_utc")
    if not isinstance(slots, list) or not slots:
        raise ConfigError("backfill.schedule_utc must be a non-empty list")
    for slot in slots:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(slot)):
            raise ConfigError(f"invalid UTC backfill schedule: {slot}")

    # Service: heartbeat, startup grace period và log rotation.
    service = config["service"]
    for key in ("backfill_guard_seconds", "startup_grace_seconds", "heartbeat_seconds", "log_max_bytes", "log_backup_count"):
        service[key] = _positive_int(service.get(key), f"service.{key}")

    # SQL Server: dùng trusted connection hoặc username/password.
    sql = config["sql_server"]
    for key in ("trusted_connection", "trust_server_certificate"):
        sql[key] = _boolean(sql.get(key), f"sql_server.{key}")
    for key in ("server", "database", "port", "username", "password"):
        sql[key] = str(sql.get(key) or "").strip()
    for key in ("timeout_seconds", "command_timeout_seconds", "retry_count", "batch_size"):
        sql[key] = _positive_int(sql.get(key), f"sql_server.{key}")
    if not sql.get("server") or not sql.get("database") or not sql.get("driver"):
        raise ConfigError("sql_server requires driver, server, and database")
    if bool(sql.get("username")) != bool(sql.get("password")):
        raise ConfigError("SQL username and password must be configured together")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the single private Config.yaml and validate the result."""
    # Đóng gói bằng PyInstaller (sys.frozen): CHỈ đọc Config.yaml cùng thư
    # mục với file .exe (sys.executable) — không fallback sang cwd, để
    # tránh đọc nhầm một Config.yaml khác nếu .exe bị chạy từ working
    # directory không đúng vị trí thật của nó.
    # Nhánh else giữ y nguyên hành vi cũ khi chạy `python -m dp_program`:
    # tìm ở project root, không có thì mới fallback về cwd.
    if getattr(sys, "frozen", False):
        default_config = Path(sys.executable).resolve().parent / "Config.yaml"
    else:
        project_config = Path(__file__).resolve().parents[2] / "Config.yaml"
        default_config = project_config if project_config.is_file() else Path.cwd() / "Config.yaml"
    config_path = Path(path) if path else default_config
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    config = deepcopy(_mapping(raw, "Config.yaml"))
    # Chặn những phần không thuộc quyền operator:
    # data lấy từ SQL, tables do code cố định, version SQL đi theo code.
    if "data" in config:
        raise ConfigError("data is owned by SQL dimensions and cannot be overridden")
    if "tables" in config:
        raise ConfigError("tables is owned by configuration.py and cannot be overridden")
    sql = _mapping(config.get("sql_server"), "sql_server")
    if "contract_version" in sql:
        raise ConfigError(
            "sql_server.contract_version is owned by configuration.py and cannot be overridden"
        )
    config.update(_static_contract())
    sql["contract_version"] = _SQL_CONTRACT_VERSION
    _apply_technical_defaults(config)
    _resolve_paths(config, config_path.parent)
    _validate(config)
    # Lưu path để lệnh settings/doctor biết config thật đang dùng ở đâu.
    config["app"]["config_path"] = str(config_path)
    return config
