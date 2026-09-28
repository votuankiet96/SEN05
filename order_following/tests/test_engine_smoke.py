"""Test logic thuần của từng engine — KHÔNG cần kết nối cTrader thật (không test connection.py/
sizing.py/orders.py/listener.py ở đây vì chúng cần socket sống; xem tests/demo_checks/ sau này)."""

import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
sys.path.insert(0, _SRC_DIR)

sys.path.insert(0, os.path.join(_SRC_DIR, "engine", "proto", "generated"))
import OpenApiMessages_pb2 as messages  # noqa: E402
import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

import main  # noqa: E402
from engine.state import StateStore, STATUS_ACCEPTED
from engine.converter import SymbolConverter, SymbolInfo, _ConversionLeg, _Holiday
from engine.exposure import ExposureBook, reconcile_exposure
from engine.log import log_event, safe_error
from engine import orders as orders_module

USD, JPY, GBP = 1, 2, 3
_TRADING_MODE = model_messages.ProtoOATradingMode


class _FakeConnection:
    """Giả lập đúng 1 hàm converter.py thật sự cần từ connection.py để test logic quy đổi mà
    không cần socket sống — get_latest_spot() trả về raw (chưa chia _PRICE_SCALE), giống hệt
    Connection thật."""

    def __init__(self, spots_raw):
        self._spots_raw = spots_raw

    def get_latest_spot(self, symbol_id):
        return self._spots_raw.get(symbol_id)


def test_state_store_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        client_order_id = "combo:H1:US30:20260917T180000Z"

        assert store.has_sent(client_order_id) is False
        store.mark_sending(client_order_id, "US30", "combo", risk_amount=50.0)
        assert store.has_sent(client_order_id) is True

        pending = store.pending_since_last_run()
        assert len(pending) == 1
        assert pending[0].risk_amount == 50.0

        store.mark_accepted(client_order_id, order_id=999)
        record = store.get_by_order_id(999)
        assert record is not None
        assert record.risk_amount == 50.0

        store.mark_filled(999, position_id=888)
        store.mark_closed(888)
        store.close()


def test_resolve_pending_matches_live_order_and_marks_rest_unresolved(caplog):
    # Sau khi crash: 1 lenh cho van con song tren server (khop clientOrderId - ProtoOAOrder field 17),
    # 1 lenh khong con dau vet nao. Cai con song -> ACCEPTED lai; cai kia -> UNRESOLVED (khong doan).
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        store.mark_sending("combo:H1:US30:A", "US30", "combo", risk_amount=50.0)
        store.mark_sending("combo:H4:J225:B", "J225", "combo", risk_amount=50.0)

        reconcile = messages.ProtoOAReconcileRes()
        reconcile.ctidTraderAccountId = 1
        order = reconcile.order.add()
        order.orderId = 777
        order.clientOrderId = "combo:H1:US30:A"
        order.orderType = model_messages.ProtoOAOrderType.STOP
        order.orderStatus = model_messages.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
        order.tradeData.symbolId = 1132
        order.tradeData.volume = 100
        order.tradeData.tradeSide = model_messages.ProtoOATradeSide.SELL

        try:
            with caplog.at_level(logging.INFO):
                main._resolve_pending(reconcile, state=store)

            # Lenh con song: lay lai duoc orderId that, van la "dang cho" mot cach chinh dang.
            assert store.get_by_order_id(777).status == STATUS_ACCEPTED
            assert [r.client_order_id for r in store.pending_since_last_run()] == ["combo:H1:US30:A"]
            # Lenh khong doi chieu duoc: chot so UNRESOLVED nen khong bao dong lai moi lan khoi dong...
            assert "event=STARTUP_UNRESOLVED" in caplog.text
            assert "combo:H4:J225:B" in caplog.text
            # ...nhung van chan gui lai vinh vien, dung nguyen tac idempotency theo clientOrderId.
            assert store.has_sent("combo:H4:J225:B") is True
        finally:
            store.close()


def _sample_us30() -> SymbolInfo:
    return SymbolInfo(
        symbol_id=1132, name="US30", og_name="US30", digits=2, pip_position=1, lot_size=100,
        min_volume=1, max_volume=100000, step_volume=1, quote_asset_id=1,
        trading_mode=_TRADING_MODE.ENABLED, schedule_time_zone="Europe/Berlin",
        # Mo ca tuan trong test mac dinh; cac test ve gio giao dich tu dat lai schedule/holidays.
        schedule=[(0, 7 * 86400)], holidays=[],
    )


def test_converter_round_price():
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._symbols = {"US30": _sample_us30()}
    assert converter.round_price("US30", 51991.334782) == 51991.33


def test_converter_units_to_volume_uses_fixed_100_scale_not_lot_size():
    # Proto: moi field volume la "0.01 of a unit" -> he so ×100 CO DINH, khong dinh toi lotSize.
    # Dat lot_size=10_000 (khac 100) de bat duoc neu code lo quay lai nhan voi lotSize.
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    info.lot_size = 10_000
    converter._symbols = {"US30": info}
    assert converter.units_to_volume("US30", 3.62) == 362  # 3.62 unit -> 362, KHONG phai 36_200


def test_converter_units_to_volume_returns_zero_below_minimum():
    # Duoi minVolume phai tra 0 (bo qua tin hieu), KHONG duoc tu nang len min - nang len nghia la
    # am tham dat lenh voi rui ro lon hon risk% da dinh. Dung hanh vi bot goc: "No order sent."
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    info.min_volume = 100
    converter._symbols = {"US30": info}
    assert converter.units_to_volume("US30", 0.5) == 0  # 0.5 unit -> 50 wire < min 100


