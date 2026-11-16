"""Phần XUẤT BẢN của DE (bộ phận phục vụ các module và decision_engine): dựng tên kênh, nội dung và hạn hiệu lực rồi ghi ra db0 của Redis đầu ra. Mọi đầu ra của DE nằm ở MỘT db (db0), ba loại key:

  1. KẾT QUẢ MODULE: LIST ``L_MODSTR_{CHIẾN LƯỢC}_{SYMBOL}_{KHUNG}`` (kết quả chiến lược) hoặc ``L_MODTRE_{YẾU TỐ}_{SYMBOL}_{KHUNG}`` (kết quả yếu tố trend) + HASH ``<LIST>:{mốc}`` (``ModuleWriter``; nội dung ``signal_rows`` / ``bias_rows``). MODULE là tên chiến lược (chỉ có dòng ở nến ỨNG VIÊN có tín hiệu) hoặc tên yếu tố trend (có dòng ở mọi nến của khung của nó);
  2. TÍN HIỆU CUỐI: LIST ``L_DE_{SYMBOL}_{KHUNG}`` + HASH ``<LIST>:{mốc}`` theo hợp đồng OE (``SignalWriter``; ``build_signal`` rồi ``to_hash_fields``). mỗi (symbol, khung trigger) chỉ có một chiến lược (config kiểm) nên key không có tên chiến lược;
  3. KÊNH: ``L_CHANNEL_{MODULE}_{SYMBOL}_{KHUNG}`` (``channel_name``), message chỉ là MỐC của tín hiệu vừa ghi; bên nhận tự đọc HASH ``L_DE_{SYMBOL}_{KHUNG}:{mốc}``. Kênh chỉ là đường truyền, không lưu key.
Tên LIST dùng chung với bên đọc (``data_engine.pair_list_key``), HASH của một mốc do ``redis_connector.write_list_hash`` đặt tên (``{LIST}:{mốc}``). Mốc luôn là giờ MỞ nến, ``YYYY-MM-DD HH:MM:SS`` (UTC).

Hạn hiệu lực: tín hiệu chỉ được phát trong cửa sổ [giờ nến đóng, hạn chót) (``signal_window``); quá hạn thì ``SignalWriter.publish`` bỏ.

File này không giữ tên chiến lược nào và không tự gọi lệnh Redis (nối và ghi do redis_connector làm). Tên chiến lược, loại lệnh, hạn hiệu lực và số lẻ làm tròn do người gọi truyền vào.
Hợp đồng dòng tín hiệu (``build_signal`` đọc từ dòng do decision_engine.decide trả về): ``bartime``, ``signal`` (±1), ``entry_price`` (giá chờ ở lệnh STOP, không ghi ra HASH ở lệnh MARKET), ``atr`` (hữu hạn, làm tròn theo số lẻ của symbol, mặc định 2), tuỳ chọn ``signal_reason``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

import pandas as pd
import redis

from core_engine.src.data_engine import STAMP_FORMAT, pair_list_key
from core_engine.src.redis_connector import (
    Link,
    trim_lists_before,
    write_list_hash,
    write_list_hash_many,
)

LOGGER = logging.getLogger(__name__)

# Loại lệnh OE hiểu, khai ở ``strategies.<tên>.order_type``: MARKET khớp ngay tại giá hiện tại (HASH không có field giá; entry = close của nến phát chỉ là tham chiếu), STOP là lệnh chờ ở giá ``stopPrice`` (= đỉnh nến phát với BUY, đáy nến phát với SELL).
ORDER_TYPES = ("MARKET", "STOP")
# Số lẻ làm tròn entry/atr của symbol không khai riêng ở live.price_decimals (chỉ số, vàng, BTC); forex (EURUSD 5, USDJPY 3...) phải khai riêng.
DEFAULT_PRICE_DECIMALS = 2


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


def signal_window(length: pd.Timedelta, bartime: Any, validity_seconds: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Hiệu lực của tín hiệu ở nến mở lúc ``bartime``: [giờ nến đóng, hạn chót), hạn chót = giờ nến đóng + ``validity_seconds``. ``length`` là độ dài khung của nến (configuration.timeframe_duration)."""
    valid_from = as_utc(bartime) + length
    return valid_from, valid_from + pd.Timedelta(seconds=int(validity_seconds))


# -- tên, nội dung -------------------------------------------------------------------------------------------------------------------------------------


