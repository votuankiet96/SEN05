"""Broker execution helpers shared by strategy engines.

Vai trò của file này
--------------------
Chiến lược chỉ cần quyết định "mua hay bán, bao nhiêu lot". File này xử lý
phần còn lại: làm tròn lot theo đúng quy định broker, ghép cấu hình broker
vào symbol, và kiểm tra cấu hình trước khi chạy backtest.

Tách riêng broker mechanics ra khỏi signal logic để:
- Chiến lược không bị "ô nhiễm" bởi các chi tiết kỹ thuật của từng broker.
- Dễ test nhiều broker khác nhau mà không cần sửa cấu hình gốc.
"""

from __future__ import annotations

from math import floor
from typing import Any


# Danh sách 10 trường bắt buộc phải có trong cấu hình mỗi symbol.
# Nếu thiếu bất kỳ trường nào, audit_symbol_specs() sẽ phát cảnh báo.
#
#   contract_value            : Giá trị 1 lot quy đổi ra đồng tiền tài khoản
#   point_size                : Mỗi 1 point/pip = bao nhiêu tiền
#   spread_pts                : Chênh lệch giá mua/bán (tính bằng point)
#   slippage_pts              : Trượt giá khi vào lệnh (tính bằng point)
#   commission_per_lot        : Hoa hồng broker cho mỗi lot giao dịch (1 chiều)
#   swap_long_per_lot_per_day : Phí qua đêm khi giữ lệnh MUA (mỗi lot/ngày)
#   swap_short_per_lot_per_day: Phí qua đêm khi giữ lệnh BÁN (mỗi lot/ngày)
#   min_lot_size              : Khối lượng tối thiểu broker cho phép đặt lệnh
#   max_lot_size              : Khối lượng tối đa broker cho phép đặt lệnh
#   lot_step                  : Bước tăng lot (ví dụ: 0.01 → chỉ được đặt 0.01, 0.02, 0.03...)
DEFAULT_REQUIRED_SPEC_FIELDS = (
    "contract_value",
    "point_size",
    "spread_pts",
    "slippage_pts",
    "commission_per_lot",
    "swap_long_per_lot_per_day",
    "swap_short_per_lot_per_day",
    "min_lot_size",
    "max_lot_size",
    "lot_step",
)


def round_lot_size(
    raw_lot: float,
    *,
    min_lot: float = 0.01,
    max_lot: float = 100.0,
    lot_step: float = 0.01,
    mode: str = "floor",
) -> float:
    """Làm tròn khối lượng lệnh về đúng bước lot mà broker chấp nhận.

    Broker không chấp nhận lệnh với lot tùy ý (ví dụ: 0.0753 lot). Hàm này
    nhận raw_lot từ risk management, làm tròn theo lot_step, rồi ép vào
    khoảng [min_lot, max_lot].

    Ví dụ: raw_lot=0.0753, lot_step=0.01 → trả về 0.07 (làm tròn xuống).

    Parameters
    ----------
    raw_lot  : Khối lượng tính ra từ công thức risk (chưa làm tròn).
    min_lot  : Lot tối thiểu broker cho phép (mặc định 0.01).
    max_lot  : Lot tối đa broker cho phép (mặc định 100.0).
    lot_step : Bước tăng lot (mặc định 0.01).
    mode     : "floor" (mặc định) làm tròn xuống — an toàn, không vượt risk.
               "nearest" làm tròn đến giá trị gần nhất.

    Returns
    -------
    float : Khối lượng hợp lệ đã làm tròn. Trả về 0.0 nếu raw_lot <= 0.
    """
    # Không đặt lệnh nếu khối lượng tính ra âm hoặc max_lot không hợp lệ.
    if raw_lot <= 0 or max_lot <= 0:
        return 0.0

    # Đảm bảo các thông số không âm và hợp lệ.
    min_lot = max(float(min_lot), 0.0)
    max_lot = max(float(max_lot), min_lot)
    lot_step = float(lot_step or min_lot or 0.01)
    if lot_step <= 0:
        lot_step = min_lot or 0.01

    # Ép raw_lot vào khoảng [min_lot, max_lot] trước khi làm tròn.
    clamped = min(max(float(raw_lot), min_lot), max_lot)

    # Tính số bước lot rồi làm tròn.
    units = clamped / lot_step
    if mode == "nearest":
        rounded = round(units) * lot_step
    else:
        # Cộng 1e-12 để tránh sai số dấu phẩy động: ví dụ 0.07/0.01 có thể
        # cho 6.9999999 thay vì 7.0, dẫn đến floor() sai xuống 6 thay vì 7.
        rounded = floor(units + 1e-12) * lot_step

    # Ép lại một lần nữa sau khi làm tròn để đảm bảo không vượt biên.
    rounded = min(max(rounded, min_lot), max_lot)

    # Xác định số chữ số thập phân cần giữ dựa trên lot_step (ví dụ: 0.01 → 2 chữ số).
    decimals = max(0, min(8, len(str(lot_step).split(".")[-1]) if "." in str(lot_step) else 0))
    return round(rounded, decimals)


