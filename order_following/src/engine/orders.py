"""Order Engine: build & gửi ProtoOANewOrderReq, cancel/close — hành động THẬT.

Khác sizing.py (thuần tính toán, an toàn gọi lại vô hạn lần): mỗi hàm ở đây chỉ nên chạy ĐÚNG 1 LẦN
cho mỗi tín hiệu — gửi trùng là 2 lệnh thật ngoài thị trường, không sửa lại được.

Bản mẫu v1 chỉ có place_stop_order (combo dùng STOP) — chưa có place_market_order (để dành ma_cross).
"""

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

_PROTO_DIR = os.path.join(os.path.dirname(__file__), "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import OpenApiMessages_pb2 as messages  # noqa: E402
import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

from engine.connection import Connection
from engine.converter import SymbolConverter
from engine.log import log_event
from engine.state import StateStore
from engine import telegram

_LOGGER = logging.getLogger(__name__)

_ERROR_RESPONSE_TYPES = (messages.ProtoOAExecutionEvent, messages.ProtoOAOrderErrorEvent, messages.ProtoOAErrorRes)


@dataclass
class StopOrderRequest:
    client_order_id: str
    label: str
    comment: str
    symbol_name: str
    trade_side: int  # model_messages.ProtoOATradeSide.BUY hoặc .SELL
    volume: int
    stop_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    valid_from_epoch: float
    expiration_epoch: float  # giây — hàm này tự nhân 1000 khi gửi cho cTrader
    risk_amount: float  # $ dự kiến rủi ro nếu SL bị chạm — lưu lại để đối chiếu với rủi ro THẬT lúc fill


def place_stop_order(
    request: StopOrderRequest,
    *,
    connection: Connection,
    converter: SymbolConverter,
    state: StateStore,
    ctid_trader_account_id: int,
) -> Optional[int]:
    """Trả về orderId nếu được chấp nhận, None nếu bị chặn (dedup/hết hạn) hoặc bị từ chối."""
    if state.has_sent(request.client_order_id):
        log_event(
            _LOGGER, "INFO", "EXPOSURE_DECISION", "LOW", component="orders",
            client_order_id=request.client_order_id, symbol=request.symbol_name,
            side=request.trade_side, decision="skip", reason="duplicate_client_order_id",
        )
        return None

    now = time.time()
    if not (request.valid_from_epoch <= now < request.expiration_epoch):
        log_event(
            _LOGGER, "INFO", "EXPOSURE_DECISION", "LOW", component="orders",
            client_order_id=request.client_order_id, symbol=request.symbol_name,
            side=request.trade_side, decision="skip", reason="signal_expired_before_send",
            now=int(now), valid_from=int(request.valid_from_epoch), expiration=int(request.expiration_epoch),
        )
        return None

    symbol_info = converter.get(request.symbol_name)

    order = messages.ProtoOANewOrderReq()
    order.ctidTraderAccountId = ctid_trader_account_id
    order.symbolId = symbol_info.symbol_id
    order.orderType = model_messages.ProtoOAOrderType.STOP
    order.tradeSide = request.trade_side
    order.volume = request.volume
    # Làm tròn LẦN CUỐI ngay trước khi lên dây: server TỪ CHỐI chứ không tự làm tròn ("Order price
    # ... has more digits than symbol allows", xác nhận qua staff Spotware). stopPrice đến thẳng từ
    # Redis (OG làm tròn 2 chữ số theo quy ước riêng của họ, không biết digits thật của symbol).
    order.stopPrice = converter.round_price(request.symbol_name, request.stop_price)
    if request.stop_loss is not None:
        order.stopLoss = converter.round_price(request.symbol_name, request.stop_loss)
    if request.take_profit is not None:
        order.takeProfit = converter.round_price(request.symbol_name, request.take_profit)
    # timeInForce mặc định proto2 = GOOD_TILL_CANCEL nếu bỏ trống (xác nhận từ schema) — nhưng ở đây
    # đặt tường minh GOOD_TILL_DATE vì Redis đã cho sẵn expirationTimestamp thật, để cTrader tự huỷ
    # đúng giờ thay vì lệnh chờ nằm mãi. Cần tự test trên demo trước khi tin hoàn toàn (đã ghi nhận).
    order.timeInForce = model_messages.ProtoOATimeInForce.GOOD_TILL_DATE
    order.expirationTimestamp = int(request.expiration_epoch * 1000)  # giây (Redis) -> ms (cTrader)
    order.label = request.label
    order.comment = request.comment
    order.clientOrderId = request.client_order_id

    # Ghi "đang gửi" TRƯỚC khi biết kết quả — để crash ngay sau dòng send() vẫn để lại dấu vết.
    state.mark_sending(request.client_order_id, request.symbol_name, request.label, request.risk_amount)
    sent_msg_id = connection.send(order)

    response = connection.wait_for(
        *_ERROR_RESPONSE_TYPES, predicate=_response_matcher(sent_msg_id, request.client_order_id)
    )

    accepted = (
        isinstance(response, messages.ProtoOAExecutionEvent)
        and response.executionType == model_messages.ProtoOAExecutionType.ORDER_ACCEPTED
    )
    if accepted:
        order_id = response.order.orderId
        state.mark_accepted(request.client_order_id, order_id)
        log_event(
            _LOGGER, "INFO", "ORDER_ACCEPTED", "NONE", component="orders",
            client_order_id=request.client_order_id, order_id=order_id, symbol=request.symbol_name,
            side=request.trade_side, volume=request.volume, stop_price=request.stop_price,
            stop_loss=request.stop_loss, take_profit=request.take_profit,
            expiration=request.expiration_epoch,
        )
        telegram.notify(
            "ORDER_ACCEPTED",
            f"🟡 <b>{telegram.escape_html(symbol_info.og_name)} {telegram.side_label(request.trade_side)}</b> — Pending order placed\n"
            f"Trigger price: {request.stop_price}\n"
            f"<code>{telegram.escape_html(request.client_order_id)}</code>",
        )
        return order_id

    error_code, error_description = _extract_error(response)
    state.mark_rejected(request.client_order_id)
    log_event(
        _LOGGER, "WARNING", "ORDER_REJECTED", "MEDIUM", component="orders",
        client_order_id=request.client_order_id, symbol=request.symbol_name, side=request.trade_side,
        error_code=error_code, error_description=error_description,
    )
    telegram.notify(
        "ORDER_REJECTED",
        f"🔴 <b>{telegram.escape_html(symbol_info.og_name)} {telegram.side_label(request.trade_side)}</b> — Rejected\n"
        f"Reason: {telegram.escape_html(error_code)} ({telegram.escape_html(error_description)})\n"
        f"<code>{telegram.escape_html(request.client_order_id)}</code>",
    )
    return None


def close_all(reconcile, *, connection: Connection, ctid_trader_account_id: int, label: str) -> None:
    """Đóng NGAY mọi vị thế + huỷ mọi lệnh chờ mang đúng `label` — dùng cho `--close-all` (tình huống
    khẩn cấp cần dừng tất cả ngay lập tức). KHÔNG phải luồng tự động: chỉ chạy khi người vận hành chủ
    động gọi qua CLI, và chỉ đụng tới đúng lệnh/vị thế của label này — không ảnh hưởng lệnh tay hay
    chiến lược khác trên cùng account (giống nguyên tắc lọc label ở listener.py)."""
    closed = cancelled = failed = 0
    for position in reconcile.position:
        if position.tradeData.label != label:
            continue
        if close_position(position.positionId, position.tradeData.volume,
                           connection=connection, ctid_trader_account_id=ctid_trader_account_id):
            closed += 1
        else:
            failed += 1
    for order in reconcile.order:
        if order.tradeData.label != label:
            continue
        if cancel_order(order.orderId, connection=connection, ctid_trader_account_id=ctid_trader_account_id):
            cancelled += 1
        else:
            failed += 1
    log_event(
        _LOGGER, "WARNING", "CLOSE_ALL_EXECUTED", "HIGH", component="orders",
        closed_count=closed, cancelled_count=cancelled, failed_count=failed,
    )
    telegram.notify(
        "CLOSE_ALL_EXECUTED",
        f"🛑 <b>CLOSE-ALL EXECUTED</b>\n"
        f"Closed {closed} position(s), cancelled {cancelled} pending order(s), {failed} failed",
    )


def cancel_order(order_id: int, *, connection: Connection, ctid_trader_account_id: int) -> bool:
    req = messages.ProtoOACancelOrderReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    req.orderId = order_id
    sent_msg_id = connection.send(req)
    response = connection.wait_for(
        *_ERROR_RESPONSE_TYPES, predicate=_order_id_matcher(sent_msg_id, order_id)
    )

    cancelled = (
        isinstance(response, messages.ProtoOAExecutionEvent)
        and response.executionType == model_messages.ProtoOAExecutionType.ORDER_CANCELLED
    )
    if cancelled:
        return True
    error_code, error_description = _extract_error(response)
    log_event(
        _LOGGER, "ERROR", "CLEANUP_FAILED", "HIGH", component="orders",
        target_id=order_id, action="cancel_order", error_code=error_code, error_description=error_description,
    )
    telegram.notify(
        "CLEANUP_FAILED",
        f"🔴 <b>Cleanup failed</b> — cancel pending order {order_id}\n"
        f"{telegram.escape_html(error_code)}: {telegram.escape_html(error_description)}",
    )
    return False


def close_position(
    position_id: int, volume: int, *, connection: Connection, ctid_trader_account_id: int
) -> bool:
    req = messages.ProtoOAClosePositionReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    req.positionId = position_id
    req.volume = volume
    sent_msg_id = connection.send(req)
    response = connection.wait_for(
        *_ERROR_RESPONSE_TYPES, predicate=_position_id_matcher(sent_msg_id, position_id)
    )

    if isinstance(response, messages.ProtoOAExecutionEvent):
        return True
    error_code, error_description = _extract_error(response)
    log_event(
        _LOGGER, "ERROR", "CLEANUP_FAILED", "HIGH", component="orders",
        target_id=position_id, action="close_position", error_code=error_code, error_description=error_description,
    )
    telegram.notify(
        "CLEANUP_FAILED",
        f"🔴 <b>Cleanup failed</b> — close position {position_id}\n"
        f"{telegram.escape_html(error_code)}: {telegram.escape_html(error_description)}",
    )
    return False


def _response_matcher(sent_msg_id: str, client_order_id: str):
    """Chỉ nhận phản hồi ĐÚNG của lệnh vừa gửi.

    2 cách khớp, chấp nhận cả hai vì mỗi cách có giới hạn riêng:
      - `clientMsgId` trên envelope: cách chính thức để khớp response tức thời với request (report
        gốc), do chính OF sinh ra nên chắc chắn duy nhất.
      - `order.clientOrderId`: phòng trường hợp server không echo clientMsgId trên ExecutionEvent —
        field này cTrader "trả nguyên lại trong execution report" (xác nhận từ tài liệu OG), và OF
        đặt nó = signal_id nên cũng là duy nhất.
    `ProtoOAOrderErrorEvent`/`ProtoOAErrorRes` KHÔNG mang clientOrderId nên không lọc chính xác
    được — chấp nhận chúng (lỗi bay về ngay sau khi gửi gần như chắc chắn là của chính lệnh này).
    Đây là giới hạn đã biết, cần xác nhận lại trên demo.
    """

    def matches(incoming) -> bool:
        if incoming.client_msg_id == sent_msg_id:
            return True
        if isinstance(incoming.message, messages.ProtoOAExecutionEvent):
            return incoming.message.order.clientOrderId == client_order_id
        return True

    return matches


def _order_id_matcher(sent_msg_id: str, order_id: int):
    def matches(incoming) -> bool:
        if incoming.client_msg_id == sent_msg_id:
            return True
        if isinstance(incoming.message, messages.ProtoOAExecutionEvent):
            return incoming.message.order.orderId == order_id
        if isinstance(incoming.message, messages.ProtoOAOrderErrorEvent):
            return incoming.message.orderId == order_id
        return True

    return matches


def _position_id_matcher(sent_msg_id: str, position_id: int):
    def matches(incoming) -> bool:
        if incoming.client_msg_id == sent_msg_id:
            return True
        if isinstance(incoming.message, messages.ProtoOAExecutionEvent):
            return incoming.message.position.positionId == position_id
        if isinstance(incoming.message, messages.ProtoOAOrderErrorEvent):
            return incoming.message.positionId == position_id
        return True

    return matches


def _extract_error(response):
    if isinstance(response, (messages.ProtoOAOrderErrorEvent, messages.ProtoOAErrorRes)):
        return response.errorCode, response.description
    if isinstance(response, messages.ProtoOAExecutionEvent):
        return response.errorCode, ""
    return "UNKNOWN", "không nhận được phản hồi rõ ràng trong thời gian chờ"