def module_token(name: str) -> str:
    """Token MODULE hoặc chiến lược trong tên key và kênh Redis: viết hoa, bỏ dấu gạch dưới (``ma_cross`` thành ``MACROSS``), nên không token nào chứa ký tự phân cách ``_``.
    Chỉ là TÊN: field ``strategy`` và ``clientOrderId`` trong HASH vẫn giữ tên thật của chiến lược."""
    return name.upper().replace("_", "")


def channel_name(channel_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """Kênh Pub/Sub của một (strategy, symbol, timeframe), vd ``L_CHANNEL_MACROSS_BTCUSD_M10``. Kênh chỉ là đường truyền, không lưu gì."""
    return f"{channel_prefix}_{module_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def _stamp(value: Any) -> str:
    return pd.Timestamp(value).strftime(STAMP_FORMAT)


def signal_rows(frame: pd.DataFrame, length: pd.Timedelta) -> list[tuple[str, dict[str, str]]]:
    """Các nến CÓ tín hiệu của bảng đã qua hàm ``detect`` của một file chiến lược thành dòng ghi ``(mốc, field)``: ``close_time`` (giờ đóng nến), ``signal`` (1 hoặc -1) và ``reason``. Nến không có tín hiệu thì không có dòng."""
    return [
        (_stamp(row.bartime), {"close_time": _stamp(row.bartime + length), "signal": str(int(row.signal)), "reason": str(row.signal_reason)})
        for row in frame.loc[frame["signal"] != 0].itertuples()
    ]


def bias_rows(frame: pd.DataFrame, length: pd.Timedelta) -> list[tuple[str, dict[str, str]]]:
    """MỌI nến của bảng đã qua một hàm ``bias_<tên>`` của de_module.trend (cột ``trend_bias``) thành dòng ghi ``(mốc, field)``: ``close_time`` (giờ đóng nến) và ``trend_bias`` (1, -1 hoặc 0)."""
    return [(_stamp(row.bartime), {"close_time": _stamp(row.bartime + length), "trend_bias": str(int(row.trend_bias))}) for row in frame.itertuples()]


def build_signal(
    *,
    strategy: str,
    timeframe: str,
    symbol: str,
    row: Any,
    valid_from: pd.Timestamp,
    valid_until: pd.Timestamp,
    order_type: str,
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
    stamp = bartime.strftime(STAMP_FORMAT)

    side_value = int(row["signal"])
    if side_value not in (-1, 1):
        raise ValueError("Only BUY/SELL rows can be published")
    order_type = order_type.upper()
    if order_type not in ORDER_TYPES:
        raise ValueError(f"order_type must be one of {ORDER_TYPES}, got '{order_type}'")

    payload = {
        "strategy": strategy,
        "timeframe": timeframe,
        "symbol": symbol,
        "side": "BUY" if side_value == 1 else "SELL",
        "order_type": order_type,
        "bartime": _iso_utc(bartime),
        "signal_id": signal_id,
        "entry": _required_number(row, "entry_price", decimals),
        "atr": _required_number(row, "atr", decimals),
        "valid_from": _iso_utc(valid_from),
        "valid_until": _iso_utc(valid_until),
        "reason": str(row.get("signal_reason", "")),
    }
    return signal_id, stamp, payload


def to_hash_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Dịch payload nội bộ thành các field HASH cho OE: định danh (symbol, timeframe, strategy, bartime), dữ liệu đặt lệnh theo tên field của ``ProtoOANewOrderReq``
    (tradeSide, orderType, stopPrice, expirationTimestamp là epoch giây, clientOrderId, comment) và hỗ trợ OE (atr, valid_from). ``stopPrice`` chỉ có ở lệnh STOP (giá chờ = entry); lệnh MARKET khớp ngay nên không có field giá."""
    fields = {
        "symbol": str(payload["symbol"]),
        "timeframe": str(payload["timeframe"]),
        "strategy": str(payload["strategy"]).lower(),
        "bartime": str(payload["bartime"]),
        "tradeSide": str(payload["side"]),
        "orderType": str(payload["order_type"]),
        "expirationTimestamp": str(int(as_utc(payload["valid_until"]).timestamp())),
        "clientOrderId": str(payload["signal_id"]),
        "comment": str(payload.get("reason", "")),
        "atr": str(payload["atr"]),
        "valid_from": str(payload["valid_from"]),
    }
    if payload["order_type"] == "STOP":
        fields["stopPrice"] = str(payload["entry"])
    return fields


# -- ghi -----------------------------------------------------------------------------------------------------------------------------------------------


class Outcome(NamedTuple):
    """Kết quả xử lý một tín hiệu cuối: ``state`` là một trong expired / published / duplicate; ``published`` là số mốc ghi mới (0 hoặc 1)."""

    state: str
    published: int


class ModuleWriter:
    """Đầu ra của các module: ghi kết quả của chiến lược và của các yếu tố trend theo List–Hash vào db0 (``L_MODSTR_...`` và ``L_MODTRE_...``, tiền tố do người gọi truyền vào).

    Việc nối Redis (tạo kết nối, ping, đóng) và ghi nguyên tử do redis_connector làm. ``client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        retention_max_entries: int,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
        self._key_prefix = key_prefix
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

    def write(
        self, *, module: str, symbol: str, timeframe: str, rows: Sequence[tuple[str, dict[str, str]]], retention_seconds: int, keep_from: str
    ) -> int:
        """Ghi các dòng ``(mốc, field)`` của một cặp (mỗi mốc đúng một lần, mốc đã có thì bỏ qua) rồi bỏ khỏi LIST các mốc cũ hơn ``keep_from`` (đầu cửa sổ nến đang tính), để LIST chỉ giữ đúng cửa sổ.
        ``retention_seconds`` là hạn lưu của mỗi HASH. Trả về số mốc thật sự được ghi mới."""
        list_key = pair_list_key(self._key_prefix, symbol, timeframe, module_token(module))
        written = write_list_hash_many(
            self._client,
            [
                {"list_key": list_key, "stamp": stamp, "fields": fields, "retention_seconds": retention_seconds, "max_entries": self._retention_max_entries}
                for stamp, fields in rows
            ],
        )
        trim_lists_before(self._client, [(list_key, keep_from)])
        return written


class SignalWriter:
    """Đầu ra của decision_engine: nhận dòng tín hiệu cuối, kiểm hạn hiệu lực rồi ghi tín hiệu theo List–Hash vào db0 và báo kênh Pub/Sub.

    ``price_decimals`` (live.price_decimals) là số lẻ làm tròn entry/atr của từng symbol (forex cần 5 hoặc 3, symbol không khai dùng 2). Việc nối Redis và ghi nguyên tử do redis_connector làm.
    ``client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối.
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
        price_decimals: Mapping[str, int] | None = None,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
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

    def publish(
        self, *, strategy: str, timeframe: str, symbol: str, row: pd.Series, length: pd.Timedelta, validity_seconds: int, order_type: str, now: pd.Timestamp
    ) -> Outcome:
        """Xử lý một dòng tín hiệu cuối: chỉ ghi khi giờ ``now`` còn trước hạn chót (tính từ giờ nến đóng). Đã có sẵn mốc đó thì không ghi và không báo kênh lại.
        ``length`` là độ dài khung vào lệnh (configuration.timeframe_duration), ``timeframe`` chỉ dùng đặt tên."""
        valid_from, valid_until = signal_window(length, row["bartime"], validity_seconds)
        if as_utc(now) >= valid_until:
            LOGGER.info("event=signal_expired strategy=%s symbol=%s timeframe=%s bartime=%s", strategy, symbol, timeframe, _stamp(row["bartime"]))
            return Outcome("expired", 0)

        signal_id, stamp, payload = build_signal(
            strategy=strategy, timeframe=timeframe, symbol=symbol, row=row, valid_from=valid_from, valid_until=valid_until, order_type=order_type,
            decimals=self._price_decimals.get(symbol.upper(), DEFAULT_PRICE_DECIMALS),
        )
        created = write_list_hash(
            self._client,
            list_key=pair_list_key(self._key_prefix, symbol, timeframe),
            stamp=stamp,
            fields=to_hash_fields(payload),
            retention_seconds=self._retention_seconds,
            max_entries=self._retention_max_entries,
            channel=channel_name(self._channel_prefix, strategy, symbol, timeframe),
            message=stamp,
        )
        if not created:
            LOGGER.info("event=signal_duplicate strategy=%s symbol=%s timeframe=%s signal_id=%s", strategy, symbol, timeframe, signal_id)
            return Outcome("duplicate", 0)
        LOGGER.info("event=signal_published strategy=%s symbol=%s timeframe=%s side=%s signal_id=%s", strategy, symbol, timeframe, payload["side"], signal_id)
        return Outcome("published", 1)
