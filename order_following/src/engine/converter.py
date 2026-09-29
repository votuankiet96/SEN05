"""Từ điển instrument: symbolId, digits, lotSize, min/max/step volume — và các hàm quy đổi (làm
tròn giá, lot→volume, tỷ giá currency sống) mà orders.py/sizing.py cần trước khi gửi lệnh.

symbolId KHÔNG portable giữa broker — cache này build lại từ đầu mỗi lần connect, không hard-code.

Quy đổi tỷ giá theo ĐÚNG hướng dẫn chính thức (help.ctrader.com/open-api/symbol-rate-conversion/):
lấy "conversion chain" qua ProtoOASymbolsForConversionReq CHỈ 1 LẦN rồi cache lại — tài liệu cảnh
báo rõ gọi lại mỗi lần cần tính có thể phải gửi request này "vài lần mỗi giây". Giá SỐNG (bid/ask)
thì luôn lấy mới qua ProtoOASpotEvent — cái được cache là "cần đi qua symbol nào", không phải giá.
"""

import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

_PROTO_DIR = os.path.join(os.path.dirname(__file__), "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import OpenApiMessages_pb2 as messages  # noqa: E402
import OpenApiModelMessages_pb2 as model_messages  # noqa: E402

from engine.connection import Connection
from engine.log import log_event

_LOGGER = logging.getLogger(__name__)

_PRICE_SCALE = 100_000  # bid/ask trong ProtoOASpotEvent: "1/100000 of unit of a price" (xác nhận từ proto gốc)
_VOLUME_SCALE = 100  # mọi field volume: "0.01 of a unit" (xác nhận từ proto gốc, OpenApiMessages.proto:89)


@dataclass
class _Holiday:
    name: str
    time_zone: str
    date_days: int  # số ngày kể từ 1/1/1970 (proto: OpenApiModelMessages.proto:698)
    is_recurring: bool
    start_second: Optional[int]  # giây kể từ 00:00 ngày nghỉ; None = nghỉ trọn ngày
    end_second: Optional[int]


@dataclass
class SymbolInfo:
    symbol_id: int
    name: str
    og_name: str  # tên OG dùng trong Redis (vd "US30") — chỉ để HIỂN THỊ cho người vận hành trong
    # Discord/log dễ đọc; mọi tra cứu/lệnh thật vẫn phải qua `name` (tên broker), không dùng field này.
    digits: int
    pip_position: int
    lot_size: int
    min_volume: int
    max_volume: int
    step_volume: int
    quote_asset_id: int
    trading_mode: int
    schedule_time_zone: str  # rỗng nếu broker không khai — khi đó bỏ qua kiểm giờ, xem unavailable_reason
    schedule: List[Tuple[int, int]]  # (startSecond, endSecond) kể từ CHỦ NHẬT 00:00 theo schedule_time_zone
    holidays: List[_Holiday]


@dataclass
class _ConversionLeg:
    symbol_id: int
    base_asset_id: int
    quote_asset_id: int


class NoConversionPathFound(Exception):
    """cTrader trả về chain rỗng — broker không có đường quy đổi nào giữa 2 loại tiền này."""


def _is_within_holiday(holiday: _Holiday, now_utc: datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(holiday.time_zone))
    holiday_date = date(1970, 1, 1) + timedelta(days=holiday.date_days)
    if holiday.is_recurring:
        same_day = (local.month, local.day) == (holiday_date.month, holiday_date.day)
    else:
        same_day = local.date() == holiday_date
    if not same_day:
        return False
    if holiday.start_second is None or holiday.end_second is None:
        return True  # không khai giờ cụ thể = nghỉ trọn ngày
    second_of_day = local.hour * 3600 + local.minute * 60 + local.second
    return holiday.start_second <= second_of_day < holiday.end_second


class SymbolConverter:
    def __init__(self, connection: Connection, ctid_trader_account_id: int):
        self._connection = connection
        self._ctid_trader_account_id = ctid_trader_account_id
        self._symbols: Dict[str, SymbolInfo] = {}
        self._conversion_chains: Dict[Tuple[int, int], List[_ConversionLeg]] = {}
        self._deposit_asset_id: Optional[int] = None

    def load_deposit_asset_id(self) -> None:
        """Gọi 1 lần lúc khởi động — tiền tệ gốc của account không đổi giữa chừng phiên chạy, nên
        cache thẳng ở đây để listener.py (lúc lệnh khớp, có thể rất lâu sau lúc sizing.py tính)
        cũng tra được, không phải truyền qua tay từng lớp hàm."""
        if self._deposit_asset_id is not None:
            return
        req = messages.ProtoOATraderReq()
        req.ctidTraderAccountId = self._ctid_trader_account_id
        self._connection.send(req)
        res = self._connection.wait_for(messages.ProtoOATraderRes)
        self._deposit_asset_id = res.trader.depositAssetId

    def get_deposit_asset_id(self) -> int:
        if self._deposit_asset_id is None:
            raise RuntimeError("load_deposit_asset_id() was not called at startup")
        return self._deposit_asset_id

    def load(self, broker_to_og: Dict[str, str]) -> None:
        """Gọi 1 lần lúc khởi động: tra symbolId theo tên broker, rồi lấy đầy đủ spec.

        `broker_to_og`: {tên broker: tên OG} (<config chiến lược>.broker_to_symbol_map()). KHOÁ theo
        tên broker để tập symbol được load LUÔN đúng bằng mọi broker_symbol khác nhau trong config —
        tương đương tuyệt đối với bản cũ (<config chiến lược>.symbol_names()); tên OG chỉ để hiển thị.
        """
        symbol_names = set(broker_to_og)

        light_req = messages.ProtoOASymbolsListReq()
        light_req.ctidTraderAccountId = self._ctid_trader_account_id
        self._connection.send(light_req)
        light_res = self._connection.wait_for(messages.ProtoOASymbolsListRes)

        name_to_light = {light.symbolName: light for light in light_res.symbol if light.symbolName in symbol_names}
        missing = symbol_names - set(name_to_light)
        if missing:
            raise ValueError(f"Symbol(s) not found on this account: {missing}")

        by_id_req = messages.ProtoOASymbolByIdReq()
        by_id_req.ctidTraderAccountId = self._ctid_trader_account_id
        by_id_req.symbolId.extend(light.symbolId for light in name_to_light.values())
        self._connection.send(by_id_req)
        by_id_res = self._connection.wait_for(messages.ProtoOASymbolByIdRes)

        id_to_name = {light.symbolId: name for name, light in name_to_light.items()}
        for full in by_id_res.symbol:
            name = id_to_name[full.symbolId]
            self._symbols[name] = self._build_info(name, broker_to_og[name], full, name_to_light[name].quoteAssetId)
        log_event(_LOGGER, "INFO", "SYMBOLS_LOADED", "NONE", component="converter",
                  symbol_count=len(self._symbols), symbols=",".join(sorted(self._symbols)))

    def refresh_by_id(self, symbol_id: int) -> None:
        """Gọi khi nhận ProtoOASymbolChangedEvent — event chỉ báo "symbol này đã đổi", không kèm
        nội dung gì, nên phải tự hỏi lại ProtoOASymbolByIdReq. Bỏ qua symbol không thuộc OF."""
        old = self.find_by_id(symbol_id)
        if old is None:
            return
        by_id_req = messages.ProtoOASymbolByIdReq()
        by_id_req.ctidTraderAccountId = self._ctid_trader_account_id
        by_id_req.symbolId.append(symbol_id)
        self._connection.send(by_id_req)
        by_id_res = self._connection.wait_for(messages.ProtoOASymbolByIdRes)
        # quote_asset_id chỉ có ở ProtoOALightSymbol, không có trong ProtoOASymbol — giữ lại từ bản cũ.
        self._symbols[old.name] = self._build_info(old.name, old.og_name, by_id_res.symbol[0], old.quote_asset_id)
        log_event(_LOGGER, "INFO", "SYMBOL_REFRESHED", "NONE", component="converter",
                  symbol=old.name, symbol_id=symbol_id)

    def get(self, symbol_name: str) -> SymbolInfo:
        return self._symbols[symbol_name]

    def find_by_id(self, symbol_id: int) -> Optional[SymbolInfo]:
        """Tra theo symbolId, trả None thay vì raise — dùng ở listener.py, nơi có thể nhận event của
        symbol mà OF không hề load (lệnh tay của người dùng, chiến lược khác trên cùng account).
        Ở đó raise sẽ làm sập cả tiến trình đang chạy 24/7."""
        for info in self._symbols.values():
            if info.symbol_id == symbol_id:
                return info
        return None

    def unavailable_reason(self, symbol_name: str, now: Optional[datetime] = None) -> Optional[str]:
        """None nếu symbol đang giao dịch được; ngược lại trả lý do (để log rồi bỏ qua tín hiệu).

        Open API KHÔNG có RPC kiểu "symbol này giao dịch được không" (khác cAlgo có
        Symbol.MarketHours.IsOpened()) — phải tự tính từ 3 field của ProtoOASymbol: `tradingMode`,
        `schedule` (giây kể từ CHỦ NHẬT 00:00 theo `scheduleTimeZone`, proto dòng 197-198) và
        `holiday` (`holidayDate` = số ngày kể từ 1/1/1970, proto dòng 698).
        """
        info = self.get(symbol_name)
        if info.trading_mode != model_messages.ProtoOATradingMode.ENABLED:
            return f"trading_mode={model_messages.ProtoOATradingMode.Name(info.trading_mode)}"
        if not info.schedule_time_zone:
            # Broker không khai timezone -> không có căn cứ tính giờ. Cho qua thay vì đoán bừa rồi
            # chặn nhầm mọi tín hiệu; server vẫn là chốt chặn cuối nếu thị trường thật sự đóng.
            return None

        now = now or datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo(info.schedule_time_zone))
        second_of_week = (
            ((local.weekday() + 1) % 7) * 86400 + local.hour * 3600 + local.minute * 60 + local.second
        )
        if not any(start <= second_of_week < end for start, end in info.schedule):
            return "outside_symbol_trading_hours"

        for holiday in info.holidays:
            if _is_within_holiday(holiday, now):
                return f"holiday_{holiday.name}"
        return None

    def round_price(self, symbol_name: str, price: float) -> float:
        """Server từ chối chứ không tự làm tròn — bắt buộc làm trước khi gửi (xác nhận qua lỗi thật
        đã ghi nhận: "has more digits than symbol allows")."""
        return round(price, self.get(symbol_name).digits)

    def units_to_volume(self, symbol_name: str, units: float) -> int:
        """Số UNIT thật -> giá trị field `volume` của cTrader.

        Proto gốc ghi rõ mọi field volume là "0.01 of a unit (e.g. 1000 in protocol means 10.00
        units)" (OpenApiMessages.proto:89) nên hệ số quy đổi là ĐÚNG 100, KHÔNG dính tới `lotSize`.
        `lotSize` (OpenApiModelMessages.proto:144, "Lot size of the Symbol (in cents)") chỉ dùng khi
        quy đổi từ LOT — mà cả OF lẫn bot gốc đều không làm việc theo lot, chúng tính thẳng ra unit.

        Trả 0 nếu dưới `minVolume` — KHÔNG tự nâng lên mức tối thiểu, vì làm vậy là âm thầm đặt lệnh
        với rủi ro lớn hơn mức risk% đã định. Đúng hành vi bot gốc: "No order sent."
        """
        info = self.get(symbol_name)
        raw_volume = int(round(units * _VOLUME_SCALE))
        if raw_volume < info.min_volume:
            return 0
        capped = min(raw_volume, info.max_volume)
        steps = (capped - info.min_volume) // info.step_volume
        return int(info.min_volume + steps * info.step_volume)

    def ensure_conversion_chain(self, from_asset_id: int, to_asset_id: int) -> None:
        """Gọi 1 lần cho mỗi cặp (from, to) cần dùng — đúng khuyến nghị chính thức: lấy chain 1 lần
        lúc đầu, không gọi lại ProtoOASymbolsForConversionReq mỗi lần tính tỷ giá. Giá SỐNG của
        từng chặng không cache ở đây — luôn đọc trực tiếp qua connection.get_latest_spot(), nơi
        DUY NHẤT ghi nhận giá mới bất kể lúc đó ai đang wait_for() gì (xem connection.py)."""
        key = (from_asset_id, to_asset_id)
        if key in self._conversion_chains:
            return

        req = messages.ProtoOASymbolsForConversionReq()
        req.ctidTraderAccountId = self._ctid_trader_account_id
        req.firstAssetId = from_asset_id
        req.lastAssetId = to_asset_id
        self._connection.send(req)
        res = self._connection.wait_for(messages.ProtoOASymbolsForConversionRes)

        if len(res.symbol) == 0:
            raise NoConversionPathFound(f"No conversion path from asset {from_asset_id} to {to_asset_id}")

        chain = [_ConversionLeg(s.symbolId, s.baseAssetId, s.quoteAssetId) for s in res.symbol]
        self._conversion_chains[key] = chain

        for leg in chain:
            if self._connection.get_latest_spot(leg.symbol_id) is not None:
                continue
            subscribe_req = messages.ProtoOASubscribeSpotsReq()
            subscribe_req.ctidTraderAccountId = self._ctid_trader_account_id
            subscribe_req.symbolId.append(leg.symbol_id)
            self._connection.send(subscribe_req)
            self._wait_until_spot_available(leg.symbol_id)

        log_event(_LOGGER, "INFO", "CONVERSION_CHAIN_RESOLVED", "NONE", component="converter",
                  from_asset=from_asset_id, to_asset=to_asset_id, hops=len(chain))

    def get_live_conversion_rate(self, from_asset_id: int, to_asset_id: int) -> float:
        """Thuật toán CHÍNH THỨC, dịch nguyên từ ví dụ C# tại
        help.ctrader.com/open-api/symbol-rate-conversion/: duyệt từng chặng trong chain, base asset
        khớp asset hiện tại thì NHÂN giá, ngược lại thì CHIA — không phải tự nghĩ ra. Đọc giá trực
        tiếp từ connection.py mỗi lần gọi — luôn là giá mới nhất nhận được, kể cả khi nó tới đúng
        lúc nơi khác đang wait_for() một message hoàn toàn khác."""
        if from_asset_id == to_asset_id:
            return 1.0
        chain = self._conversion_chains.get((from_asset_id, to_asset_id))
        if chain is None:
            raise RuntimeError(
                f"ensure_conversion_chain({from_asset_id}, {to_asset_id}) was not called before use"
            )
        rate = 1.0
        current_asset = from_asset_id
        for leg in chain:
            raw = self._connection.get_latest_spot(leg.symbol_id)
            bid_raw = raw[0] if raw is not None else None
            # bid la field OPTIONAL (proto: `optional uint64 bid = 4`; tai lieu: "you may not
            # necessarily see ProtoOASpotEvent messages where both are specified"). Chua co bid -> bao
            # loi RO RANG (tin hieu bi bo qua + UNEXPECTED_ERROR) thay vi chia 0 / tinh ra lot sai.
            if not bid_raw:
                raise RuntimeError(f"No valid bid yet for symbolId={leg.symbol_id} — cannot convert rate")
            bid = bid_raw / _PRICE_SCALE
            if leg.base_asset_id == current_asset:
                rate *= bid
                current_asset = leg.quote_asset_id
            else:
                rate *= 1.0 / bid
                current_asset = leg.base_asset_id
        return rate

    def _wait_until_spot_available(self, symbol_id: int, timeout_seconds: float = 15.0) -> None:
        """Tài liệu chính thức xác nhận: "After successful subscription you'll receive technical
        ProtoOASpotEvent with latest price" — chờ đúng event đầu tiên của symbol này.

        Bắt buộc đi qua wait_for() chứ KHÔNG tự gọi receive() rồi bỏ kết quả: receive() có thể trả
        ra message đang nằm trong hàng đợi hoãn (vd ORDER_FILLED), bỏ đi là mất vĩnh viễn — đúng
        loại lỗi mà chính wait_for() vừa được sửa để tránh.

        Chờ tới khi có BID (thứ get_live_conversion_rate dùng), không chỉ "có event": event có thể chỉ
        mang ask (bid/ask đều optional). connection.py cập nhật cache ngay lúc bóc message, nên sau
        mỗi lần wait_for() trả về chỉ cần đọc lại cache.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            raw = self._connection.get_latest_spot(symbol_id)
            if raw is not None and raw[0]:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"No bid received for symbolId={symbol_id} within {timeout_seconds}s")
            self._connection.wait_for(
                messages.ProtoOASpotEvent,
                predicate=lambda incoming: incoming.message.symbolId == symbol_id,
                timeout_seconds=remaining,
            )

    @staticmethod
    def _build_info(name: str, og_name: str, full, quote_asset_id: int) -> SymbolInfo:
        return SymbolInfo(
            symbol_id=full.symbolId,
            name=name,
            og_name=og_name,
            digits=full.digits,
            pip_position=full.pipPosition,
            lot_size=full.lotSize,
            min_volume=full.minVolume,
            max_volume=full.maxVolume,
            step_volume=full.stepVolume,
            quote_asset_id=quote_asset_id,
            trading_mode=full.tradingMode,
            schedule_time_zone=full.scheduleTimeZone,
            schedule=[(interval.startSecond, interval.endSecond) for interval in full.schedule],
            holidays=[
                _Holiday(
                    name=holiday.name,
                    time_zone=holiday.scheduleTimeZone,
                    date_days=holiday.holidayDate,
                    is_recurring=holiday.isRecurring,
                    start_second=holiday.startSecond if holiday.HasField("startSecond") else None,
                    end_second=holiday.endSecond if holiday.HasField("endSecond") else None,
                )
                for holiday in full.holiday
            ],
        )
