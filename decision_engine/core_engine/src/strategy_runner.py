"""Phía TÍNH CẤU PHẦN của DE (nhóm tiến trình 1): từ nến của nguồn (Core AEN hoặc Redis DF của OG8) tính các cấu phần THÔ cho MỌI (symbol, khung) -- không phụ thuộc cấu hình nào -- và ghi vào Redis
cổng 6380: SETUP ở db0; TRIGGER, CONTEXT và TREND ở db1. Chỉ tính toán và ghi: không biết khung trend của chiến lược, không biết db2 hay OE, không đọc config.

Mỗi nến đã đóng một mốc (mốc = giờ MỞ nến, kèm ``close_time`` = giờ ĐÓNG để signal_publisher ghép theo luật nến đóng). Mọi danh sách chỉ giữ cửa sổ 500 nến gần nhất:
  * TRIGGER ``{SYMBOL}_TRIGGER_{CHIẾN LƯỢC}_{KHUNG}`` (db1): SỰ KIỆN. Chỉ nến mà chiến lược BẮN tín hiệu thô (``signal`` = 1/-1, đã qua lọc trùng) mới có dòng, kèm giá nến, ``atr`` và ``reason``;
    nến không có tín hiệu thì không ghi gì. Khởi động lại thì xét lại 500 nến gần nhất xem nến nào có tín hiệu; sự kiện trôi ra ngoài cửa sổ 500 nến thì bị bỏ khỏi danh sách;
  * SETUP ``{SYMBOL}_SETUP_{CHIẾN LƯỢC}_{KHUNG}`` (db0): TRẠNG THÁI điều kiện của chiến lược ở TỪNG nến (không phải sự kiện): ``setup`` (1/-1/0) là mặt nạ "nến được phép bắn" mà phần DỜI của DE cần theo từng
    nến, kèm giá nến và ``atr`` của nến đó (nến dời sẽ lấy giá và atr từ đây). Là dấu chốt của lượt ghi (xem dưới);
  * CONTEXT ``{SYMBOL}_CONTEXT_{KHUNG}`` (db1): chỉ báo tính thêm ở mọi khung để đối chiếu với trigger ở khung nhỏ hơn: ``macd_h`` (MACD histogram) và ``ma`` (MA), tham số chuẩn khai ở ``context:``;
  * TREND ``{SYMBOL}_TREND_{PHƯƠNG PHÁP}_{KHUNG}`` (db1): hướng trend ``trend_bias`` (+1/-1/0) của từng nến, do khối trend tính trên chính nến của khung đó (rules/trend_tf.build_trend_frame).

Thứ tự ghi trong một lượt: TREND (db1), rồi CONTEXT, rồi với từng chiến lược TRIGGER (sự kiện) và SETUP CUỐI CÙNG -- SETUP là "dấu chốt" vì nó có dòng cho MỌI nến: signal_publisher thấy nến vào lệnh mới ở SETUP thì
TRIGGER (nếu nến đó có tín hiệu), CONTEXT và TREND của nến đó đã có sẵn.

``ComponentWriter`` chỉ gửi các mốc CHƯA ghi của từng list (lần đầu: cả bảng; mốc đã có thì redis_connector bỏ qua), mỗi HASH hết hạn sau 2 × số mốc tối đa × độ dài khung (gấp đôi để chịu cuối tuần).
Việc nối và ghi nguyên tử do redis_connector làm; tên key do data_engine đặt (cùng hàm với bên đọc). Chỉ báo có nhớ đệm (``enable_memo``) vì cùng một bảng nến được dùng cho nhiều chiến lược.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
import redis

from core_engine.src.configuration import StrategySpec
from core_engine.src.data_engine import STAMP_FORMAT, pair_list_key
from core_engine.src.indicator import macd_hist, sma, timeframe_minutes
from core_engine.src.redis_connector import (
    Link,
    trim_lists_before,
    write_list_hash_many,
)
from core_engine.src.rules.trend_tf import build_trend_frame

LOGGER = logging.getLogger(__name__)
_RETENTION_MARGIN = 2

# =============================================================================
# Nhớ đệm (TUỲ CHỌN) cho phép tính chỉ báo, phép THUẦN bị lặp nhiều nhất ở đường live. Khoá = NỘI DUNG đầu vào (băm cả khung nến + đúng các tham số mà phép tính đọc) nên cùng đầu vào
# luôn ra cùng kết quả. Mặc định TẮT (test chạy như cũ); chỉ dịch vụ live bật bằng enable_memo(). Giả định về tham số được khoá bằng tests/test_memo.py:
# chỉ báo không đọc khoá TREND*.
# =============================================================================


class _FrameMemo:
    """LRU nhớ DataFrame; luôn trả BẢN SAO để người gọi không làm hỏng bản đã nhớ."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.items: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: tuple, compute: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        found = self.items.get(key)
        if found is not None:
            self.items.move_to_end(key)
            self.hits += 1
            return found.copy()
        self.misses += 1
        value = compute()
        self.items[key] = value
        if len(self.items) > self.maxsize:
            self.items.popitem(last=False)
        return value.copy()


