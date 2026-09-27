"""
Entry level helpers cho từng chiến lược.

order_gateway chịu trách nhiệm tạo signal, điểm vào lệnh, và (từ 2026-09-16)
SL/TP tuyệt đối cho combo/ma_cross — theo yêu cầu của luồng OF (Order
Follower, VM-OF11): cTrader Open API cần SL/TP số tuyệt đối để đặt lệnh,
không tự tính được ở phía OF. sizing (volume) và quản trị lệnh cuối cùng
vẫn thuộc OF, không phải ở đây.

Với các chiến lược có dữ liệu Dow từ indicator.py, levels.py có thể copy
tham chiếu đó sang cột sl_dow để OF đọc sau này (tham chiếu phụ, không
phải sl_price cuối cùng).

SL/TP = entry ∓/± K×atr, K lấy từ 10 mức cố định KSL_LEVELS/KTP_LEVELS bên
dưới (công thức phi^(n/2), xem lịch sử quyết định trong memory
project_of_ctrader_integration). Đây là hằng số công thức, không phải
tham số vận hành — giống DowStructureParams trong indicator.py — nên nằm
thẳng trong code, không phải config.yaml. Combo và ma_cross đều có SL/TP
kiểu này.

Mức K thật sự dùng cho từng (strategy, symbol, timeframe) tra từ
ksl_ktp.csv cùng thư mục (bảng do walk-forward optimize sinh ra, đọc bởi
configuration.py) — file này chỉ giữ công thức và 20 mức cố định, không
giữ bảng tra theo symbol/timeframe.

Từ 2026-09-18: ngoài sl_price/tp_price (đã nhân atr), _apply_ksl_ktp còn
giữ lại chính ratio K vào 2 cột ksl/ktp -- không nhân gì thêm, để
signal_publisher.py publish kèm ra Redis DB1 cho OF tự tính khoảng cách
tương đối (cần cho ma_cross, entry MARKET chỉ biết giá thật lúc OF khớp
lệnh, khác giá đóng nến lúc OG sinh tín hiệu).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 10 mức mỗi bên, theo phi^(n/2) (bước nhân đều sqrt(phi) ~= 1.272). Cố
# định — không đổi theo thời gian, không phải giá trị operator chỉnh (xem
# docstring module). Đổi thứ tự/thêm/bớt phần tử ở đây sẽ đổi nghĩa của
# MỌI mã KSLxxxx/KTPxxxx đã ghi trong ksl_ktp.csv — vì vậy identity của
# 1 mức là chính giá trị của nó (mã hoá thẳng vào tên, "KSL0618" = 0.618),
# không phải vị trí trong tuple này.
KSL_LEVELS: tuple[float, ...] = (
    0.618, 0.786, 1.000, 1.272, 1.618, 2.058, 2.618, 3.330, 4.236, 5.388,
)
KTP_LEVELS: tuple[float, ...] = (
    1.000, 1.272, 1.618, 2.058, 2.618, 3.330, 4.236, 5.388, 6.854, 8.719,
)


def decode_level(code: str, prefix: str, valid_levels: tuple[float, ...]) -> float:
    """Giải mã 1 mã kiểu "KSL0618" -> 0.618.

    Validate ngược lại đúng 1 trong 10 mức đã chốt (valid_levels) -- gõ
    sai 1 chữ số (vd "KSL0619") sẽ raise ngay thay vì âm thầm dùng 1 số
    chưa từng nằm trong search-space đã optimize.
    """
    text = str(code).strip().upper()
    if not text.startswith(prefix):
        raise ValueError(f"{code!r} thiếu tiền tố {prefix!r}")
    digits = text[len(prefix):]
    if len(digits) != 4 or not digits.isdigit():
        raise ValueError(f"{code!r} phải có đúng 4 chữ số sau tiền tố {prefix!r}")
    value = int(digits) / 1000
    if value not in valid_levels:
        raise ValueError(f"{code!r} -> {value} không thuộc {valid_levels}")
    return value


def add_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm entry pending cho Combo.

    BUY dùng high + X, SELL dùng low - X. X vẫn thuộc core vì nó quyết định
    chính điểm vào lệnh của Combo, không phải TP/SL.
    """
    _ = symbol
    out = _with_empty_order_columns(df)
    if out.empty or not {"signal", "bartime", "high", "low"}.issubset(out.columns):
        return out

    x = float(params["X"])
    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)
    has_signal = buy | sell

    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + x
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - x
    return _apply_ksl_ktp(out, params, buy, sell)


