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
        symbol_id=1132, name="US30", digits=2, pip_position=1, lot_size=100,
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


def test_safe_error_redacts_secret_value():
    # Quan trong: config.yaml gio chua secret plaintext, va URL refresh token nhet client_secret
    # thang vao query string - loi mang lo URL do vao exception message phai duoc che truoc khi log.
    exc = RuntimeError("failed calling https://x?client_secret=abc123XYZ&foo=bar")
    text = safe_error(exc)
    assert "abc123XYZ" not in text
    assert "client_secret=[REDACTED]" in text