class _MemoSwitch:
    store: _FrameMemo | None = None


_MEMO = _MemoSwitch()


def enable_memo(maxsize: int = 256) -> None:
    """Bật nhớ đệm (gọi một lần lúc dịch vụ live khởi động). ``maxsize`` = số khung được giữ (mỗi khung vài chục đến vài trăm KB)."""
    _MEMO.store = _FrameMemo(int(maxsize))


def disable_memo() -> None:
    _MEMO.store = None


def memo_stats() -> dict[str, int]:
    """{"hits", "misses", "size"} khi nhớ đệm đang bật, rỗng khi tắt."""
    store = _MEMO.store
    return {"hits": store.hits, "misses": store.misses, "size": len(store.items)} if store else {}


def _frame_digest(frame: pd.DataFrame) -> str:
    """Băm NỘI DUNG khung nến (giá trị, giờ nến, tên cột), không phụ thuộc chỉ số hàng."""
    rows = pd.util.hash_pandas_object(frame, index=False).to_numpy()
    return hashlib.blake2b(rows.tobytes() + repr(list(frame.columns)).encode(), digest_size=16).hexdigest()


def _add_indicators(spec: StrategySpec, bars: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """spec.add_indicators, có nhớ đệm khi bật. Chỉ báo chỉ đọc tham số của chiến lược, nên khoá bỏ các khoá TREND*
    (mọi kế hoạch cùng chiến lược, có hay không có trend, dùng chung một kết quả chỉ báo)."""
    store = _MEMO.store
    if store is None:
        return spec.add_indicators(bars, params)
    own = tuple(sorted((k, repr(v)) for k, v in params.items() if not k.startswith("TREND")))
    return store.get_or_compute(("ind", spec.key, own, _frame_digest(bars)), lambda: spec.add_indicators(bars, params))


# =============================================================================
# Tính ba cấu phần: bảng thuần (không Redis). Mỗi bảng có bartime (giờ mở), close_time (giờ đóng) rồi các cột riêng của loại.
# =============================================================================


def _with_close_time(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Chèn ``close_time`` = giờ mở + độ dài khung ngay sau ``bartime`` (cùng độ phân giải datetime64[us] với bartime)."""
    out = frame.copy()
    out.insert(1, "close_time", (out["bartime"] + pd.Timedelta(minutes=timeframe_minutes(timeframe))).astype("datetime64[us]"))
    return out


def trigger_table(spec: StrategySpec, candles: pd.DataFrame, params: dict[str, Any], symbol: str, timeframe: str) -> pd.DataFrame:
    """Bảng TỪNG NẾN của một chiến lược tại một khung: chạy NGUYÊN ``add_indicators`` rồi ``detect_signals`` của chiến lược trên nến khung đó (``params`` đã chuẩn hoá, trend tắt) và giữ: bartime, close_time,
    ``signal`` (= raw_signal, sau lọc trùng), ``setup`` (= buy_mask - sell_mask: 1 mua, -1 bán, 0 không), open, high, low, close, atr, ``reason`` (lý do tín hiệu, rỗng ở nến không có tín hiệu).
    Đây là bảng TÍNH TOÁN trong bộ nhớ; thứ ghi lên Redis là hai lát cắt của nó: ``trigger_events`` (chỉ nến có tín hiệu) và ``setup_view`` (trạng thái từng nến)."""
    enriched = _add_indicators(spec, candles, params)
    out = spec.detect_signals(enriched, symbol=symbol, params=params)
    setup = out["buy_mask"].astype(int) - out["sell_mask"].astype(int)
    frame = pd.DataFrame(
        {
            "bartime": out["bartime"].to_numpy(),
            "signal": out["raw_signal"].astype(int).to_numpy(),
            "setup": setup.to_numpy(),
            "open": out["open"].to_numpy(),
            "high": out["high"].to_numpy(),
            "low": out["low"].to_numpy(),
            "close": out["close"].to_numpy(),
            "atr": out["atr"].to_numpy(),
            "reason": out["signal_reason"].fillna("").astype(str).to_numpy(),
        }
    )
    return _with_close_time(frame, timeframe)


SETUP_COLUMNS = ("bartime", "close_time", "setup", "open", "high", "low", "close", "atr")


def trigger_events(table: pd.DataFrame) -> pd.DataFrame:
    """Phần SỰ KIỆN của bảng từng nến: chỉ các nến mà chiến lược bắn tín hiệu thô (``signal`` khác 0). Đây là thứ được ghi vào TRIGGER; nến không có tín hiệu không có dòng nào."""
    return table[table["signal"] != 0]


def setup_view(table: pd.DataFrame) -> pd.DataFrame:
    """Phần TRẠNG THÁI của bảng từng nến, một dòng cho MỌI nến: mặt nạ ``setup`` cùng giá nến và atr. Đây là thứ được ghi vào SETUP, để phần dời của DE biết nến nào sau tín hiệu còn đủ điều kiện và lấy giá,
    atr của nến dời; tín hiệu (``signal``, ``reason``) KHÔNG nằm ở đây mà ở TRIGGER."""
    return table[list(SETUP_COLUMNS)]


def context_table(candles: pd.DataFrame, context: dict[str, int], timeframe: str) -> pd.DataFrame:
    """CONTEXT của một khung: ``macd_h`` (MACD histogram) và ``ma`` (trung bình động đơn giản) tính trên close của chính khung đó, tham số chuẩn ``context`` (macd_fast, macd_slow, macd_signal, ma_period)."""
    close = candles["close"].astype(float)
    frame = pd.DataFrame(
        {
            "bartime": candles["bartime"].to_numpy(),
            "macd_h": macd_hist(close, fast=context["macd_fast"], slow=context["macd_slow"], signal=context["macd_signal"]).to_numpy(),
            "ma": sma(close, context["ma_period"]).to_numpy(),
        }
    )
    return _with_close_time(frame, timeframe)


def trend_table(candles: pd.DataFrame, method: str, timeframe: str) -> pd.DataFrame:
    """TREND của một phương pháp tại một khung: chạy khối ``method`` trên nến khung đó (rules/trend_tf.build_trend_frame). Bảng: bartime, close_time (giờ đóng nến), trend_bias."""
    frame = build_trend_frame(candles, {"TREND_METHODS": (method,), "TREND_TF": timeframe})
    return pd.DataFrame(
        {
            "bartime": frame["bartime"].astype("datetime64[us]").to_numpy(),
            "close_time": frame["trend_close_time"].astype("datetime64[us]").to_numpy(),
            "trend_bias": frame["trend_bias"].to_numpy(),
        }
    )


# =============================================================================
# Ghi vào db0 của DE.
# =============================================================================


def _text(value: Any) -> str:
    """Giá trị của một trường HASH: chữ giữ nguyên, số nguyên thành số nguyên, số thực dạng ``repr`` (đọc lại đúng từng bit; NaN là ``nan``)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return repr(float(value))


class ComponentWriter:
    """Ghi cấu phần vào db0 của DE theo List–Hash. Nhớ mốc đã ghi của từng (symbol, vai trò, khung) để các lần sau chỉ gửi mốc mới; ``client`` cho test thay bằng bản giả."""

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
        self._prefix = key_prefix
        self._max_entries = int(retention_max_entries)
        self._link = Link(
            host=host, port=port, password=password, db=db,
            socket_timeout=socket_timeout, connect_timeout=connect_timeout, healthcheck_interval=healthcheck_interval, client=client,
        )
        self._written: dict[tuple[str, str, str, str], pd.Timestamp] = {}

    def ping(self) -> None:
        self._link.ping()

    def close(self) -> None:
        self._link.close()

    def write(self, symbol: str, role: str, timeframe: str, table: pd.DataFrame, qualifier: str = "") -> int:
        """Ghi các mốc CHƯA ghi của ``table`` (lần đầu: cả bảng) vào LIST ``{SYMBOL}_{ROLE}[_{QUALIFIER}]_{KHUNG}``. Trả về số mốc thật sự ghi mới (mốc đã có sẵn trong Redis không tính)."""
        key = (symbol.upper(), role, timeframe.upper(), qualifier.upper())
        last = self._written.get(key)
        fresh = table if last is None else table[table["bartime"] > last]
        if fresh.empty:
            return 0
        list_key = pair_list_key(self._prefix, symbol, timeframe, role, qualifier)
        retention = int(_RETENTION_MARGIN * self._max_entries * timeframe_minutes(timeframe) * 60)
        values = [column for column in fresh.columns if column not in ("bartime", "close_time")]
        rows = []
        for bartime, close_time, *rest in fresh[["bartime", "close_time", *values]].itertuples(index=False, name=None):
            stamp = pd.Timestamp(bartime).strftime(STAMP_FORMAT)
            fields = {"bartime": stamp, "close_time": pd.Timestamp(close_time).strftime(STAMP_FORMAT)}
            fields.update({column: _text(value) for column, value in zip(values, rest, strict=True)})
            rows.append(
                {"list_key": list_key, "stamp": stamp, "fields": fields, "retention_seconds": retention, "max_entries": self._max_entries}
            )
        created = write_list_hash_many(self._link.client, rows)
        self._written[key] = fresh["bartime"].iloc[-1]
        return created

    def drop_before(self, symbol: str, role: str, timeframe: str, first_bar: pd.Timestamp, qualifier: str = "") -> int:
        """Bỏ khỏi LIST ``{SYMBOL}_{ROLE}[_{QUALIFIER}]_{KHUNG}`` (và xoá HASH) mọi mốc cũ hơn nến ``first_bar``, tức nến đầu của cửa sổ đang giữ. Cần cho danh sách chỉ giữ SỰ KIỆN (TRIGGER): cửa sổ trượt
        theo nến chứ không theo số dòng, nên sự kiện trôi ra ngoài cửa sổ nến gần nhất phải bị bỏ bằng giờ nến. Trả về số mốc đã bỏ."""
        list_key = pair_list_key(self._prefix, symbol, timeframe, role, qualifier)
        return trim_lists_before(self._link.client, [(list_key, pd.Timestamp(first_bar).strftime(STAMP_FORMAT))])
