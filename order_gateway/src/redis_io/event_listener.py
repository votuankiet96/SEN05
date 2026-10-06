"""React to DP candle writes on Redis DB0 through keyspace notifications.

OG và nguồn nến (Core AEN, qua Redis replica trên og18) chỉ gặp nhau ở DB0.
OG lắng nghe trực tiếp keyspace notification của chính Redis -- tín hiệu do
Redis phát ra khi một key THẬT SỰ bị ghi, không dựa vào message nào do nguồn
tự soạn.

Cơ chế: PSUBSCRIBE ``__keyspace@{db}__:{key_prefix}_*``, lọc payload=="hset".
Core ghi mỗi nến bằng HMSET (Redis phát event ``hset`` cho HSET/HSETNX/HMSET)
rồi LPUSH/LTRIM/UNLINK list. Mọi nến mới hay nến cũ bị ghi lại đều đi qua
HMSET, nên chỉ cần lớp 'h' (hash) trong ``notify-keyspace-events`` -- bật
thêm 'l' (list) chỉ tạo tiếng ồn mà không mang thêm thông tin nào OG cần.
Core có ghi lại cả nến cũ; xử lý lại một cặp là vô hại vì chỉ nến cuối được
xét và publish tự chống trùng.

Redis keyspace notification không bao giờ mang theo giá trị hay tên field
đã đổi -- payload chỉ là tên lệnh ("hset"); CHANNEL (khi dùng cờ 'K') là nơi
duy nhất chứa tên key vừa bị ghi. Parse channel để lấy lại (symbol,
timeframe) không phải bước phát sinh thêm -- đó CHÍNH LÀ cách OG tự xác
minh, và tái dùng thẳng candle_reader.parse_pair_key() đã có sẵn cho việc
này (cùng 1 hợp đồng đặt tên key mà pair_list_key()/candle_key() dùng để
ghi/đọc nến, không phải một khái niệm parse mới).

Indicators need the whole window (currently up to 500 bars per pair), not
just the changed bar(s) -- OG re-reads that fresh via read_candles_from_redis
instead of keeping any incremental state of its own that could drift.

Keyspace notification vẫn là fire-and-forget ở tầng Redis y hệt Pub/Sub
thường -- một subscriber mất kết nối ngắn hạn sẽ mất event đó vĩnh viễn,
và một lần replica full resync (nạp lại toàn bộ dữ liệu từ Core) cũng không
đi qua lệnh ghi từng key. reconcile_known_pairs định kỳ (tự SCAN lại DB0
trực tiếp) là lưới an toàn của OG trước cả hai rủi ro đó.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import redis

from order_gateway.src.redis_io.candle_reader import parse_pair_key

LOGGER = logging.getLogger("event_listener")

_HSET_PAYLOAD = "hset"


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def listen_for_candle_hash_events(
    client: redis.Redis,
    *,
    db: int,
    key_prefix: str,
    on_update: Callable[[str, str], None],
    on_started: Callable[[], None] | None = None,
    reconcile_interval_seconds: int | None = None,
    on_reconcile: Callable[[], None] | None = None,
) -> None:
    """PSUBSCRIBE keyspace notification cho nến DB0, gọi ``on_update(timeframe,
    symbol)`` đúng 1 lần mỗi event ``hset`` trên HASH nến (xem module docstring).
    """
    channel_prefix = f"__keyspace@{int(db)}__:"
    pattern = f"{channel_prefix}{key_prefix}_*"
    pubsub = client.pubsub()
    try:
        pubsub.psubscribe(pattern)

        # Confirm the subscription before startup scanning so events are buffered.
        while True:
            message = pubsub.get_message(timeout=1)
            if message and message.get("type") == "psubscribe":
                break

        if on_started is not None:
            on_started()

        next_reconcile = (
            time.monotonic() + reconcile_interval_seconds
            if reconcile_interval_seconds
            else None
        )

        while True:
            timeout = 1.0
            if next_reconcile is not None:
                timeout = max(0.0, min(next_reconcile - time.monotonic(), 1.0))

            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
            if message and message.get("type") == "pmessage" and _text(message.get("data", "")) == _HSET_PAYLOAD:
                channel = _text(message.get("channel", ""))
                if channel.startswith(channel_prefix):
                    # HASH key = LIST key + ":" + stamp (candle_key). Bỏ phần
                    # stamp (không dùng tới), phần còn lại là đúng LIST key
                    # mà parse_pair_key() vẫn đọc mỗi ngày.
                    list_key = channel[len(channel_prefix):].split(":", 1)[0]
                    pair = parse_pair_key(list_key, key_prefix)
                    if pair is not None:
                        symbol, timeframe = pair
                        on_update(timeframe, symbol)

            if next_reconcile is not None and time.monotonic() >= next_reconcile:
                if on_reconcile is not None:
                    on_reconcile()
                next_reconcile = time.monotonic() + reconcile_interval_seconds
    finally:
        pubsub.close()
