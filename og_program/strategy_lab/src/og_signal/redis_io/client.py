"""Kết nối Redis + đọc sl_config.yaml cho luồng "backtest cấp 2".

Chỉ giữ phần dùng chung (nạp config, tạo client) -- KHÔNG có hàm đọc nến
từ Redis DB0 như bản gốc order_gateway/og_signal/redis_io/client.py, vì
strategy_lab đọc OHLCV từ SQL Server (db_connector.py), không bao giờ đọc
nến từ Redis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import redis
import yaml

from strategy_lab.src.og_signal.redis_io.logging_setup import log_event

LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "sl_config.yaml"

# Tinh chỉnh kỹ thuật thuần, chưa từng cần operator đổi -- cùng lý do với
# REDIS_DEFAULTS của order_gateway (xem CLAUDE.md mục 5).
REDIS_DEFAULTS: dict[str, Any] = {
    "port": 6379,
    "socket_timeout_seconds": 5,
    "socket_connect_timeout_seconds": 5,
    "healthcheck_interval_seconds": 30,
    "reconnect_delay_seconds": 3,
}

# Nhịp heartbeat của worker (chỉ log "còn sống", KHÔNG quét lại dữ liệu).
# Default trong code nhưng operator ĐÈ ĐƯỢC qua redis.backfill_event trong
# sl_config.yaml -- đúng cách order_gateway làm với
# redis.input.reconcile_interval_seconds (order_gateway/src/candle_reader.py).
# Trước 2026-09-22 số 1800 viết thẳng vào lời gọi listen_for_backfill_events()
# trong worker.py: không đè được, không validate.
_BACKFILL_EVENT_DEFAULTS: dict[str, Any] = {
    "reconcile_interval_seconds": 1800,
}

_REQUIRED_BACKFILL_EVENT_KEYS = ("channel",)
_REQUIRED_PAST_SIGNAL_KEYS = ("db", "key_prefix")
_REQUIRED_PAST_SIGNAL_TREND_KEYS = ("db", "key_prefix")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Nạp sl_config.yaml + áp default kỹ thuật."""
    with path.open("r", encoding="utf-8") as file:
        config = normalize_app_config(yaml.safe_load(file) or {})
    log_event(LOGGER, logging.INFO, "config_loaded", path=str(path))
    return config


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config)
    out["redis"] = normalize_redis_config(out.get("redis") or {})
    return out


def normalize_redis_config(redis_config: dict[str, Any]) -> dict[str, Any]:
    """Dựng config Redis đầy đủ từ phần operator khai trong sl_config.yaml.

    ``redis.backfill_event.channel``, ``redis.past_signal.{db,key_prefix}``
    và ``redis.past_signal_trend.{db,key_prefix}`` là bắt buộc, không
    fallback -- thiếu key nào raise KeyError rõ ràng ngay lúc nạp config,
    cùng kiểu với ``configuration._require()``. ``past_signal`` là DB2
    (không lọc trend), ``past_signal_trend`` là DB3 (có lọc trend) -- 2
    kênh xuất song song cùng 1 trigger, xem CLAUDE.md.
    """
    normalized = {**REDIS_DEFAULTS, **redis_config}
    backfill_cfg = redis_config.get("backfill_event") or {}
    past_signal_cfg = redis_config.get("past_signal") or {}
    past_signal_trend_cfg = redis_config.get("past_signal_trend") or {}
    for key in _REQUIRED_BACKFILL_EVENT_KEYS:
        _require(backfill_cfg, key, f"redis.backfill_event.{key}")
    for key in _REQUIRED_PAST_SIGNAL_KEYS:
        _require(past_signal_cfg, key, f"redis.past_signal.{key}")
    for key in _REQUIRED_PAST_SIGNAL_TREND_KEYS:
        _require(past_signal_trend_cfg, key, f"redis.past_signal_trend.{key}")
    normalized["backfill_event"] = {**_BACKFILL_EVENT_DEFAULTS, **backfill_cfg}
    normalized["past_signal"] = dict(past_signal_cfg)
    normalized["past_signal_trend"] = dict(past_signal_trend_cfg)
    if int(normalized["backfill_event"]["reconcile_interval_seconds"]) <= 0:
        raise ValueError("redis.backfill_event.reconcile_interval_seconds must be positive")
    return normalized


def _require(cfg: dict[str, Any], key: str, path: str) -> Any:
    if key not in cfg:
        raise KeyError(f"sl_config.yaml thiếu '{path}' — xem CLAUDE.md.")
    return cfg[key]


def create_client(redis_config: dict[str, Any], db: int) -> redis.Redis:
    """Tạo 1 client Redis đã decode cho đúng 1 DB logic."""
    redis_config = normalize_redis_config(redis_config)
    client = redis.Redis(
        host=redis_config["host"],
        port=int(redis_config["port"]),
        db=int(db),
        password=redis_config.get("password") or None,
        decode_responses=True,
        socket_timeout=float(redis_config["socket_timeout_seconds"]),
        socket_connect_timeout=float(redis_config["socket_connect_timeout_seconds"]),
        health_check_interval=int(redis_config["healthcheck_interval_seconds"]),
    )
    # KHÔNG log password -- chỉ host/port/db, đủ để tra cứu đang trỏ đâu.
    log_event(
        LOGGER, logging.DEBUG, "redis_client_created",
        host=redis_config["host"], port=int(redis_config["port"]), db=int(db),
    )
    return client
