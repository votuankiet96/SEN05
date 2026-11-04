"""Tính hướng trend (tăng +1 / giảm -1 / trung tính 0) từ nến khung trend, GHI kết quả vào Redis (cổng 6380, db1) theo List–Hash và ĐỌC lại kết quả đó. Không đọc config, không biết chiến lược nào.

TÍNH (``compute_timeline``): nhận bảng nến của MỘT (symbol, khung trend) và thông số của khối trend đang bật (``params["TREND_METHODS"]``), chạy khối đó (trend_methods/<khối>.py qua
rules/trend_tf.build_trend_frame) và trả DÒNG THỜI GIAN hướng trend: mỗi nến trend một dòng (mốc mở nến, hướng, giờ đóng nến). Là cả bảng chứ không phải một giá trị vì các khối breakout, tstat và
cloud DỜI tín hiệu (bắn ở nến đầu tiên trong đợt mà trend đã cùng chiều), nên bên ghép cần hướng trend ở TỪNG nến.

GHI (``TrendOutput``): LIST ``{key_prefix}_{KHỐI}_{SYMBOL}_{KHUNG}`` (vd ``L_TREND_KNN_US30_H1``, tên do ``trend_list_key`` đặt) giữ các mốc nến trend (cũ đứng trước); HASH ``{LIST}:{giờ mở}`` giữ hướng
trend của nến đó. Mỗi lần chỉ gửi các nến chưa ghi (lần đầu cả cửa sổ); nến đã có thì redis_connector bỏ qua. Không có kênh Pub/Sub. Việc nối và ghi nguyên tử do redis_connector làm.

ĐỌC (``TrendReader``): đối xứng với ``TrendOutput``, dùng chung ``trend_list_key``. Tiến trình chiến lược dùng nó lấy lại dòng thời gian của một (khối, symbol, khung trend) để ghép vào nến vào lệnh
(strategy_runner.run_strategy). Đây là nơi duy nhất ngoài data_engine được đọc dữ liệu từ Redis (``lrange``, ``hmget``) vì file này giữ định dạng db trend; nối, ghi và nghe vẫn chỉ ở redis_connector.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import redis

from core_engine.src.redis_connector import Link, write_list_hash_many
from core_engine.src.rules.trend_tf import build_trend_frame

LOGGER = logging.getLogger(__name__)
TIMELINE_COLUMNS = ["bartime", "trend_bias", "trend_close_time"]
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def trend_list_key(prefix: str, method: str, symbol: str, trend_tf: str) -> str:
    """LIST các mốc xu hướng của một (khối, symbol, khung trend): ``{prefix}_{KHỐI}_{SYMBOL}_{KHUNG}``, vd ``L_TREND_KNN_US30_H1``. MỘT nơi đặt tên cho cả bên ghi lẫn bên đọc."""
    return f"{prefix}_{method.upper()}_{symbol.upper()}_{trend_tf.upper()}"


def _empty_timeline() -> pd.DataFrame:
    return pd.DataFrame(
        {"bartime": pd.Series(dtype="datetime64[us]"), "trend_bias": pd.Series(dtype="int64"), "trend_close_time": pd.Series(dtype="datetime64[us]")}
    )


def compute_timeline(candles: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Dòng thời gian hướng trend từ nến khung trend (tăng dần): chạy khối đang bật trong ``params["TREND_METHODS"]`` và giữ đúng ba cột ``TIMELINE_COLUMNS``."""
    frame = build_trend_frame(candles, params)
    return frame[TIMELINE_COLUMNS].reset_index(drop=True)


