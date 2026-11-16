"""
Danh mục khối trend của DE: có những khối nào, dựng khung trend ra sao, và mỗi khối được dùng thế nào (chính sách). Mỗi phương pháp xác nhận xu hướng là MỘT khối riêng, độc lập, trong thư mục
này (một file mỗi khối; KNN, BREAKOUT, ZIGZAG, TSTAT, CLOUD) và chỉ trả hướng +1/-1/0 của từng nến. File này nối các khối với phần còn lại, gồm ba việc:

    1. Sổ đăng ký METHODS ("tên -> khối") và parse_methods (chuẩn hoá danh sách khối từ config, báo lỗi rõ). THÊM KHỐI MỚI = thêm file khối, import nó, thêm một dòng vào METHODS và (nếu khối cần
       chính sách khác mặc định) một dòng vào POLICY. Config tự nhận tên mới.
    2. build_trend_frame: chạy MỘT khối trên nến khung lớn, ra cột trend_bias (+1/-1/0). strategy_runner gọi hàm này để ghi bảng TREND (db1), kèm close_time (giờ đóng nến) mà runner tự tính.
    3. Bảng chính sách POLICY (một ô cho mỗi khối) cùng các hàm trả lời: same_timeframe (khối tính trên CHÍNH khung của tín hiệu; min_trend_tf nâng khung đó lên mức tối thiểu), first_aligned_per_run
       (mỗi đợt bắn đúng MỘT tín hiệu ở nến đầu tiên trend đã cùng chiều; thuật toán ở rules/shift.py), trend_warmup_bars (số nến khung trend khối đòi, vì có máy trạng thái nhớ lâu).
       configuration dùng chúng để dựng ô và cửa sổ tính; signal_publisher dùng uses_first_aligned_per_run để chọn cách lọc.

Việc LỌC tín hiệu theo trend và context nằm ở signal_publisher.apply_trend_filter; lọc lặp cùng chiều ở rules/alternating.py. Các khối KHÔNG được import file này (sẽ vòng import).
Phụ thuộc: chỉ trend_methods/<khối>.py. File này không đọc mã khung (M45, H4...): việc đổi mã khung ra độ dài và chọn khung trend của từng ô là của configuration.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType
from typing import NamedTuple

import pandas as pd
from core_engine.src.trend_methods import breakout, cloud, knn, tstat, zigzag

# =============================================================================
# Sổ đăng ký các khối xác nhận xu hướng + dựng khung trend (phía TẠO trend_bias).
# =============================================================================

METHODS: dict[str, ModuleType] = {
    knn.NAME: knn,
    breakout.NAME: breakout,
    zigzag.NAME: zigzag,
    tstat.NAME: tstat,
    cloud.NAME: cloud,
}


class MethodPolicy(NamedTuple):
    """Chính sách lọc của MỘT khối trend. Khối không có ô trong POLICY dùng giá trị mặc định (không cờ nào bật, min_trend_tf rỗng)."""

    # True = khối tính trên CHÍNH khung của tín hiệu (không có khung tham chiếu riêng, không tra timeframes_by_symbol); riêng
    # min_trend_tf (cuối bảng) có thể nâng khung đó lên một mức tối thiểu.
    same_timeframe: bool = False
    # True = mỗi đợt setup cùng chiều (khoảng giữa hai lần bộ lọc xen kẽ bắn) bắn đúng MỘT tín hiệu, ở nến ĐẦU TIÊN mà
    # trend đã cùng chiều (người dùng chốt 2026-10-22 cho breakout, 2026-10-26 cho tstat; thuật toán ở rules/shift.py). False = chỉ xét nến đầu của đợt, nến đó lệch trend
    # thì mất cả đợt.
    first_aligned_per_run: bool = False
    # Số nến KHUNG TREND tải thêm TRƯỚC mốc tính lại (warm-up của nến trend) để đường làm mới delta của worker khớp bản tính
    # từ đầu. 0 = dùng warm-up chung của hệ (defaults.warmup_bars). Khối có máy trạng thái nhớ lâu (zigzag: hướng chỉ lật
    # khi đủ cả cấu trúc lẫn giá đóng cửa) cần nhiều hơn; chỉ ảnh hưởng đường delta của worker, bootstrap tính từ đầu.
    trend_warmup_bars: int = 0
    # Chỉ có nghĩa khi same_timeframe=True: khung trend = khung vào lệnh NHƯNG không nhỏ hơn mức này (mã khung, vd "H1"); "" = không giới hạn
    # (breakout: đúng khung vào lệnh). Đặt SAU các trường cũ để mọi lời gọi MethodPolicy(a, b, c) theo vị trí vẫn nguyên nghĩa.
    min_trend_tf: str = ""


# Chính sách theo khối — NƠI DUY NHẤT quyết định; khối trend không mang cờ chính sách nào.
POLICY: dict[str, MethodPolicy] = {
    # knn: khung tham chiếu riêng, KHÔNG dời. Từng bật dời rồi tắt lại ngày 2026-10-23 (chủ hệ chốt): hướng KNN đổi chiều quá nhanh (đoạn cùng hướng ~10 nến, breakout ~150), nên luật
    # "chờ nến đầu tiên trend cùng chiều" luôn tìm được nến hợp lệ trong vài nến: macd_break 96,4% đợt ra tín hiệu, chỉ bị lùi giờ chứ không được lọc thêm.
    knn.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=False),
    # breakout: giữ hướng tới cú phá kế tiếp (máy trạng thái) mà đoạn cùng hướng có thể dài hơn warm-up chung (HK50 M10: lần phá gần nhất nằm ngoài 400 nến nên hướng bị mất).
    # Chủ hệ chốt 2026-11-10: tính trên cả nến nguồn giữ (1200); trend_warmup_bars chỉ chọn số nến tính của runner (bị chặn ở số nến nguồn giữ).
    breakout.NAME: MethodPolicy(same_timeframe=True, first_aligned_per_run=True, trend_warmup_bars=1200),
    # zigzag (2026-10-24): khung tham chiếu riêng như knn, KHÔNG dời (chủ hệ chưa chọn khác; first_aligned_per_run=True là lựa chọn có thể cân nhắc vì hướng đổi thưa, gần breakout).
    # trend_warmup_bars=3000: máy trạng thái nhớ lâu. Đo 2026-10-24 (60 chuỗi x 15 cửa sổ x 2 loại; delta của worker so với tính từ nến đầu): N=500 lệch 1,00-2,89% cửa sổ;
    # N=1.000 lệch 0,11-0,56%; N=1.500..3.000 lệch 0 trên 1.800 cửa sổ. Bộ Depth 13 (nhớ lâu hơn) cần 2.500. Đặt 3.000 = gấp đôi mức thấp nhất đã đo là 0 lệch.
    zigzag.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=False, trend_warmup_bars=3000),
    # tstat (2026-10-26): khung tham chiếu riêng như knn, CÓ dời (chủ hệ chốt sau khi xem số đo: macd_break giữ 6-32% -> 43-68% tín hiệu thô khi khung vào lệnh M10-M45, 25-27% -> 79-81% ở H1-H4;
    # trễ trung vị ~22 nến ở H1-H4, ~1 ngày ở M10-M45 với khung H1). Không có trạng thái dài (hướng nến t chỉ phụ thuộc N nến cuối) nên warm-up chung đủ khi N + trễ Newey-West < 300 (test khoá).
    tstat.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=True),
    # cloud (2026-10-28): khung trend = chính khung vào lệnh nhưng không nhỏ hơn H1 (M10-M45 dùng H1; H1-H4 dùng chính nó), CÓ dời như breakout/tstat. Chưa có backtest/P&L.
    cloud.NAME: MethodPolicy(same_timeframe=True, first_aligned_per_run=True, min_trend_tf="H1"),
}

_CONTRACT = ("NAME", "build")


def bias_column(name: str) -> str:
    """Tên cột hướng riêng của một khối (khối PHẢI tạo đúng cột này trong build())."""
    return f"trend_bias_{name}"


def _check_contract() -> None:
    """Báo lỗi ngay lúc nạp nếu một khối thiếu phần của hợp đồng (xem docstring knn.py) hoặc POLICY nhắc khối chưa đăng ký."""
    for name, block in METHODS.items():
        missing = [attr for attr in _CONTRACT if not hasattr(block, attr)]
        if missing or block.NAME != name:
            raise ValueError(f"trend method '{name}' violates the block contract (missing {missing}, NAME={getattr(block, 'NAME', None)!r}).")
    unregistered = [name for name in POLICY if name not in METHODS]
    if unregistered:
        raise ValueError(f"POLICY lists trend method(s) that are not registered: {', '.join(unregistered)}.")
    for name, policy in POLICY.items():
        if policy.min_trend_tf and not policy.same_timeframe:
            raise ValueError(f"POLICY['{name}'] sets min_trend_tf but not same_timeframe: the minimum only applies to entry-based blocks.")


_check_contract()


def method_names() -> tuple[str, ...]:
    """Tên các khối đã đăng ký, theo thứ tự đăng ký."""
    return tuple(METHODS)


def get_method(name: str) -> ModuleType:
    """Khối theo tên (tên đã qua parse_methods)."""
    return METHODS[name]


def policy_of(name: str) -> MethodPolicy:
    """Chính sách lọc của một khối theo tên (khối không có ô trong POLICY: mặc định)."""
    get_method(name)  # tên lạ -> KeyError, như mọi hàm tra theo tên khác
    return POLICY.get(name, MethodPolicy())


def is_same_timeframe(name: str) -> bool:
    """True nếu khối tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe), không có khung tham chiếu riêng."""
    return policy_of(name).same_timeframe


def uses_entry_timeframe(methods: Iterable[str]) -> bool:
    """
    True nếu mọi khối đang bật tính trên chính khung của tín hiệu: khung trend suy ra từ khung vào lệnh (entry_based_trend_tf:
    đúng khung vào lệnh, hoặc mức tối thiểu min_trend_tf nếu khung vào lệnh nhỏ hơn), không tra bảng timeframes_by_symbol và không
    bị giới hạn trong trend_timeframes. Trộn khối kiểu này với khối cần khung tham chiếu riêng (vd knn + breakout) là lỗi vì mọi
    khối đang bật dùng chung MỘT khung trend (docstring đầu file).
    """
    names = tuple(methods)
    flags = [is_same_timeframe(name) for name in names]
    if any(flags) and not all(flags):
        entry_side = ", ".join(name for name, flag in zip(names, flags, strict=True) if flag)
        reference_side = ", ".join(name for name, flag in zip(names, flags, strict=True) if not flag)
        raise ValueError(
            f"Trend methods {entry_side} (entry timeframe) and {reference_side} (reference timeframe) cannot be combined: "
            "all enabled trend methods share one trend timeframe."
        )
    return bool(flags) and all(flags)


def uses_first_aligned_per_run(methods: Iterable[str]) -> bool:
    """
    True nếu có khối đang bật mang chính sách first_aligned_per_run: bộ lọc trend giữ tín hiệu ĐẦU TIÊN SAU KHI trend
    xác nhận trong mỗi đợt setup, thay vì chỉ xét nến đầu của đợt (xem apply_trend_filter). Chỉ cần MỘT khối có chính
    sách là đủ (hiện không thể trộn knn với breakout vì hai khối cần khung trend khác nhau, xem uses_entry_timeframe).
    """
    return any(policy_of(name).first_aligned_per_run for name in methods)


def max_trend_warmup_bars(methods: Iterable[str]) -> int:
    """
    Số nến khung trend lớn nhất mà các khối đang bật đòi tải trước mốc tính lại (chính sách trend_warmup_bars); 0 = không khối
    nào đòi thêm, người gọi dùng warm-up chung của hệ. Nhiều khối bật cùng lúc thì lấy khối đòi nhiều nhất.
    """
    return max((policy_of(name).trend_warmup_bars for name in methods), default=0)


def parse_methods(value: object, *, label: str = "trend methods") -> tuple[str, ...]:
    """
    Chuẩn hoá danh sách khối xác nhận xu hướng được bật.

    value: list/tuple (từ config YAML) hoặc chuỗi cách nhau bằng dấu phẩy.
    Trả về tuple tên chữ thường, giữ thứ tự khai báo. Danh sách rỗng, tên lạ hoặc tên trùng đều raise
    ValueError kèm danh sách tên hợp lệ; kiểu dữ liệu sai (không phải list/tuple/chuỗi) raise TypeError. `label` là tên hiển thị trong thông báo lỗi (vd đường dẫn config).
    """
    if isinstance(value, str):
        items = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip().lower() for part in value]
    else:
        raise TypeError(f"{label} must be a list of trend methods or a comma-separated string.")
    available = ", ".join(METHODS)
    if not items or any(not item for item in items):
        raise ValueError(f"{label} must list at least one trend method with no empty entry (available: {available}).")
    unknown = [item for item in items if item not in METHODS]
    if unknown:
        raise ValueError(f"{label} has unknown trend method(s): {', '.join(unknown)} (available: {available}).")
    duplicated = sorted({item for item in items if items.count(item) > 1})
    if duplicated:
        raise ValueError(f"{label} lists the same trend method more than once: {', '.join(duplicated)}.")
    return tuple(items)


def build_trend_frame(trend_bars: pd.DataFrame, method: str) -> pd.DataFrame:
    """
    Dựng frame trend từ nến khung lớn: chạy khối ``method``, lấy hướng của nó thành cột trend_bias (+1/-1/0, thiếu coi là 0).

    Cột hướng riêng của khối (trend_bias_<tên>) và các cột riêng khác nằm lại trong frame trend (nội bộ), không được mang sang khung vào lệnh (xem signal_publisher.merge_trend_reference).
    """
    out = get_method(method).build(trend_bars)
    if bias_column(method) not in out.columns:
        raise ValueError(f"trend method '{method}' did not produce the '{bias_column(method)}' column.")

    out = out.copy()
    out["trend_bias"] = pd.to_numeric(out[bias_column(method)], errors="coerce").fillna(0).astype(int)
    return out