def test_converter_units_to_volume_respects_step_and_max():
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    info.min_volume = 100
    info.step_volume = 50
    info.max_volume = 400
    converter._symbols = {"US30": info}
    assert converter.units_to_volume("US30", 2.2) == 200  # 220 -> lam tron XUONG buoc 50
    assert converter.units_to_volume("US30", 99.0) == 400  # vuot max -> kep ve max


def _unavailable(info: SymbolInfo, now: datetime):
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._symbols = {"US30": info}
    return converter.unavailable_reason("US30", now=now)


def test_converter_unavailable_when_trading_mode_not_enabled():
    # Broker co the khoa symbol giua phien (vd chi cho dong lenh). Gui lenh luc do chi nhan ve loi.
    info = _sample_us30()
    info.trading_mode = _TRADING_MODE.CLOSE_ONLY_MODE
    reason = _unavailable(info, datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    assert reason is not None and "CLOSE_ONLY_MODE" in reason


def test_converter_unavailable_outside_schedule():
    # schedule tinh bang giay ke tu CHU NHAT 00:00 theo scheduleTimeZone (proto dong 197-198).
    # Khung mo: thu Hai 00:00 -> thu Sau 24:00 theo gio Berlin = giay 86_400 -> 518_400.
    info = _sample_us30()
    info.schedule = [(86_400, 518_400)]
    # 2026-09-19 la thu Bay -> ngoai khung.
    assert _unavailable(info, datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)) == "outside_symbol_trading_hours"
    # 2026-09-18 la thu Sau 12:00 UTC (14:00 Berlin) -> trong khung.
    assert _unavailable(info, datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)) is None


def test_converter_unavailable_on_recurring_holiday():
    # holidayDate = so ngay ke tu 1/1/1970. 20_454 ngay = 2026-01-01 (Tet Duong lich).
    info = _sample_us30()
    info.holidays = [_Holiday(
        name="New Year", time_zone="Europe/Berlin", date_days=20_454,
        is_recurring=True, start_second=None, end_second=None,
    )]
    # Nam KHAC nhung cung ngay/thang: isRecurring=True phai van tinh la nghi.
    reason = _unavailable(info, datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc))
    assert reason == "holiday_New Year"
    assert _unavailable(info, datetime(2027, 1, 2, 12, 0, tzinfo=timezone.utc)) is None


def test_converter_conversion_rate_same_currency():
    converter = SymbolConverter.__new__(SymbolConverter)
    assert converter.get_live_conversion_rate(USD, USD) == 1.0


def test_converter_conversion_rate_single_hop_inverse():
    # Chain la 1 symbol "USD/JPY" (base=USD, quote=JPY). Doi JPY -> USD nghia la di NGUOC chieu
    # cap nay (base khac from_asset_id=JPY) => phai CHIA, khong nhan.
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._conversion_chains = {(JPY, USD): [_ConversionLeg(symbol_id=1, base_asset_id=USD, quote_asset_id=JPY)]}
    converter._connection = _FakeConnection({1: (15_000_000, 15_002_000)})  # 150.00000 / 150.02000 x 100000
    rate = converter.get_live_conversion_rate(JPY, USD)
    assert abs(rate - (1.0 / 150.0)) < 1e-9


def test_converter_conversion_rate_multi_hop_walks_chain_in_order():
    # GBP -> JPY qua 2 chang: GBP/USD (base=GBP,quote=USD) roi USD/JPY (base=USD,quote=JPY).
    # Dung cong thuc CHINH THUC (help.ctrader.com/open-api/symbol-rate-conversion/): base khop
    # asset hien tai thi NHAN, khong khop thi CHIA.
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._conversion_chains = {
        (GBP, JPY): [
            _ConversionLeg(symbol_id=1, base_asset_id=GBP, quote_asset_id=USD),
            _ConversionLeg(symbol_id=2, base_asset_id=USD, quote_asset_id=JPY),
        ]
    }
    converter._connection = _FakeConnection({1: (125_000, 125_020), 2: (15_000_000, 15_002_000)})
    rate = converter.get_live_conversion_rate(GBP, JPY)
    assert abs(rate - (1.25 * 150.0)) < 1e-9


def test_converter_conversion_rate_uses_latest_price_not_a_frozen_one():
    # Bang chung truc tiep cho yeu cau "khong duoc dung 1 ty gia co dinh": goi 2 lan lien tiep voi
    # 2 gia raw khac nhau tra ve tu _FakeConnection phai ra 2 rate khac nhau tuong ung - tuc ham
    # nay luon doc lai gia moi nhat, khong cache/tinh 1 lan roi tai su dung.
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._conversion_chains = {(JPY, USD): [_ConversionLeg(symbol_id=1, base_asset_id=USD, quote_asset_id=JPY)]}
    connection = _FakeConnection({1: (15_000_000, 15_002_000)})
    converter._connection = connection

    rate_at_sizing_time = converter.get_live_conversion_rate(JPY, USD)

    connection._spots_raw[1] = (14_500_000, 14_502_000)  # gia da doi luc lenh khop, tick khac
    rate_at_fill_time = converter.get_live_conversion_rate(JPY, USD)

    assert rate_at_sizing_time != rate_at_fill_time
    assert abs(rate_at_fill_time - (1.0 / 145.0)) < 1e-9


