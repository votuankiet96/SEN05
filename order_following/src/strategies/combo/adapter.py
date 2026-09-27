"""Combo adapter: file DUY NHẤT trong OF biết Redis DB1/OG tồn tại và trông như thế nào.

Dịch 1 trigger Pub/Sub -> 1 tín hiệu combo thật (HGETALL) -> gọi đúng thứ tự engine: exposure ->
sizing -> orders. Không tự tính toán gì ngoài parse/map — mọi quyết định/con số thật đều do engine
tính. Xem memory of-og-source-findings cho toàn bộ bằng chứng nguồn gốc các field dưới đây.
"""

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import redis

_PROTO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "engine", "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

from configuration import Config
from engine.connection import Connection
from engine.converter import SymbolConverter
from engine.exposure import ExposureBook, reconcile_exposure
from engine.log import log_event
from engine.orders import StopOrderRequest, place_stop_order
from engine.sizing import calculate as calculate_sizing
from engine.state import StateStore
from engine import telegram

_LOGGER = logging.getLogger(__name__)

# OG không có field label (chỉ có "comment") — OF tự đặt cứng cho đúng chiến lược này, dùng để
# exposure.py lọc đúng lệnh của combo, không đụng lệnh tay hay chiến lược khác trên cùng account.
LABEL = "combo"

_TRADE_SIDE_MAP = {
    "BUY": model_messages.ProtoOATradeSide.BUY,
    "SELL": model_messages.ProtoOATradeSide.SELL,
}


@dataclass
class ComboSignal:
    symbol: str
    timeframe: str
    trade_side: int
    stop_price: float
    atr: float
    ksl: float
    ktp: float
    valid_from_epoch: float
    expiration_epoch: float
    client_order_id: str
    comment: str


def connect_redis(config: Config) -> redis.Redis:
    return redis.Redis(
        host=config.redis.host,
        port=config.redis.port,
        db=config.redis.db,
        password=config.redis.password or None,
        decode_responses=True,
    )


def subscribe(client: redis.Redis, channel: str):
    """Trả về đối tượng pubsub đã subscribe — gọi 1 lần lúc khởi động."""
    pubsub = client.pubsub()
    pubsub.subscribe(channel)
    return pubsub


def poll_once(
    pubsub,
    *,
    redis_client: redis.Redis,
    config: Config,
    connection: Connection,
    converter: SymbolConverter,
    exposure_book: ExposureBook,
    state: StateStore,
) -> None:
    """Gọi 1 lần mỗi vòng lặp chính của main.py — không block quá 1 giây, đúng kiểu OG tự làm cho
    chính họ (og_signal/redis_listener.py: pubsub.get_message(timeout=1, ignore_subscribe...))."""
    message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    if message is None or message.get("type") != "message":
        return

    trigger = _parse_trigger(message.get("data"))
    if trigger is None:
        return
    symbol, timeframe, strategy, stamp = trigger
    log_event(
        _LOGGER, "INFO", "SIGNAL_TRIGGER_RECEIVED", "NONE", component="adapter",
        symbol=symbol, timeframe=timeframe, strategy=strategy,
    )

    # Kênh og:events:signals dùng CHUNG cho mọi chiến lược OG chạy — bắt buộc tự lọc ở đây.
    if strategy != "combo":
        log_event(_LOGGER, "INFO", "SIGNAL_FILTERED_OUT", "NONE", component="adapter",
                   symbol=symbol, timeframe=timeframe, reason="not_combo_strategy")
        return
    if not _is_registered_instrument(config, symbol, timeframe):
        log_event(_LOGGER, "INFO", "SIGNAL_FILTERED_OUT", "NONE", component="adapter",
                   symbol=symbol, timeframe=timeframe, reason="instrument_not_registered")
        return

    hash_key = f"{config.redis.key_prefix}_{symbol}_{timeframe}_COMBO:{stamp}"
    raw = redis_client.hgetall(hash_key)
    if not raw:
        # Trigger tới nhưng HASH không còn (đã hết TTL 7 ngày hoặc bị LTRIM khỏi list) — bỏ qua,
        # không phải lỗi của OF.
        log_event(_LOGGER, "INFO", "SIGNAL_FILTERED_OUT", "NONE", component="adapter",
                   symbol=symbol, timeframe=timeframe, reason="hash_expired_or_missing")
        return

    signal = _parse_signal(raw, config, signal_id=hash_key)
    if signal is None:
        return

    _handle_signal(
        signal,
        broker_symbol=config.combo.broker_symbol_for(signal.symbol),
        connection=connection,
        converter=converter,
        exposure_book=exposure_book,
        state=state,
        ctid_trader_account_id=config.ctrader.ctid_trader_account_id,
        risk_percent=config.risk.risk_percent_per_trade,
        max_concurrent_trades=config.risk.max_concurrent_trades,
    )


