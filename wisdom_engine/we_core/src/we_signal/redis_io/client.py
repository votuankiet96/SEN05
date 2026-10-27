"""Kết nối Redis + đọc we_config.yaml cho luồng "backtest cấp 2".

Chỉ giữ phần dùng chung (nạp config, tạo client) -- KHÔNG có hàm đọc nến
từ Redis DB0 của DF như bản gốc order_gateway/og_signal/redis_io/client.py, vì
wisdom_engine đọc OHLCV từ SQL Server (db_connector.py), không bao giờ đọc
nến từ Redis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import redis
import yaml
from wisdom_engine.we_core.src.rules.trend_tf import parse_methods
from wisdom_engine.we_core.src.we_signal.redis_io.logging_setup import log_event

LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "we_config.yaml"

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
# we_config.yaml -- đúng cách order_gateway làm với
# redis.input.reconcile_interval_seconds (order_gateway/src/candle_reader.py).
# Trước 2026-09-22 số 1800 viết thẳng vào lời gọi listen_for_backfill_events()
# trong worker.py: không đè được, không validate.
_BACKFILL_EVENT_DEFAULTS: dict[str, Any] = {
    "reconcile_interval_seconds": 1800,
}

_REQUIRED_BACKFILL_EVENT_KEYS = ("channel",)
_REQUIRED_PAST_SIGNAL_KEYS = ("db", "key_prefix")
_REQUIRED_PAST_SIGNAL_TREND_KEYS = ("db", "key_prefix")
_REQUIRED_EXTRA_TREND_OUTPUT_KEYS = ("db", "key_prefix", "trend_methods")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Nạp we_config.yaml + áp default kỹ thuật."""
    with path.open("r", encoding="utf-8") as file:
        config = normalize_app_config(yaml.safe_load(file) or {})
    log_event(LOGGER, logging.INFO, "config_loaded", path=str(path))
    return config


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config)
    out["redis"] = normalize_redis_config(out.get("redis") or {})
    return out


def normalize_redis_config(redis_config: dict[str, Any]) -> dict[str, Any]:
    """Dựng config Redis đầy đủ từ phần operator khai trong we_config.yaml.

    ``redis.backfill_event.channel``, ``redis.past_signal.{db,key_prefix}``
    và ``redis.past_signal_trend.{db,key_prefix}`` là bắt buộc, không
    fallback -- thiếu key nào raise KeyError rõ ràng ngay lúc nạp config,
    cùng kiểu với ``configuration._require()``. ``past_signal`` là DB0
    (không lọc trend), ``past_signal_trend`` là DB1 (có lọc trend) -- 2
    kênh xuất song song cùng 1 trigger, xem CLAUDE.md.

    ``redis.extra_trend_outputs`` là TUỲ CHỌN (mặc định rỗng): mỗi phần tử
    thêm một đầu ra có lọc trend ngoài DB1, cùng trigger, mỗi đầu ra một DB
    riêng + khối trend riêng (đè ``strategies.<tên>.trend_methods`` chỉ cho
    đầu ra đó) -- xem ``_normalize_extra_trend_outputs()``.
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
    normalized["extra_trend_outputs"] = _normalize_extra_trend_outputs(
        redis_config.get("extra_trend_outputs"),
        taken_dbs={int(past_signal_cfg["db"]), int(past_signal_trend_cfg["db"])},
    )
    if int(normalized["backfill_event"]["reconcile_interval_seconds"]) <= 0:
        raise ValueError("redis.backfill_event.reconcile_interval_seconds must be positive")
    return normalized


def _normalize_extra_trend_outputs(raw: object, *, taken_dbs: set[int]) -> list[dict[str, Any]]:
    """Chuẩn hoá ``redis.extra_trend_outputs`` (thiếu / None / [] = không có đầu ra thêm).

    Mỗi phần tử: ``db`` (số nguyên >= 0, KHÔNG trùng DB0/DB1 hay đầu ra khác -- hai đầu ra chung một DB sẽ ghi đè
    và xoá lẫn nhau), ``key_prefix`` (không rỗng), ``trend_methods`` (danh sách khối trend, kiểm bằng
    ``rules.trend_tf.parse_methods`` nên tên lạ/rỗng/trùng báo lỗi kèm đường dẫn config). Trả về danh sách dict
    đã chuẩn hoá (``trend_methods`` là tuple) -- chuẩn hoá lại kết quả này vẫn ra đúng nó (``create_client`` gọi
    ``normalize_redis_config`` lần nữa).
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise TypeError("redis.extra_trend_outputs must be a list of outputs (db, key_prefix, trend_methods).")
    used = set(taken_dbs)
    outputs: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        path = f"redis.extra_trend_outputs[{index}]"
        if not isinstance(item, dict):
            raise TypeError(f"{path} must be a mapping with db, key_prefix and trend_methods.")
        for key in _REQUIRED_EXTRA_TREND_OUTPUT_KEYS:
            _require(item, key, f"{path}.{key}")
        db = item["db"]
        if isinstance(db, bool) or not isinstance(db, int) or db < 0:
            raise ValueError(f"{path}.db must be a non-negative integer (a Redis DB number).")
        key_prefix = str(item["key_prefix"] or "").strip()
        if not key_prefix:
            raise ValueError(f"{path}.key_prefix must not be empty.")
        if db in used:
            raise ValueError(f"{path}.db {db} is already used by another WE output (past_signal, past_signal_trend or an earlier extra output).")
        used.add(db)
        outputs.append({"db": db, "key_prefix": key_prefix, "trend_methods": parse_methods(item["trend_methods"], label=f"{path}.trend_methods")})
    return outputs


def _require(cfg: dict[str, Any], key: str, path: str) -> Any:
    if key not in cfg:
        raise KeyError(f"we_config.yaml thiếu '{path}' — xem CLAUDE.md.")
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