def test_wait_for_does_not_lose_unmatched_messages():
    # Loi that truoc day: wait_for() vut bo moi message khong dung kieu dang cho -> 1 ORDER_FILLED
    # bay ve dung luc sizing.py dang cho ProtoOATraderRes se mat vinh vien, state/exposure khong
    # bao gio biet lenh da khop. Gio chung phai duoc day lai hang doi va receive() tra ra sau.
    from engine.connection import Connection, IncomingMessage

    class _Msg:
        pass

    class _Wanted:
        pass

    connection = Connection.__new__(Connection)
    connection._deferred = []
    unrelated = IncomingMessage(payload_type=2126, message=_Msg(), raw_payload=b"", client_msg_id=None)
    wanted = IncomingMessage(payload_type=2122, message=_Wanted(), raw_payload=b"", client_msg_id="abc")
    inbox = [unrelated, wanted]

    connection.receive = lambda: inbox.pop(0) if inbox else None

    result = connection.wait_for(_Wanted, timeout_seconds=5.0)
    assert isinstance(result, _Wanted)
    # message khong khop PHAI con nguyen trong hang doi, khong bi vut
    assert len(connection._deferred) == 1
    assert connection._deferred[0] is unrelated


def test_wait_for_predicate_rejects_same_type_from_another_request():
    # Loi that truoc day: chi khop theo KIEU message -> 1 ExecutionEvent cua lenh CU bay ve bi nham
    # la phan hoi cua lenh vua gui, khien lenh vua gui bi ghi nham thanh "rejected".
    from engine.connection import Connection, IncomingMessage

    class _Event:
        def __init__(self, tag):
            self.tag = tag

    connection = Connection.__new__(Connection)
    connection._deferred = []
    other = IncomingMessage(payload_type=2126, message=_Event("cua-lenh-cu"), raw_payload=b"", client_msg_id="other")
    mine = IncomingMessage(payload_type=2126, message=_Event("cua-toi"), raw_payload=b"", client_msg_id="mine")
    inbox = [other, mine]
    connection.receive = lambda: inbox.pop(0) if inbox else None

    result = connection.wait_for(
        _Event, predicate=lambda incoming: incoming.client_msg_id == "mine", timeout_seconds=5.0
    )
    assert result.tag == "cua-toi"
    assert connection._deferred[0] is other


def test_listener_ignores_events_from_other_labels():
    # Account co the co lenh tay cua nguoi dung / chien luoc khac. Khong loc theo label thi chung
    # se bi nhet vao ExposureBook cua combo va lam sai moi quyet dinh vao lenh sau do.
    # Bot goc loc dung nhu vay: if (position.Label != Label ...) return;
    from engine.listener import _belongs_to_us

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine", "proto", "generated"))
    import OpenApiMessages_pb2 as messages

    event = messages.ProtoOAExecutionEvent()
    event.position.tradeData.label = "manual-trade-cua-nguoi-dung"
    assert _belongs_to_us(event, "combo") is False

    ours = messages.ProtoOAExecutionEvent()
    ours.position.tradeData.label = "combo"
    assert _belongs_to_us(ours, "combo") is True


def test_execution_event_with_close_detail_routes_to_closed_not_filled():
    # Bug that (2026-09-25, BTCUSD+GOLD dinh SL): cTrader bao executionType=ORDER_FILLED cho CA 2
    # truong hop mo lenh moi VA dong lenh do SL/TP tu kich hoat - phai uu tien kiem
    # deal.closePositionDetail TRUOC, khong duoc de nhanh ORDER_FILLED chan mat.
    from engine.listener import _handle_execution_event

    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}

    book = ExposureBook()
    book.on_position_opened(position_id=999, symbol_id=info.symbol_id, trade_side=1, volume=1000)

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:US30:X", "US30", "combo", risk_amount=50.0)
            store.mark_accepted("combo:H1:US30:X", order_id=111)
            store.mark_filled(111, position_id=999)

            event = messages.ProtoOAExecutionEvent()
            event.executionType = model_messages.ProtoOAExecutionType.ORDER_FILLED  # de gay hieu lam
            event.position.positionId = 999
            event.position.tradeData.symbolId = info.symbol_id
            event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.BUY
            event.position.tradeData.label = "combo"
            event.deal.closePositionDetail.entryPrice = 100.0
            event.deal.closePositionDetail.grossProfit = 500
            event.deal.executionPrice = 101.0

            _handle_execution_event(event, converter=converter, exposure_book=book, state=store)

            # Phai duoc xu ly nhu DONG vi the (mark_closed + goi ra khoi book), KHONG phai nhu mo moi.
            assert book.for_symbol(info.symbol_id) == []
            record = store.get_by_order_id(111)
            assert record.status == "CLOSED"
        finally:
            store.close()


def test_position_closed_partial_deal_does_not_remove_exposure_or_mark_closed():
    # Bug that (BTCUSD 2026-09-27): 1 position dong qua NHIEU deal partial-close trong vai giay,
    # moi deal la 1 ProtoOAExecutionEvent rieng voi remaining volume > 0 (chua ve 0). Deal dau tien
    # DA bi xoa khoi ExposureBook + mark CLOSED ngay - trong khi vi the THAT ra van con mo mot phan.
    # Phai CHI finalize (mark_closed/xoa exposure/bao Telegram) o deal CUOI (remaining volume == 0).
    from engine.listener import _handle_execution_event

    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}

    book = ExposureBook()
    book.on_position_opened(position_id=999, symbol_id=info.symbol_id, trade_side=1, volume=1680)

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:US30:X", "US30", "combo", risk_amount=50.0)
            store.mark_accepted("combo:H1:US30:X", order_id=111)
            store.mark_filled(111, position_id=999)

            def _close_event(remaining_volume: int, gross_profit: int):
                event = messages.ProtoOAExecutionEvent()
                event.position.positionId = 999
                event.position.tradeData.symbolId = info.symbol_id
                event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.BUY
                event.position.tradeData.label = "combo"
                event.position.tradeData.volume = remaining_volume
                event.deal.closePositionDetail.entryPrice = 100.0
                event.deal.closePositionDetail.grossProfit = gross_profit
                event.deal.executionPrice = 101.0
                return event

            # Deal 1/2: con 118 (1.18 lot) chua ve 0 -> CHUA duoc coi la dong xong.
            _handle_execution_event(_close_event(118, 500), converter=converter, exposure_book=book, state=store)
            assert book.for_symbol(info.symbol_id) != []  # van con trong book
            assert store.get_by_order_id(111).status == "FILLED"  # chua bi ghi de thanh CLOSED
            assert store.get_net_profit(999) == 500.0  # moneyDigits khong dat -> scale=1, cong don deal nay

            # Deal 2/2: ve 0 -> DAY la deal cuoi, gio moi finalize.
            _handle_execution_event(_close_event(0, 300), converter=converter, exposure_book=book, state=store)
            assert book.for_symbol(info.symbol_id) == []
            assert store.get_by_order_id(111).status == "CLOSED"
            # Tong net_profit phai la CA 2 deal cong lai (500 + 300), khong phai chi deal cuoi.
            assert store.get_net_profit(999) == 800.0
        finally:
            store.close()


