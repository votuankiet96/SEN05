"""Phần DÙNG CHUNG về tín hiệu của DE: tên key/kênh, nội dung (hợp đồng với OE) và cửa sổ hiệu lực. Không nối Redis, không ghi gì.

  1. TÊN:       key LIST và tên kênh Pub/Sub của một tín hiệu (``de_list_key``, ``channel_name``); HASH của một mốc do ``redis_connector.write_list_hash`` đặt tên (``{LIST}:{mốc}``);
  2. NỘI DUNG:  dòng tín hiệu của chiến lược -> payload (``build_signal``) -> trường HASH theo hợp đồng OE (``to_hash_fields``), message kênh (``signal_message``);
  3. HẠN:       tín hiệu chỉ được phát trong cửa sổ hiệu lực [giờ nến đóng, hạn chót) (``signal_window``).

strategy_runner dùng các hàm này để ghi cả tín hiệu thô (db0) lẫn tín hiệu đã lọc trend của từng khối (db lọc) trên cổng 6380; hai loại cùng cấu trúc, chỉ khác tiền tố khoá và kênh. Không còn là một tiến trình riêng.

Không biết chiến lược nào: loại lệnh (STOP/MARKET), hạn hiệu lực và tên chiến lược do người gọi truyền vào.
Hợp đồng dòng tín hiệu (cột ``build_signal`` đọc từ dòng cuối của bảng do chiến lược trả về): ``bartime``, ``signal`` (±1), ``entry_price``, ``atr`` (hữu hạn, làm tròn theo số lẻ của symbol, mặc định 2), tuỳ chọn ``signal_reason``.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

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
