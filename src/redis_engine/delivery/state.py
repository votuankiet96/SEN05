"""
Dedup tín hiệu theo signal_id — dùng CHUNG một instance bởi cả
candle_snapshot_consumer (trigger nhanh) và safety_net_poller (trigger an
toàn), để tín hiệu nào đã publish qua đường nhanh không bị publish trùng
khi lưới an toàn quét trúng lại (xem plan: "1 đường dedup dùng chung cho cả
2 nguồn trigger").

File JSON local: {signal_id: seen_at_unix_ts}. Redis chỉ là kênh truyền tin
thuần tuý, không lưu trạng thái ứng dụng ở đây.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from redis_engine.config import runtime_dir

logger = logging.getLogger(__name__)

TTL_SECONDS = 14 * 24 * 3600  # 14 ngày — đủ dài để bao trọn mọi retry hợp lý


class SignalState:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (runtime_dir() / "state.json")
        self._lock = threading.Lock()
        self._seen: dict[str, float] = self._load()

    def check_and_mark(self, signal_id: str) -> bool:
        """
        Kiểm tra + đánh dấu "đã thấy" NGUYÊN TỬ trong 1 lần khoá.

        Trước đây has() và add() là 2 lời gọi tách rời — candle_snapshot_consumer
        và safety_net_poller có thể cùng đọc has()==False trước khi bên nào
        kịp add(), khiến cả 2 cùng publish trùng 1 signal_id. Gộp lại thành
        1 thao tác duy nhất để loại bỏ khoảng hở đó.

        Returns:
            True nếu signal_id này CHƯA từng thấy (và vừa được đánh dấu) —
            caller nên publish. False nếu đã thấy trước đó — bỏ qua.
        """
        with self._lock:
            if signal_id in self._seen:
                return False
            self._seen[signal_id] = time.time()
            self._save()
            return True

    def _load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            raw: dict[str, float] = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("state: failed to load %s, starting empty: %s", self._path, exc)
            return {}
        cutoff = time.time() - TTL_SECONDS
        return {signal_id: seen_at for signal_id, seen_at in raw.items() if seen_at >= cutoff}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._seen))
        tmp.replace(self._path)