def _parse_trigger(raw_data) -> Optional[tuple]:
    try:
        event = json.loads(raw_data)
        return (
            str(event["symbol"]).upper(),
            str(event["timeframe"]).upper(),
            str(event["strategy"]).lower(),
            str(event["stamp"]),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _is_registered_instrument(config: Config, symbol: str, timeframe: str) -> bool:
    return any(
        inst.symbol.upper() == symbol and inst.timeframe.upper() == timeframe
        for inst in config.combo.instruments
    )


def _signal_skipped(signal_id: str, symbol: str, reason: str) -> None:
    log_event(_LOGGER, "WARNING", "SIGNAL_SKIPPED", "LOW", component="adapter",
              signal_id=signal_id, symbol=symbol, reason=reason)
    telegram.notify("SIGNAL_SKIPPED", f"⏭️ {signal_id} {symbol}: {reason}")


def _parse_signal(raw: dict, config: Config, *, signal_id: str) -> Optional[ComboSignal]:
    """Trả None = bỏ qua tín hiệu. Mọi lý do đều được LOG: im lặng ở đây đồng nghĩa OG đổi schema mà
    OF cứ thế ngừng vào lệnh, không ai biết (đúng rủi ro đã ghi nhận trong audit)."""
    symbol = raw.get("symbol", "?")
    try:
        if raw["strategy"].lower() != "combo":
            _signal_skipped(signal_id, symbol, f"strategy={raw['strategy']}_not_combo")
            return None
        if raw["orderType"] != "STOP":
            # combo luôn là STOP theo thiết kế OG (_ORDER_TYPE_BY_STRATEGY) — khác đi là dữ liệu
            # bất thường, bỏ qua an toàn thay vì đoán cách xử lý.
            _signal_skipped(signal_id, symbol, f"orderType={raw['orderType']}_combo_only_sends_stop")
            return None
        trade_side = _TRADE_SIDE_MAP[raw["tradeSide"]]
        ksl = float(raw["ksl"]) if "ksl" in raw else config.risk.fallback_ksl
        ktp = float(raw["ktp"]) if "ktp" in raw else config.risk.fallback_ktp
        return ComboSignal(
            symbol=raw["symbol"],
            timeframe=raw["timeframe"],
            trade_side=trade_side,
            stop_price=float(raw["stopPrice"]),
            atr=float(raw["atr"]),
            ksl=ksl,
            ktp=ktp,
            valid_from_epoch=datetime.fromisoformat(raw["valid_from"]).timestamp(),
            expiration_epoch=float(raw["expirationTimestamp"]),
            client_order_id=raw["clientOrderId"],
            comment=raw.get("comment", ""),
        )
    except (KeyError, ValueError) as exc:
        _signal_skipped(signal_id, symbol, f"unreadable_og_data_{type(exc).__name__}")
        return None


def _handle_signal(
    signal: ComboSignal,
    *,
    broker_symbol: str,
    connection: Connection,
    converter: SymbolConverter,
    exposure_book: ExposureBook,
    state: StateStore,
    ctid_trader_account_id: int,
    risk_percent: float,
    max_concurrent_trades: int,
) -> None:
    # Check sớm, rẻ — orders.py sẽ check has_sent() lại lần nữa (nguồn sự thật cuối) ngay trước khi
    # gửi; ở đây chỉ để khỏi tốn công gọi sizing/exposure cho 1 signal chắc chắn sẽ bị chặn.
    if state.has_sent(signal.client_order_id):
        return

    symbol_info = converter.get(broker_symbol)

    # Symbol có thể đang đóng cửa/nghỉ lễ/bị broker khoá — gửi lệnh lúc đó chỉ nhận về lỗi. Chặn
    # TRƯỚC exposure để 1 signal không giao dịch được cũng không kích hoạt đảo chiều đóng vị thế cũ.
    unavailable = converter.unavailable_reason(broker_symbol)
    if unavailable is not None:
        _signal_skipped(signal.client_order_id, broker_symbol, unavailable)
        return

    allowed = reconcile_exposure(
        book=exposure_book,
        connection=connection,
        ctid_trader_account_id=ctid_trader_account_id,
        client_order_id=signal.client_order_id,
        symbol_id=symbol_info.symbol_id,
        symbol_name=broker_symbol,
        new_trade_side=signal.trade_side,
        max_concurrent_trades=max_concurrent_trades,
    )
    if not allowed:
        return

    direction = 1 if signal.trade_side == model_messages.ProtoOATradeSide.BUY else -1
    sizing_result = calculate_sizing(
        connection=connection,
        converter=converter,
        ctid_trader_account_id=ctid_trader_account_id,
        symbol_name=broker_symbol,
        order_type=model_messages.ProtoOAOrderType.STOP,
        direction=direction,
        entry_price=signal.stop_price,
        atr=signal.atr,
        ksl=signal.ksl,
        ktp=signal.ktp,
        risk_percent=risk_percent,
        client_order_id=signal.client_order_id,
    )

    placed_volume_units = sizing_result.volume / 100.0
    expected_loss = placed_volume_units * sizing_result.sl_pips * sizing_result.pip_value
    expected_profit = placed_volume_units * sizing_result.tp_pips * sizing_result.pip_value
    log_event(
        _LOGGER, "INFO", "PLAN_COMPUTED", "NONE", component="adapter",
        client_order_id=signal.client_order_id, symbol=broker_symbol, side=signal.trade_side,
        balance=sizing_result.balance, risk_percent=risk_percent, risk_budget=sizing_result.risk_amount,
        atr=signal.atr, ksl=signal.ksl, ktp=signal.ktp,
        sl_pips=sizing_result.sl_pips, tp_pips=sizing_result.tp_pips, pip_value=sizing_result.pip_value,
        raw_volume=sizing_result.raw_volume_units, placed_volume=placed_volume_units,
        volume_step=symbol_info.step_volume, expected_loss=expected_loss, expected_profit=expected_profit,
    )
    telegram.notify(
        "PLAN_COMPUTED",
        f"📊 {signal.client_order_id} {broker_symbol} side={signal.trade_side}: "
        f"risk ${sizing_result.risk_amount:.2f} ({risk_percent:.2f}%), "
        f"SL {sizing_result.sl_pips:.1f}p / TP {sizing_result.tp_pips:.1f}p, volume {placed_volume_units:.2f}",
    )

    account_asset = converter.get_deposit_asset_id()
    log_event(
        _LOGGER, "INFO", "FX_CONVERSION_APPLIED", "NONE", component="adapter",
        client_order_id=signal.client_order_id, quote_asset=symbol_info.quote_asset_id,
        account_asset=account_asset, factor=sizing_result.conversion_rate,
    )
    if symbol_info.quote_asset_id == account_asset:
        telegram.notify("FX_CONVERSION_APPLIED", f"💱 {signal.client_order_id}: no conversion needed (quote==account)")
    else:
        telegram.notify(
            "FX_CONVERSION_APPLIED",
            f"💱 {signal.client_order_id}: 1 asset#{symbol_info.quote_asset_id} = "
            f"{sizing_result.conversion_rate:.6f} asset#{account_asset}",
        )

    if sizing_result.volume <= 0:
        # sizing.py đã tự log lý do cụ thể (SIZE TOO SMALL) — không gửi lệnh khối lượng 0.
        return

    request = StopOrderRequest(
        client_order_id=signal.client_order_id,
        label=LABEL,
        comment=signal.comment,
        symbol_name=broker_symbol,
        trade_side=signal.trade_side,
        volume=sizing_result.volume,
        stop_price=signal.stop_price,
        stop_loss=sizing_result.stop_loss,
        take_profit=sizing_result.take_profit,
        valid_from_epoch=signal.valid_from_epoch,
        expiration_epoch=signal.expiration_epoch,
        risk_amount=sizing_result.risk_amount,
    )
    place_stop_order(
        request,
        connection=connection,
        converter=converter,
        state=state,
        ctid_trader_account_id=ctid_trader_account_id,
    )
