"""
Bộ điều phối DUY NHẤT của phần xu hướng và của hai bộ lọc tín hiệu (lọc lặp cùng chiều, rồi lọc trend). Mọi CHÍNH SÁCH
lọc nằm ở file này: khối trend chỉ tính hướng, chiến lược chỉ khai báo setup và câu lý do của mình.

Mỗi phương pháp xác nhận xu hướng là MỘT khối riêng, độc lập, trong trend_methods/ (một file mỗi khối; KNN là khối đầu
tiên, BREAKOUT là khối thứ hai, ZIGZAG là khối thứ ba, TSTAT là khối thứ tư) và chỉ trả hướng +1/-1/0 của từng nến. File này nối các khối với phần còn lại, gồm bốn
nhóm việc:

    1. Sổ đăng ký + dựng khung trend (phía TẠO trend_bias):
       - METHODS: sổ đăng ký "tên -> khối". THÊM KHỐI MỚI = thêm file khối trong trend_methods/, import
         nó, thêm một dòng vào METHODS và (nếu khối cần chính sách khác mặc định) một dòng vào POLICY ở dưới.
         Config/CLI/dashboard tự nhận tên mới, không phải sửa nơi khác.
       - parse_methods: chuẩn hoá danh sách khối được bật (từ config hoặc override) và báo lỗi rõ.
       - build_trend_frame: chạy từng khối đang bật trên nến khung lớn, ghép hướng của chúng thành MỘT cột
         trend_bias (đồng thuận, xem combine_trend_bias) và thêm trend_close_time (giờ đóng nến khung lớn,
         để ghép không nhìn trước tương lai).
    2. Bảng chính sách POLICY (một ô cho mỗi khối): MỘT khối được dùng như thế nào.
       - same_timeframe: khối tính trên CHÍNH khung của tín hiệu (không có khung tham chiếu riêng, TREND_TF = khung
         vào lệnh, xem uses_entry_timeframe);
       - first_aligned_per_run: mỗi đợt setup bắn đúng MỘT tín hiệu, ở nến đầu tiên trend đã cùng chiều, nên tín hiệu
         đầu tiên sau khi trend xác nhận không bị mất khi nến đầu của đợt tới trước lúc trend đổi hướng (xem
         uses_first_aligned_per_run, apply_trend_filter; thuật toán ở rules/shift.py);
       - trend_warmup_bars: số nến khung trend khối đòi tải trước mốc tính lại (khối có máy trạng thái nhớ lâu), để đường
         làm mới delta của worker khớp bản tính từ đầu (xem max_trend_warmup_bars).
    3. Bộ lọc trend (phía DÙNG trend_bias): trend_filter_enabled, combine_trend_bias, apply_trend_filter.
    4. Điều phối: apply_signal_filters chạy lọc lặp cùng chiều (rules/alternating.py) rồi lọc trend và ghi bốn cột
       raw_signal, signal, trend_filter_status, signal_reason. Thứ tự hai bộ lọc, định nghĩa "đợt", cách giữ tín hiệu của
       chiến lược có setup là SỰ KIỆN và câu lý do kèm theo đều quyết ở đây.

Chiến lược chỉ gọi apply_signal_filters (truyền mask setup và câu lý do của mình) và không biết (không cần biết) có bao
nhiêu khối đang bật, khối nào dùng khung nào, hay khối đó được lọc theo chính sách nào.

Phụ thuộc: rules/alternating.py, rules/shift.py, trend_methods/<khối>.py và indicator.py (timeframe_minutes). Đây là file DUY NHẤT
trong rules/ import indicator.py/trend_methods/ (ngoại lệ của quy tắc "rules/ là tầng đáy", CLAUDE.md mục 2); các
khối KHÔNG được import file này (sẽ vòng import). Giả định hiện tại: mọi khối đang bật dùng CHUNG một khung tham chiếu (TREND_TF).
"""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType
from typing import NamedTuple

import pandas as pd
from wisdom_engine.we_core.src.indicator import timeframe_minutes
from wisdom_engine.we_core.src.rules.alternating import apply_alternating_filter
from wisdom_engine.we_core.src.rules.shift import allowed_masks, first_aligned_signal
from wisdom_engine.we_core.src.trend_methods import breakout, knn, tstat, zigzag

