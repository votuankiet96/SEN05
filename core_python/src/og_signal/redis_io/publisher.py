"""Publish backtest signal data to Redis DB0 ("past_signal", không lọc
trend) và DB1 ("past_signal_trend", có lọc trend) cho core_python's
"backtest cấp 2" flow -- 2 kênh đối chứng, cùng schema, chỉ khác client/
key_prefix truyền vào, xem CLAUDE.md.

Khác hẳn L_SIGNAL live (og_signal cũ / order_gateway, publish-once + TTL 7 ngày +
List bị LTRIM giữ N phần tử mới nhất, vì đó là tín hiệu LIVE để OF đặt
lệnh): dữ liệu ở đây là kết quả backtest lâu dài, cần giữ TOÀN BỘ lịch sử,
không hết hạn.

Chốt 2026-09-23: từ khi DP publish kèm phạm vi nến thay đổi thật
(`candle_count`/`from`/`to` trong message `dp:events:backfill`, xem
listener.py), worker.py chỉ tính lại ĐÚNG đoạn `[from, to]` đó rồi GHÉP vào
phần DB hiện có (đọc bằng read_past_signals() bên dưới, ghép ở
worker._merge_delta_frame()) trước khi gọi publish_past_signals() -- hàm
này vẫn luôn nhận 1 frame ĐẦY ĐỦ (đã ghép xong) và luôn DELETE+RPUSH lại từ
đầu như trước, không tự biết gì về khái niệm "delta". Tách trách nhiệm rõ:
publisher.py chỉ lo đọc/ghi Redis đúng schema, việc ghép nằm ở worker.py
(orchestration). Khi DP không gửi được from/to (payload cũ, hoặc
delivered_candles rỗng phía DP), worker.py tự rơi về tính lại TOÀN BỘ từ
SIGNAL_START_DATE như hành vi gốc trước 2026-09-23 -- publisher.py không
đổi gì trong trường hợp đó. Xem memory project_strategy_lab_redesign +
docstring worker.refresh_pair().

Hash dùng ĐÚNG schema cột của CSV (xem export_cli.signal_frame()) --
KHÔNG phải schema kiểu OF (tradeSide/orderType/stopLoss/ksl/ktp...) mà L_SIGNAL
live dùng, vì đây không phải dữ liệu đặt lệnh, chỉ là dữ liệu cho ctrader cli
simulation replay lại backtest. DB0/DB1 dùng chung đúng schema này --
trend chỉ ảnh hưởng hàng nào có signal, không đổi cột nào.

Kênh Pub/Sub báo ngược (`sl:events:signals`) đã bị bỏ 2026-09-26: xác nhận
thật trên Redis 0 subscriber, và ctrader cli simulation đọc thẳng dữ liệu
tại thời điểm backtest chứ không cần được báo -- publish_past_signals()
không còn tham số `event_channel`.
"""

from __future__ import annotations

import logging

import pandas as pd
import redis
from core_python.src.og_signal.redis_io.logging_setup import log_event

LOGGER = logging.getLogger(__name__)


def signal_list_key(key_prefix: str, symbol: str, timeframe: str, strategy: str) -> str:
    """LIST của 1 cặp (symbol, timeframe, strategy) -- cùng hình dạng key
    với L_SIGNAL (order_gateway/og_signal/redis_io/publisher.py): underscore
    nối, không dấu ':', SYMBOL trước TIMEFRAME, STRATEGY ở cuối vì 1 dòng
    dữ liệu backtest luôn gắn với đúng 1 chiến lược.
    """
    return f"{key_prefix}_{symbol.upper()}_{timeframe.upper()}_{strategy.upper()}"


def signal_hash_key(list_key: str, stamp: str) -> str:
    """HASH của 1 dòng tín hiệu: key LIST + ':' + stamp."""
    return f"{list_key}:{stamp}"


def publish_past_signals(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    frame: pd.DataFrame,
) -> int:
    """Ghi đè toàn bộ cửa sổ past-signal của (symbol, timeframe, strategy)
    từ `frame` (output của export_cli.signal_frame()) trong 1 lượt.

    Xoá sạch LIST + từng HASH cũ rồi ghi lại từ đầu -- không EXPIRE, không
    LTRIM giới hạn số lượng (khác hẳn publish_signal() của L_SIGNAL live), vì đây là
    dữ liệu backtest cần giữ đủ lịch sử. `stamp` dùng chính chuỗi "bartime"
    đã có sẵn trong `frame` -- "YYYY-MM-DD HH:MM:SS" (có giây, chốt
    2026-09-24, cùng quy ước với CSV lẫn DB0/DB1 của DP/OG live, xem
    export_cli.signal_frame()) -- field và tên key không bao giờ lệch nhau.

    Trả về số dòng đã ghi.
    """
    list_key = signal_list_key(key_prefix, symbol, timeframe, strategy)
    columns = list(frame.columns)

    log_event(
        LOGGER, logging.DEBUG, "redis_write_start", list_key=list_key,
        rows=len(frame), columns=",".join(columns),
    )

    pipe = client.pipeline(transaction=True)
    pipe.delete(list_key)
    stamps: list[str] = []
    for _, row in frame.iterrows():
        stamp = str(row["bartime"])
        stamps.append(stamp)
        hash_key = signal_hash_key(list_key, stamp)
        pipe.delete(hash_key)
        pipe.hset(hash_key, mapping={column: str(row[column]) for column in columns})
    if stamps:
        pipe.rpush(list_key, *stamps)
    log_event(
        LOGGER, logging.DEBUG, "redis_delete_rebuild", list_key=list_key,
        hash_keys_written=len(stamps),
    )

    pipe.execute()
    log_event(
        LOGGER, logging.DEBUG, "redis_write_done", list_key=list_key, rows_written=len(stamps),
    )
    return len(stamps)


