"""
Outbox cục bộ cho tín hiệu publish thất bại (Redis lỗi/không kết nối được
đúng lúc XADD signal_stream).

Không dùng cho candle_snapshot — DP6 tự nuốt lỗi publish của chính họ
(fire-and-forget) và an toàn nhờ safety_net_poller quét lại định kỳ (xem
plan). Outbox này chỉ bảo vệ đường publish tín hiệu OG → OF, dùng chung bởi
candle_snapshot_consumer và safety_net_poller.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from redis_engine.config import runtime_dir

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], "str | None"]


class DeliveryOutbox:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (runtime_dir() / "delivery_outbox.json")
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = self._load()

    def add_pending(self, strategy: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._pending.append({"strategy": strategy, "payload": payload})
            self._save()
        logger.warning("outbox: queued signal_id=%s (Redis unreachable)", payload.get("signal_id"))

    def retry_all(self, publish_fn: PublishFn) -> int:
        """
        Thử publish lại toàn bộ hàng chờ. Trả về số tín hiệu gửi thành công.

        Chỉ XOÁ đúng các item vừa gửi thành công khỏi self._pending (không
        gán đè cả danh sách) — nếu add_pending() được gọi bởi thread khác
        trong lúc retry đang chạy, item mới đó không bị mất (trước đây gán
        đè bằng "remaining" tính từ snapshot cũ sẽ xoá mất item mới đó).
        """
        with self._lock:
            pending_snapshot = list(self._pending)
        if not pending_snapshot:
            return 0

        delivered_items: list[dict[str, Any]] = []
        for item in pending_snapshot:
            result = publish_fn(item["strategy"], item["payload"])
            if result is not None:
                delivered_items.append(item)

        if not delivered_items:
            return 0

        with self._lock:
            for item in delivered_items:
                try:
                    self._pending.remove(item)
                except ValueError:
                    pass  # đã bị xoá bởi 1 lần retry_all() khác — bỏ qua
            self._save()
        return len(delivered_items)

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("outbox: failed to load %s, starting empty: %s", self._path, exc)
            return []

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._pending))
        tmp.replace(self._path)