def test_converter_find_by_id_returns_none_instead_of_raising():
    # listener.py phai dung find_by_id: mot event cua symbol OF khong load (lenh tay tren symbol
    # khac) ma raise KeyError se lam sap ca tien trinh chay 24/7.
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._symbols = {"US30": _sample_us30()}
    assert converter.find_by_id(1132) is not None
    assert converter.find_by_id(999999) is None


def test_exposure_book_add_remove():
    book = ExposureBook()
    book.on_position_opened(position_id=1, symbol_id=1132, trade_side=2, volume=1500)
    assert len(book.for_symbol(1132)) == 1
    assert book.for_symbol(1132)[0].trade_side == 2

    book.on_position_closed(1)
    assert len(book.for_symbol(1132)) == 0


def test_reconcile_exposure_blocks_new_symbol_at_cap():
    # 4 symbol khac nhau da mo du (trung = 1000..1003), gio 1 symbol THU 5 hoan toan moi co tin
    # hieu -> phai bi chan boi tran max_concurrent_trades=4, KHONG duoc goi orders.close/cancel gi
    # ca vi symbol nay chua co exposure nao (khong phai dao chieu).
    book = ExposureBook()
    for i in range(4):
        book.on_position_opened(position_id=100 + i, symbol_id=1000 + i, trade_side=1, volume=1000)

    allowed = reconcile_exposure(
        book=book, connection=None, ctid_trader_account_id=1,
        client_order_id="new-symbol", symbol_id=9999, symbol_name="NEWSYM",
        new_trade_side=1, max_concurrent_trades=4,
    )
    assert allowed is False


def test_reconcile_exposure_allows_reversal_even_at_cap(monkeypatch):
    # Da du 4 lenh (trung dung max_concurrent_trades=4), nhung tin hieu NGUOC HUONG tren 1 symbol
    # DA CO SAN trong 4 lenh do (dao chieu) - KHONG duoc bi tran chan, vi day la thay cho, khong
    # phai mo them "cho" moi.
    book = ExposureBook()
    book.on_position_opened(position_id=200, symbol_id=1132, trade_side=2, volume=1500)  # SELL
    for i in range(3):
        book.on_position_opened(position_id=300 + i, symbol_id=2000 + i, trade_side=1, volume=1000)
    assert book.total_count() == 4

    monkeypatch.setattr(orders_module, "close_position", lambda *a, **k: True)

    allowed = reconcile_exposure(
        book=book, connection=None, ctid_trader_account_id=1,
        client_order_id="reversal", symbol_id=1132, symbol_name="US30",
        new_trade_side=1, max_concurrent_trades=4,  # BUY - nguoc voi SELL dang mo
    )
    assert allowed is True
    # Bug that: dong xong khong xoa "bong ma" khoi book, lam total_count sai (van la 4 thay vi 3)
    # va lan dao chieu SAU tren dung symbol nay bi chan nham vi tuong con SELL cu dang mo.
    assert book.total_count() == 3
    assert book.for_symbol(1132) == []


def test_reconcile_exposure_removes_ghost_on_cancelled_pending_order(monkeypatch):
    # Giong bug tren nhung voi lenh CHO (pending order) thay vi vi the da khop.
    book = ExposureBook()
    book.on_pending_order_opened(order_id=500, symbol_id=1132, trade_side=2, volume=1000)  # SELL cho
    monkeypatch.setattr(orders_module, "cancel_order", lambda *a, **k: True)

    allowed = reconcile_exposure(
        book=book, connection=None, ctid_trader_account_id=1,
        client_order_id="reversal-pending", symbol_id=1132, symbol_name="US30",
        new_trade_side=1, max_concurrent_trades=4,  # BUY - nguoc voi SELL dang cho
    )
    assert allowed is True
    assert book.total_count() == 0
    assert book.for_symbol(1132) == []


def test_log_event_formats_structured_key_value_line(caplog):
    # Dinh dang moi (hoc theo dp_program/log.py): component=x event=Y risk=Z pid=N field=value...
    # Cung kiem tra chuan hoa: event/risk truyen thuong -> UPPER, component truyen hoa -> lower.
    test_logger = logging.getLogger("test.log_event")
    with caplog.at_level(logging.INFO, logger="test.log_event"):
        log_event(test_logger, "INFO", "order_accepted", "none", component="ORDERS",
                   client_order_id="x", order_id=999, price=51772.0)
    assert "event=ORDER_ACCEPTED" in caplog.text
    assert "risk=NONE" in caplog.text
    assert "component=orders" in caplog.text
    assert "client_order_id=x" in caplog.text
    assert "order_id=999" in caplog.text


def test_log_event_rejects_invalid_field_name():
    # Bat loi ngay luc goi thay vi sinh ra 1 dong log khong parse duoc.
    with pytest.raises(ValueError):
        log_event(logging.getLogger("test.log_event"), "INFO", "X", "NONE", component="c", **{"Bad-Field": 1})


