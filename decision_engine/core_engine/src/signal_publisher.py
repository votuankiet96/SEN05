"""Phía QUYẾT ĐỊNH của DE (nhóm tiến trình 2): đọc các cấu phần strategy_runner đã ghi ở db0 (TRIGGER, SETUP, CONTEXT) và db1 (TREND), ghép theo từng ô (symbol, chiến lược, khung trigger), lọc trend/dời, rồi ghi tín hiệu vào db2.

  1. GHÉP BẢNG: ``assemble_cell_table`` dựng bảng từng nến của ô từ SETUP (mặt nạ, giá, atr của MỌI nến) cộng tín hiệu thô và lý do lấy từ TRIGGER (chỉ nến có tín hiệu có dòng), rồi nối CONTEXT của khung context
                (mặc định chính khung trigger) theo luật "reality epoch": nến tham chiếu
                ĐÃ ĐÓNG có giờ đóng không muộn hơn giờ đóng của nến trigger (``indicator.merge_closed_reference``). ``entry_complete`` / ``reference_ready`` trả lời "đủ chưa": nến tham chiếu (context hoặc
                trend) đóng ĐÚNG lúc nến trigger đóng mà chưa về thì PHẢI CHỜ (không dùng nến cũ thay), còn không đúng lúc trùng thì dùng nến đã đóng gần nhất. Việc chờ không có hàng chờ hay hạn riêng:
                người gọi chạy lại khi cấu phần về, và tín hiệu quá hạn hiệu lực thì tự bị bỏ ở ``RawSignalWriter.publish_latest``;
  2. TÊN:       key LIST và tên kênh Pub/Sub của một tín hiệu (``de_list_key``, ``channel_name``); HASH của một mốc do ``redis_connector.write_list_hash`` đặt tên (``{LIST}:{mốc}``);
  3. NỘI DUNG:  dòng tín hiệu của ô -> payload (``build_signal``) -> trường HASH theo hợp đồng OE (``to_hash_fields``), message kênh (``signal_message``);
  4. HẠN:       tín hiệu chỉ được phát trong cửa sổ hiệu lực [giờ nến đóng, hạn chót) (``signal_window``);
  5. GHI:       ``RawSignalWriter`` ghi tín hiệu (dòng cuối của bảng) theo List–Hash vào db2 của Redis cổng 6380 sau khi kiểm hạn. Mỗi nguồn một bộ ghi với tiền tố riêng: tín hiệu không lọc trend
                và tín hiệu đã lọc của từng khối trend cùng ở db2, cùng cấu trúc, chỉ khác tiền tố khoá và kênh.

File này không biết chiến lược hay luật lọc nào, và không tự gọi lệnh Redis (nối, đọc, ghi do redis_connector / data_engine làm). Loại lệnh (STOP/MARKET), hạn hiệu lực và tên chiến lược do người gọi truyền vào.
Hợp đồng dòng tín hiệu (cột ``build_signal`` đọc từ dòng cuối của bảng tín hiệu): ``bartime``, ``signal`` (±1), ``entry_price``, ``atr`` (hữu hạn, làm tròn theo số lẻ của symbol, mặc định 2), tuỳ chọn ``signal_reason``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from typing import Any, NamedTuple

import pandas as pd
import redis

from core_engine.src.indicator import merge_closed_reference
from core_engine.src.redis_connector import Link, write_list_hash

LOGGER = logging.getLogger(__name__)

# Từ vựng loại lệnh OE hiểu. STOP là lệnh chờ tại mức entry; MARKET là lệnh khớp ngay (hiệu lực ngắn sau khi nến đóng).
ORDER_TYPES = ("STOP", "MARKET")
# Số lẻ làm tròn entry/atr của symbol không khai riêng ở live.price_decimals (chỉ số, vàng, BTC); forex (EURUSD 5, USDJPY 3...) phải khai riêng.
DEFAULT_PRICE_DECIMALS = 2


def check_order_type(order_type: str) -> str:
    """Trả lại ``order_type`` nếu OE hiểu được, ngược lại ValueError (gọi lúc khởi động VÀ ngay trước khi dựng trường HASH)."""
    if order_type not in ORDER_TYPES:
        raise ValueError(f"Unsupported orderType '{order_type}' (OE understands: {', '.join(ORDER_TYPES)})")
    return order_type


def as_utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _iso_utc(value: Any) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _required_number(row: Any, column: str, decimals: int) -> float:
    value = float(row[column])
    if not math.isfinite(value):
        raise ValueError(f"Signal field '{column}' must be a finite number")
    return round(value, decimals)


# -- hạn hiệu lực --------------------------------------------------------------------------------------------------------------------------------------


def timeframe_duration(timeframe: str) -> pd.Timedelta:
    """Đổi mã khung thành độ dài chính xác."""
    code = timeframe.strip().upper()
    units = {"M": "minutes", "H": "hours", "D": "days", "W": "weeks"}
    unit = units.get(code[:1])
    amount_text = code[1:] or "1"
    if unit is None or not amount_text.isdigit() or int(amount_text) <= 0:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")
    return pd.Timedelta(**{unit: int(amount_text)})


def signal_window(strategy: str, timeframe: str, bartime: Any, rule: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Tính hiệu lực từ giờ MỞ của nến và luật của chiến lược (live.signal_validity): [giờ nến đóng, hạn chót)."""
    duration = timeframe_duration(timeframe)
    valid_from = as_utc(bartime) + duration
    mode = str(rule["mode"]).lower()
    if mode == "next_bar":
        valid_until = valid_from + duration * int(rule["valid_bars"])
    elif mode == "seconds_after_bar_close":
        valid_until = valid_from + pd.Timedelta(seconds=int(rule["seconds"]))
    else:
        raise ValueError(f"Unsupported validity mode for '{strategy}': {mode}")
    return valid_from, valid_until


