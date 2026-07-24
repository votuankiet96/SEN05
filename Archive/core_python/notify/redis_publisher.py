"""
Redis publisher cho pipeline signal OG → OF.

Hai nhiệm vụ:
    1. publish_bar_ready(symbol, tf, bartime)
       → PUBLISH lên channel pub/sub nội bộ "bar_ready:{symbol}:{tf}" để
         signal_watcher (Thread A) biết có bar mới đã commit vào DB.
    2. xadd_signal(payload)
       → XADD signal hợp lệ vào Redis Stream "signal_stream:{strategy}" (durable),
         áp dụng MINID time-based retention. Đây là kênh giao signal sang OF.

Nguyên tắc thiết kế:
    - KHÔNG raise: mọi lỗi Redis được nuốt + log warning, trả None/0 để caller
      (relay, ws_live hook) tiếp tục chạy. Delivery guarantee do outbox + relay lo.
    - Lazy connect, timeout connect 2s. Lỗi *kết nối* KHÔNG được cache (chỉ cache
      lỗi ImportError) để Thread D có thể reconnect khi Redis sống lại.
    - _enabled() đọc REDIS_ENABLED động mỗi lần gọi → bật/tắt qua .env, an toàn test.

Phụ thuộc:
    redis>=5.0.0 (xem requirements.txt). Nếu chưa cài → _get_client() trả None,
    pipeline vẫn chạy (chỉ mất kênh Redis).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

try:  # đồng bộ với alerts.py — nạp .env khi import
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv là optional ở môi trường test
    pass

logger = logging.getLogger(__name__)

# Retention cho stream: trim bằng MINID theo thời gian (mặc định 7 ngày).
STREAM_RETENTION_DAYS = int(os.getenv("REDIS_STREAM_RETENTION_DAYS", "7"))

# Client dùng chung (lazy). Lỗi import redis được cache vĩnh viễn; lỗi kết nối thì KHÔNG.
_client = None
_redis_import_failed = False
_client_lock = threading.Lock()

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def _enabled() -> bool:
    """REDIS_ENABLED có truthy không (đọc động mỗi lần để test/bật-tắt qua .env)."""
    return os.getenv("REDIS_ENABLED", "").strip().lower() in _TRUTHY


def _get_client():
    """
    Trả về Redis client dùng chung, lazy-init với connect timeout 2s.

    - ImportError (chưa cài redis) → cache vĩnh viễn, không thử lại.
    - Lỗi kết nối (Redis down) → trả None nhưng KHÔNG cache → lần sau thử lại
      (cho phép Thread D / Thread A reconnect khi Redis sống lại).
    - Không set socket_timeout để pubsub listen() có thể block chờ message;
      chỉ giới hạn socket_connect_timeout.
    """
    global _client, _redis_import_failed
    if _redis_import_failed:
        return None
    if not _enabled():
        return None
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import redis  # noqa: PLC0415 — lazy import để optional dependency
        except ImportError:
            logger.warning("[Redis] chưa cài redis-py — `pip install redis>=5.0.0`")
            _redis_import_failed = True
            return None
        host = os.getenv("REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", "") or None
        try:
            client = redis.Redis(
                host=host,
                port=port,
                password=password,
                socket_connect_timeout=2,
                socket_keepalive=True,
                health_check_interval=30,
                decode_responses=True,
            )
            client.ping()
        except Exception as exc:  # noqa: BLE001 — connect lỗi không cache
            logger.warning("[Redis] kết nối thất bại (%s:%s): %s", host, port, exc)
            return None
        _client = client
        logger.info("[Redis] đã kết nối %s:%s", host, port)
        return _client


def _reset_client() -> None:
    """Đóng và xoá client cache (dùng cho test / reload cấu hình)."""
    global _client, _redis_import_failed
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _redis_import_failed = False


def _flatten(payload: dict) -> dict:
    """
    Chuẩn hoá payload → dict[str, str] để XADD (Redis Streams chỉ nhận field phẳng).

    - None → "" (rỗng)
    - scalar (str/int/float/bool) → str()
    - nested (dict/list) → JSON-encode (payload v3.2 toàn scalar nên thường không cần)
    """
    flat: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            flat[str(key)] = ""
        elif isinstance(value, bool):
            flat[str(key)] = "1" if value else "0"
        elif isinstance(value, (str, int, float)):
            flat[str(key)] = str(value)
        else:
            flat[str(key)] = json.dumps(value, default=str, ensure_ascii=False)
    return flat


def publish_bar_ready(symbol: str, tf: str, bartime: str) -> int:
    """
    PUBLISH event bar-ready lên channel nội bộ "bar_ready:{symbol}:{tf}".

    Trả số subscriber nhận được (0 nếu Redis off/down/lỗi). KHÔNG raise.
    `bartime` là chuỗi ISO (bar OPEN time), subscriber dùng làm event.bartime.
    """
    if not _enabled():
        return 0
    client = _get_client()
    if client is None:
        return 0
    channel = f"bar_ready:{symbol}:{tf}"
    try:
        return int(client.publish(channel, bartime or ""))
    except Exception as exc:  # noqa: BLE001 — không để lỗi Redis làm gãy ws_live
        logger.warning("[Redis] PUBLISH thất bại (%s): %s", channel, exc)
        return 0


def xadd_signal(payload: dict) -> str | None:
    """
    XADD signal hợp lệ vào stream "signal_stream:{strategy}" với MINID retention.

    Trả entry-id (str) nếu thành công, None nếu off/down/lỗi (relay sẽ retry).

    MINID — redis-py API ĐÚNG (P1-3 fix):
        minid là stream-ID dạng "{ms}-0"; ký tự '~' (approximate) truyền qua
        tham số approximate=True, KHÔNG nằm trong chuỗi ID.
        SAI (v3.1): minid=f"~{ms}-0" → redis-py reject vì '~' không hợp lệ trong ID.
    """
    if not _enabled():
        return None
    client = _get_client()
    if client is None:
        return None
    strategy = str(payload.get("strategy", "unknown"))
    stream = f"signal_stream:{strategy}"
    retention_days = int(os.getenv("REDIS_STREAM_RETENTION_DAYS", str(STREAM_RETENTION_DAYS)))
    minid_ms = int((time.time() - 86400 * retention_days) * 1000)
    try:
        return client.xadd(
            stream,
            _flatten(payload),
            minid=f"{minid_ms}-0",
            approximate=True,
        )
    except Exception as exc:  # noqa: BLE001 — relay xử lý retry qua outbox
        logger.warning("[Redis] XADD thất bại (%s): %s", stream, exc)
        return None
