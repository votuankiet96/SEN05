"""React to DP candle writes on Redis DB0 through keyspace notifications.

Chốt 2026-09-23: OG và DP là 2 hệ độc lập, chỉ gặp nhau ở Redis DB0 -- OG
KHÔNG còn dựa vào ``dp:events:candles`` (message do chính DP tự soạn, tự
quyết định khi nào bắn -- một hợp đồng nội bộ của DP, có thể đổi/gãy bất cứ
lúc nào mà OG không hay biết, đúng như schema DB0 đã tự đổi 4 lần trong 1
tuần trước đây). Thay vào đó OG lắng nghe trực tiếp keyspace notification
của chính Redis trên DB0 -- tín hiệu do bản thân Redis phát ra khi một key
THẬT SỰ bị ghi, độc lập hoàn toàn với việc DP có tự soạn thêm message nào
khác hay không. OG tự xác minh, không tin lời DP báo.

Cơ chế: PSUBSCRIBE ``__keyspace@{db}__:{key_prefix}_*``, lọc payload=="hset"
-- DP chỉ HSET một nến khi giá trị thật sự đổi (kể cả nến hoàn toàn mới:
HASH chưa tồn tại thì HMGET luôn trả nil, luôn "khác"), nên notification kế
thừa nguyên tính "chỉ bắn khi thật sự đổi" mà không cần OG tin lời DP. Chỉ
cần lớp lệnh 'h' (hash) trong ``notify-keyspace-events`` -- không cần 'l'
(list): RPUSH không bao giờ bắn mà HSET không bắn cùng lúc (mọi nến mới
hay bị sửa giá đều đi qua HSET), nên bật thêm 'l' chỉ tạo tiếng ồn từ LPOP
evict mỗi cycle mà không mang thêm thông tin nào OG cần.

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
không có cách nào lấy lại. reconcile_known_pairs định kỳ (độc lập với việc
có event nào tới hay không, tự SCAN lại DB0 trực tiếp) vẫn là lưới an toàn
của OG trước rủi ro đó -- không đổi gì so với thiết kế cũ.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import redis

from order_gateway.src.candle_reader import parse_pair_key

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
    symbol)`` đúng 1 lần mỗi HASH key vừa bị ``HSET`` (nến mới hoặc nến cũ bị
    sửa giá -- DP chỉ HSET khi giá trị thật sự đổi, xem module docstring).
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