# -- tên, nội dung -------------------------------------------------------------------------------------------------------------------------------------


def strategy_token(strategy: str) -> str:
    """Token CHIẾN LƯỢC trong tên key và kênh Redis: viết hoa, bỏ dấu gạch dưới (``ma_cross`` thành ``MACROSS``), nên không token nào chứa ký tự phân cách ``_``.
    Chỉ là TÊN: field ``strategy`` và ``clientOrderId`` trong HASH/message vẫn giữ tên thật của chiến lược."""
    return strategy.upper().replace("_", "")


def de_list_key(key_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """LIST các mốc tín hiệu của một (strategy, symbol, timeframe): ``{key_prefix}_{STRATEGY}_{SYMBOL}_{TIMEFRAME}``, vd ``L_DE_COMBO_US30_H4``."""
    return f"{key_prefix}_{strategy_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def channel_name(channel_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """Kênh Pub/Sub của một (strategy, symbol, timeframe), vd ``L_CHANNEL_COMBO_US30_H4``. Kênh chỉ là đường truyền, không lưu gì."""
    return f"{channel_prefix}_{strategy_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def build_signal(
    *,
    strategy: str,
    timeframe: str,
    symbol: str,
    row: Any,
    valid_from: pd.Timestamp,
    valid_until: pd.Timestamp,
    decimals: int = DEFAULT_PRICE_DECIMALS,
) -> tuple[str, str, dict[str, Any]]:
    """Dựng payload nội bộ (chưa phải tên field Redis); ``to_hash_fields`` là nơi DUY NHẤT dịch nó thành thứ được ghi vào HASH.

    Trả về ``(signal_id, stamp, payload)``. `signal_id` thành ``clientOrderId`` trên dây; `stamp` là bartime theo đúng định dạng mốc của Core, dùng đặt tên HASH.
    ``decimals`` là số lẻ làm tròn entry và atr (do người gọi tra theo symbol từ live.price_decimals).
    """
    strategy = strategy.lower()
    timeframe = timeframe.upper()
    symbol = symbol.upper()
    bartime = as_utc(row["bartime"])
    bartime_key = bartime.strftime("%Y%m%dT%H%M%SZ")
    signal_id = f"{strategy}:{timeframe}:{symbol}:{bartime_key}"
    stamp = bartime.strftime("%Y-%m-%d %H:%M:%S")

    side_value = int(row["signal"])
    if side_value not in (-1, 1):
        raise ValueError("Only BUY/SELL rows can be published")

    payload = {
        "strategy": strategy,
        "timeframe": timeframe,
        "symbol": symbol,
        "side": "BUY" if side_value == 1 else "SELL",
        "bartime": _iso_utc(bartime),
        "signal_id": signal_id,
        "entry": _required_number(row, "entry_price", decimals),
        "atr": _required_number(row, "atr", decimals),
        "valid_from": _iso_utc(valid_from),
        "valid_until": _iso_utc(valid_until),
        "reason": str(row.get("signal_reason", "")),
    }
    return signal_id, stamp, payload


def to_hash_fields(payload: dict[str, Any], *, order_type: str) -> dict[str, str]:
    """Dịch payload nội bộ thành các field HASH cho OE: định danh (symbol, timeframe, strategy, bartime), dữ liệu đặt lệnh theo tên field của ``ProtoOANewOrderReq``
    (tradeSide, orderType, stopPrice cho lệnh STOP, expirationTimestamp là epoch giây, clientOrderId, comment) và hỗ trợ OE (atr, valid_from).

    ``order_type`` do người gọi truyền (sự thật của chiến lược: combo vào ở mép nến tín hiệu nên là lệnh chờ STOP; các chiến lược còn lại vào ở close nên là MARKET)."""
    check_order_type(order_type)
    fields: dict[str, str] = {
        "symbol": str(payload["symbol"]),
        "timeframe": str(payload["timeframe"]),
        "strategy": str(payload["strategy"]).lower(),
        "bartime": str(payload["bartime"]),
        "tradeSide": str(payload["side"]),
        "orderType": order_type,
        "expirationTimestamp": str(int(as_utc(payload["valid_until"]).timestamp())),
        "clientOrderId": str(payload["signal_id"]),
        "comment": str(payload.get("reason", "")),
        "atr": str(payload["atr"]),
        "valid_from": str(payload["valid_from"]),
    }
    if order_type == "STOP":
        fields["stopPrice"] = str(payload["entry"])
    return fields


def signal_message(hash_fields: dict[str, str]) -> str:
    """Message Pub/Sub: toàn bộ tín hiệu, tức đúng các field của HASH (message mang đủ thông tin, không chỉ là trigger)."""
    return json.dumps(hash_fields, ensure_ascii=False)


# -- lắp bảng từ db0 -----------------------------------------------------------------------------------------------------------------------------------


def entry_complete(setup: pd.DataFrame, context: pd.DataFrame) -> bool:
    """CONTEXT của khung vào lệnh đã có mốc của nến vào lệnh MỚI NHẤT (dòng cuối SETUP) chưa. strategy_runner ghi CONTEXT trước SETUP nên bình thường là có; chưa có thì chờ."""
    if setup.empty or context.empty:
        return False
    return bool((context["bartime"] == setup["bartime"].iloc[-1]).any())


def reference_ready(reference: pd.DataFrame, entry_close: pd.Timestamp, reference_length: pd.Timedelta) -> bool:
    """Nến tham chiếu (khung chỉ báo hoặc khung trend) có đủ để quyết định cho nến vào lệnh đóng lúc ``entry_close`` chưa.

    CHƯA (phải chờ) khi: bảng tham chiếu rỗng; hoặc nến tham chiếu mới nhất đóng TRƯỚC ``entry_close`` và ``entry_close`` rơi đúng lưới đóng của khung tham chiếu (cách nến đó một số nguyên lần
    ``reference_length``) tức là có một nến tham chiếu đóng ĐÚNG lúc này mà chưa về. ĐỦ ở mọi trường hợp khác: đã có nến đóng đúng ``entry_close``; hoặc ``entry_close`` không trùng lưới nên
    nến đã đóng gần nhất (không muộn hơn) chính là nến phải dùng (kể cả khi nó cũ vì nghỉ phiên). Lưới suy ra từ chính các nến tham chiếu đang có nên không giả định giờ neo nào."""
    if reference.empty:
        return False
    closes = reference["close_time"]
    if (closes == entry_close).any():
        return True
    latest = closes.max()
    return not (latest < entry_close and (entry_close - latest) % reference_length == pd.Timedelta(0))


def assemble_cell_table(setup: pd.DataFrame, events: pd.DataFrame | None, context: pd.DataFrame, *, same_frame: bool) -> pd.DataFrame:
    """Bảng từng nến của một ô (symbol, chiến lược, khung trigger): các nến SETUP (bartime, close_time, setup, open, high, low, close, atr) cộng ``signal`` và ``reason`` lấy từ TRIGGER (``events``) rồi cột
    CONTEXT ``context_macd_h`` và ``context_ma``.

    TRIGGER chỉ có dòng ở nến CÓ tín hiệu: nến nào không có dòng thì ``signal`` = 0 và ``reason`` rỗng, và ``events`` None hoặc rỗng (cửa sổ không nến nào có tín hiệu) là chuyện bình thường. Sự kiện nằm ngoài
    các nến của SETUP thì bỏ. Kết quả có cùng hình dạng bảng TRIGGER từng nến mà chiến lược tính ra, nên phần lọc trend/dời phía sau không đổi.

    ``same_frame`` True (khung context = khung trigger): nối theo ``bartime``, nến nào cũng có CONTEXT của chính nó. False: khung context lớn hơn, ghép theo luật nến đóng (nến context đã đóng gần nhất
    có giờ đóng không muộn hơn giờ đóng của nến trigger). Hiện DE đọc và mang theo hai cột này nhưng chưa dùng chúng làm điều kiện."""
    if events is None or events.empty:
        signals = pd.DataFrame({"bartime": pd.Series(dtype="datetime64[us]"), "signal": pd.Series(dtype="float64"), "reason": pd.Series(dtype="object")})
    else:
        signals = events[["bartime", "signal", "reason"]]
    bars = setup.merge(signals, on="bartime", how="left")
    bars["signal"] = bars["signal"].fillna(0.0)
    bars["reason"] = bars["reason"].fillna("")
    ctx = context.rename(columns={"macd_h": "context_macd_h", "ma": "context_ma"})
    if same_frame:
        return bars.merge(ctx.drop(columns=["close_time"]), on="bartime", how="left")
    return merge_closed_reference(bars, ctx, ["context_macd_h", "context_ma"], ref_close_name="context_close_time")


# -- ghi tín hiệu --------------------------------------------------------------------------------------------------------------------------------------


class Outcome(NamedTuple):
    """Kết quả xử lý dòng cuối của một cặp: ``state`` là một trong no_signal / expired / published / duplicate (tiến trình ghi cấu phần dùng ``written``); ``published`` là số mốc ghi mới (0 hoặc 1 với tín hiệu)."""

    state: str
    published: int


class RawSignalWriter:
    """Đầu ra của MỘT nguồn tín hiệu: nhận dòng cuối của bảng chiến lược, kiểm hạn hiệu lực rồi ghi tín hiệu theo List–Hash (và kênh Pub/Sub) lên Redis đầu ra, db và tiền tố của nguồn đó.

    Nguồn không lọc (``source="raw"``) ghi dòng cuối của bảng KHÔNG lọc trend; nguồn lọc (``source`` = tên khối trend) ghi dòng cuối của bảng đã lọc trend. Cả hai cùng db1. Tên lớp giữ nguyên từ
    thời chỉ có nguồn thô. ``source`` chỉ để gắn nhãn dòng log, nên log của các nguồn không lẫn nhau. ``price_decimals`` (live.price_decimals) là số lẻ làm tròn entry/atr của từng symbol
    (forex cần 5 hoặc 3, symbol không khai dùng 2).

    Việc nối Redis (tạo kết nối, ping, đóng) và ghi nguyên tử do redis_connector làm. ``client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối (một kết nối riêng).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        channel_prefix: str,
        retention_seconds: int,
        retention_max_entries: int,
        source: str = "raw",
        price_decimals: Mapping[str, int] | None = None,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
        self._source = source
        self._price_decimals = dict(price_decimals or {})  # symbol -> số lẻ làm tròn entry/atr; symbol không có dùng DEFAULT_PRICE_DECIMALS
        self._key_prefix = key_prefix
        self._channel_prefix = channel_prefix
        self._retention_seconds = int(retention_seconds)
        self._retention_max_entries = int(retention_max_entries)
        self._link = Link(
            host=host, port=port, password=password, db=db,
            socket_timeout=socket_timeout, connect_timeout=connect_timeout, healthcheck_interval=healthcheck_interval, client=client,
        )
        self._client = self._link.client

    def ping(self) -> None:
        self._link.ping()

    def close(self) -> None:
        self._link.close()

    def publish_raw(
        self,
        *,
        strategy: str,
        order_type: str,
        timeframe: str,
        symbol: str,
        row: pd.Series,
        valid_from: pd.Timestamp,
        valid_until: pd.Timestamp,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Dựng tín hiệu từ dòng cuối rồi ghi (HASH + LIST + Pub/Sub nguyên tử). Trả về (đã ghi mới?, signal_id, payload); đã có sẵn thì không ghi và không publish lại."""
        signal_id, stamp, payload = build_signal(
            strategy=strategy, timeframe=timeframe, symbol=symbol, row=row, valid_from=valid_from, valid_until=valid_until,
            decimals=self._price_decimals.get(symbol.upper(), DEFAULT_PRICE_DECIMALS),
        )
        hash_fields = to_hash_fields(payload, order_type=order_type)
        created = write_list_hash(
            self._client,
            list_key=de_list_key(self._key_prefix, strategy, symbol, timeframe),
            stamp=stamp,
            fields=hash_fields,
            retention_seconds=self._retention_seconds,
            max_entries=self._retention_max_entries,
            channel=channel_name(self._channel_prefix, strategy, symbol, timeframe),
            message=signal_message(hash_fields),
        )
        return created, signal_id, payload

    def publish_latest(
        self,
        *,
        strategy: str,
        order_type: str,
        validity: dict[str, Any],
        timeframe: str,
        symbol: str,
        latest: pd.Series,
        now: pd.Timestamp,
    ) -> Outcome:
        """Xử lý dòng cuối (nến vào lệnh mới nhất) của bảng chiến lược: không tín hiệu thì thôi; còn tín hiệu thì chỉ ghi khi giờ ``now`` còn trước hạn chót (nến đã đóng, cấu phần đã đủ)."""
        side_value = int(latest["signal"])
        if side_value == 0:
            LOGGER.debug("event=no_signal strategy=%s symbol=%s timeframe=%s source=%s", strategy, symbol, timeframe, self._source)
            return Outcome("no_signal", 0)

        valid_from, valid_until = signal_window(strategy, timeframe, latest["bartime"], validity)
        current_time = as_utc(now)
        if current_time >= valid_until:
            LOGGER.info("event=signal_expired strategy=%s symbol=%s timeframe=%s source=%s", strategy, symbol, timeframe, self._source)
            return Outcome("expired", 0)

        created, signal_id, payload = self.publish_raw(
            strategy=strategy, order_type=order_type, timeframe=timeframe, symbol=symbol, row=latest,
            valid_from=valid_from, valid_until=valid_until,
        )
        if not created:
            LOGGER.info(
                "event=signal_duplicate strategy=%s symbol=%s timeframe=%s signal_id=%s source=%s", strategy, symbol, timeframe, signal_id, self._source,
            )
            return Outcome("duplicate", 0)
        LOGGER.info(
            "event=signal_published strategy=%s symbol=%s timeframe=%s side=%s signal_id=%s source=%s",
            strategy, symbol, timeframe, payload["side"], signal_id, self._source,
        )
        return Outcome("published", 1)
