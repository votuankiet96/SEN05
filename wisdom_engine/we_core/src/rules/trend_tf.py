"""
Bộ lọc xu hướng khung lớn dùng chung cho các chiến lược entry — và là cửa vào DUY NHẤT của phần xu hướng.

Mỗi phương pháp xác nhận xu hướng là MỘT khối riêng, độc lập, trong trend_methods/ (một file mỗi khối;
KNN là khối đầu tiên). File này nối các khối với phần còn lại, gồm hai nhóm việc:

    1. Sổ đăng ký + dựng khung trend (phía TẠO trend_bias):
       - METHODS: sổ đăng ký "tên -> khối". THÊM KHỐI MỚI = thêm file khối trong trend_methods/, import
         nó và thêm một dòng vào METHODS ở dưới. Config/CLI/dashboard tự nhận tên mới, không phải sửa nơi khác.
       - parse_methods: chuẩn hoá danh sách khối được bật (từ config hoặc override) và báo lỗi rõ.
       - build_trend_frame: chạy từng khối đang bật trên nến khung lớn, ghép hướng của chúng thành MỘT cột
         trend_bias (đồng thuận, xem combine_trend_bias) và thêm trend_close_time (giờ đóng nến khung lớn,
         để ghép không nhìn trước tương lai).
    2. Bộ lọc (phía DÙNG trend_bias):
       - trend_filter_enabled: đọc cờ bật/tắt trend filter từ params;
       - combine_trend_bias: ghép hướng (+1/-1/0) của các khối đang bật theo kiểu đồng thuận (chỉ ra +1/-1
         khi TẤT CẢ khối cùng hướng);
       - apply_trend_filter: nhận raw_signal từ strategy entry và chỉ giữ tín hiệu cùng chiều trend_bias.

Chiến lược chỉ gọi apply_trend_filter/trend_filter_enabled và chỉ đọc cột trend_bias nên không biết (và không
cần biết) có bao nhiêu khối đang bật.

Phụ thuộc: trend_methods/<khối>.py và indicator.py (timeframe_minutes). Đây là file DUY NHẤT
trong rules/ import indicator.py/trend_methods/ (ngoại lệ của quy tắc "rules/ là tầng đáy", CLAUDE.md mục 2); các
khối KHÔNG được import file này (sẽ vòng import). Giả định hiện tại: mọi khối đang bật dùng CHUNG một khung tham chiếu (TREND_TF).
"""

from __future__ import annotations

from types import ModuleType

import pandas as pd
from wisdom_engine.we_core.src.indicator import timeframe_minutes
from wisdom_engine.we_core.src.trend_methods import knn

# =============================================================================
# Sổ đăng ký các khối xác nhận xu hướng + dựng khung trend (phía TẠO trend_bias).
# =============================================================================

METHODS: dict[str, ModuleType] = {
    knn.NAME: knn,
}

_CONTRACT = ("NAME", "OUTPUT_COLUMNS", "OVERLAYS", "build")


def bias_column(name: str) -> str:
    """Tên cột hướng riêng của một khối (khối PHẢI tạo đúng cột này trong build())."""
    return f"trend_bias_{name}"


def _check_contract() -> None:
    """Báo lỗi ngay lúc nạp nếu một khối thiếu phần của hợp đồng (xem docstring knn.py)."""
    for name, block in METHODS.items():
        missing = [attr for attr in _CONTRACT if not hasattr(block, attr)]
        if missing or block.NAME != name:
            raise ValueError(f"trend method '{name}' violates the block contract (missing {missing}, NAME={getattr(block, 'NAME', None)!r}).")


_check_contract()


def method_names() -> tuple[str, ...]:
    """Tên các khối đã đăng ký, theo thứ tự đăng ký."""
    return tuple(METHODS)


def get_method(name: str) -> ModuleType:
    """Khối theo tên (tên đã qua parse_methods)."""
    return METHODS[name]


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
) -> pd.DataFrame:
    """
    Tạo signal cuối cùng từ raw_signal và trend reference tùy chọn.

    Nếu trend filter tắt:
        signal = raw_signal

    Nếu trend filter bật:
        BUY chỉ giữ khi trend_bias = 1.
        SELL chỉ giữ khi trend_bias = -1.
        trend neutral/thiếu/ngược chiều đều bị lọc thành signal = 0.
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
    aligned = raw_signal.ne(0) & raw_signal.eq(trend_bias)
    out[output_col] = 0
    out.loc[aligned, output_col] = raw_signal.loc[aligned]
    out["trend_filter_status"] = "filtered"
    out.loc[aligned, "trend_filter_status"] = "aligned"
    out.loc[raw_signal.eq(0), "trend_filter_status"] = "no_raw_signal"
    out.loc[raw_signal.ne(0) & trend_bias.eq(0), "trend_filter_status"] = "neutral_trend"
    return out