def clear_all_signals(client: redis.Redis, *, key_prefix: str) -> int:
    """Xoá SẠCH toàn bộ dữ liệu past-signal (mọi LIST + mọi HASH mang tiền
    tố `key_prefix`, vd "L_PastSignal_*") -- dùng đúng 1 lần khi TIẾN
    TRÌNH worker thật sự khởi động lại (systemctl restart/start), KHÔNG
    phải mỗi lần chỉ mất kết nối Redis tạm thời rồi tự nối lại (xem nơi
    gọi ở worker.run_forever(), có cờ chặn gọi lặp).

    Lý do cần bước này (chốt 2026-09-24, sau sự cố thật): trước đó
    publish_past_signals() chỉ xoá-rồi-ghi-đè đúng tên (stamp) sẽ tồn tại
    trong frame mới -- nếu 1 "thế hệ" dữ liệu trước đổi cách đặt tên (đổi
    format bartime, đổi tham số chiến lược khiến tập bar có signal khác
    đi...), tên cũ không còn xuất hiện trong bất kỳ frame mới nào nên
    không bao giờ bị xoá, tồn tại vĩnh viễn làm rác (~53% dữ liệu DB0 rơi
    vào tình trạng này, phát hiện 2026-09-24). Thay vì sửa
    publish_past_signals() để tự dò-diệt rác (phức tạp, phải phân biệt
    "tên đổi" khỏi "tên vẫn vậy nhưng giá trị đổi"), đảm bảo Redis LUÔN
    sạch tinh trước mỗi lần bootstrap lại từ đầu -- đơn giản hơn nhiều và
    giải quyết đúng gốc: rác chỉ có thể sinh ra do đổi code/tham số, mà
    việc đó chỉ có hiệu lực sau khi restart tiến trình.

    Quét theo pattern `{key_prefix}_*` -- khớp cả LIST lẫn HASH vì cả 2
    đều mang tiền tố này (xem signal_list_key()/signal_hash_key()), xoá
    theo batch để không giữ 1 lệnh DEL quá lớn.

    Trong lúc hàm này chạy tới khi bootstrap xong, Redis DB0 của toàn bộ
    pair sẽ TRỐNG -- chấp nhận được có chủ đích (chốt cùng ngày): đây là
    dữ liệu phục vụ backtest, không cần luôn sẵn sàng tức thời.

    Trả về số key đã xoá.
    """
    deleted = 0
    batch: list[str] = []
    for key in client.scan_iter(f"{key_prefix}_*", count=1000):
        batch.append(key)
        if len(batch) >= 1000:
            deleted += client.delete(*batch)
            batch.clear()
    if batch:
        deleted += client.delete(*batch)
    return deleted


def read_past_signals(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
) -> pd.DataFrame:
    """Đọc lại toàn bộ signal hiện có trên DB0 cho 1 pair -- dùng để GHÉP
    delta vào (xem worker._merge_delta_frame()), KHÔNG dùng cho luồng ghi
    thường (publish_past_signals() không đọc gì trước khi ghi, luôn nhận
    frame đã đầy đủ).

    Đọc qua LRANGE (thứ tự stamp) + 1 pipeline HGETALL -- 2 round-trip bất
    kể bao nhiêu dòng, cùng cách order_gateway đọc DB0 của DP
    (candle_reader.read_candles_from_redis). Giá trị field vẫn là chuỗi
    (Redis luôn trả chuỗi, decode_responses=True ở client.py) -- caller tự
    ép kiểu nếu cần so sánh, giống hệt cách publish_past_signals() ghi
    xuống (`str(row[column])`), nên round-trip qua đây không đổi giá trị.

    Trả về DataFrame rỗng nếu LIST chưa từng tồn tại (pair mới, trigger
    delta đầu tiên rơi đúng vào 1 pair chưa có gì trên DB0).
    """
    list_key = signal_list_key(key_prefix, symbol, timeframe, strategy)
    stamps = client.lrange(list_key, 0, -1)
    if not stamps:
        return pd.DataFrame()

    pipe = client.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hgetall(signal_hash_key(list_key, stamp))
    rows = pipe.execute()
    return pd.DataFrame(rows)
