"""Mọi việc NỐI và GHI-qua-Redis dùng chung của DE nằm ở file này, và chỉ ở đây: tạo kết nối, ping, đóng, kiểm cờ keyspace, đăng ký nghe và lấy message, nối lại khi mất kết nối, và cách ghi List–Hash
nguyên tử mà mọi đầu ra của DE dùng chung.

Các file khác chỉ làm việc nghiệp vụ trên kết nối đã có: data_engine đọc nến db0; trend_filtering đọc lại kết quả trend của chính nó; strategy_runner, trend_filtering quyết định GHI GÌ rồi giao cho
``write_list_hash`` ghi; không file nào tự gọi ``redis.Redis(...)`` hay tự chứa script ghi.
  * ``make_client`` – chỗ DUY NHẤT tạo ``redis.Redis`` (host, port, db, mật khẩu, timeout, health check).
  * ``Link`` – một kết nối tới một db: ``client``, ``ping``, ``close``.
  * ``KeyspaceListener`` – kết nối chuyên NGHE keyspace notification: kiểm cờ ``K`` + ``h``, đăng ký mẫu và chờ xác nhận, lấy message đang chờ.
  * ``keep_connected`` – vòng "nối, chạy, mất Redis thì ghi log, đợi rồi nối lại" dùng chung cho mọi loại tiến trình.
  * ``write_list_hash`` / ``write_list_hash_many`` – ghi List–Hash nguyên tử: LIST ``{list_key}`` giữ các mốc (cũ đứng trước), HASH ``{list_key}:{mốc}`` giữ dữ liệu của mốc đó; ghi đúng một lần, tự chống trùng,
    tuỳ chọn PUBLISH một kênh.
Tham số kết nối do configuration.py đưa vào, file này không tự đọc config.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import redis

LOGGER = logging.getLogger(__name__)
SUBSCRIBE_TIMEOUT_SECONDS = 10.0


def make_client(
    *, host: str, port: int, password: str, db: int, socket_timeout: float = 5.0, connect_timeout: float = 5.0, healthcheck_interval: int = 30
) -> redis.Redis:
    """Tạo kết nối tới một db của một Redis (chưa nối thật cho tới lệnh đầu tiên). Mật khẩu rỗng = không xác thực."""
    return redis.Redis(
        host=host,
        port=int(port),
        db=int(db),
        password=password or None,
        decode_responses=True,
        socket_timeout=float(socket_timeout),
        socket_connect_timeout=float(connect_timeout),
        health_check_interval=int(healthcheck_interval),
    )


class Link:
    """Một kết nối tới một db của một Redis. ``client`` cho test thay bằng bản giả; không truyền thì tự tạo bằng ``make_client``."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
        self.client = client if client is not None else make_client(
            host=host, port=port, password=password, db=db,
            socket_timeout=socket_timeout, connect_timeout=connect_timeout, healthcheck_interval=healthcheck_interval,
        )

    def ping(self) -> None:
        self.client.ping()

    def close(self) -> None:
        self.client.close()