def test_log_event_rejects_unknown_risk():
    with pytest.raises(ValueError):
        log_event(logging.getLogger("test.log_event"), "INFO", "X", "SEVERE", component="c")


def test_telegram_notify_skips_internal_reasoning_events(monkeypatch):
    # 2026-09-27: PLAN_COMPUTED/FX_CONVERSION_APPLIED/EXPOSURE_DECISION bi bo khoi Telegram (nguon
    # nhieu chinh - fire tren MOI tin hieu ke ca khong co gi xay ra). Van con day du trong file log,
    # chi khong duoc goi sang API Telegram nua.
    from engine import telegram

    calls = []
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *a, **k: calls.append(1))
    telegram.configure("fake-token", "fake-chat-id")
    try:
        telegram.notify("PLAN_COMPUTED", "x")
        telegram.notify("FX_CONVERSION_APPLIED", "x")
        telegram.notify("EXPOSURE_DECISION", "x")
        assert calls == []
        telegram.notify("ORDER_FILLED", "x")
        assert calls == [1]
    finally:
        telegram.configure("", "")


def test_telegram_notify_uses_html_parse_mode(monkeypatch):
    from engine import telegram

    captured = {}

    def _fake_urlopen(request, timeout=5):
        captured["data"] = request.data
        class _Resp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _Resp()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", _fake_urlopen)
    telegram.configure("fake-token", "fake-chat-id")
    try:
        telegram.notify("ORDER_FILLED", "<b>hello</b>")
        assert b"parse_mode=HTML" in captured["data"]
    finally:
        telegram.configure("", "")


def test_translate_reason_known_and_unknown_codes():
    from engine import telegram
    assert telegram.translate_reason("expired_good_till_date") == "expired before it could fill"
    assert telegram.translate_reason("some_future_reason") == "some_future_reason"  # khong co -> giu nguyen


def test_side_label_buy_and_sell():
    from engine import telegram
    assert telegram.side_label(model_messages.ProtoOATradeSide.BUY) == "BUY"
    assert telegram.side_label(model_messages.ProtoOATradeSide.SELL) == "SELL"


def test_escape_html_escapes_special_characters():
    from engine import telegram
    assert telegram.escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_state_accumulate_and_sum_net_profit():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:US30:A", "US30", "combo", risk_amount=50.0)
            store.mark_accepted("combo:H1:US30:A", order_id=1)
            store.mark_filled(1, position_id=100)

            before = datetime.now(timezone.utc).isoformat()
            store.accumulate_net_profit(100, 30.0)
            store.accumulate_net_profit(100, -5.0)  # partial close lo mot phan, van cong don duoc
            store.mark_closed(100)

            assert store.get_net_profit(100) == 25.0
            assert store.sum_net_profit_since(before) == 25.0
        finally:
            store.close()


class _ScriptedConnection:
    """Gia lap send()/wait_for() cho 1 request: tra dung message da dinh san, kem clientMsgId de
    kiem tra predicate cua caller (khong can socket song)."""

    def __init__(self, reply, reply_client_msg_id="sent-1"):
        self._reply = reply
        self._reply_client_msg_id = reply_client_msg_id

    def send(self, message, client_msg_id=None):
        return "sent-1"

    def wait_for(self, *expected_classes, predicate=None, timeout_seconds=15.0):
        from engine.connection import IncomingMessage
        incoming = IncomingMessage(payload_type=0, message=self._reply, raw_payload=b"",
                                   client_msg_id=self._reply_client_msg_id)
        assert isinstance(self._reply, expected_classes)
        assert predicate is None or predicate(incoming)
        return self._reply


def test_get_unrealized_pnl_uses_server_values_scaled_by_money_digits():
    # Floating P&L lay tu SERVER (ProtoOAGetPositionUnrealizedPnLReq - khuyen nghi chinh thuc cua
    # Spotware), khong tu tinh tu gia spot. Gia tri tho scale bang moneyDigits cua response.
    from engine import sizing

    res = messages.ProtoOAGetPositionUnrealizedPnLRes()
    res.ctidTraderAccountId = 1
    res.moneyDigits = 2
    item = res.positionUnrealizedPnL.add()
    item.positionId = 10
    item.grossUnrealizedPnL = 12345
    item.netUnrealizedPnL = 12000  # -> 120.00
    item2 = res.positionUnrealizedPnL.add()
    item2.positionId = 11
    item2.grossUnrealizedPnL = -500
    item2.netUnrealizedPnL = -550  # -> -5.50

    result = sizing.get_unrealized_pnl(_ScriptedConnection(res), ctid_trader_account_id=1)
    assert result == {10: 120.0, 11: -5.5}


def test_get_unrealized_pnl_raises_on_error_response():
    from engine import sizing

    err = messages.ProtoOAErrorRes()
    err.errorCode = "SOME_ERROR"
    err.description = "not available"
    with pytest.raises(RuntimeError, match="SOME_ERROR"):
        sizing.get_unrealized_pnl(_ScriptedConnection(err), ctid_trader_account_id=1)


def _snapshot_config():
    class _CTrader:
        ctid_trader_account_id = 1

    class _Cfg:
        ctrader = _CTrader()

    return _Cfg()


