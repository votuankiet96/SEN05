"""Đối chiếu "kỳ vọng" với "đã publish" cho MỘT cặp (strategy, symbol, timeframe).

Mô tả:
    "Kỳ vọng" = tín hiệu chart tự tính bằng chính hàm chiến lược của des trên
    nến vừa đọc từ db đầu vào. "Đã publish" = tín hiệu des thực sự ghi ở db đầu
    ra. File này chỉ làm phép so sánh thuần -- không I/O, không biết Flask hay
    Redis -- nên test được bằng DataFrame dựng tay.

    Trạng thái cho mỗi nến có tín hiệu:
        ok          kỳ vọng và des đều có, cùng hướng
        pending     kỳ vọng có ở nến MỚI NHẤT nhưng des chưa ghi (có thể chỉ chậm)
        missed      kỳ vọng có ở nến đã CŨ hơn nến mới nhất mà des không ghi (đã
                    lỡ: des chỉ xét nến cuối của cặp)
        extra       des ghi nhưng chart tính không ra tín hiệu
        wrong_side  cả hai có nhưng ngược hướng
        unchecked   nằm ngoài vùng đối chiếu (trước khi des chạy, chưa đủ lịch
                    sử, hoặc nến chart chưa đọc tới)

    Độ chính xác: des chỉ thấy 500 nến gần nhất mỗi lần xử lý, còn chart chạy
    chiến lược trên cả cửa sổ đọc được (tối đa 1200 nến). Hai bên có thể khác
    nhau ở nhiễu làm tròn tại điểm hoà SMA (đo thật: ~1/12.400 tín hiệu). Vì vậy
    mọi khác biệt đều được kiểm lại bằng đúng cửa sổ của des (hàm `exact_signal`
    do server cung cấp) trước khi kết luận lỡ/thừa.

    File này CÓ import hàm dựng HASH của des (build_signal / to_hash_fields) để
    biết des SẼ ghi field gì cho một nến -- không chép lại công thức đó.

Đầu vào:
    DataFrame đã chạy chiến lược (cột bartime, signal), dict tín hiệu đã publish
    (stamp -> field HASH), mốc bắt đầu vùng đối chiếu.

Đầu ra:
    list[Check]; dict field HASH kỳ vọng; dict field khác biệt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from wisdom_engine.des.src.redis_io.candle_reader import STAMP_FORMAT
from wisdom_engine.des.src.redis_io.signal_publisher import (
    build_signal,
    to_hash_fields,
)

STATUS_OK = "ok"
STATUS_PENDING = "pending"
STATUS_MISSED = "missed"
STATUS_EXTRA = "extra"
STATUS_WRONG_SIDE = "wrong_side"
STATUS_UNCHECKED = "unchecked"

# Số lần tối đa kiểm lại bằng cửa sổ 500 nến mỗi lần đối chiếu. Khi des đứng hẳn
# sẽ có rất nhiều "kỳ vọng mà không có"; chặn trần để một lần tính không kéo dài.
# Quá trần thì tin kết quả cửa sổ lớn (chỉ sai ở nhiễu hoà SMA, ~1/12.400).
MAX_RECHECKS = 60

# Các field HASH được so giữa "des ghi" và "chart tự dựng". Đủ để bắt lệch cấu
# hình / lệch dữ liệu đầu vào, không phụ thuộc thời điểm ghi.
COMPARED_FIELDS = (
    "symbol",
    "timeframe",
    "strategy",
    "bartime",
    "tradeSide",
    "orderType",
    "stopPrice",
    "clientOrderId",
    "comment",
    "atr",
    "valid_from",
)

_SIDE_BY_TRADE = {"BUY": 1, "SELL": -1}


@dataclass(frozen=True)
class Check:
    """Kết quả đối chiếu cho một nến: giờ MỞ nến, hướng (+1 BUY / -1 SELL), trạng thái."""

    time: pd.Timestamp
    side: int
    status: str


def published_sides(
    published: dict[str, dict[str, str]],
) -> dict[pd.Timestamp, int]:
    """{giờ mở nến: +1/-1} từ HASH des đã ghi; HASH hỏng (không có tradeSide) bị bỏ qua."""
    sides: dict[pd.Timestamp, int] = {}
    for stamp, fields in published.items():
        side = _SIDE_BY_TRADE.get(str(fields.get("tradeSide", "")).upper())
        if side is not None:
            sides[pd.Timestamp(stamp)] = side
    return sides


def expected_sides(enriched: pd.DataFrame) -> dict[pd.Timestamp, int]:
    """{giờ mở nến: +1/-1} cho mọi nến mà chiến lược cho ra tín hiệu."""
    signal = pd.to_numeric(enriched["signal"], errors="coerce").fillna(0).astype(int)
    return {
        pd.Timestamp(time): int(side)
        for time, side in zip(enriched["bartime"], signal, strict=True)
        if side != 0
    }


class _Rechecker:
    """Gọi `exact_signal` tối đa `limit` lần; quá trần thì trả luôn giá trị dự phòng."""

    def __init__(self, exact_signal: Callable[[pd.Timestamp], int], limit: int) -> None:
        self._exact_signal = exact_signal
        self._left = limit

    def __call__(self, time: pd.Timestamp, fallback: int) -> int:
        if self._left <= 0:
            return fallback
        self._left -= 1
        return self._exact_signal(time)


def _check_one(
    time: pd.Timestamp,
    expected: int | None,
    actual: int | None,
    *,
    head_time: pd.Timestamp,
    check_from: pd.Timestamp | None,
    recheck: _Rechecker,
) -> Check | None:
    """Kết luận cho một nến; None = không có gì đáng ghi (không tín hiệu, hoặc ngoài vùng)."""
    in_region = check_from is not None and check_from <= time <= head_time
    if not in_region:
        # Ngoài vùng đối chiếu: tín hiệu des đã ghi vẫn được hiện (không kết luận).
        return Check(time, actual, STATUS_UNCHECKED) if actual is not None else None
    if expected is not None and actual is not None:
        status = STATUS_OK if expected == actual else STATUS_WRONG_SIDE
        return Check(time, actual, status)
    if expected is not None:
        exact = recheck(time, expected)
        if exact == 0:
            # Cửa sổ 500 nến của des không có tín hiệu (nhiễu hoà SMA): des
            # không ghi là ĐÚNG, không phải lỡ.
            return None
        status = STATUS_PENDING if time == head_time else STATUS_MISSED
        return Check(time, exact, status)
    # Chỉ des có: chart kiểm lại bằng cửa sổ của des trước khi gọi là "thừa".
    exact = recheck(time, actual)
    return Check(time, actual, STATUS_OK if exact == actual else STATUS_EXTRA)


def classify(
    enriched: pd.DataFrame,
    published: dict[str, dict[str, str]],
    *,
    check_from: pd.Timestamp | None,
    exact_signal: Callable[[pd.Timestamp], int],
    max_rechecks: int = MAX_RECHECKS,
) -> list[Check]:
    """Đối chiếu mọi nến trong `enriched` (và mọi tín hiệu đã publish).

    Args:
        enriched: DataFrame đã chạy chiến lược, bartime tăng dần; dòng cuối là
            nến mới nhất (nến duy nhất có thể ở trạng thái "pending").
        published: {stamp: field HASH} des đã ghi (chỉ phần trong vùng hiển thị).
        check_from: nến sớm nhất được đối chiếu; None = chưa có mốc (des chưa ghi
            gì) -> không kết luận nến nào.
        exact_signal: tính lại tín hiệu bằng đúng cửa sổ 500 nến kết thúc tại
            nến cho trước (+1/-1/0).
        max_rechecks: trần số lần gọi `exact_signal`.

    Returns:
        list[Check] theo thứ tự thời gian.
    """
    actual = published_sides(published)
    if enriched.empty:
        return [Check(time, side, STATUS_UNCHECKED) for time, side in sorted(actual.items())]
    head_time = pd.Timestamp(enriched["bartime"].iloc[-1])
    expected = expected_sides(enriched)
    recheck = _Rechecker(exact_signal, max_rechecks)
    checks = (
        _check_one(
            time,
            expected.get(time),
            actual.get(time),
            head_time=head_time,
            check_from=check_from,
            recheck=recheck,
        )
        for time in sorted(set(expected) | set(actual))
    )
    return [check for check in checks if check is not None]


def expected_fields(
    strategy: str,
    symbol: str,
    timeframe: str,
    row: pd.Series,
    *,
    tf_minutes: int,
) -> dict[str, str] | None:
    """Field HASH mà des SẼ ghi cho nến `row` (None nếu nến không có tín hiệu / thiếu số)."""
    valid_from = pd.Timestamp(row["bartime"]) + pd.Timedelta(minutes=tf_minutes)
    try:
        _, _, payload = build_signal(
            strategy=strategy,
            timeframe=timeframe,
            symbol=symbol,
            row=row,
            valid_from=valid_from,
        )
    except (ValueError, KeyError):
        return None
    return to_hash_fields(strategy, payload)


def field_diffs(
    checks: list[Check],
    expected: dict[pd.Timestamp, dict[str, str]],
    published: dict[str, dict[str, str]],
) -> dict[pd.Timestamp, list[str]]:
    """Field HASH khác nhau giữa "des ghi" và "chart tự dựng", chỉ cho nến trạng thái ok."""
    diffs: dict[pd.Timestamp, list[str]] = {}
    for check in checks:
        if check.status != STATUS_OK:
            continue
        des_fields = published.get(check.time.strftime(STAMP_FORMAT))
        chart_fields = expected.get(check.time)
        if des_fields is None or chart_fields is None:
            continue
        names = [
            name
            for name in COMPARED_FIELDS
            if chart_fields.get(name) != des_fields.get(name)
        ]
        if names:
            diffs[check.time] = names
    return diffs


def count_by_status(checks: list[Check]) -> dict[str, int]:
    """Đếm số nến theo từng trạng thái (đủ mọi khoá, kể cả 0)."""
    counts = dict.fromkeys(
        (
            STATUS_OK,
            STATUS_PENDING,
            STATUS_MISSED,
            STATUS_EXTRA,
            STATUS_WRONG_SIDE,
            STATUS_UNCHECKED,
        ),
        0,
    )
    for check in checks:
        counts[check.status] += 1
    return counts