# =============================================================================
# Sổ đăng ký các khối xác nhận xu hướng + dựng khung trend (phía TẠO trend_bias).
# =============================================================================

METHODS: dict[str, ModuleType] = {
    knn.NAME: knn,
    breakout.NAME: breakout,
    zigzag.NAME: zigzag,
    tstat.NAME: tstat,
}


class MethodPolicy(NamedTuple):
    """Chính sách lọc của MỘT khối trend. Khối không có ô trong POLICY dùng giá trị mặc định (cả hai False)."""

    # True = khối tính trên CHÍNH khung của tín hiệu (không có khung tham chiếu riêng, không tra trend_tf_by_symbol).
    same_timeframe: bool = False
    # True = mỗi đợt setup cùng chiều (khoảng giữa hai lần bộ lọc xen kẽ bắn) bắn đúng MỘT tín hiệu, ở nến ĐẦU TIÊN mà
    # trend đã cùng chiều (người dùng chốt 2026-10-22 cho breakout, 2026-10-26 cho tstat; thuật toán ở rules/shift.py). False = chỉ xét nến đầu của đợt, nến đó lệch trend
    # thì mất cả đợt.
    first_aligned_per_run: bool = False
    # Số nến KHUNG TREND tải thêm TRƯỚC mốc tính lại (warm-up của nến trend) để đường làm mới delta của worker khớp bản tính
    # từ đầu. 0 = dùng warm-up chung của hệ (defaults.warmup_bars). Khối có máy trạng thái nhớ lâu (zigzag: hướng chỉ lật
    # khi đủ cả cấu trúc lẫn giá đóng cửa) cần nhiều hơn; chỉ ảnh hưởng đường delta của worker, bootstrap tính từ đầu.
    trend_warmup_bars: int = 0


# Chính sách theo khối — NƠI DUY NHẤT quyết định; khối trend không mang cờ chính sách nào.
POLICY: dict[str, MethodPolicy] = {
    # knn: khung tham chiếu riêng và KHÔNG dời tín hiệu. Đã bật first_aligned_per_run cho knn ngày 2026-10-23 rồi tắt lại cùng
    # ngày (người dùng chốt) vì hướng KNN đổi chiều quá nhanh: đoạn cùng hướng trung bình ~10 nến (~9,8 lần đổi/100 nến) so
    # với ~150 nến (0,6–0,7 lần/100 nến) của breakout, nên luật "chờ nến đầu tiên trend cùng chiều" luôn tìm được nến hợp lệ
    # trong vài nến: macd_break có 96,4% đợt ra tín hiệu, chỉ bị lùi giờ chứ không được lọc thêm gì.
    knn.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=False),
    breakout.NAME: MethodPolicy(same_timeframe=True, first_aligned_per_run=True),
    # zigzag (thêm 2026-10-24): khung tham chiếu riêng như knn (nến khung lớn, tra trend_tf_by_symbol) và KHÔNG dời tín hiệu (mặc định, người dùng chưa
    # chọn khác). Hướng đổi thưa (đo H1/H2/H4: ~46/25/14 lần mỗi năm, gần breakout), nên first_aligned_per_run=True là lựa chọn có thể cân nhắc sau.
    # trend_warmup_bars=3000: máy trạng thái của zigzag nhớ lâu hơn warm-up chung 300 nến. Đo 2026-10-24 với bộ số đang dùng (ATR 5 × 3,14,
    # Depth 5; đường delta của worker: 300 nến vào lệnh + N nến trend, so với bản tính từ bar đầu tiên; 60 chuỗi × 15 cửa sổ × 2 loại × 24 nến
    # mới): N = 500 → 1,00% (cửa sổ ngẫu nhiên) / 2,89% (hướng đã cũ > 300 nến trend) cửa sổ lệch tín hiệu; N = 1.000 → 0,11% / 0,56%;
    # N = 1.500, 2.000, 2.500, 3.000 → 0 lệch trên 1.800 cửa sổ. Bộ Depth 13 (nhớ lâu hơn) cần 2.500 mới hết lệch. Đặt 3.000: gấp đôi mức
    # thấp nhất đã đo là 0 lệch với bộ hiện tại, và vẫn đủ nếu đổi sang bộ số chậm hơn.
    zigzag.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=False, trend_warmup_bars=3000),
    # tstat (thêm 2026-10-26, người dùng chốt "khung tham chiếu như KNN và Zigzag"): khung tham chiếu riêng (nến khung lớn, tra
    # trend_tf_by_symbol) và CÓ dời tín hiệu (người dùng chốt bật 2026-10-26 sau khi xem số đo; lúc thêm khối là không dời).
    # Đo 16 ô × 15 symbol, khung tham chiếu nhỏ nhất, từ 2024-01-01, tỷ lệ tín hiệu còn lại so với raw, không dời → có dời: combo
    # 35% → 45%; ma_cross 32–37% → 40–45%; macd_break 6–32% → 43–68% (khung vào M10–M45) và 25–27% → 79–81% (H1–H4). Phần tín hiệu
    # macd_break bị dời 26–91%, trễ trung vị từ ~22 nến (H1–H4) tới ~1 ngày (M10–M45 với khung H1). Không có trạng thái dài: hướng
    # của nến t chỉ phụ thuộc N nến cuối (cửa sổ cuốn) nên warm-up chung 300 nến đủ khi N + độ trễ Newey–West < 300 (khoá bởi
    # test); 0 = dùng warm-up chung.
    tstat.NAME: MethodPolicy(same_timeframe=False, first_aligned_per_run=True),
}