def test_account_snapshot_counts_only_own_label_and_uses_lot_size(monkeypatch):
    from engine import sizing

    converter = SymbolConverter.__new__(SymbolConverter)
    gold = _sample_us30()
    gold.symbol_id, gold.name, gold.og_name, gold.lot_size = 41, "XAUUSD", "GOLD", 10_000  # 1 lot = 100 unit
    converter._symbols = {"XAUUSD": gold}

    reconcile = messages.ProtoOAReconcileRes()
    ours = reconcile.position.add()
    ours.positionId = 10
    ours.tradeData.symbolId = 41
    ours.tradeData.tradeSide = model_messages.ProtoOATradeSide.SELL
    ours.tradeData.volume = 5_000  # 50 unit = 0.50 lot (KHONG phai 50.00 "lot" nhu volume/100)
    ours.tradeData.label = "combo"
    manual = reconcile.position.add()  # lenh tay tren cung account - KHONG duoc tinh
    manual.positionId = 99
    manual.tradeData.symbolId = 41
    manual.tradeData.tradeSide = model_messages.ProtoOATradeSide.BUY
    manual.tradeData.volume = 100_000
    manual.tradeData.label = "manual"

    monkeypatch.setattr(sizing, "get_balance", lambda *a, **k: 1000.0)
    monkeypatch.setattr(sizing, "get_unrealized_pnl", lambda *a, **k: {10: -12.5, 99: 999.0})
    monkeypatch.setattr(main, "_reconcile", lambda *a, **k: reconcile)

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            text = main._build_account_snapshot(None, converter, _snapshot_config(), store, "2000-01-01")
        finally:
            store.close()

    assert "Open: 1 position(s)" in text
    assert "GOLD SELL 0.50 lot (floating -$12.50)" in text
    assert "Floating: -$12.50" in text  # tong chi gom vi the cua label combo, bo qua 999 cua lenh tay
    assert "Balance: $1000.00" in text


def test_account_snapshot_failure_never_propagates(monkeypatch):
    # Bao cao goi mang (moi request cho toi 15s). TimeoutError la lop con cua OSError - neu lan ra,
    # main() coi la CONNECTION_LOST va khoi dong lai ca engine dat lenh. Phai nuot, chi log.
    def _boom(*a, **k):
        raise TimeoutError("Expected response not received within 15.0s")

    monkeypatch.setattr(main, "_build_account_snapshot", _boom)
    assert main._send_account_snapshot(None, None, None, None, "2000-01-01") is False


def test_position_closed_final_when_status_closed_even_if_volume_nonzero():
    # 2 dieu kien doc lap: positionStatus=CLOSED cung du de chot so, khong chi dua vao volume==0.
    from engine.listener import _handle_execution_event

    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}
    book = ExposureBook()
    book.on_position_opened(position_id=777, symbol_id=info.symbol_id, trade_side=1, volume=100)

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:US30:Z", "US30", "combo", risk_amount=50.0)
            store.mark_accepted("combo:H1:US30:Z", order_id=5)
            store.mark_filled(5, position_id=777)

            event = messages.ProtoOAExecutionEvent()
            event.position.positionId = 777
            event.position.tradeData.symbolId = info.symbol_id
            event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.BUY
            event.position.tradeData.label = "combo"
            event.position.tradeData.volume = 100
            event.position.positionStatus = model_messages.ProtoOAPositionStatus.POSITION_STATUS_CLOSED
            event.deal.closePositionDetail.entryPrice = 100.0
            event.deal.executionPrice = 101.0

            _handle_execution_event(event, converter=converter, exposure_book=book, state=store)
            assert book.for_symbol(info.symbol_id) == []
            assert store.get_by_order_id(5).status == "CLOSED"
        finally:
            store.close()


def test_broker_to_symbol_map_loads_every_distinct_broker_symbol():
    # converter.load() phai nhan DU moi broker_symbol khac nhau (tuong duong symbol_names() cu), ke ca
    # khi 2 instrument trung ten OG - khoa theo ten broker, khong theo ten OG.
    from configuration import ComboConfig, ComboInstrument

    combo = ComboConfig(pubsub_channel="x", instruments=[
        ComboInstrument(symbol="US30", timeframe="H1", broker_symbol="#US30"),
        ComboInstrument(symbol="US30", timeframe="H4", broker_symbol="US30.cash"),
        ComboInstrument(symbol="GOLD", timeframe="H1", broker_symbol="XAUUSD"),
    ])
    assert set(combo.broker_to_symbol_map()) == set(combo.symbol_names())
    assert combo.broker_to_symbol_map()["XAUUSD"] == "GOLD"


def _framed(message) -> bytes:
    """Dong goi 1 message dung framing that cua cTrader (4 byte do dai + ProtoMessage)."""
    import struct
    import OpenApiCommonMessages_pb2 as common_messages
    envelope = common_messages.ProtoMessage()
    envelope.payloadType = int(message.payloadType)
    envelope.payload = message.SerializeToString()
    data = envelope.SerializeToString()
    return struct.pack(">I", len(data)) + data


def _bare_connection():
    from engine.connection import Connection
    connection = Connection.__new__(Connection)
    connection._recv_buffer = b""
    connection._deferred = []
    connection._latest_spot_raw = {}
    connection._spot_event_count = 0
    return connection


def _spot(symbol_id, bid=None, ask=None):
    event = messages.ProtoOASpotEvent()
    event.ctidTraderAccountId = 1
    event.symbolId = symbol_id
    if bid is not None:
        event.bid = bid
    if ask is not None:
        event.ask = ask
    return event


def test_spot_event_missing_a_side_keeps_previous_value():
    # Tai lieu chinh thuc: "As the bid and ask fields are optional, you may not necessarily see
    # ProtoOASpotEvent messages where both are specified" (help.ctrader.com/open-api/symbol-data/).
    # Ban cu ghi thang (bid, ask) -> field vang thanh 0 -> ty gia tinh lot = 0 / chia 0.
    connection = _bare_connection()
    connection._recv_buffer = (
        _framed(_spot(7, bid=15_000_000, ask=15_002_000))
        + _framed(_spot(7, ask=15_003_000))  # chi co ask
        + _framed(_spot(7, bid=14_999_000))  # chi co bid
    )
    connection._extract_one_message()
    assert connection.get_latest_spot(7) == (15_000_000, 15_002_000)
    connection._extract_one_message()
    assert connection.get_latest_spot(7) == (15_000_000, 15_003_000)  # bid GIU NGUYEN, khong ve 0
    connection._extract_one_message()
    assert connection.get_latest_spot(7) == (14_999_000, 15_003_000)


