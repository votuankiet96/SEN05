"""
Cấu hình cho redis_engine — tiến trình 24/7 nhận "candle_snapshot" từ DP6
qua Redis Streams (mỗi entry mang sẵn 500 bar OHLCV mới nhất của 1
symbol/tf), tính tín hiệu chiến lược bằng og_core, và publish tín hiệu mới
lên Redis Streams cho hệ downstream (OF).

Đầu vào:
    Biến môi trường / file .env ở repo root (dùng chung file với og_core —
    xem og_core.config cho cách nạp .env theo đường dẫn tuyệt đối).

Đầu ra:
    REDIS_HOST/PORT/USERNAME/PASSWORD — kết nối Redis.
    CANDLE_SNAPSHOT_STREAM/CONSUMER_GROUP/CONSUMER_NAME — Redis Streams cho
    trigger nhanh (DP6 → OG). SIGNAL_STREAM_PREFIX — Redis Streams cho
    publish tín hiệu (OG → OF), xem signal_stream_key().
    WATCHED — danh sách {strategy, symbols, tf, bars} redis_engine theo dõi.
    SAFETY_NET_INTERVAL_SECONDS — chu kỳ quét lưới an toàn (giây).
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # python-dotenv là optional — vẫn chạy được qua OS env vars
    pass


# -----------------------------------------------------------------------------
# 1. REDIS CONNECTION
# -----------------------------------------------------------------------------
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")


# -----------------------------------------------------------------------------
# 2. STREAMS
# -----------------------------------------------------------------------------
CANDLE_SNAPSHOT_STREAM = "candle_snapshot"
CONSUMER_GROUP = "og_watchers"
CONSUMER_NAME = "og-primary"
SIGNAL_STREAM_PREFIX = "signal_stream"

# Trim gần đúng (~) khi vượt ngưỡng — tránh signal_stream:* phình vô hạn khi
# chạy 24/7. Consumer downstream (OF) cần đọc kịp trong khoảng này.
SIGNAL_STREAM_MAXLEN = 10_000


def signal_stream_key(strategy: str) -> str:
    """Tên stream Redis nơi tín hiệu của một chiến lược được publish."""
    return f"{SIGNAL_STREAM_PREFIX}:{strategy}"


# -----------------------------------------------------------------------------
# 3. LƯỚI AN TOÀN (safety net) — quét định kỳ, độc lập với Streams.
# -----------------------------------------------------------------------------
SAFETY_NET_INTERVAL_SECONDS = 300  # 5 phút


# -----------------------------------------------------------------------------
# 4. DANH SÁCH THEO DÕI — v1 thu hẹp vào Combo, 2 symbol dễ quan sát.
# -----------------------------------------------------------------------------
WATCHED: list[dict[str, object]] = [
    {"strategy": "combo", "symbols": ["US30", "DE40"], "tf": "H1", "bars": 300},
]


def runtime_dir() -> Path:
    """Thư mục lưu state.json/delivery_outbox.json — dữ liệu vận hành, gitignored."""
    path = Path(__file__).resolve().parent / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path
