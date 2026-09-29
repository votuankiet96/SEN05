"""OF (Order Follower) — điểm khởi động duy nhất.

Ghép engine/ + strategies/combo/adapter.py thành 1 chương trình chạy được: khởi động (connect+auth
cTrader, load symbol, load deposit asset id, reconcile exposure, connect+subscribe Redis), rồi vào
1 vòng lặp đơn — không thread riêng cho Redis (adapter.poll_once và listener.poll_once đều đã
non-blocking, đúng phát hiện từ chính cách OG tự làm cho họ — xem memory of-og-source-findings).
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import redis

_PROTO_DIR = os.path.join(os.path.dirname(__file__), "engine", "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)
import OpenApiMessages_pb2 as messages  # noqa: E402
import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

from configuration import Config, load_config
from engine.connection import AuthError, Connection
from engine.converter import SymbolConverter
from engine.exposure import ExposureBook
from engine.log import configure_logging, log_event, safe_error
from engine.state import StateStore
from engine import discord, listener, orders, sizing
from strategies.combo import adapter

_LOGGER = logging.getLogger(__name__)
_RECONNECT_DELAY_SECONDS = 5.0
_HISTORY_REQUEST_INTERVAL_SECONDS = 0.25  # <= 4 request/giây, dưới trần 5/giây cho request "historical"
_ORDER_STATUS = model_messages.ProtoOAOrderStatus
_MODES = (None, "--check", "--close-all")


def main() -> int:
    config = load_config()
    configure_logging(config.log_dir)
    discord.configure(config.discord.webhook_url, config.discord.thread_id)
    state = StateStore(config.state_db_path)

    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode not in _MODES:
        print(f"Unrecognized argument: {mode!r} (only --check or --close-all are supported)")
        return 1

    try:
        if mode == "--check":
            return _run_check(config)
        if mode == "--close-all":
            return _run_close_all(config)

        while True:
            try:
                _run_once(config, state)
            except KeyboardInterrupt:
                log_event(_LOGGER, "INFO", "SHUTDOWN", "NONE", component="runtime", reason="ctrl_c")
                discord.notify("SHUTDOWN", "⏹️ **OF shutting down** — Ctrl+C")
                return 0
            except (ConnectionError, OSError, TimeoutError, redis.RedisError) as exc:
                log_event(_LOGGER, "ERROR", "CONNECTION_LOST", "MEDIUM", component="runtime",
                          error=exc, retry_seconds=_RECONNECT_DELAY_SECONDS)
                discord.notify(
                    "CONNECTION_LOST",
                    f"🔴 **Connection lost** — retrying in {_RECONNECT_DELAY_SECONDS:.0f}s\n"
                    f"{discord.escape_markdown(safe_error(exc))}",
                )
                time.sleep(_RECONNECT_DELAY_SECONDS)
    finally:
        state.close()


def _new_connection(config: Config) -> Connection:
    return Connection(
        host=config.ctrader.host,
        port=config.ctrader.port,
        heartbeat_seconds=config.ctrader.heartbeat_seconds,
        token_file=config.ctrader.token_file,
        oauth_token_url=config.ctrader.oauth_token_url,
        client_id=config.ctrader.client_id,
        client_secret=config.ctrader.client_secret,
    )


def _startup_complete(config: Config, reconcile) -> None:
    symbol_count = len(config.combo.symbol_names())
    position_count = len(reconcile.position)
    pending_order_count = len(reconcile.order)
    log_event(_LOGGER, "INFO", "STARTUP_COMPLETE", "NONE", component="runtime",
              symbol_count=symbol_count, position_count=position_count, pending_order_count=pending_order_count)
    discord.notify(
        "STARTUP_COMPLETE",
        f"✅ **OF started**\n"
        f"{symbol_count} symbol(s) loaded | {position_count} open position(s) | {pending_order_count} pending order(s)",
    )


def _run_check(config: Config) -> int:
    """`--check`: chạy đúng chuỗi khởi động thật (connect, auth, nạp symbol, reconcile) rồi THOÁT
    NGAY — không vào vòng lặp, không đụng Redis, không sửa SQLite. Thuần đọc, để xác nhận
    credential/cấu hình còn sống trước khi để chạy 24/7 không ai trông."""
    connection = _new_connection(config)
    connection.connect_with_backoff()
    try:
        _authenticate_with_refresh(connection, config.ctrader.ctid_trader_account_id)
        converter = SymbolConverter(connection, config.ctrader.ctid_trader_account_id)
        converter.load(config.combo.broker_to_symbol_map())
        converter.load_deposit_asset_id()
        reconcile = _reconcile(connection, config.ctrader.ctid_trader_account_id)
        _startup_complete(config, reconcile)
        return 0
    finally:
        connection.close()


def _run_close_all(config: Config) -> int:
    """`--close-all`: tình huống khẩn cấp — đóng NGAY mọi vị thế + huỷ mọi lệnh chờ mang label của
    combo, rồi thoát. KHÔNG phải luồng tự động — chỉ chạy khi người vận hành chủ động gọi qua CLI."""
    connection = _new_connection(config)
    connection.connect_with_backoff()
    try:
        _authenticate_with_refresh(connection, config.ctrader.ctid_trader_account_id)
        reconcile = _reconcile(connection, config.ctrader.ctid_trader_account_id)
        orders.close_all(
            reconcile, connection=connection, ctid_trader_account_id=config.ctrader.ctid_trader_account_id,
            label=adapter.LABEL,
        )
        return 0
    finally:
        connection.close()


def _run_once(config: Config, state: StateStore) -> None:
    """1 lượt chạy đầy đủ: khởi động lại từ đầu rồi chạy vòng lặp chính tới khi có lỗi kết nối.
    Bất kỳ lỗi kết nối nào (cTrader hoặc Redis) đều làm hàm này thoát ra — main() phía trên sẽ
    gọi lại từ đầu, đúng nguyên tắc "reconnect thì làm lại toàn bộ chuỗi xác thực + reconcile"
    (không có tài liệu nào xác nhận resume session được — xem report gốc)."""
    connection = _new_connection(config)
    connection.connect_with_backoff()
    try:
        _authenticate_with_refresh(connection, config.ctrader.ctid_trader_account_id)

        converter = SymbolConverter(connection, config.ctrader.ctid_trader_account_id)
        converter.load(config.combo.broker_to_symbol_map())
        converter.load_deposit_asset_id()

        reconcile = _reconcile(connection, config.ctrader.ctid_trader_account_id)
        exposure_book = ExposureBook()
        exposure_book.seed_from_reconcile(reconcile, adapter.LABEL)
        _resolve_pending(reconcile, state=state, connection=connection,
                         ctid_trader_account_id=config.ctrader.ctid_trader_account_id)

        _startup_complete(config, reconcile)

        redis_client = adapter.connect_redis(config)
        pubsub = adapter.subscribe(redis_client, config.combo.pubsub_channel)
        try:
            _loop(connection, converter, exposure_book, state, redis_client, pubsub, config)
        finally:
            pubsub.close()
            redis_client.close()
    finally:
        connection.close()


def _authenticate_with_refresh(connection: Connection, ctid_trader_account_id: int) -> None:
    """Access token sống ~30 ngày. Refresh CHỦ ĐỘNG khi sắp hết hạn (an toàn hơn: tránh token chết
    giữa phiên khi thị trường đang mở), và refresh BỊ ĐỘNG đúng 1 lần nếu auth vẫn báo lỗi. Auth
    lỗi tiếp sau khi refresh là lỗi cấu hình thật (client_id/secret sai, refresh token cũng hết
    hạn...), không phải sự cố mạng — không nên lặp lại vô hạn với đúng credential hỏng."""
    connection.refresh_access_token_if_expiring()
    try:
        connection.authenticate(ctid_trader_account_id)
    except AuthError:
        connection.refresh_access_token()
        connection.authenticate(ctid_trader_account_id)


def _reconcile(connection: Connection, ctid_trader_account_id: int):
    """Ảnh chụp sự thật phía server lúc khởi động: vị thế mở + lệnh chờ. Gọi ĐÚNG 1 LẦN, dùng cho
    cả seed exposure lẫn đối chiếu record dở dang bên dưới."""
    req = messages.ProtoOAReconcileReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    connection.send(req)
    return connection.wait_for(messages.ProtoOAReconcileRes)


def _resolve_pending(reconcile, *, state: StateStore, connection: Connection, ctid_trader_account_id: int) -> None:
    """Chốt sổ record còn dở dang (SENDING/ACCEPTED) từ lần chạy trước bị ngắt giữa chừng, và THỬ LẠI
    những record đã UNRESOLVED nhưng có orderId. Chỉ là SỔ SÁCH — exposure đã được seed thẳng từ chính
    reconcile này, không phụ thuộc bước này.

    1. Lệnh chờ còn sống trên server: `ProtoOAOrder.clientOrderId` khớp trực tiếp id OF đã gửi -> ACCEPTED.
    2. Có orderId nhưng không còn là lệnh chờ: tra `ProtoOAOrderDetailsReq` — "Request for getting Order
       and its related Deals", deal mang `positionId` (proto ProtoOADeal) -> biết CHÍNH XÁC lệnh đã khớp
       vào vị thế nào / bị huỷ / hết hạn / bị từ chối. Trước đây nhánh này đoán không được vì
       `ProtoOAPosition` không mang clientOrderId -> chốt UNRESOLVED (case thật: OF11 UK100 28/9, lệnh
       MARKET khớp ngay nhưng tin khớp kẹt trong hàng đợi rồi mất kết nối).
    3. Không có orderId (chết ngay sau khi gửi) hoặc tra không ra: UNRESOLVED như cũ, không đoán. Record
       đã UNRESOLVED từ trước mà vẫn không ra thì chỉ log, không báo động lặp lại mỗi lần khởi động.

    Mọi lỗi khi tra lịch sử bị chặn TẠI ĐÂY: sổ sách không bao giờ được cản engine khởi động.
    """
    live_orders = {order.clientOrderId: order.orderId for order in reconcile.order if order.clientOrderId}
    open_positions = {position.positionId for position in reconcile.position}
    candidates = [(record, True) for record in state.pending_since_last_run()]
    candidates += [(record, False) for record in state.unresolved_with_order_id()]
    for record, newly_pending in candidates:
        live_order_id = live_orders.get(record.client_order_id)
        if live_order_id is not None:
            state.mark_accepted(record.client_order_id, live_order_id)
            continue

        outcome = None
        if record.order_id is not None:
            try:
                outcome = _recover_from_order_history(
                    record, state=state, connection=connection,
                    ctid_trader_account_id=ctid_trader_account_id, open_positions=open_positions,
                )
            except Exception as exc:
                log_event(_LOGGER, "WARNING", "STARTUP_RECOVERY_FAILED", "LOW", component="runtime",
                          client_order_id=record.client_order_id, order_id=record.order_id, error=exc)
        if outcome is not None:
            log_event(_LOGGER, "INFO", "STARTUP_RECOVERED", "NONE", component="runtime",
                      client_order_id=record.client_order_id, previous_status=record.status, outcome=outcome)
            discord.notify(
                "STARTUP_RECOVERED",
                f"🟢 **Recovered after restart** — {discord.escape_markdown(outcome)}\n"
                f"Previous status: {discord.escape_markdown(record.status)}\n"
                f"`{discord.escape_markdown(record.client_order_id)}`",
            )
            continue

        if not newly_pending:
            log_event(_LOGGER, "INFO", "STARTUP_STILL_UNRESOLVED", "LOW", component="runtime",
                      client_order_id=record.client_order_id, order_id=record.order_id)
            continue
        state.mark_unresolved(record.client_order_id)
        log_event(_LOGGER, "ERROR", "STARTUP_UNRESOLVED", "MEDIUM", component="runtime",
                  client_order_id=record.client_order_id, status=record.status)
        discord.notify(
            "STARTUP_UNRESOLVED",
            f"🔴 **Needs manual check** — could not reconcile with server\n"
            f"Previous status: {discord.escape_markdown(record.status)}\n"
            f"`{discord.escape_markdown(record.client_order_id)}`",
        )


def _recover_from_order_history(record, *, state: StateStore, connection: Connection,
                                ctid_trader_account_id: int, open_positions: set) -> Optional[str]:
    """Mô tả kết quả đã ghi vào state, hoặc None nếu lịch sử lệnh không đủ để kết luận chắc chắn."""
    req = messages.ProtoOAOrderDetailsReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    req.orderId = record.order_id
    details = _history_request(connection, req, messages.ProtoOAOrderDetailsRes,
                               lambda res: res.order.orderId == record.order_id)
    status = details.order.orderStatus
    position_ids = {deal.positionId for deal in details.deal if deal.positionId}

    if status == _ORDER_STATUS.ORDER_STATUS_FILLED or position_ids:
        if len(position_ids) != 1:
            return None  # 0 hoặc nhiều vị thế cho 1 lệnh — không đủ chắc để tự nối
        position_id = next(iter(position_ids))
        if position_id in open_positions:
            state.mark_filled(record.order_id, position_id)
            return f"order filled while offline, position {position_id} still open"
        # Khớp rồi ĐÓNG luôn trong lúc mất kết nối (reconcile là nguồn sự thật về vị thế đang mở).
        net = _closed_position_net(connection, ctid_trader_account_id, position_id, record.created_at)
        state.mark_filled(record.order_id, position_id)
        if net is not None:
            state.accumulate_net_profit(position_id, net)
        state.mark_closed(position_id)
        net_text = f"net ${net:.2f}" if net is not None else "net P&L unavailable"
        return f"order filled and position {position_id} closed while offline, {net_text}"
    if status == _ORDER_STATUS.ORDER_STATUS_CANCELLED:
        state.mark_cancelled(record.order_id)
        return "order cancelled while offline"
    if status == _ORDER_STATUS.ORDER_STATUS_EXPIRED:
        state.mark_expired(record.order_id)
        return "order expired while offline"
    if status == _ORDER_STATUS.ORDER_STATUS_REJECTED:
        state.mark_rejected(record.client_order_id)
        return "order rejected"
    return None


def _closed_position_net(connection: Connection, ctid_trader_account_id: int, position_id: int,
                         created_at_iso: str) -> Optional[float]:
    """Tổng lãi/lỗ ròng các deal ĐÓNG của 1 vị thế (ProtoOADealListByPositionIdReq), cùng công thức
    listener.deal_net_profit. None nếu không lấy được — vị thế vẫn được chốt CLOSED, chỉ thiếu số."""
    req = messages.ProtoOADealListByPositionIdReq()
    req.ctidTraderAccountId = ctid_trader_account_id
    req.positionId = position_id
    # Vị thế chỉ có thể mở SAU khi lệnh được ghi SENDING; lùi thêm 1 giờ cho chắc.
    req.fromTimestamp = int((datetime.fromisoformat(created_at_iso).timestamp() - 3600) * 1000)
    req.toTimestamp = int(time.time() * 1000)
    try:
        res = _history_request(connection, req, messages.ProtoOADealListByPositionIdRes, lambda res: True)
    except Exception as exc:
        log_event(_LOGGER, "WARNING", "STARTUP_RECOVERY_NET_UNAVAILABLE", "LOW", component="runtime",
                  position_id=position_id, error=exc)
        return None
    closing = [deal for deal in res.deal if deal.positionId == position_id and deal.HasField("closePositionDetail")]
    if res.hasMore or not closing:
        return None
    return sum(listener.deal_net_profit(deal.closePositionDetail) for deal in closing)


def _history_request(connection: Connection, req, response_cls, matches):
    """Gửi 1 request tra lịch sử, chờ đúng response. Tài liệu giới hạn "5 requests per second per
    connection for any historical data requests" nhưng không nói request nào thuộc loại đó — coi như
    thuộc, giãn cách sau mỗi lần (chỉ chạy lúc khởi động, vài request)."""
    sent_msg_id = connection.send(req)
    res = connection.wait_for(
        response_cls, messages.ProtoOAErrorRes,
        predicate=lambda incoming: (isinstance(incoming.message, response_cls) and matches(incoming.message))
        or incoming.client_msg_id == sent_msg_id,
    )
    time.sleep(_HISTORY_REQUEST_INTERVAL_SECONDS)
    if isinstance(res, messages.ProtoOAErrorRes):
        raise RuntimeError(f"{type(req).__name__} failed: {res.errorCode} {res.description}")
    return res


def _signed_money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):.2f}"


def _build_account_snapshot(connection, converter, config: Config, state: StateStore, since_iso: str) -> str:
    """Account snapshot — số dư, vị thế đang mở của label này (kèm floating P&L do SERVER tính), hoạt
    động trong kỳ (kèm lãi/lỗ thật đã đóng). Thay bản cũ chỉ đếm status ("nothing new" vô nghĩa).
    KHÔNG có MDD (đã bàn, quyết định bỏ). Chỉ ĐỌC — không đụng exposure_book/state của engine."""
    ctid = config.ctrader.ctid_trader_account_id
    balance = sizing.get_balance(connection, ctid)
    reconcile = _reconcile(connection, ctid)
    unrealized = sizing.get_unrealized_pnl(connection, ctid)

    # Chỉ tính vị thế/lệnh chờ của ĐÚNG label này — account có thể có lệnh tay/chiến lược khác,
    # cùng nguyên tắc lọc label như listener._belongs_to_us và ExposureBook.seed_from_reconcile.
    positions = [p for p in reconcile.position if p.tradeData.label == adapter.LABEL]
    pending_count = sum(1 for o in reconcile.order if o.tradeData.label == adapter.LABEL)
    total_floating = sum(unrealized.get(p.positionId, 0.0) for p in positions)

    lines = [
        "📊 **Account snapshot**",
        f"Balance: ${balance:.2f} | Floating: {_signed_money(total_floating)}",
        f"Open: {len(positions)} position(s), {pending_count} pending order(s)",
    ]
    for p in positions:
        info = converter.find_by_id(p.tradeData.symbolId)
        name = info.og_name if info is not None else str(p.tradeData.symbolId)
        # Proto: volume và lotSize đều tính theo "cents" -> số lot = volume / lotSize (KHÔNG phải
        # volume/100 — đó là số unit; với GOLD 1 lot thường = 100 unit, chia 100 sẽ sai 100 lần).
        size = f"{p.tradeData.volume / info.lot_size:.2f} lot" if info is not None and info.lot_size else \
            f"{p.tradeData.volume / 100.0:.2f} units"
        floating = unrealized.get(p.positionId)
        floating_text = _signed_money(floating) if floating is not None else "n/a"
        lines.append(
            f"  • {discord.escape_markdown(name)} {discord.side_label(p.tradeData.tradeSide)} "
            f"{size} (floating {floating_text})"
        )

    counts = state.count_by_status_since(since_iso)
    period_net = state.sum_net_profit_since(since_iso)
    activity = ", ".join(f"{k.lower()} {v}" for k, v in sorted(counts.items())) or "no activity"
    lines.append("──────")
    lines.append(f"This period: {activity} | closed net: {_signed_money(period_net)}")
    return "\n".join(lines)


def _send_account_snapshot(connection, converter, config: Config, state: StateStore, since_iso: str) -> bool:
    """True nếu đã gửi. Báo cáo KHÔNG được phép làm sập/reconnect engine giao dịch: nó gọi mạng
    (balance/reconcile/unrealized P&L, mỗi cái chờ tối đa 15s) và TimeoutError là lớp con của OSError
    — để lan ra, main() coi là CONNECTION_LOST và khởi động lại toàn bộ. Nuốt MỌI lỗi ở đây; nếu kết
    nối thật sự đã chết, listener.poll_once ngay sau đó vẫn tự raise như bình thường."""
    try:
        text = _build_account_snapshot(connection, converter, config, state, since_iso)
    except Exception as exc:
        log_event(_LOGGER, "WARNING", "SESSION_SUMMARY_FAILED", "LOW", component="runtime", error=exc)
        discord.notify(
            "SESSION_SUMMARY_FAILED",
            f"⚠️ **Account snapshot unavailable** — will retry next cycle\n"
            f"{discord.escape_markdown(safe_error(exc))}",
        )
        return False
    log_event(_LOGGER, "INFO", "SESSION_SUMMARY", "NONE", component="runtime", summary=text.replace("\n", " | "),
              spot_events_total=connection.spot_event_count)
    discord.notify("SESSION_SUMMARY", text)
    return True


def _loop(connection, converter, exposure_book, state, redis_client, pubsub, config: Config) -> None:
    last_summary_at = time.monotonic()
    last_summary_iso = datetime.now(timezone.utc).isoformat()
    while True:
        connection.maybe_send_heartbeat()

        if time.monotonic() - last_summary_at >= config.summary_interval_seconds:
            # Luôn dời mốc TRƯỚC khi thử — lỗi lặp lại cũng chỉ thử lại sau 1 chu kỳ, không thử lại
            # mỗi vòng lặp (sẽ chặn vòng lặp liên tục vì mỗi lần có thể chờ tới 15s/request).
            last_summary_at = time.monotonic()
            now_iso = datetime.now(timezone.utc).isoformat()
            if _send_account_snapshot(connection, converter, config, state, last_summary_iso):
                last_summary_iso = now_iso  # lỗi thì giữ mốc cũ để lần thành công sau phủ trọn kỳ bị lỡ

        # Lỗi kết nối phải nổi lên trên để _run_once thoát ra và làm lại toàn bộ; mọi lỗi khác chỉ
        # log rồi chạy tiếp — 1 message dị dạng không được phép làm sập tiến trình 24/7. Đúng mẫu
        # OG tự dùng cho họ (live_worker.process(): except RedisError: raise / except Exception: log).
        for step, run in (
            ("listener", lambda: listener.poll_once(
                connection=connection, converter=converter, exposure_book=exposure_book,
                state=state, label=adapter.LABEL,
            )),
            ("adapter", lambda: adapter.poll_once(
                pubsub, redis_client=redis_client, config=config, connection=connection,
                converter=converter, exposure_book=exposure_book, state=state,
            )),
        ):
            try:
                run()
            except (ConnectionError, OSError, TimeoutError, redis.RedisError):
                raise
            except Exception as exc:
                log_event(_LOGGER, "ERROR", "UNEXPECTED_ERROR", "HIGH", component="runtime", step=step, error=exc)
                discord.notify(
                    "UNEXPECTED_ERROR",
                    f"🔴 **Unexpected error** in step '{discord.escape_markdown(step)}'\n"
                    f"{discord.escape_markdown(safe_error(exc))}",
                )


if __name__ == "__main__":
    raise SystemExit(main())