def merge_broker_profile(
    symbol_config: dict[str, Any],
    profile: dict[str, Any] | None = None,
    symbol_key: str | None = None,
) -> dict[str, Any]:
    """Ghép cấu hình broker vào symbol config để backtest với chi phí thực tế.

    Cùng một symbol (ví dụ: EURUSD) nhưng mỗi broker có spread, swap, commission
    khác nhau. Thay vì sửa config gốc, ta dùng broker profile để override —
    dễ test nhiều broker mà không đụng vào cấu hình chiến lược.

    Thứ tự ưu tiên (sau thắng trước):
      1. symbol_config          — cấu hình mặc định của symbol
      2. profile["defaults"]    — giá trị mặc định áp cho toàn bộ broker
      3. profile["symbols"][symbol_key] — override riêng từng symbol của broker

    Parameters
    ----------
    symbol_config : Dict cấu hình gốc của symbol (từ config.py hoặc get_symbol_params).
    profile       : Dict cấu hình broker. None → trả về symbol_config nguyên vẹn.
    symbol_key    : Tên symbol để tra cứu override riêng trong profile["symbols"].

    Returns
    -------
    dict : Symbol config đã kết hợp đầy đủ với thông tin broker.
           Luôn thêm 3 key: broker_profile, broker_label, broker_platform.
    """
    # Không có profile → trả về bản sao của symbol_config, không thay đổi gì.
    if not profile:
        return dict(symbol_config)

    # Lấy giá trị mặc định của broker (áp dụng cho tất cả symbol).
    defaults = profile.get("defaults", {})
    # Lấy override riêng cho symbol này (nếu có).
    symbol_overrides = profile.get("symbols", {}).get(symbol_key or "", {})

    # Merge theo thứ tự ưu tiên: symbol_config < defaults < symbol_overrides.
    # Key trùng nhau → dict sau ghi đè dict trước.
    return {
        **symbol_config,
        **defaults,
        **symbol_overrides,
        "broker_profile":  profile.get("name", ""),
        "broker_label":    profile.get("label", ""),
        "broker_platform": profile.get("platform", ""),
    }


def audit_symbol_specs(
    symbols: dict[str, dict[str, Any]],
    *,
    required_fields: tuple[str, ...] = DEFAULT_REQUIRED_SPEC_FIELDS,
) -> dict[str, Any]:
    """Kiểm tra cấu hình broker của tất cả symbol trước khi chạy backtest.

    Phát hiện sớm cấu hình thiếu hoặc sai logic — tránh backtest chạy hàng giờ
    rồi ra kết quả sai im lặng vì thiếu thông số chi phí.

    Phân biệt hai mức nghiêm trọng:
    - warnings : Thiếu trường hoặc chưa xác nhận. Backtest vẫn chạy được
                 nhưng kết quả có thể không chính xác (ví dụ: swap tính = 0).
    - errors   : Lỗi logic không thể bỏ qua (min_lot > max_lot, lot_step <= 0).
                 Nếu có errors thì backtest sẽ sinh lỗi ngay khi dùng — phải sửa trước.

    Parameters
    ----------
    symbols         : Dict ánh xạ symbol_key → spec dict (thường từ SYMBOLS trong config.py).
    required_fields : Danh sách trường bắt buộc cần kiểm tra (mặc định DEFAULT_REQUIRED_SPEC_FIELDS).

    Returns
    -------
    dict với 3 key:
        "warnings" : list[str] — danh sách cảnh báo (thiếu trường, chưa verified)
        "errors"   : list[str] — danh sách lỗi nghiêm trọng (logic sai)
        "ok"       : bool      — True nếu không có errors (warnings không chặn)
    """
    warnings: list[str] = []
    errors: list[str] = []

    for sym_key, spec in symbols.items():
        # Kiểm tra từng trường bắt buộc có tồn tại trong spec không.
        for field in required_fields:
            if field not in spec:
                warnings.append(f"{sym_key}: missing {field}.")

        # Kiểm tra logic lot: min không được lớn hơn max.
        min_lot  = spec.get("min_lot_size")
        max_lot  = spec.get("max_lot_size")
        lot_step = spec.get("lot_step")
        if min_lot is not None and max_lot is not None and float(min_lot) > float(max_lot):
            errors.append(f"{sym_key}: min_lot_size > max_lot_size.")

        # lot_step <= 0 sẽ gây chia-cho-0 trong round_lot_size().
        if lot_step is not None and float(lot_step) <= 0:
            errors.append(f"{sym_key}: lot_step must be > 0.")

        # spec_verified = True có nghĩa là ai đó đã xác nhận thông số broker là đúng.
        # Chưa verified → chỉ cảnh báo, không chặn.
        if not spec.get("spec_verified", False):
            warnings.append(f"{sym_key}: broker spec is not marked verified.")

    return {
        "warnings": warnings,
        "errors":   errors,
        "ok":       not errors,
    }
