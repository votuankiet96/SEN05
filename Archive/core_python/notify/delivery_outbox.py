"""
Transactional outbox cho delivery signal → OF (Redis Streams).

Bản chất (Outbox + Relay pattern):
    Worker detect signal hợp lệ → ghi BỀN VỮNG vào outbox (add_pending) NGAY,
    độc lập với alert (Telegram/Discord) và độc lập với Redis sống hay chết.
    Thread D (DeliveryRelay) là tác nhân DUY NHẤT đọc pending rồi XADD sang Redis,
    đánh dấu delivered khi thành công, hoặc tăng attempt + backoff khi thất bại.

    → Tách "đã phát hiện signal" khỏi "đã giao tới OF": crash/Redis-down giữa chừng
      không làm mất signal — restart xong relay drain lại pending.

File lưu: {state_dir}/delivery_outbox.json
    {
      "pending":   { signal_id: {payload, produced_at, attempts, last_attempt} },
      "delivered": { signal_id: delivered_at_iso }
    }

Ghi atomic: .tmp → os.replace (giống state.py) chống corrupt khi bị kill giữa chừng.

NullDeliveryOutbox: bản no-op dùng khi REDIS_ENABLED=false để TUYỆT ĐỐI không
    tạo/sửa delivery_outbox.json (P1-1) — tránh tích pending rồi replay khi bật Redis.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Thời điểm hiện tại dạng ISO 8601 UTC (tz-aware, hậu tố +00:00)."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse ISO 8601 (chấp nhận hậu tố 'Z'); naive được coi là UTC. Lỗi → None."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class DeliveryOutbox:
    """
    Outbox bền vững, thread-safe cho signal chờ giao sang OF.

    Mọi thao tác đọc/ghi state đều dưới self._lock. get_pending() trả SNAPSHOT
    (copy) để Thread D iterate ngoài lock, tránh giữ lock lâu / deadlock.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    # ---- load / persist -------------------------------------------------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"pending": {}, "delivered": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[Outbox] đọc %s lỗi (%s) — khởi tạo rỗng", self._path, exc)
            return {"pending": {}, "delivered": {}}
        pending = raw.get("pending", {})
        delivered = raw.get("delivered", {})
        return {
            "pending": pending if isinstance(pending, dict) else {},
            "delivered": delivered if isinstance(delivered, dict) else {},
        }

    def _write(self) -> None:
        """Ghi atomic: .tmp → os.replace. Phải gọi trong self._lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # ---- mutators -------------------------------------------------------

    def add_pending(self, signal_id: str, payload: dict) -> None:
        """
        Thêm signal vào pending — IDEMPOTENT theo signal_id deterministic.

        Bỏ qua nếu đã delivered (không re-deliver) hoặc đã pending (không ghi đè
        attempts/last_attempt đang chạy). Chỉ ghi file khi thực sự thêm mới.
        """
        with self._lock:
            if signal_id in self._data["delivered"]:
                return
            if signal_id in self._data["pending"]:
                return
            self._data["pending"][signal_id] = {
                "payload": payload,
                "produced_at": _utcnow_iso(),
                "attempts": 0,
                "last_attempt": None,
            }
            self._write()

    def record_attempt(self, signal_id: str) -> None:
        """Tăng attempts + cập nhật last_attempt (phục vụ backoff ở relay)."""
        with self._lock:
            entry = self._data["pending"].get(signal_id)
            if entry is None:
                return
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry["last_attempt"] = _utcnow_iso()
            self._write()

    def mark_delivered(self, signal_id: str) -> None:
        """Chuyển signal_id từ pending → delivered (đánh dấu thời điểm giao)."""
        with self._lock:
            self._data["pending"].pop(signal_id, None)
            self._data["delivered"][signal_id] = _utcnow_iso()
            self._write()

    def prune_delivered(self, older_than_days: int = 7) -> None:
        """Xoá các bản ghi delivered cũ hơn ngưỡng để file không phình mãi."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        with self._lock:
            kept = {}
            for sid, ts in self._data["delivered"].items():
                parsed = _parse_iso(ts)
                if parsed is None or parsed > cutoff:
                    kept[sid] = ts
            if len(kept) != len(self._data["delivered"]):
                self._data["delivered"] = kept
                self._write()

    # ---- read-only ------------------------------------------------------

    def get_pending(self) -> dict:
        """Trả SNAPSHOT (deep-ish copy) pending để iterate an toàn ngoài lock."""
        with self._lock:
            return {
                sid: {
                    "payload": dict(entry.get("payload", {})),
                    "produced_at": entry.get("produced_at"),
                    "attempts": int(entry.get("attempts", 0)),
                    "last_attempt": entry.get("last_attempt"),
                }
                for sid, entry in self._data["pending"].items()
            }

    def oldest_pending_age_seconds(self) -> float | None:
        """
        Tuổi (giây) của signal pending CŨ NHẤT theo produced_at — None nếu rỗng.

        Dùng để cảnh báo khi delivery bị kẹt quá lâu (relay alert > RELAY_ALERT_AGE).
        """
        with self._lock:
            if not self._data["pending"]:
                return None
            now = datetime.now(timezone.utc)
            oldest: float | None = None
            for entry in self._data["pending"].values():
                produced = _parse_iso(entry.get("produced_at"))
                if produced is None:
                    continue
                age = (now - produced).total_seconds()
                if oldest is None or age > oldest:
                    oldest = age
            return oldest

    def pending_count(self) -> int:
        with self._lock:
            return len(self._data["pending"])


class NullDeliveryOutbox:
    """
    Outbox no-op — dùng khi REDIS_ENABLED=false (P1-1).

    Đảm bảo TUYỆT ĐỐI không tạo/sửa delivery_outbox.json khi Redis tắt, nên không
    có pending tích luỹ để replay nhầm khi bật Redis trở lại.
    """

    def add_pending(self, *args, **kwargs) -> None:  # noqa: D102
        pass

    def record_attempt(self, *args, **kwargs) -> None:  # noqa: D102
        pass

    def mark_delivered(self, *args, **kwargs) -> None:  # noqa: D102
        pass

    def prune_delivered(self, *args, **kwargs) -> None:  # noqa: D102
        pass

    def get_pending(self) -> dict:  # noqa: D102
        return {}

    def oldest_pending_age_seconds(self) -> None:  # noqa: D102
        return None

    def pending_count(self) -> int:  # noqa: D102
        return 0
