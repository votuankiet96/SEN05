"""Đọc Redis cho chart_simul -- CHỈ ĐỌC, không có lệnh ghi/xoá nào ở đây.

Mô tả:
    Đây là cửa DUY NHẤT chart_simul chạm vào Redis: nến do DFS phát (db đầu vào
    của des), tín hiệu des đã ghi ở HAI db đầu ra (db không lọc trend và db đã lọc
    trend, xem redis.output / redis.output_trend) và đồng hồ ảo của DFS. Không tính
    chỉ báo, không dựng JSON -- đó là việc của server.py / payload.py.

    Nến được đọc bằng ĐÚNG hàm của des (candle_reader.read_candles_from_redis)
    để chart thấy nến y hệt cách des thấy. Tên key (prefix, db) lấy từ
    des_config.yaml qua normalize_app_config, nên đổi prefix không phải sửa gì ở
    đây.

    Mọi lệnh ở đây là lệnh đọc (LRANGE/LINDEX/LLEN/HMGET/HGETALL/GET) và chạy
    trên client riêng của chart, không dùng chung kết nối với des.

Đầu vào:
    dict cấu hình des (des_config.yaml) -- redis.input / redis.output / redis.output_trend.

Đầu ra:
    DataFrame nến (bartime tăng dần), dict tín hiệu (stamp -> field HASH) và
    dict đồng hồ DFS.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd
import redis
from wisdom_engine.des.src.redis_io.candle_reader import (
    STAMP_FORMAT,
    create_client,
    normalize_app_config,
    pair_list_key,
    read_candles_from_redis,
)
from wisdom_engine.des.src.redis_io.signal_publisher import (
    og_list_key,
    stamp_hash_key,
)

# Hợp đồng với DFS (dfs/src/redis_writer.py): đồng hồ ảo và checkpoint nằm ngoài
# mẫu L_DFS_* mà des lắng nghe, cùng db với nến.
DFS_CLOCK_KEY = "dfs:clock"
DFS_STATE_KEY = "dfs:state"

# Sức chứa LIST nến của DFS (bars_per_snapshot) -- đọc nhiều hơn cũng không có.
READ_BARS = 1200

# 15 timeframe mà DFS phát cho mỗi symbol (CLAUDE.md mục 10), xếp nhỏ -> lớn theo số phút.
DFS_TIMEFRAMES = (
    "M5", "M10", "M15", "M20", "M30", "M45", "H1", "M90", "H2", "H3", "H4", "H6", "H8", "D1", "W",
)

Pair = tuple[str, str, str]  # (strategy, symbol, timeframe)


class RedisSource:
    """Truy cập Redis chỉ-đọc cho một phiên chart."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        input_client: redis.Redis | None = None,
        output_client: redis.Redis | None = None,
        trend_output_client: redis.Redis | None = None,
    ) -> None:
        redis_config = normalize_app_config(config)["redis"]
        input_config = redis_config["input"]
        output_config = redis_config["output"]
        trend_output_config = redis_config["output_trend"]
        self.input_prefix = str(input_config["key_prefix"])
        self.output_prefix = str(output_config["key_prefix"])
        self.trend_output_prefix = str(trend_output_config["key_prefix"])
        # Số nến des đọc mỗi lần xử lý một sự kiện -- chart dùng đúng số này khi
        # dựng lại "cửa sổ mà des đã thấy" (xem server.py: exact_signal).
        self.snapshot_bars = int(input_config["snapshot_bars"])
        # redis.Redis chỉ nối khi có lệnh đầu tiên, nên dựng client ở đây không
        # làm server chết khi Redis chưa sẵn sàng.
        self._input = input_client or create_client(redis_config, input_config["db"])
        self._output = output_client or create_client(redis_config, output_config["db"])
        self._trend_output = trend_output_client or create_client(redis_config, trend_output_config["db"])

    # ------------------------------------------------------------------ DFS
    def sim_clock(self) -> dict[str, Any]:
        """Đồng hồ ảo + trạng thái chạy của DFS (khoá thiếu -> giá trị None)."""
        pipe = self._input.pipeline(transaction=False)
        pipe.get(DFS_CLOCK_KEY)
        pipe.hgetall(DFS_STATE_KEY)
        clock, state = pipe.execute()
        state = state or {}
        tick = state.get("tick_seq")
        return {
            "simTime": clock or state.get("sim_time"),
            "tick": int(tick) if tick not in (None, "") else None,
            "status": state.get("status"),
            "runId": state.get("run_id"),
        }

    # ------------------------------------------------------------------ nến
    def head_stamp(self, symbol: str, timeframe: str) -> str | None:
        """Stamp nến mới nhất (đầu LIST) -- 1 lệnh, dùng để biết có nến mới chưa."""
        list_key = pair_list_key(self.input_prefix, symbol, timeframe)
        return self._input.lindex(list_key, 0)

    def heads(self, symbol: str, timeframes: Sequence[str]) -> dict[str, str | None]:
        """Stamp nến mới nhất của từng timeframe -- 1 vòng Redis (pipeline) cho cả symbol."""
        pipe = self._input.pipeline(transaction=False)
        for timeframe in timeframes:
            pipe.lindex(pair_list_key(self.input_prefix, symbol, timeframe), 0)
        return dict(zip(timeframes, pipe.execute(), strict=True))

    def read_candles(
        self, symbol: str, timeframe: str, count: int = READ_BARS
    ) -> pd.DataFrame:
        """`count` nến mới nhất, bartime tăng dần -- cùng hàm đọc với des."""
        return read_candles_from_redis(
            self._input,
            symbol=symbol,
            timeframe=timeframe,
            key_prefix=self.input_prefix,
            snapshot_bars=count,
        )

    # -------------------------------------------------------------- tín hiệu
    def signal_count(self, pair: Pair, *, trend: bool = False) -> int:
        """Số tín hiệu des đã ghi cho cặp này (độ dài LIST) -- 1 lệnh. `trend=True`: db đã lọc trend."""
        client, _ = self._output_for(trend)
        return int(client.llen(self._list_key(pair, trend=trend)))

    def read_signals(
        self, pair: Pair, since: pd.Timestamp | None = None, *, trend: bool = False
    ) -> dict[str, dict[str, str]]:
        """Tín hiệu đã ghi từ `since` trở đi: {stamp: field của HASH}.

        LIST tín hiệu của des xếp stamp TĂNG dần; chuỗi stamp cố định độ rộng
        nên so sánh chuỗi đúng thứ tự thời gian. HASH rỗng (đã bị xoá giữa
        chừng) bị bỏ qua. `trend=True`: đọc db đã lọc trend thay cho db không lọc.
        """
        client, _ = self._output_for(trend)
        list_key = self._list_key(pair, trend=trend)
        stamps = client.lrange(list_key, 0, -1)
        if since is not None:
            floor = since.strftime(STAMP_FORMAT)
            stamps = [stamp for stamp in stamps if stamp >= floor]
        if not stamps:
            return {}
        pipe = client.pipeline(transaction=False)
        for stamp in stamps:
            pipe.hgetall(stamp_hash_key(list_key, stamp))
        rows = pipe.execute()
        return {
            stamp: fields
            for stamp, fields in zip(stamps, rows, strict=True)
            if fields
        }

    def first_signal_stamp(self, pairs: list[Pair]) -> str | None:
        """Stamp sớm nhất trong mọi LIST tín hiệu của db KHÔNG lọc trend -- mốc gần đúng des bắt đầu ghi.

        Dùng để không báo "LỠ" cho các nến TRƯỚC khi des chạy (db tín hiệu bị
        xoá mỗi lần des khởi động, nên mốc này tự dời theo lần chạy hiện tại).
        Luôn lấy từ db không lọc trend, kể cả khi đang xem trang có trend: mọi tín hiệu thô đều vào db đó,
        còn db đã lọc trend ít tín hiệu hơn nên mốc của nó trễ hơn mốc des thật sự bắt đầu.
        """
        pipe = self._output.pipeline(transaction=False)
        for pair in pairs:
            pipe.lindex(self._list_key(pair), 0)
        heads = [stamp for stamp in pipe.execute() if stamp]
        return min(heads) if heads else None

    def _output_for(self, trend: bool) -> tuple[redis.Redis, str]:
        """(client, tiền tố) của db đầu ra cần đọc: đã lọc trend hoặc không lọc."""
        if trend:
            return self._trend_output, self.trend_output_prefix
        return self._output, self.output_prefix

    def _list_key(self, pair: Pair, *, trend: bool = False) -> str:
        strategy, symbol, timeframe = pair
        return og_list_key(self._output_for(trend)[1], strategy, symbol, timeframe)