class TrendOutput:
    """Đầu ra của một tiến trình trend: ghi hướng trend của từng nến theo List–Hash lên Redis đầu ra (db riêng cho trend).

    ``client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối (một kết nối riêng). Nhớ mốc nến đã ghi của từng (khối, symbol, khung) để các lần sau chỉ gửi nến mới.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        retention_seconds: int,
        retention_max_entries: int,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
        if not str(key_prefix).strip():
            raise ValueError("trend output key_prefix must be configured")
        self._prefix = key_prefix
        self._retention_seconds = int(retention_seconds)
        self._retention_max_entries = int(retention_max_entries)
        self._link = Link(
            host=host, port=port, password=password, db=db,
            socket_timeout=socket_timeout, connect_timeout=connect_timeout, healthcheck_interval=healthcheck_interval, client=client,
        )
        self._written: dict[tuple[str, str, str], pd.Timestamp] = {}

    def list_key(self, method: str, symbol: str, trend_tf: str) -> str:
        return trend_list_key(self._prefix, method, symbol, trend_tf)

    def ping(self) -> None:
        self._link.ping()

    def close(self) -> None:
        self._link.close()

    def write(self, method: str, symbol: str, trend_tf: str, timeline: pd.DataFrame) -> int:
        """Ghi các nến trend CHƯA ghi của dòng thời gian (lần đầu: cả bảng). Trả về số nến thật sự được ghi mới (nến đã có sẵn trong Redis không tính)."""
        key = (method, symbol, trend_tf)
        last = self._written.get(key)
        fresh = timeline if last is None else timeline[timeline["bartime"] > last]
        if fresh.empty:
            return 0
        list_key = self.list_key(method, symbol, trend_tf)
        rows = [
            {
                "list_key": list_key,
                "stamp": stamp,
                "fields": {
                    "method": method.lower(),
                    "symbol": symbol.upper(),
                    "timeframe": trend_tf.upper(),
                    "bartime": stamp,
                    "trend_bias": str(int(bias)),
                    "close_time": pd.Timestamp(close_time).strftime(STAMP_FORMAT),
                },
                "retention_seconds": self._retention_seconds,
                "max_entries": self._retention_max_entries,
            }
            for stamp, bias, close_time in zip(fresh["bartime"].dt.strftime(STAMP_FORMAT), fresh["trend_bias"], fresh["trend_close_time"], strict=True)
        ]
        created = write_list_hash_many(self._link.client, rows)
        self._written[key] = fresh["bartime"].iloc[-1]
        return created


class TrendReader:
    """Đầu vào xu hướng của tiến trình chiến lược: đọc lại dòng thời gian hướng trend mà ``TrendOutput`` (tiến trình trend) đã ghi vào Redis đầu ra (db trend).

    Mỗi lần đọc là hai lượt gọi Redis bất kể bao nhiêu mốc: ``lrange`` lấy các mốc mới nhất rồi một pipeline ``hmget`` lấy hướng và giờ đóng của từng mốc. ``client`` cho test thay bằng bản giả;
    không truyền thì redis_connector tự nối (một kết nối riêng, chỉ đọc).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
    ) -> None:
        if not str(key_prefix).strip():
            raise ValueError("trend reader key_prefix must be configured")
        self._prefix = key_prefix
        self._link = Link(
            host=host, port=port, password=password, db=db,
            socket_timeout=socket_timeout, connect_timeout=connect_timeout, healthcheck_interval=healthcheck_interval, client=client,
        )

    def list_key(self, method: str, symbol: str, trend_tf: str) -> str:
        return trend_list_key(self._prefix, method, symbol, trend_tf)

    def ping(self) -> None:
        self._link.ping()

    def close(self) -> None:
        self._link.close()

    def read(self, method: str, symbol: str, trend_tf: str, count: int) -> pd.DataFrame:
        """``count`` mốc xu hướng MỚI NHẤT của (khối, symbol, khung trend), tăng dần, đúng ba cột ``TIMELINE_COLUMNS`` (``bartime`` và ``trend_close_time`` kiểu datetime64[us] như bảng nến).

        Chưa có LIST (tiến trình trend chưa ghi) thì trả bảng rỗng. Mốc có trong LIST mà HASH đã hết hạn thì bị bỏ và ghi cảnh báo (số mốc thiếu), để bên ghép không dùng nhầm hướng đã mất.
        """
        client = self._link.client
        list_key = self.list_key(method, symbol, trend_tf)
        stamps = client.lrange(list_key, -int(count), -1)
        if not stamps:
            return _empty_timeline()
        pipe = client.pipeline(transaction=False)
        for stamp in stamps:
            pipe.hmget(f"{list_key}:{stamp}", "trend_bias", "close_time")
        fields = pipe.execute()
        rows = [(stamp, bias, close) for stamp, (bias, close) in zip(stamps, fields, strict=True) if bias is not None and close is not None]
        if len(rows) < len(stamps):
            LOGGER.warning(
                "event=trend_marks_missing trend=%s symbol=%s timeframe=%s missing=%d of=%d", method, symbol, trend_tf, len(stamps) - len(rows), len(stamps),
            )
        if not rows:
            return _empty_timeline()
        frame = pd.DataFrame(rows, columns=TIMELINE_COLUMNS)
        frame["bartime"] = pd.to_datetime(frame["bartime"], format=STAMP_FORMAT).astype("datetime64[us]")
        frame["trend_close_time"] = pd.to_datetime(frame["trend_close_time"], format=STAMP_FORMAT).astype("datetime64[us]")
        frame["trend_bias"] = pd.to_numeric(frame["trend_bias"]).astype("int64")
        return frame.sort_values("bartime").reset_index(drop=True)