_CONTRACT = ("NAME", "OUTPUT_COLUMNS", "OVERLAYS", "build")


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
    True nếu mọi khối đang bật tính trên chính khung của tín hiệu: khung trend = khung vào lệnh, không tra bảng
    trend_tf_by_symbol và không bị giới hạn trong trend_timeframes. Trộn khối kiểu này với khối cần khung tham
    chiếu riêng (vd knn + breakout) là lỗi vì mọi khối đang bật dùng chung MỘT khung trend (docstring đầu file).
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


def parse_methods(value: object, *, label: str = "TREND_METHODS") -> tuple[str, ...]:
    """
    Chuẩn hoá danh sách khối xác nhận xu hướng được bật.

    value: list/tuple (từ config YAML) hoặc chuỗi cách nhau bằng dấu phẩy (override từ CLI/dashboard).
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


def build_trend_frame(trend_bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Dựng frame trend từ nến khung lớn: chạy từng khối trong params["TREND_METHODS"], ghép hướng
    thành cột trend_bias và thêm trend_close_time = giờ mở nến + độ dài khung TREND_TF.

    Cột hướng riêng từng khối (trend_bias_<tên>) nằm lại trong frame trend (nội bộ, để ghép và để
    chẩn đoán), không được mang sang khung vào lệnh (xem indicator.merge_trend_reference).
    """
    methods = tuple(params["TREND_METHODS"])
    trend_tf = str(params.get("TREND_TF") or "").strip().upper()
    if not trend_tf:
        raise ValueError("TREND_TF is required to build the trend reference frame.")

    out = trend_bars
    for name in methods:
        out = get_method(name).build(out, params)
        if bias_column(name) not in out.columns:
            raise ValueError(f"trend method '{name}' did not produce the '{bias_column(name)}' column.")

    out = out.copy()
    out["trend_bias"] = combine_trend_bias(out, [bias_column(name) for name in methods])
    out["trend_close_time"] = pd.to_datetime(out["bartime"]) + pd.Timedelta(minutes=timeframe_minutes(trend_tf))
    return out


# =============================================================================
# Ghép hướng và lọc tín hiệu (phía DÙNG trend_bias).
# =============================================================================


