"""Position/Exposure Engine: quyết định BỎ QUA hay ĐẢO CHIỀU trước khi cho phép đặt lệnh mới.

Rule (giống hệt nhau ở Combo.cs và MA Cross.cs, xác nhận không phải logic riêng chiến lược nào):
tối đa 1 exposure ròng (position đang mở HOẶC pending order đang chờ) mỗi symbol dưới 1 label tại
1 thời điểm. Tín hiệu cùng hướng -> bỏ qua. Ngược hướng -> đóng/huỷ cái cũ rồi mới cho mở mới.

ExposureBook được seed từ ProtoOAReconcileReq lúc khởi động, cập nhật sống bởi listener.py — không
tự đọc socket.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List

from engine.connection import Connection
from engine.log import log_event
from engine import orders

_LOGGER = logging.getLogger(__name__)


@dataclass
class _Exposure:
    id: int
    kind: str  # "position" hoặc "pending_order"
    symbol_id: int
    trade_side: int
    volume: int


class ExposureBook:
    def __init__(self):
        self._items: Dict[int, _Exposure] = {}  # key = positionId hoặc orderId

    def seed_from_reconcile(self, reconcile_res, label: str) -> None:
        self._items.clear()
        for position in reconcile_res.position:
            if position.tradeData.label == label:
                self._items[position.positionId] = _Exposure(
                    id=position.positionId, kind="position",
                    symbol_id=position.tradeData.symbolId,
                    trade_side=position.tradeData.tradeSide,
                    volume=position.tradeData.volume,
                )
        for order in reconcile_res.order:
            if order.tradeData.label == label:
                self._items[order.orderId] = _Exposure(
                    id=order.orderId, kind="pending_order",
                    symbol_id=order.tradeData.symbolId,
                    trade_side=order.tradeData.tradeSide,
                    volume=order.tradeData.volume,
                )

    def on_position_opened(self, position_id: int, symbol_id: int, trade_side: int, volume: int) -> None:
        self._items[position_id] = _Exposure(id=position_id, kind="position", symbol_id=symbol_id,
                                              trade_side=trade_side, volume=volume)

    def on_position_closed(self, position_id: int) -> None:
        self._items.pop(position_id, None)

    def on_pending_order_opened(self, order_id: int, symbol_id: int, trade_side: int, volume: int) -> None:
        self._items[order_id] = _Exposure(id=order_id, kind="pending_order", symbol_id=symbol_id,
                                           trade_side=trade_side, volume=volume)

    def on_pending_order_dropped(self, order_id: int) -> None:
        self._items.pop(order_id, None)

    def for_symbol(self, symbol_id: int) -> List[_Exposure]:
        return [item for item in self._items.values() if item.symbol_id == symbol_id]

    def total_count(self) -> int:
        """Tổng số exposure (position + pending order) đang mở, TÍNH TRÊN MỌI SYMBOL — dùng cho
        trần "tối đa N lệnh cùng lúc" khi 1 chiến lược chạy nhiều symbol/timeframe song song."""
        return len(self._items)


def reconcile_exposure(
    *,
    book: ExposureBook,
    connection: Connection,
    ctid_trader_account_id: int,
    client_order_id: str,
    symbol_id: int,
    symbol_name: str,
    new_trade_side: int,
    max_concurrent_trades: int,
) -> bool:
    """Trả True nếu được phép mở lệnh mới, False nếu nên bỏ qua tín hiệu."""
    existing = book.for_symbol(symbol_id)

    same_direction = [item for item in existing if item.trade_side == new_trade_side]
    if same_direction:
        _report(client_order_id, symbol_name, new_trade_side, "skip", "same_direction_exposure_open")
        return False

    # Trần "tối đa N lệnh cùng lúc" — CHỈ áp khi symbol này CHƯA có exposure gì (mở thêm 1 "chỗ"
    # mới thật sự). Đảo chiều (nhánh "opposing" dưới đây) không tính, vì nó thay 1 exposure cũ
    # bằng 1 cái mới trên ĐÚNG symbol đó — tổng số lệnh đang mở không đổi.
    if not existing and book.total_count() >= max_concurrent_trades:
        _report(client_order_id, symbol_name, new_trade_side, "skip", f"max_concurrent_trades_reached_{max_concurrent_trades}")
        return False

    opposing = [item for item in existing if item.trade_side != new_trade_side]
    for item in opposing:
        if item.kind == "position":
            success = orders.close_position(
                item.id, item.volume, connection=connection, ctid_trader_account_id=ctid_trader_account_id,
            )
        else:
            success = orders.cancel_order(
                item.id, connection=connection, ctid_trader_account_id=ctid_trader_account_id,
            )
        if not success:
            _report(client_order_id, symbol_name, new_trade_side, "skip", f"could_not_clear_opposing_{item.kind}_{item.id}")
            return False
        # QUAN TRONG: tin xac nhan da dong/huy bi close_position()/cancel_order() tu "giu lai" de tra
        # ve True/False (qua connection.wait_for() ben trong 2 ham do), nen KHONG BAO GIO toi duoc
        # listener.py - noi von phu trach goi on_position_closed()/on_pending_order_dropped(). Neu
        # khong tu xoa ngay o day, item nay se nam "bong ma" trong book mai mai, lam sai tran
        # max_concurrent_trades va co the chan nham lan dao chieu sau tren dung symbol nay.
        if item.kind == "position":
            book.on_position_closed(item.id)
        else:
            book.on_pending_order_dropped(item.id)

    reason = "reversed_closed_previous_exposure" if opposing else "no_existing_exposure"
    _report(client_order_id, symbol_name, new_trade_side, "proceed", reason)
    return True


def _report(client_order_id: str, symbol_name: str, side: int, decision: str, reason: str) -> None:
    level = "INFO" if decision == "proceed" else "INFO"
    risk = "NONE" if decision == "proceed" else "LOW"
    # Chỉ ghi file log — EXPOSURE_DECISION không đẩy Discord từ 2026-09-27 (trace nội bộ, fire trên
    # mọi tín hiệu; xem discord._DISCORD_EVENTS).
    log_event(
        _LOGGER, level, "EXPOSURE_DECISION", risk, component="exposure",
        client_order_id=client_order_id, symbol=symbol_name, side=side, decision=decision, reason=reason,
    )
