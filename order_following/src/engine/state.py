"""Kho lưu trữ bền vững: dedup theo clientOrderId + tracking trạng thái lệnh, sống qua restart.

Chỉ ghi/đọc theo yêu cầu của nơi khác — không tự quyết định gì, không tự gọi sang engine khác.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

STATUS_SENDING = "SENDING"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_FILLED = "FILLED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
STATUS_EXPIRED = "EXPIRED"
STATUS_CLOSED = "CLOSED"
STATUS_UNRESOLVED = "UNRESOLVED"  # dở dang từ lần chạy trước, không đối chiếu được với server

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    order_id INTEGER,
    position_id INTEGER,
    status TEXT NOT NULL,
    symbol TEXT NOT NULL,
    label TEXT NOT NULL,
    risk_amount REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class OrderRecord:
    client_order_id: str
    order_id: Optional[int]
    position_id: Optional[int]
    status: str
    symbol: str
    label: str
    risk_amount: float
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, db_path: str):
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._migrate_add_net_profit_column()
        self._conn.commit()

    def _migrate_add_net_profit_column(self) -> None:
        """DB production đã tồn tại từ trước cột này — CREATE TABLE IF NOT EXISTS không tự thêm cột
        vào bảng đã có sẵn, phải ALTER TABLE riêng, chỉ 1 lần (kiểm tra qua PRAGMA table_info)."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(orders)")}
        if "net_profit" not in columns:
            self._conn.execute("ALTER TABLE orders ADD COLUMN net_profit REAL NOT NULL DEFAULT 0")

    def has_sent(self, client_order_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return row is not None

    def mark_sending(self, client_order_id: str, symbol: str, label: str, risk_amount: float) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO orders (client_order_id, order_id, position_id, status, symbol, label, risk_amount, created_at, updated_at) "
            "VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)",
            (client_order_id, STATUS_SENDING, symbol, label, risk_amount, now, now),
        )
        self._conn.commit()

    def get_by_order_id(self, order_id: int) -> Optional[OrderRecord]:
        row = self._conn.execute(
            "SELECT client_order_id, order_id, position_id, status, symbol, label, risk_amount, created_at, updated_at "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return OrderRecord(*row) if row else None

    def mark_accepted(self, client_order_id: str, order_id: int) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, order_id = ?, updated_at = ? WHERE client_order_id = ?",
            (STATUS_ACCEPTED, order_id, _now(), client_order_id),
        )
        self._conn.commit()

    def mark_filled(self, order_id: int, position_id: int) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, position_id = ?, updated_at = ? WHERE order_id = ?",
            (STATUS_FILLED, position_id, _now(), order_id),
        )
        self._conn.commit()

    def mark_rejected(self, client_order_id: str) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
            (STATUS_REJECTED, _now(), client_order_id),
        )
        self._conn.commit()

    def mark_cancelled(self, order_id: int) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (STATUS_CANCELLED, _now(), order_id),
        )
        self._conn.commit()

    def mark_expired(self, order_id: int) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (STATUS_EXPIRED, _now(), order_id),
        )
        self._conn.commit()

    def mark_closed(self, position_id: int) -> None:
        self._conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE position_id = ?",
            (STATUS_CLOSED, _now(), position_id),
        )
        self._conn.commit()

    def accumulate_net_profit(self, position_id: int, net_profit_delta: float) -> None:
        """CỘNG DỒN (không ghi đè) — 1 position có thể đóng qua NHIỀU deal khớp từng phần (partial
        close), mỗi deal có lãi/lỗ riêng; gọi hàm này ở MỌI deal (kể cả deal cuối cùng khiến volume
        về 0, lúc mark_closed() cũng được gọi) để tổng net_profit phản ánh ĐÚNG toàn bộ position,
        không chỉ riêng deal cuối. Case thật đã gặp: BTCUSD 2026-09-27, 1 position đóng qua 4 deal
        trong 6 giây, mỗi deal lãi khác nhau — ghi đè sẽ mất 3/4 số lãi thật."""
        self._conn.execute(
            "UPDATE orders SET net_profit = net_profit + ?, updated_at = ? WHERE position_id = ?",
            (net_profit_delta, _now(), position_id),
        )
        self._conn.commit()

    def get_net_profit(self, position_id: int) -> float:
        """Tổng net_profit đã cộng dồn cho position này tính tới hiện tại — dùng ngay khi vừa
        accumulate_net_profit() ở deal CUỐI (volume về 0) để hiện đúng tổng lãi/lỗ thật cả position
        trong 1 message Discord, không phải chỉ số của riêng deal cuối đó."""
        row = self._conn.execute(
            "SELECT net_profit FROM orders WHERE position_id = ?", (position_id,)
        ).fetchone()
        return row[0] if row else 0.0

    def sum_net_profit_since(self, since_iso: str) -> float:
        """Tổng lãi/lỗ THẬT (đã cộng dồn mọi partial close) của các position đã đóng HOÀN TOÀN
        (status=CLOSED) trong kỳ — nguồn số liệu cho account snapshot định kỳ."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(net_profit), 0) FROM orders WHERE status = ? AND updated_at >= ?",
            (STATUS_CLOSED, since_iso),
        ).fetchone()
        return row[0]

    def mark_unresolved(self, client_order_id: str) -> None:
        """Chốt sổ 1 record không đối chiếu được (xem main._resolve_pending). Có trạng thái riêng để
        nó không bị coi là "đang chờ" ở mọi lần khởi động sau — nhưng has_sent() vẫn trả True nên
        clientOrderId đó vĩnh viễn không bị gửi lại."""
        self._conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
            (STATUS_UNRESOLVED, _now(), client_order_id),
        )
        self._conn.commit()

    def pending_since_last_run(self) -> List[OrderRecord]:
        """Record còn SENDING/ACCEPTED lúc khởi động — nghĩa là lần chạy trước bị ngắt giữa chừng,
        cần đối chiếu ProtoOAReconcileReq trước khi làm gì tiếp, không suy đoán."""
        rows = self._conn.execute(
            "SELECT client_order_id, order_id, position_id, status, symbol, label, risk_amount, created_at, updated_at "
            "FROM orders WHERE status IN (?, ?)",
            (STATUS_SENDING, STATUS_ACCEPTED),
        ).fetchall()
        return [OrderRecord(*row) for row in rows]

    def unresolved_with_order_id(self) -> List[OrderRecord]:
        """Record đã chốt UNRESOLVED nhưng CÓ orderId — vẫn tra được lịch sử lệnh trên server
        (ProtoOAOrderDetailsReq) để khôi phục chính xác ở lần khởi động sau. Record không có orderId
        (chết ngay sau khi gửi, server chưa kịp trả) không nằm ở đây — không có gì để tra."""
        rows = self._conn.execute(
            "SELECT client_order_id, order_id, position_id, status, symbol, label, risk_amount, created_at, updated_at "
            "FROM orders WHERE status = ? AND order_id IS NOT NULL",
            (STATUS_UNRESOLVED,),
        ).fetchall()
        return [OrderRecord(*row) for row in rows]

    def count_by_status_since(self, since_iso: str) -> dict:
        """Đếm số lệnh theo trạng thái, chỉ tính bản ghi có cập nhật từ `since_iso` trở đi — nguồn số
        liệu cho báo cáo định kỳ (logger.session_summary). Không suy đoán "đã xử lý bao nhiêu tín
        hiệu"/"đảo chiều bao nhiêu lần" vì 2 con số đó không có bản ghi nào lưu lại (tín hiệu bị bỏ
        qua trước khi tạo dòng nào trong bảng này) — chỉ báo cáo đúng những gì SQLite thực sự biết."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM orders WHERE updated_at >= ? GROUP BY status", (since_iso,)
        ).fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
