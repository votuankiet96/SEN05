"""Chạy MỘT chiến lược cho một cặp: bảng nến vào, bảng chiến lược ra (chỉ báo, setup, tín hiệu thô, tín hiệu đã lọc trend nếu có bảng xu hướng, entry). Chỉ tính toán: không chạm Redis, không đọc config,
không tự tính xu hướng.

Đây là nơi tổng hợp các cụm làm nên một chiến lược, theo đúng thứ tự cố định của ``StrategySpec`` (configuration.py): bảng nến đầu vào (do data_engine đưa) -> chỉ báo (indicator.py)
-> [ghép xu hướng, nếu được đưa bảng xu hướng] -> quy tắc BUY/SELL của chiến lược (strategies/<tên>.py) -> rules (alternating, trend, shift, entries). Thông số của chiến lược do configuration.py nạp
và chuẩn hoá (``StrategySpec.normalize_params``).

XU HƯỚNG: ``run_strategy(..., trend=bảng)``. Không có bảng thì chạy KHÔNG lọc trend (tín hiệu thô). Có bảng (hướng của từng nến khung trend, do trend_filtering tính và người gọi đọc từ db trend) thì mỗi nến
vào lệnh được gắn xu hướng của nến trend ĐÃ ĐÓNG trước khi nó mở (indicator.merge_trend_reference, không nhìn trước), rồi cùng chiến lược chạy với lọc trend bật; việc lọc và dời tín hiệu do
rules/trend_tf.py và rules/shift.py lo. Bảng xu hướng chỉ là đầu vào: file này không biết khối trend nào và không đọc Redis.

ĐẦU RA: ``RawSignalWriter`` ghi tín hiệu (dòng cuối của bảng) theo List–Hash vào Redis cổng 6380 (cấu trúc tín hiệu OE đọc, kèm kênh Pub/Sub), sau khi kiểm cửa sổ hiệu lực. Mỗi nguồn một bộ ghi với tiền tố riêng:
tín hiệu thô ở db0, tín hiệu đã lọc của từng khối trend ở db lọc. Việc nối và ghi nguyên tử do redis_connector làm; tên, nội dung, hạn hiệu lực là các hàm dùng chung ở signal_publisher.py.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

import pandas as pd
import redis

from core_engine.src.configuration import StrategySpec, get_strategy
from core_engine.src.indicator import merge_trend_reference
from core_engine.src.redis_connector import Link, write_list_hash
from core_engine.src.signal_publisher import (
    DEFAULT_PRICE_DECIMALS,
    as_utc,
    build_signal,
    channel_name,
    de_list_key,
    signal_message,
    signal_window,
    to_hash_fields,
)

LOGGER = logging.getLogger(__name__)

# =============================================================================
# Nhớ đệm (TUỲ CHỌN) cho phép tính chỉ báo, phép THUẦN bị lặp nhiều nhất ở đường live. Khoá = NỘI DUNG đầu vào (băm cả khung nến + đúng các tham số mà phép tính đọc) nên cùng đầu vào
# luôn ra cùng kết quả. Mặc định TẮT (test, CLI, dashboard chạy như cũ); chỉ dịch vụ live bật bằng enable_memo(). Giả định về tham số được khoá bằng tests/test_memo.py:
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
    (nguồn raw và mọi nguồn trend dùng chung một kết quả chỉ báo)."""
    store = _MEMO.store
    if store is None:
        return spec.add_indicators(bars, params)
    own = tuple(sorted((k, repr(v)) for k, v in params.items() if not k.startswith("TREND")))
    return store.get_or_compute(("ind", spec.key, own, _frame_digest(bars)), lambda: spec.add_indicators(bars, params))


def run_strategy(
    key: str,
    *,
    symbol: str,
    tf: str,
    bars: pd.DataFrame,
    overrides: dict[str, Any] | None = None,
    trend: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Chạy trọn pipeline của một chiến lược trên OHLCV đã load sẵn (theo đúng
    thứ tự invariant của StrategySpec).

    Args:
        key: Mã chiến lược ("combo", "ma_cross", "macd_break").
        symbol: Mã symbol — dùng để resolve symbol-specific params (session hours, khung trend).
        tf: Mã timeframe — bị từ chối nếu ngoài spec.supported_timeframes.
        bars: DataFrame OHLCV [bartime, open, high, low, close, volume],
            đã sort tăng dần (output của data_engine.read_candles / DataEngine.snapshot).
        overrides: Tham số override (khi chạy có lọc trend: TREND_METHODS, TREND_FILTER_ENABLED, TREND_TF).
        trend: Bảng xu hướng của MỘT khối trend (cột ``trend_close_time`` và ``trend_bias``, tăng dần). Không truyền thì không ghép
            xu hướng (chạy như tín hiệu thô). Có truyền thì ghép sau bước chỉ báo: mỗi nến vào lệnh nhận hướng của nến trend đã
            đóng không muộn hơn giờ MỞ của nó; nên truyền kèm ``overrides`` bật lọc trend để lọc có tác dụng.

    Returns:
        DataFrame OHLCV gốc + cột chỉ báo + signal + entry (có xu hướng thì thêm trend_close_time và trend_bias).

    Raises:
        KeyError: Chiến lược không tồn tại.
        ValueError: tf ngoài spec.supported_timeframes.
    """
    spec = get_strategy(key)
    symbol = str(symbol).strip().upper()
    tf = str(tf).strip().upper()
    if spec.supported_timeframes and tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(f"Strategy '{spec.key}' supports only these timeframes: {allowed}.")

    params = spec.normalize_params(overrides, symbol, tf)
    enriched = _add_indicators(spec, bars, params)
    if trend is not None:
        enriched = merge_trend_reference(enriched, trend)
    enriched = spec.detect_signals(enriched, symbol=symbol, params=params)
    enriched = spec.add_entries(enriched, params, symbol)
    return enriched


# =============================================================================
# Đầu ra: ghi tín hiệu vào Redis (cổng 6380): thô ở db0, đã lọc trend ở db lọc; mỗi nguồn một bộ ghi với tiền tố riêng.
# =============================================================================


class Outcome(NamedTuple):
    """Kết quả xử lý dòng cuối của một cặp: ``state`` là một trong no_signal / expired / published / duplicate; ``published`` là số tín hiệu ghi mới (0 hoặc 1)."""

    state: str
    published: int


class RawSignalWriter:
    """Đầu ra của MỘT nguồn tín hiệu: nhận dòng cuối của bảng chiến lược, kiểm hạn hiệu lực rồi ghi tín hiệu theo List–Hash (và kênh Pub/Sub) lên Redis đầu ra, db và tiền tố của nguồn đó.

    Nguồn thô (``source="raw"``, db0) ghi dòng cuối của bảng KHÔNG lọc trend; nguồn lọc (``source`` = tên khối trend, db lọc) ghi dòng cuối của bảng đã lọc trend. Tên lớp giữ nguyên từ
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
        """Xử lý dòng cuối (nến mới nhất) của bảng chiến lược: không tín hiệu thì thôi; còn tín hiệu thì chỉ ghi khi giờ ``now`` còn trước hạn chót (sự kiện chỉ tới sau khi nến đã đóng)."""
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