def trend_filter_enabled(params: dict | None) -> bool:
    """
    Trả về trạng thái bật/tắt trend filter từ params đã normalize.
    """
    if not params:
        return False
    value = params.get("TREND_FILTER_ENABLED", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def combine_trend_bias(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """
    Ghép cột hướng của nhiều khối xác nhận xu hướng thành một cột trend_bias.

    Đồng thuận: kết quả +1 chỉ khi TẤT CẢ cột cùng +1, -1 chỉ khi TẤT CẢ cùng -1, còn lại 0 (có khối
    đi ngang/thiếu dữ liệu, hoặc các khối bất đồng). Một khối duy nhất thì giữ nguyên hướng của nó.
    NaN coi là 0. Trả về Series int64 cùng index với df.
    """
    if not columns:
        raise ValueError("combine_trend_bias needs at least one bias column.")
    biases = [pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int) for column in columns]
    if len(biases) == 1:
        return biases[0]
    stacked = pd.concat(biases, axis=1)
    combined = pd.Series(0, index=df.index, dtype="int64")
    combined.loc[stacked.eq(1).all(axis=1)] = 1
    combined.loc[stacked.eq(-1).all(axis=1)] = -1
    return combined


def apply_trend_filter(
    df: pd.DataFrame,
    params: dict | None,
    *,
    raw_signal_col: str = "raw_signal",
    output_col: str = "signal",
    trend_bias_col: str = "trend_bias",
    buy_setup: pd.Series | None = None,
    sell_setup: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Tạo signal cuối cùng từ raw_signal và trend reference tùy chọn.

    Nếu trend filter tắt:
        signal = raw_signal

    Nếu trend filter bật:
        BUY chỉ giữ khi trend_bias = 1.
        SELL chỉ giữ khi trend_bias = -1.
        trend neutral/thiếu/ngược chiều đều bị lọc thành signal = 0.

    Nếu trend filter bật, một khối đang bật có chính sách first_aligned_per_run (POLICY, uses_first_aligned_per_run) và nơi
    gọi truyền buy_setup/sell_setup (hai mask "nến được phép bắn" do apply_signal_filters quyết định: chính setup nếu setup
    là TRẠNG THÁI, còn đúng ở nhiều nến liền nhau; hướng của đợt đang giữ nếu setup là SỰ KIỆN ở một nến, tức tín hiệu
    thô bị loại được giữ tới hết đợt): mỗi đợt bắn đúng MỘT tín hiệu, ở nến đầu tiên có mask mà trend_bias đã cùng
    chiều (first_aligned_signal trong rules/shift.py). Nến đầu của đợt cùng chiều trend thì kết quả giống hệt cách trên; nến đầu lệch trend
    thì tín hiệu dời tới lúc trend đúng chiều thay vì mất cả đợt. signal luôn ⊇ cách trên (không mất tín hiệu nào)
    nhưng KHÔNG còn ⊆ raw_signal: tín hiệu dời nằm giữa đợt nên không phải nến có raw_signal != 0 (DB1 có tín hiệu DB0
    không có). Không truyền setup, hoặc không khối nào có chính sách đó, thì luôn dùng cách trên.

    trend_filter_status mô tả từng nến theo signal và raw_signal: "aligned" = có tín hiệu; "no_raw_signal" = không có cả
    raw_signal lẫn tín hiệu; "neutral_trend" = có raw_signal nhưng trend trung tính; "filtered" = có raw_signal nhưng
    trend ngược chiều (tín hiệu của đợt đó, nếu có, nằm ở nến sau).
    """
    out = df.copy()
    raw_signal = pd.to_numeric(out.get(raw_signal_col, 0), errors="coerce").fillna(0).astype(int)
    out[output_col] = raw_signal

    if not trend_filter_enabled(params):
        out["trend_filter_status"] = "disabled"
        return out

    if trend_bias_col not in out.columns:
        raise ValueError(
            "TREND_FILTER_ENABLED=true requires a trend reference frame with "
            f"'{trend_bias_col}' column."
        )

    trend_bias = pd.to_numeric(out[trend_bias_col], errors="coerce").fillna(0).astype(int)
    if (
        buy_setup is not None
        and sell_setup is not None
        and uses_first_aligned_per_run(tuple((params or {}).get("TREND_METHODS") or ()))
    ):
        out[output_col] = first_aligned_signal(raw_signal, trend_bias, buy_setup, sell_setup)
    else:
        aligned = raw_signal.ne(0) & raw_signal.eq(trend_bias)
        out[output_col] = 0
        out.loc[aligned, output_col] = raw_signal.loc[aligned]
    signal = out[output_col]
    out["trend_filter_status"] = "filtered"
    out.loc[signal.ne(0), "trend_filter_status"] = "aligned"
    out.loc[raw_signal.eq(0) & signal.eq(0), "trend_filter_status"] = "no_raw_signal"
    out.loc[raw_signal.ne(0) & trend_bias.eq(0), "trend_filter_status"] = "neutral_trend"
    return out


# =============================================================================
# Điều phối: lọc lặp cùng chiều rồi lọc trend — chiến lược chỉ gọi hàm này.
# =============================================================================

_TREND_SUFFIX = "; aligned with trend reference"


def apply_signal_filters(
    df: pd.DataFrame,
    params: dict | None,
    *,
    buy_setup: pd.Series,
    sell_setup: pd.Series,
    buy_reason: str,
    sell_reason: str,
    hold_valid: pd.Series | None = None,
    delayed_buy_reason: str | None = None,
    delayed_sell_reason: str | None = None,
) -> pd.DataFrame:
    """
    Chạy cả hai bộ lọc của một chiến lược entry rồi ghi bốn cột: raw_signal, signal, trend_filter_status, signal_reason.

    Thứ tự (chốt 2026-10-16, "cách B"): lọc lặp cùng chiều (apply_alternating_filter, cờ trong config) chạy TRƯỚC trên MỌI
    setup và cho raw_signal — đúng cột DB0, cũng là tín hiệu trước khi lọc trend; rồi lọc trend (apply_trend_filter) cho
    signal — cột DB1. Trend tắt thì signal == raw_signal. Hai bộ lọc không giao hoán nên đổi thứ tự là đổi tập tín hiệu
    DB1 (CLAUDE.md mục 6).

    Args:
        df: khung của chiến lược (đã có nến + chỉ báo; không bị sửa tại chỗ).
        params: tham số đã chuẩn hoá (ALTERNATING_FILTER_ENABLED, TREND_FILTER_ENABLED, TREND_METHODS...).
        buy_setup, sell_setup: hai mask setup BUY/SELL của chiến lược, đã gồm điều kiện hợp lệ (chỉ báo đủ dữ liệu...).
        buy_reason, sell_reason: câu lý do ghi vào signal_reason; khi trend bật mọi tín hiệu có thêm hậu tố
            "; aligned with trend reference".
        hold_valid: None = setup là TRẠNG THÁI (còn đúng ở nhiều nến liền nhau, như ma_cross và macd_break): nến được
            phép bắn là chính nến còn setup. Có giá trị = setup là SỰ KIỆN ở đúng một nến (như combo, nến cắt MA) nên
            sau lúc trend đổi không còn nến setup nào: tín hiệu thô bị trend loại được GIỮ tới hết đợt (tới khi có setup
            ngược) và được phép bắn ở mọi nến mà hold_valid đúng (chỉ báo đủ dữ liệu, đúng giờ session), không giới hạn
            tuổi (người dùng chốt 2026-10-22). Chỉ có tác dụng với khối có chính sách first_aligned_per_run.
        delayed_buy_reason, delayed_sell_reason: câu lý do của tín hiệu bắn DỜI (nến không phải nến raw_signal, chỉ có
            với chính sách first_aligned_per_run); None = dùng câu thường.

    Returns:
        Bản sao df với các cột raw_signal, signal, trend_filter_status, signal_reason (theo thứ tự này).
    """
    raw_signal = apply_alternating_filter(buy_setup, sell_setup, params)
    out = df.assign(raw_signal=raw_signal)

    buy_mask, sell_mask = allowed_masks(raw_signal, buy_setup, sell_setup, hold_valid)
    out = apply_trend_filter(
        out, params, raw_signal_col="raw_signal", output_col="signal", buy_setup=buy_mask, sell_setup=sell_mask
    )

    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = buy_reason
    out.loc[out["signal"].eq(-1), "signal_reason"] = sell_reason
    delayed = out["signal"].ne(0) & raw_signal.eq(0)
    if delayed_buy_reason is not None:
        out.loc[delayed & out["signal"].eq(1), "signal_reason"] = delayed_buy_reason
    if delayed_sell_reason is not None:
        out.loc[delayed & out["signal"].eq(-1), "signal_reason"] = delayed_sell_reason
    if trend_filter_enabled(params):
        has_signal = out["signal"].fillna(0).astype(int).ne(0)
        out.loc[has_signal, "signal_reason"] = out.loc[has_signal, "signal_reason"] + _TREND_SUFFIX
    return out