def test_spot_events_are_counted_not_logged_per_tick(caplog):
    connection = _bare_connection()
    connection._recv_buffer = b"".join(_framed(_spot(7, bid=1, ask=2)) for _ in range(50))
    with caplog.at_level(logging.INFO):
        for _ in range(50):
            connection._extract_one_message()
    assert connection.spot_event_count == 50
    assert "event=MESSAGE_RECEIVED" not in caplog.text  # khong 1 dong log/tick


def test_non_spot_messages_are_still_logged(caplog):
    import OpenApiCommonMessages_pb2 as common_messages
    connection = _bare_connection()
    connection._recv_buffer = _framed(common_messages.ProtoHeartbeatEvent())
    with caplog.at_level(logging.INFO):
        connection._extract_one_message()
    assert "event=MESSAGE_RECEIVED" in caplog.text
    assert "ProtoHeartbeatEvent" in caplog.text


def test_conversion_rate_raises_clear_error_when_bid_unknown():
    # Chua co bid (moi nhan event chi co ask) -> loi RO RANG, khong chia 0 va khong ra lot sai.
    converter = SymbolConverter.__new__(SymbolConverter)
    converter._conversion_chains = {(JPY, USD): [_ConversionLeg(symbol_id=1, base_asset_id=USD, quote_asset_id=JPY)]}
    converter._connection = _FakeConnection({1: (None, 15_002_000)})
    with pytest.raises(RuntimeError, match="No valid bid"):
        converter.get_live_conversion_rate(JPY, USD)


def test_wait_until_spot_available_waits_until_a_bid_arrives():
    # Event dau tien chi co ask -> chua duoc coi la "co gia"; phai cho tiep toi khi co bid.
    class _Conn:
        def __init__(self):
            self.cache = {9: (None, 200)}
            self.waits = 0

        def get_latest_spot(self, symbol_id):
            return self.cache.get(symbol_id)

        def wait_for(self, *classes, predicate=None, timeout_seconds=15.0):
            self.waits += 1
            self.cache[9] = (150, 200)  # tick tiep theo mang bid
            return None

    converter = SymbolConverter.__new__(SymbolConverter)
    converter._connection = _Conn()
    converter._wait_until_spot_available(9)
    assert converter._connection.waits == 1
    assert converter._connection.get_latest_spot(9) == (150, 200)


class _QueueConnection:
    def __init__(self, items):
        self.items = list(items)

    def receive(self):
        return self.items.pop(0) if self.items else None


def test_poll_once_drains_backlog_so_execution_event_is_not_delayed(monkeypatch):
    # Ban cu: 1 message/luot (~1-2s/luot) -> 1000 tick dung truoc 1 ORDER_FILLED lam no bi tre
    # ~1000 luot. Gio: moi luot xu ly het message co san, toi da _MAX_MESSAGES_PER_POLL.
    from engine import listener
    from engine.connection import IncomingMessage

    handled = []
    monkeypatch.setattr(listener, "_dispatch", lambda incoming, **k: handled.append(incoming.message))
    backlog = [IncomingMessage(0, f"spot-{i}", b"", None) for i in range(1000)]
    backlog.append(IncomingMessage(0, "ORDER_FILLED", b"", None))
    conn = _QueueConnection(backlog)

    calls = 0
    while "ORDER_FILLED" not in handled:
        before = len(handled)
        listener.poll_once(connection=conn, converter=None, exposure_book=None, state=None, label="combo")
        calls += 1
        assert len(handled) - before <= listener._MAX_MESSAGES_PER_POLL
    assert calls == 3  # 500 + 500 + 1 — thay vi 1001 luot


def test_poll_once_stops_at_time_budget(monkeypatch):
    # 1 message xu ly lau (vd gui Telegram dong bo) khong duoc giu vong lap qua ngan sach thoi gian.
    from engine import listener
    from engine.connection import IncomingMessage

    clock = {"t": 0.0}

    class _FakeTime:
        @staticmethod
        def monotonic():
            return clock["t"]

    def _slow_dispatch(incoming, **k):
        clock["t"] += 0.2

    monkeypatch.setattr(listener, "time", _FakeTime)
    monkeypatch.setattr(listener, "_dispatch", _slow_dispatch)
    conn = _QueueConnection([IncomingMessage(0, i, b"", None) for i in range(10)])
    listener.poll_once(connection=conn, converter=None, exposure_book=None, state=None, label="combo")
    assert 10 - len(conn.items) == 3  # 0.2 + 0.2 + 0.2 >= 0.5s -> dung sau message thu 3


