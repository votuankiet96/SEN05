"""Central configuration loader for DP Program V3."""
from __future__ import annotations
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
GUEST_TOKEN = "unauthorized_user_token"

class ConfigError(ValueError):
    """Raised when Config.yaml is missing or invalid."""

_TIMEFRAMES = (
    ("M5", "5", 5, 20000),
    ("M10", "10", 10, 20000),
    ("M15", "15", 15, 20000),
    ("M20", "20", 20, 20000),
    ("M30", "30", 30, 20000),
    ("M45", "45", 45, 10000),
    ("H1", "60", 60, 20000),
    ("M90", "90", 90, 10000),
    ("H2", "120", 120, 10000),
    ("H3", "180", 180, 10000),
    ("H4", "240", 240, 10000),
    ("H6", "360", 360, 10000),
    ("H8", "480", 480, 10000),
    ("D1", "1D", 1440, 5000),
    ("W", "1W", 10080, 1000),
)
_SYMBOLS = (
    (2, "FR40", "Indice", True),
    (3, "DE40", "Indice", True),
    (4, "HK50", "Indice", True),
    (5, "J225", "Indice", True),
    (6, "SP35", "Indice", True),
    (7, "UK100", "Indice", True),
    (8, "US500", "Indice", True),
    (9, "US100", "Indice", True),
    (10, "US30", "Indice", True),
    (11, "AUDCAD", "FOREX", False),
    (12, "AUDJPY", "FOREX", False),
    (13, "AUDNZD", "FOREX", False),
    (14, "AUDCHF", "FOREX", False),
    (15, "AUDUSD", "FOREX", False),
    (16, "GBPAUD", "FOREX", False),
    (17, "GBPCAD", "FOREX", False),
    (18, "GBPJPY", "FOREX", False),
    (19, "GBPNZD", "FOREX", False),
    (20, "GBPCHF", "FOREX", False),
    (21, "GBPUSD", "FOREX", False),
    (22, "CADJPY", "FOREX", False),
    (23, "CADCHF", "FOREX", False),
    (24, "EURAUD", "FOREX", False),
    (25, "EURGBP", "FOREX", False),
    (26, "EURCAD", "FOREX", False),
    (27, "EURJPY", "FOREX", False),
    (28, "EURNZD", "FOREX", False),
    (32, "EURCHF", "FOREX", False),
    (33, "EURUSD", "FOREX", False),
    (34, "NZDCAD", "FOREX", False),
    (35, "NZDJPY", "FOREX", False),
    (36, "NZDUSD", "FOREX", False),
    (37, "USDCAD", "FOREX", False),
    (41, "USDJPY", "FOREX", False),
    (48, "USDCHF", "FOREX", False),
    (56, "GOLD", "Metal", True),
    (81, "BTCUSD", "Crypto", True),
)

_TABLES = {
    "fact_table": "DWH.Fact_OHLCV",
    "backfill_state_table": "SEN.DP_BackfillState",
    "load_procedure": "DWH.usp_LoadDirect",
}
_SQL_CONTRACT_VERSION = "4"
_TECHNICAL_DEFAULTS = {
    "tradingview": {
        "websocket_url": "wss://prodata.tradingview.com/socket.io/websocket",
        "timezone": "Etc/UTC",
        "timeout_seconds": 45,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "browser_profile_dir": "runtime/cache/tradingview-profile",
        "headless_fresh_login": True,
        "proactive_refresh_seconds": 900,
        "refresh_retry_seconds": 600,
    },
    "backfill": {
        "overlap_bars": 3,
        "max_bars_per_request": 20000,
        "policy_epoch_utc": "2026-07-28T04:00:00+00:00",
    },
    "live": {
        "interval_minutes": 5,
        "bars_per_request": 3,
        "closed_candles_only": True,
    },
    "service": {
        "backfill_guard_seconds": 150, "startup_grace_seconds": 300,
        "heartbeat_seconds": 15,
        "log_max_bytes": 20971520,
        "log_backup_count": 30,
    },
    "sql_server": {
        "driver": "ODBC Driver 18 for SQL Server",
        "timeout_seconds": 30,
        "command_timeout_seconds": 30,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "batch_size": 1000,
    },
}

def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value

def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result

def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be a boolean")