def add_ma_cross_levels(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Thêm market entry cho MA Cross, SL/TP theo KSL/KTP*ATR, và copy stop
    reference Dow nếu có.
    """
    _ = symbol
    out = _with_empty_order_columns(df, include_sl_dow=True)
    if out.empty or not {"signal", "bartime", "close"}.issubset(out.columns):
        return out

    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)
    has_signal = buy | sell
    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]
    out = _apply_ksl_ktp(out, params, buy, sell)
    return _copy_sl_dow_reference(out, signals)


def _with_empty_order_columns(df: pd.DataFrame, *, include_sl_dow: bool = False) -> pd.DataFrame:
    """
    Tạo sẵn các cột level với NaN để mọi dòng có cùng schema, kể cả dòng
    không có tín hiệu.
    """
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["ksl"] = np.nan
    out["ktp"] = np.nan
    if include_sl_dow:
        out["sl_dow"] = np.nan
    return out


def _apply_ksl_ktp(
    df: pd.DataFrame,
    params: dict,
    buy: pd.Series,
    sell: pd.Series,
) -> pd.DataFrame:
    """Tính sl_price/tp_price = entry ∓/± K×atr theo hướng lệnh.

    params["KSL"]/params["KTP"] đã là SỐ THẬT (configuration.py giải mã
    "KSL0618" -> 0.618 từ ksl_ktp.csv trước khi gọi tới đây) -- ở đây chỉ
    áp công thức, không decode/validate gì thêm. Thiếu KSL/KTP trong
    params (gọi trực tiếp trong test không qua configuration.py) hay
    thiếu cột atr (fixture tổng hợp không có atr) thì bỏ qua, giữ nguyên
    NaN đã có sẵn từ _with_empty_order_columns -- không suy diễn số nào cả.
    """
    out = df.copy()
    if "KSL" not in params or "KTP" not in params or "atr" not in out.columns:
        return out

    ksl = float(params["KSL"])
    ktp = float(params["KTP"])
    entry = pd.to_numeric(out["entry_price"], errors="coerce")
    atr = pd.to_numeric(out["atr"], errors="coerce")
    sl_distance = ksl * atr
    tp_distance = ktp * atr
    has_signal = buy | sell

    out.loc[buy, "sl_price"] = entry.loc[buy] - sl_distance.loc[buy]
    out.loc[buy, "tp_price"] = entry.loc[buy] + tp_distance.loc[buy]
    out.loc[sell, "sl_price"] = entry.loc[sell] + sl_distance.loc[sell]
    out.loc[sell, "tp_price"] = entry.loc[sell] - tp_distance.loc[sell]
    # Giữ lại chính ratio (không phải distance đã nhân atr) -- publisher cần
    # số này để publish "ksl"/"ktp" ra Redis DB1 cho OF, xem
    # signal_publisher.py. Ghi cùng lúc với sl_price/tp_price vì cùng điều
    # kiện has_signal, không phải 1 bước tính riêng.
    out.loc[has_signal, "ksl"] = ksl
    out.loc[has_signal, "ktp"] = ktp
    return out


def _signals(df: pd.DataFrame) -> pd.Series:
    if "signal" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    return pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)


def _copy_sl_dow_reference(df: pd.DataFrame, signals: pd.Series) -> pd.DataFrame:
    """
    Copy dữ liệu Dow từ indicator sang sl_dow theo hướng lệnh.
    """
    out = df.copy()
    if "sl_dow" not in out.columns:
        out["sl_dow"] = np.nan

    entries = pd.to_numeric(out["entry_price"], errors="coerce")
    if "sl_dow_buy" in out.columns:
        buy_sl = pd.to_numeric(out["sl_dow_buy"], errors="coerce")
        valid_buy = signals.eq(1) & entries.notna() & buy_sl.lt(entries)
        out.loc[valid_buy, "sl_dow"] = buy_sl.loc[valid_buy]
    if "sl_dow_sell" in out.columns:
        sell_sl = pd.to_numeric(out["sl_dow_sell"], errors="coerce")
        valid_sell = signals.eq(-1) & entries.notna() & sell_sl.gt(entries)
        out.loc[valid_sell, "sl_dow"] = sell_sl.loc[valid_sell]
    return out