def test_net_profit_adds_signed_commission_real_gold_close(monkeypatch):
    # So THAT (GOLD 28/9, position 154587385): gross 742.39, commission -5.04, balance 98278.74 ->
    # 99016.09 (+737.35). Cong thuc cu gross-commission ra 747.43 (bao lai cao hon thuc te 10.08).
    from engine import listener, telegram

    sent = []
    monkeypatch.setattr(telegram, "notify", lambda event, text: sent.append((event, text)))
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}
    book = ExposureBook()
    book.on_position_opened(position_id=154587385, symbol_id=info.symbol_id, trade_side=2, volume=1700)

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:GOLD:X", "XAUUSD", "combo", risk_amount=493.92)
            store.mark_accepted("combo:H1:GOLD:X", order_id=248943476)
            store.mark_filled(248943476, position_id=154587385)

            event = messages.ProtoOAExecutionEvent()
            event.position.positionId = 154587385
            event.position.tradeData.symbolId = info.symbol_id
            event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.SELL
            event.position.tradeData.label = "combo"
            event.position.tradeData.volume = 0
            detail = event.deal.closePositionDetail
            detail.entryPrice = 4256.42
            detail.grossProfit = 74239
            detail.commission = -504
            detail.swap = 0
            detail.balance = 9901609
            detail.moneyDigits = 2
            event.deal.executionPrice = 4212.75

            listener._handle_execution_event(event, converter=converter, exposure_book=book, state=store)
            assert abs(store.get_net_profit(154587385) - 737.35) < 1e-9
            assert any("Net P&amp;L: $737.35" in text for _, text in sent)
        finally:
            store.close()


def test_net_profit_adds_signed_swap_real_of11_close(monkeypatch):
    # So THAT (OF11 Pepperstone 26/9, position 243805847): gross -50.16, swap +1.66, commission 0;
    # so du 10000.00 (budget 0.5% = 50 luc khop) -> 9951.50 = -50.16 + 1.66. Ban cu bo qua swap.
    from engine import listener, telegram

    monkeypatch.setattr(telegram, "notify", lambda *a, **k: None)
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("ma_cross:M30:BTCUSD:X", "BTCUSD", "ma_cross", risk_amount=50.0)
            store.mark_accepted("ma_cross:M30:BTCUSD:X", order_id=363268775)
            store.mark_filled(363268775, position_id=243805847)
            event = messages.ProtoOAExecutionEvent()
            event.position.positionId = 243805847
            event.position.tradeData.symbolId = info.symbol_id
            event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.SELL
            event.position.tradeData.volume = 0
            detail = event.deal.closePositionDetail
            detail.entryPrice = 83990.77
            detail.grossProfit = -5016
            detail.swap = 166
            detail.commission = 0
            detail.balance = 995150
            detail.moneyDigits = 2
            listener._handle_execution_event(event, converter=converter, exposure_book=ExposureBook(), state=store)
            assert abs(store.get_net_profit(243805847) - (-48.50)) < 1e-9
        finally:
            store.close()


def _fill_event(info, *, position_sl=None, order_sl=None, order_relative_sl=None):
    # So THAT (BTCUSD 28/9): SELL STOP trigger 84045.5, khop 84038.55, SL 84605.47, volume 88 (0.88 unit)
    event = messages.ProtoOAExecutionEvent()
    event.executionType = model_messages.ProtoOAExecutionType.ORDER_FILLED
    event.order.orderId = 248943483
    event.order.clientOrderId = "combo:H1:BTCUSD:X"
    event.order.stopPrice = 84045.5
    if order_sl is not None:
        event.order.stopLoss = order_sl
    if order_relative_sl is not None:
        event.order.relativeStopLoss = order_relative_sl
    event.position.positionId = 154587389
    event.position.tradeData.symbolId = info.symbol_id
    event.position.tradeData.tradeSide = model_messages.ProtoOATradeSide.SELL
    event.position.tradeData.label = "combo"
    event.position.tradeData.volume = 88
    event.position.price = 84038.55
    if position_sl is not None:
        event.position.stopLoss = position_sl
    return event


def _run_fill(monkeypatch, **sl):
    from engine import listener, telegram

    sent, logged = [], []
    monkeypatch.setattr(telegram, "notify", lambda event, text: sent.append(text))
    converter = SymbolConverter.__new__(SymbolConverter)
    info = _sample_us30()
    converter._symbols = {"US30": info}
    converter._deposit_asset_id = info.quote_asset_id  # cung tien te -> ty gia 1.0
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(os.path.join(tmp, "test.sqlite"))
        try:
            store.mark_sending("combo:H1:BTCUSD:X", "BITCOIN", "combo", risk_amount=493.92)
            store.mark_accepted("combo:H1:BTCUSD:X", order_id=248943483)
            listener._handle_execution_event(_fill_event(info, **sl), converter=converter,
                                             exposure_book=ExposureBook(), state=store)
        finally:
            store.close()
    return sent[-1]


def test_fill_real_risk_uses_order_stop_loss_when_position_lacks_it(monkeypatch):
    # Log that: position.stopLoss VANG o ca 4 lan khop -> ban cu luon bao "Real risk: $0.00".
    text = _run_fill(monkeypatch, order_sl=84605.47)
    assert "Real risk: $498.89" in text  # 0.88 x (84605.47 - 84038.55) = 498.8896


def test_fill_real_risk_uses_relative_stop_loss_for_market_orders(monkeypatch):
    text = _run_fill(monkeypatch, order_relative_sl=56_692_000)  # 566.92 x 100000
    assert "Real risk: $498.89" in text


def test_fill_real_risk_prefers_position_stop_loss_when_present(monkeypatch):
    text = _run_fill(monkeypatch, position_sl=84605.47, order_sl=99999.0)
    assert "Real risk: $498.89" in text


def test_fill_without_any_stop_loss_reports_na_not_zero(monkeypatch):
    text = _run_fill(monkeypatch)
    assert "Real risk: n/a" in text
    assert "$0.00" not in text


def test_safe_error_redacts_secret_value():
    # Quan trong: config.yaml gio chua secret plaintext, va URL refresh token nhet client_secret
    # thang vao query string - loi mang lo URL do vao exception message phai duoc che truoc khi log.
    exc = RuntimeError("failed calling https://x?client_secret=abc123XYZ&foo=bar")
    text = safe_error(exc)
    assert "abc123XYZ" not in text
    assert "client_secret=[REDACTED]" in text
