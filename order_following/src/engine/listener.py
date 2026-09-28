"""Execution Event Engine: chạy LIÊN TỤC suốt phiên (khác orders.py, chỉ chạy 1 lần mỗi lệnh).

cTrader dùng ProtoOAExecutionEvent làm kênh DUY NHẤT báo mọi thay đổi vòng đời order/position — kể
cả khi việc đó xảy ra rất lâu sau, không liên quan gì tới lần gửi gần nhất (vd 1 STOP order khớp
sau 3 tiếng). File này đọc, phân loại, rồi cập nhật state.py (bền vững) và exposure.py (sống) —
không tự quyết định gì.
"""

import logging
import os
import sys
import time

_PROTO_DIR = os.path.join(os.path.dirname(__file__), "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import OpenApiMessages_pb2 as messages  # noqa: E402
import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

from engine.connection import Connection, IncomingMessage
from engine.converter import SymbolConverter
from engine.exposure import ExposureBook
from engine.log import log_event
from engine.state import StateStore
from engine import telegram

_LOGGER = logging.getLogger(__name__)
_MAX_MESSAGES_PER_POLL = 500
_MAX_POLL_SECONDS = 0.5
_RELATIVE_PRICE_SCALE = 100_000  # proto: relativeStopLoss "Specified in 1/100000 of unit of a price"
_EXECUTION_TYPE = model_messages.ProtoOAExecutionType
_TRADE_SIDE = model_messages.ProtoOATradeSide


def poll_once(
    *,
    connection: Connection,
    converter: SymbolConverter,
    exposure_book: ExposureBook,
    state: StateStore,
    label: str,
) -> None:
    """Gọi 1 lần mỗi vòng lặp chính của main.py — xử lý HẾT message đang có sẵn (có trần), rồi trả
    về. connection.receive() chỉ chặn tối đa 1 giây khi buffer không còn message trọn vẹn nào.

    Bản cũ xử lý đúng 1 message/lượt; mỗi lượt vòng lặp chính mất ~1-2 giây (chờ socket 1s + chờ
    Redis 1s — đo trên log live OF10: khoảng cách heartbeat 10s/12s). Khi có luồng spot của conversion
    leg (cách Spotware hướng dẫn), tick về nhanh hơn 1/giây sẽ dồn ứ, ORDER_FILLED/POSITION_CLOSED bị
    xử lý trễ dần. Trần số lượng/thời gian để heartbeat và Redis vẫn được phục vụ đều mỗi vòng.
    """
    deadline = time.monotonic() + _MAX_POLL_SECONDS
    for _ in range(_MAX_MESSAGES_PER_POLL):
        incoming = connection.receive()
        if incoming is None:
            return
        _dispatch(incoming, converter=converter, exposure_book=exposure_book, state=state, label=label)
        if time.monotonic() >= deadline:
            return


def _dispatch(incoming: IncomingMessage, *, converter, exposure_book, state, label) -> None:
    message = incoming.message
    if isinstance(message, messages.ProtoOASpotEvent):
        # Khong can lam gi: connection.py da tu cache gia moi nhat ngay trong _extract_one_message()
        # (self._latest_spot_raw), truoc ca khi message di ra toi day - xem docstring connection.py.
        # Spot event chi xuat hien neu converter.ensure_conversion_chain() tung subscribe 1 conversion
        # leg. (2026-09-27: nhanh nay TRUOC DAY goi converter.update_spot_price() - method KHONG TON
        # TAI tren SymbolConverter, chua tung crash chi vi chua symbol nao can quy doi ty gia.)
        pass
    elif isinstance(message, messages.ProtoOAExecutionEvent):
        log_event(
            _LOGGER, "INFO", "EXECUTION_EVENT_RECEIVED", "NONE", component="listener",
            execution_type=_EXECUTION_TYPE.Name(message.executionType),
        )
        if not _belongs_to_us(message, label):
            return
        _handle_execution_event(message, converter=converter, exposure_book=exposure_book, state=state)
    elif isinstance(message, messages.ProtoOAOrderErrorEvent):
        log_event(
            _LOGGER, "ERROR", "CLEANUP_FAILED", "HIGH", component="listener",
            target_id=message.orderId, action="unsolicited_order_error",
            error_code=message.errorCode, error_description=message.description,
        )
        telegram.notify(
            "CLEANUP_FAILED",
            f"🔴 <b>Unsolicited error</b> — orderId {message.orderId}\n"
            f"{telegram.escape_html(message.errorCode)}: {telegram.escape_html(message.description)}",
        )
    elif isinstance(message, messages.ProtoOASymbolChangedEvent):
        # Event chỉ báo "symbol đã đổi", không kèm nội dung — phải tự hỏi lại spec, nếu không
        # digits/volume step trong cache sẽ cũ dần và lệnh bắt đầu bị từ chối.
        for symbol_id in message.symbolId:
            converter.refresh_by_id(symbol_id)
    elif isinstance(message, messages.ProtoOAErrorRes):
        log_event(
            _LOGGER, "ERROR", "CLEANUP_FAILED", "HIGH", component="listener",
            target_id="unknown", action="unsolicited_error",
            error_code=message.errorCode, error_description=message.description,
        )
        telegram.notify(
            "CLEANUP_FAILED",
            f"🔴 <b>Unsolicited error</b>\n"
            f"{telegram.escape_html(message.errorCode)}: {telegram.escape_html(message.description)}",
        )


def _belongs_to_us(event, label: str) -> bool:
    """Chỉ xử lý event của lệnh do CHÍNH OF đặt (khớp `label`).

    Account có thể có lệnh tay của người dùng hoặc lệnh của chiến lược khác — nếu không lọc, chúng
    sẽ được nhét vào ExposureBook của combo và làm sai mọi quyết định vào lệnh sau đó. Bot gốc lọc
    đúng như vậy ở mọi handler: `if (position.Label != Label || position.SymbolName != SymbolName)
    return;` (Combo.cs, OnPendingOrderFilled/OnPositionClosed).
    """
    for holder in (event.position, event.order):
        if holder.HasField("tradeData"):
            return holder.tradeData.label == label
    return False


def _handle_execution_event(event, *, converter: SymbolConverter, exposure_book: ExposureBook,
                              state: StateStore) -> None:
    # PHẢI kiểm tra đóng vị thế TRƯỚC executionType: cTrader báo executionType=ORDER_FILLED cho CẢ
    # 2 trường hợp — mở lệnh mới VÀ đóng lệnh do SL/TP tự kích hoạt (order.orderType=
    # STOP_LOSS_TAKE_PROFIT lúc đó, order.stopPrice = giá SL/TP, không phải giá vào lệnh mới) — xác
    # nhận qua staff Spotware: "ProtoOAOrder.ProtoOAOrderType should contain the information you
    # need" (community.ctrader.com/forum/connect-api-support/40134) + dữ liệu cộng đồng cho thấy
    # closingOrder=true/orderType=STOP_LOSS_TAKE_PROFIT trên chính event FILLED đó
    # (forum/connect-api-support/38747). payloadType không phân biệt được 2 trường hợp — CHỈ
    # deal.closePositionDetail phân biệt được (field proto ghi rõ "Valid only for closing deal").
    # Bug thật đã xảy ra (2026-09-25, BTCUSD+GOLD dính SL): kiểm executionType trước làm nhánh dưới
    # không bao giờ chạy tới, OF tưởng nhầm "mở vị thế mới" trong khi thực ra vừa đóng.
    if event.HasField("deal") and event.deal.HasField("closePositionDetail"):
        _handle_position_closed(event, converter=converter, exposure_book=exposure_book, state=state)
        return

    execution_type = event.executionType

    if execution_type == _EXECUTION_TYPE.ORDER_FILLED:
        _handle_filled(event, converter=converter, exposure_book=exposure_book, state=state)
        return

    if execution_type in (_EXECUTION_TYPE.ORDER_CANCELLED, _EXECUTION_TYPE.ORDER_EXPIRED):
        _handle_dropped(event, converter=converter, exposure_book=exposure_book, state=state)
        return

    if execution_type == _EXECUTION_TYPE.ORDER_REJECTED:
        # Trường hợp thường gặp đã được orders.py xử lý đồng bộ ngay lúc gửi (connection.wait_for) —
        # nhánh này chỉ còn lại nếu phản hồi tới muộn bất thường, chưa cần xử lý thêm ở v1.
        return

    # ORDER_PARTIAL_FILL, ORDER_REPLACED, SWAP...: chưa cần xử lý sâu ở bản mẫu combo/STOP v1.


def _handle_filled(event, *, converter: SymbolConverter, exposure_book: ExposureBook,
                     state: StateStore) -> None:
    order = event.order
    position = event.position
    symbol_info = converter.find_by_id(position.tradeData.symbolId)
    if symbol_info is None:
        # Symbol không nằm trong danh sách OF load — không đủ dữ liệu (digits/pipPosition) để tính
        # gì cả. Bỏ qua thay vì raise: tiến trình chạy 24/7 không được sập vì 1 event lạ.
        return
    pip_size = 10 ** (-symbol_info.pip_position)
    direction = 1 if position.tradeData.tradeSide == _TRADE_SIDE.BUY else -1

    record = state.get_by_order_id(order.orderId)
    state.mark_filled(order.orderId, position.positionId)
    exposure_book.on_pending_order_dropped(order.orderId)
    exposure_book.on_position_opened(
        position.positionId, symbol_info.symbol_id, position.tradeData.tradeSide, position.tradeData.volume
    )

    trigger_price = order.stopPrice if order.HasField("stopPrice") else position.price
    fill_price = position.price
    slippage_pips = direction * (fill_price - trigger_price) / pip_size

    sl_distance, sl_source = _stop_loss_distance(order, position, fill_price)
    real_sl_pips = sl_distance / pip_size if sl_distance is not None else None
    real_risk_amount = None
    if sl_distance is not None:
        try:
            units = position.tradeData.volume / 100.0  # wire volume theo thang "cents" ×100
            # Tỷ giá SỐNG tại đúng lúc khớp lệnh — KHÔNG phải tỷ giá đã dùng lúc sizing.py tính trước
            # đó, vì có thể lệnh chờ (STOP) khớp rất lâu sau, tỷ giá đã đổi khác. Chain đã được
            # sizing.py gọi ensure_conversion_chain() từ trước nên ở đây chỉ cần đọc giá mới nhất.
            deposit_asset_id = converter.get_deposit_asset_id()
            conversion_rate = converter.get_live_conversion_rate(symbol_info.quote_asset_id, deposit_asset_id)
            real_risk_amount = units * sl_distance * conversion_rate
        except Exception as exc:
            # Chỉ phục vụ báo cáo — state/exposure đã cập nhật xong ở trên, không được để lỗi tỷ giá
            # (vd chưa có bid) làm mất luôn dòng log/Telegram của lệnh khớp.
            log_event(_LOGGER, "WARNING", "REAL_RISK_UNAVAILABLE", "LOW", component="listener",
                      client_order_id=order.clientOrderId, error=exc)

    budgeted_risk_amount = record.risk_amount if record is not None else 0.0

    log_event(
        _LOGGER, "INFO", "ORDER_FILLED", "NONE", component="listener",
        client_order_id=order.clientOrderId, order_id=order.orderId, position_id=position.positionId,
        fill_price=fill_price, trigger_price=trigger_price, slippage_pips=slippage_pips,
        sl_source=sl_source, real_sl_pips=real_sl_pips, real_risk_amount=real_risk_amount,
        budgeted_risk_amount=budgeted_risk_amount,
    )
    real_risk_text = f"${real_risk_amount:.2f}" if real_risk_amount is not None else "n/a"
    telegram.notify(
        "ORDER_FILLED",
        f"🟢 <b>{telegram.escape_html(symbol_info.og_name)} {telegram.side_label(position.tradeData.tradeSide)}</b> — Filled\n"
        f"Fill price: {fill_price} | Real risk: {real_risk_text} (budgeted ${budgeted_risk_amount:.2f})\n"
        f"<code>{telegram.escape_html(order.clientOrderId)}</code>",
    )


def _stop_loss_distance(order, position, fill_price: float):
    """(khoảng cách giá tới SL tính từ giá KHỚP thật, nguồn) — hoặc (None, "unknown").

    Log thật (26/9 + 28/9): `position.stopLoss` VẮNG ở cả 4 lần khớp, nên bản cũ luôn ra 0.0 và
    Telegram báo "Real risk: $0.00" dù lệnh có SL (BTCUSD 28/9 đóng đúng tại SL). ProtoOAOrder mang
    `stopLoss` tuyệt đối (proto dòng 382 — lệnh STOP của combo gửi kiểu này) và `relativeStopLoss`
    (dòng 387, "1/100000 of unit of a price" — lệnh MARKET gửi kiểu này). Thử lần lượt 3 nguồn; `source`
    được log để lần khớp thật kế tiếp cho BẰNG CHỨNG field nào cTrader thực sự điền.
    """
    if position.HasField("stopLoss"):
        return abs(fill_price - position.stopLoss), "position"
    if order.HasField("stopLoss"):
        return abs(fill_price - order.stopLoss), "order"
    if order.HasField("relativeStopLoss"):
        return order.relativeStopLoss / _RELATIVE_PRICE_SCALE, "order_relative"
    return None, "unknown"


def _handle_dropped(event, *, converter: SymbolConverter, exposure_book: ExposureBook, state: StateStore) -> None:
    order = event.order
    order_id = order.orderId
    if event.executionType == _EXECUTION_TYPE.ORDER_CANCELLED:
        state.mark_cancelled(order_id)
        reason = "reversed_or_manually_cancelled"
    else:
        state.mark_expired(order_id)
        reason = "expired_good_till_date"
    exposure_book.on_pending_order_dropped(order_id)
    log_event(
        _LOGGER, "INFO", "ORDER_DROPPED", "NONE", component="listener",
        client_order_id=order.clientOrderId, order_id=order_id, reason=reason,
    )
    symbol_info = converter.find_by_id(order.tradeData.symbolId)
    display_symbol = symbol_info.og_name if symbol_info is not None else str(order.tradeData.symbolId)
    telegram.notify(
        "ORDER_DROPPED",
        f"⚪ <b>{telegram.escape_html(display_symbol)}</b> — Pending order dropped\n"
        f"Reason: {telegram.escape_html(telegram.translate_reason(reason))}\n"
        f"<code>{telegram.escape_html(order.clientOrderId)}</code>",
    )


def deal_net_profit(detail) -> float:
    """Lãi/lỗ ròng của 1 deal đóng (ProtoOAClosePositionDetail), theo tiền tệ tài khoản.

    = TỔNG các số CÓ DẤU broker trả về (khoản phí là số âm). Proto không ghi quy ước dấu; bằng chứng
    là chuỗi balance_after của 7 lần đóng liên tiếp OF10 (26-28/9): delta số dư khớp 7/7 với
    gross + commission + swap. Công thức cũ (gross - commission) sai đúng lần duy nhất có phí: GOLD
    28/9, 742.39 + (-5.04) = 737.35 = mức tăng số dư thật, bản cũ ra 747.43. Swap: OF11 (Pepperstone)
    26/9 gross -50.16, swap +1.66, số dư 10000.00 -> 9951.50 = -50.16 + 1.66 (cộng theo dấu; bản cũ bỏ
    qua swap). pnlConversionFee: OF11 JPN225 28/9 (cặp quy đổi thật) vẫn = 0 -> CHƯA thấy giá trị khác 0,
    giả định cùng quy ước.
    """
    money_scale = (10 ** detail.moneyDigits) if detail.moneyDigits else 1
    pnl_conversion_fee = detail.pnlConversionFee if detail.HasField("pnlConversionFee") else 0
    return (detail.grossProfit + detail.swap + detail.commission + pnl_conversion_fee) / money_scale


def _handle_position_closed(event, *, converter: SymbolConverter, exposure_book: ExposureBook,
                              state: StateStore) -> None:
    position = event.position
    deal = event.deal
    detail = deal.closePositionDetail
    symbol_info = converter.find_by_id(position.tradeData.symbolId)
    if symbol_info is None:
        return
    pip_size = 10 ** (-symbol_info.pip_position)
    direction = 1 if position.tradeData.tradeSide == _TRADE_SIDE.BUY else -1
    money_scale = (10 ** detail.moneyDigits) if detail.moneyDigits else 1

    pips = direction * (deal.executionPrice - detail.entryPrice) / pip_size
    remaining_volume = position.tradeData.volume  # volume CÒN LẠI sau deal này, không phải volume vừa đóng

    pnl_conversion_fee = detail.pnlConversionFee if detail.HasField("pnlConversionFee") else 0
    net_profit = deal_net_profit(detail)
    balance_after = detail.balance / money_scale
    # 1 position co the dong qua NHIEU deal partial-close, moi deal 1 execution event rieng, voi
    # tradeData.volume = volume CON LAI (bang chung log that BTCUSD 2026-09-27: mo 168 -> 4 deal voi
    # volume 118/68/18/0, lai tung deal ty le dung khoi luong dong). Chi coi la DONG HOAN TOAN khi
    # volume con lai = 0 HOAC positionStatus = CLOSED (proto: "Current status of the position") —
    # 2 dieu kien doc lap de khong bao gio bo sot deal cuoi. Lam o MOI deal (ban cu) se xoa exposure
    # som khi vi the that ra van con mo mot phan.
    is_final_close = (
        remaining_volume == 0
        or position.positionStatus == model_messages.ProtoOAPositionStatus.POSITION_STATUS_CLOSED
    )
    if is_final_close:
        # Chot state/exposure TRUOC — day la phan engine dat lenh phu thuoc vao; ghi net_profit (chi
        # phuc vu bao cao) de sau, loi o buoc bao cao cung khong de lai exposure "bong ma".
        state.mark_closed(position.positionId)
        exposure_book.on_position_closed(position.positionId)
    state.accumulate_net_profit(position.positionId, net_profit)

    log_event(
        _LOGGER, "INFO", "POSITION_CLOSED", "NONE", component="listener",
        position_id=position.positionId, symbol=symbol_info.name, side=position.tradeData.tradeSide,
        reason="sl_tp_or_manual_close", entry_price=detail.entryPrice, close_price=deal.executionPrice,
        pips=pips, remaining_volume=remaining_volume / 100.0, is_final_close=is_final_close,
        gross_profit=detail.grossProfit / money_scale, commission=detail.commission / money_scale,
        swap=detail.swap / money_scale, pnl_conversion_fee=pnl_conversion_fee / money_scale,
        deal_net_profit=net_profit, balance_after=balance_after,
    )
    if not is_final_close:
        return  # partial close: đã log đủ trace + cộng dồn net_profit, KHÔNG báo Telegram riêng đợt này

    total_net_profit = state.get_net_profit(position.positionId)
    telegram.notify(
        "POSITION_CLOSED",
        f"🏁 <b>{telegram.escape_html(symbol_info.og_name)}</b> — Position closed\n"
        f"Close price: {deal.executionPrice} | Net P&amp;L: ${total_net_profit:.2f} | Balance: ${balance_after:.2f}",
    )