def _static_contract() -> dict[str, Any]:
    return {
        "tables": dict(_TABLES),
        "data": {
            "timeframes": [
                {
                    "code": code,
                    "interval": interval,
                    "minutes": minutes,
                    "staging_table": f"SEN.TF_{code}",
                    "default_bars": default_bars,
                }
                for code, interval, minutes, default_bars in _TIMEFRAMES
            ],
            "symbols": [
                {
                    "symbol_id": symbol_id,
                    "exchange": "CAPITALCOM",
                    "symbol": symbol,
                    "asset_type": asset_type,
                    "enabled": True,
                    "live": live,
                }
                for symbol_id, symbol, asset_type, live in _SYMBOLS
            ],
        },
    }

def _apply_technical_defaults(config: dict[str, Any]) -> None:
    for section, defaults in _TECHNICAL_DEFAULTS.items():
        target = _mapping(config.get(section), section)
        overridden = sorted(set(target).intersection(defaults))
        if overridden:
            names = ", ".join(f"{section}.{key}" for key in overridden)
            raise ConfigError(f"technical settings are owned by configuration.py: {names}")
        target.update(deepcopy(defaults))

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

def _validate(config: dict[str, Any]) -> None:
    for section in (
        "app", "tradingview", "discord", "data", "backfill", "live", "service", "sql_server", "tables"
    ):
        _mapping(config.get(section), section)
    discord = config["discord"]
    discord["enabled"] = _boolean(discord.get("enabled", False), "discord.enabled")
    discord["webhook_url"] = str(discord.get("webhook_url") or "").strip()
    if discord["enabled"] and not discord["webhook_url"].startswith(
        ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
    ):
        raise ConfigError("discord.webhook_url must be configured when Discord is enabled")
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
    if tv.get("auth_token") == GUEST_TOKEN:
        raise ConfigError("guest TradingView token is not permitted")

    live = config["live"]
    live["enabled"] = _boolean(live.get("enabled", True), "live.enabled")
    live["closed_candles_only"] = _boolean(
        live.get("closed_candles_only", True), "live.closed_candles_only"
    )
    live["interval_minutes"] = _positive_int(live.get("interval_minutes"), "live.interval_minutes")
    live["bars_per_request"] = _positive_int(live.get("bars_per_request"), "live.bars_per_request")
    backfill = config["backfill"]
    backfill["enabled"] = _boolean(backfill.get("enabled", True), "backfill.enabled")
    if "scan_bars" in backfill:
        raise ConfigError("backfill.scan_bars was replaced by backfill.lookback_days")
    for key in ("lookback_days", "overlap_bars", "max_bars_per_request"):
        backfill[key] = _positive_int(backfill.get(key), f"backfill.{key}")
    try:
        policy_epoch = datetime.fromisoformat(str(backfill["policy_epoch_utc"]))
    except (TypeError, ValueError) as exc:
        raise ConfigError("backfill.policy_epoch_utc must be an ISO UTC timestamp") from exc
    if policy_epoch.tzinfo is None:
        raise ConfigError("backfill.policy_epoch_utc must include a UTC offset")
    backfill["policy_epoch_utc"] = policy_epoch.astimezone(timezone.utc).isoformat()
    minimum_minutes = min(item[2] for item in _TIMEFRAMES)
    required_bars = (
        backfill["lookback_days"] * 24 * 60 + minimum_minutes - 1
    ) // minimum_minutes + backfill["overlap_bars"]
    if required_bars > backfill["max_bars_per_request"]:
        raise ConfigError(
            "backfill.max_bars_per_request cannot cover lookback_days "
            "for the smallest timeframe"
        )
    service = config["service"]
    service["backfill_on_start"] = _boolean(
        service.get("backfill_on_start", True), "service.backfill_on_start"
    )
    for key in ("backfill_guard_seconds", "startup_grace_seconds", "heartbeat_seconds", "log_max_bytes", "log_backup_count"):
        service[key] = _positive_int(service.get(key), f"service.{key}")
    slots = service.get("backfill_schedule_utc")
    if not isinstance(slots, list) or not slots:
        raise ConfigError("service.backfill_schedule_utc must be a non-empty list")
    for slot in slots:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(slot)):
            raise ConfigError(f"invalid UTC backfill schedule: {slot}")
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
    for section in ("data", "tables"):
        if section in config:
            raise ConfigError(f"{section} is owned by configuration.py and cannot be overridden")
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
    config["app"]["config_path"] = str(config_path)
    return config