class KeyspaceListener(Link):
    """Kết nối chuyên nghe keyspace notification (một kết nối nghe không dùng để đọc được, nên tách riêng với kết nối đọc)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pubsub: Any = None

    @property
    def subscribed(self) -> bool:
        return self._pubsub is not None

    def check_flags(self) -> str:
        """Redis phải bật đúng lớp 'K' + 'h' của notify-keyspace-events: thiếu thì raise RuntimeError rõ ràng ngay (không âm thầm chạy mà không bao giờ nhận event nào)."""
        flags = str(self.client.config_get("notify-keyspace-events").get("notify-keyspace-events", ""))
        missing = [c for c in ("K", "h") if c not in flags]
        if missing:
            raise RuntimeError(
                f"Redis notify-keyspace-events thiếu lớp {missing} (hiện tại: '{flags}') -- chạy CONFIG SET notify-keyspace-events "
                f"'{flags}{''.join(missing)}' rồi CONFIG REWRITE để lưu bền."
            )
        return flags

    def subscribe(self, patterns: Sequence[str]) -> int:
        """PSUBSCRIBE các mẫu và chờ Redis xác nhận ĐỦ (quá ``SUBSCRIBE_TIMEOUT_SECONDS`` thì ``redis.exceptions.TimeoutError``), để event tới trong lúc quét khởi động được đệm lại."""
        pubsub = self.client.pubsub()
        pubsub.psubscribe(*patterns)
        self._pubsub = pubsub
        confirmed = 0
        deadline = time.monotonic() + SUBSCRIBE_TIMEOUT_SECONDS
        while confirmed < len(patterns):
            message = pubsub.get_message(timeout=1)
            if message and message.get("type") == "psubscribe":
                confirmed += 1
            elif time.monotonic() > deadline:
                raise redis.exceptions.TimeoutError(f"keyspace subscribe confirmed {confirmed}/{len(patterns)} patterns")
        return len(patterns)

    def poll(self, timeout: float) -> list[dict[str, Any]]:
        """Chờ tối đa ``timeout`` giây cho message đầu tiên rồi hút hết message đang chờ (không chờ thêm). Chưa ``subscribe`` thì RuntimeError."""
        if self._pubsub is None:
            raise RuntimeError("poll before subscribe")
        messages: list[dict[str, Any]] = []
        message = self._pubsub.get_message(ignore_subscribe_messages=True, timeout=max(0.0, timeout))
        while message is not None:
            messages.append(message)
            message = self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
        return messages

    def close(self) -> None:
        if self._pubsub is not None:
            self._pubsub.close()
            self._pubsub = None
        super().close()


def keep_connected(
    connect: Callable[[Callable[[Any], Any]], Any], run: Callable[[Any], None], *, retry_delay_seconds: float
) -> None:
    """Chạy ``run(connect(register))`` mãi mãi. ``connect`` tạo từng phần (kết nối) và đưa qua ``register`` NGAY khi tạo (``register`` trả lại chính phần đó), nên phần nào đã tạo thì được đóng
    dù bước nối sau đó lỗi. Mất Redis (``redis.RedisError``, kể cả lúc đăng ký nghe): ghi ``event=redis_connection_lost``, đợi ``retry_delay_seconds`` giây rồi nối lại từ đầu; mọi phần luôn được đóng.
    Lỗi khác (cấu hình sai, tắt tiến trình) nổi lên cho người gọi."""
    while True:
        opened: list[Any] = []

        def register(part: Any, opened: list[Any] = opened) -> Any:
            opened.append(part)
            return part

        try:
            run(connect(register))
        except redis.RedisError as exc:
            LOGGER.error("event=redis_connection_lost error=%s retry_in_s=%s", exc, retry_delay_seconds)
            time.sleep(retry_delay_seconds)
        finally:
            for part in opened:
                part.close()


# -- ghi List–Hash nguyên tử ---------------------------------------------------------------------------------------------------------------------------

# KEYS[1]=hash KEYS[2]=list; ARGV[1]=mốc ARGV[2]=retention_seconds ARGV[3]=max_entries ARGV[4]=kênh ('' = không publish) ARGV[5]=message ARGV[6..]=field1 value1 ...
# "Đã ghi chưa?" được trả lời bằng việc HASH có tồn tại không: một bản trùng (event ghi lại, restart) không ghi và không publish gì, nên bên nhận không bao giờ bị báo hai lần về cùng một mốc.
# Nếu chưa có, trong MỘT vòng gọi nguyên tử: HASH (TTL retention_seconds) và LIST (cắt bằng LTRIM còn max_entries) được ghi trước, rồi (nếu có kênh) message được PUBLISH.
# Kênh không lưu gì: bên nhận lỡ lượt giao at-most-once của Pub/Sub thì đọc lại từ LIST/HASH.
_WRITE_LIST_HASH_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('HSET', KEYS[1], unpack(ARGV, 6))
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('LTRIM', KEYS[2], -tonumber(ARGV[3]), -1)
if ARGV[4] ~= '' then
  redis.call('PUBLISH', ARGV[4], ARGV[5])
end
return 1
"""


def _eval_args(
    list_key: str, stamp: str, fields: dict[str, str], retention_seconds: int, max_entries: int, channel: str, message: str
) -> tuple[Any, ...]:
    args: list[str] = [stamp, str(int(retention_seconds)), str(int(max_entries)), channel, message]
    for field, value in fields.items():
        args.append(field)
        args.append(value)
    return (_WRITE_LIST_HASH_SCRIPT, 2, f"{list_key}:{stamp}", list_key, *args)


def write_list_hash(
    client: redis.Redis,
    *,
    list_key: str,
    stamp: str,
    fields: dict[str, str],
    retention_seconds: int,
    max_entries: int,
    channel: str = "",
    message: str = "",
) -> bool:
    """Ghi MỘT mốc theo List–Hash, nguyên tử và đúng một lần: HASH ``{list_key}:{stamp}`` (hết hạn sau ``retention_seconds``), mốc nối cuối LIST ``list_key`` (giữ tối đa ``max_entries`` mốc mới nhất)
    và, nếu ``channel`` khác rỗng, PUBLISH ``message`` lên kênh đó. Mốc đã có sẵn thì không làm gì. Trả về True chỉ khi nó thật sự ghi."""
    return bool(int(client.eval(*_eval_args(list_key, stamp, fields, retention_seconds, max_entries, channel, message))))


def write_list_hash_many(client: redis.Redis, rows: Iterable[dict[str, Any]]) -> int:
    """Ghi nhiều mốc (mỗi phần tử là các tham số của ``write_list_hash`` trừ ``client``) trong MỘT lượt gửi lên Redis, theo đúng thứ tự. Trả về số mốc thật sự được ghi mới."""
    pipe = client.pipeline(transaction=False)
    queued = 0
    for row in rows:
        pipe.eval(*_eval_args(
            row["list_key"], row["stamp"], row["fields"], row["retention_seconds"], row["max_entries"], row.get("channel", ""), row.get("message", ""),
        ))
        queued += 1
    return sum(int(created) for created in pipe.execute()) if queued else 0
