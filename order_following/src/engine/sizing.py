"""Money Management Engine: tính SL/TP và volume cho 1 tín hiệu.

Thuần tính toán — mọi việc liên quan tới tỷ giá (lấy conversion chain, giữ giá sống) đã chuyển hết
sang converter.py (xem NoConversionPathFound ở đó); file này chỉ GỌI, không tự làm. Rẽ nhánh theo
orderType (khái niệm cTrader: STOP/MARKET) — KHÔNG theo tên chiến lược.
"""

import logging
import os
import sys
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
from engine import telegram

_LOGGER = logging.getLogger(__name__)


@dataclass
class SizingResult:
    stop_loss: Optional[float]  # giá tuyệt đối — chỉ có khi orderType=STOP
    take_profit: Optional[float]  # giá tuyệt đối — chỉ có khi orderType=STOP
    sl_distance_price: float  # khoảng cách giá — luôn có, dùng cho MARKET hoặc để log
    tp_distance_price: float
    sl_pips: float
    tp_pips: float
    pip_value: float
    conversion_rate: float  # 1 unit quote-asset = bao nhiêu unit account-asset — 1.0 nếu cùng currency
    balance: float
    risk_amount: float
    raw_volume_units: float  # UNIT thật, trước khi chuẩn hoá — không phải lot, không phải wire volume
    volume: int  # giá trị gửi lên cTrader (đã ×100 theo thang "0.01 of a unit")
    quote_equals_account: bool


def calculate(
    *,
    connection: Connection,
    converter: SymbolConverter,
    ctid_trader_account_id: int,
    symbol_name: str,
    order_type: int,
    direction: int,  # +1 Buy, -1 Sell
    entry_price: Optional[float],  # có với STOP (=stopPrice), None với MARKET
    atr: float,
    ksl: float,
    ktp: float,
    risk_percent: float,
    client_order_id: str,
) -> SizingResult:
    info = converter.get(symbol_name)
    pip_size = 10 ** (-info.pip_position)

    sl_distance_price = ksl * atr
    tp_distance_price = ktp * atr
    sl_pips = sl_distance_price / pip_size
    tp_pips = tp_distance_price / pip_size

    stop_loss = None
    take_profit = None
    if order_type == model_messages.ProtoOAOrderType.STOP:
        if entry_price is None:
            raise ValueError("orderType=STOP nhưng thiếu entry_price")
        stop_loss = converter.round_price(symbol_name, entry_price - direction * sl_distance_price)
        take_profit = converter.round_price(symbol_name, entry_price + direction * tp_distance_price)

    balance = _get_balance(connection, ctid_trader_account_id)
    deposit_asset_id = converter.get_deposit_asset_id()  # cache sẵn lúc khởi động — không đổi giữa phiên
    quote_equals_account = info.quote_asset_id == deposit_asset_id
    if not quote_equals_account:
        converter.ensure_conversion_chain(info.quote_asset_id, deposit_asset_id)
    conversion_rate = converter.get_live_conversion_rate(info.quote_asset_id, deposit_asset_id)
    pip_value = pip_size * conversion_rate

    risk_amount = balance * risk_percent / 100.0
    # Thứ nguyên: tiền ÷ (pip × tiền/pip/unit) = UNIT. pip_value là giá trị 1 pip cho 1 UNIT
    # (đúng định nghĩa Symbol.PipValue của cAlgo mà PipValueNow() trong bot gốc mô phỏng lại).
    raw_volume_units = risk_amount / (sl_pips * pip_value)
    raw_volume_wire = raw_volume_units * 100  # so sánh với min/max vốn cũng ở thang "cents"
    if raw_volume_wire < info.min_volume:
        log_event(
            _LOGGER, "WARNING", "SIZE_TOO_SMALL", "LOW", component="sizing",
            client_order_id=client_order_id, symbol=symbol_name, risk_percent=risk_percent,
            risk_amount=risk_amount, sl_pips=sl_pips, requested_volume=raw_volume_wire,
            min_volume=info.min_volume,
        )
        telegram.notify(
            "SIZE_TOO_SMALL",
            f"⚠️ {client_order_id} {symbol_name}: volume {raw_volume_wire:.2f} < min {info.min_volume} — skipped",
        )
    elif raw_volume_wire > info.max_volume:
        log_event(
            _LOGGER, "WARNING", "SIZE_CAPPED", "LOW", component="sizing",
            client_order_id=client_order_id, symbol=symbol_name,
            requested_volume=raw_volume_wire, max_volume=info.max_volume,
        )
        telegram.notify(
            "SIZE_CAPPED",
            f"⚠️ {client_order_id} {symbol_name}: volume {raw_volume_wire:.2f} > max {info.max_volume} — capped",
        )
    volume = converter.units_to_volume(symbol_name, raw_volume_units)

    return SizingResult(
        stop_loss=stop_loss,
        take_profit=take_profit,
        sl_distance_price=sl_distance_price,
        tp_distance_price=tp_distance_price,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        pip_value=pip_value,
        conversion_rate=conversion_rate,
        balance=balance,
        risk_amount=risk_amount,
        raw_volume_units=raw_volume_units,
        volume=volume,
        quote_equals_account=quote_equals_account,
    )


def _get_balance(connection: Connection, ctid_trader_account_id: int) -> float:
    """balance PHẢI lấy mới mỗi lần gọi (thay đổi liên tục theo lệnh) — khác deposit_asset_id
    (không đổi, cache trong converter.py). Scale bằng moneyDigits, KHÔNG phải hằng số cố định
    ×100 như volume — xác nhận trực tiếp từ comment field moneyDigits trong proto gốc."""
    req = messages.ProtoOATraderReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    connection.send(req)
    res = connection.wait_for(messages.ProtoOATraderRes)
    trader = res.trader
    return trader.balance / (10 ** trader.moneyDigits)
