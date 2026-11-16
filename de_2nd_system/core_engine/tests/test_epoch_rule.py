"""Test luật "reality epoch" trong decision_engine.py — hermetic, thuần pandas.

Nến trigger đóng lúc Ce chỉ được nhìn nến trend ĐÃ ĐÓNG có giờ đóng không muộn hơn Ce (``merge_closed_reference``: CHỌN và GHÉP, cho từng yếu tố trend). Nếu có một nến trend phải đóng ĐÚNG lúc Ce
mà chưa về thì PHẢI CHỜ, không dùng nến cũ thay (``reference_ready``: "đủ chưa"). Thứ tự về của hai nến không ảnh hưởng luật.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core_engine.src.decision_engine import (
    merge_closed_reference,
    reference_ready,
)

M10, M30 = pd.Timedelta(minutes=10), pd.Timedelta(minutes=30)


def merge_trend_reference(frame: pd.DataFrame, trend: pd.DataFrame) -> pd.DataFrame:
    """Ghép ``trend_bias`` của nến trend đã đóng gần nhất vào từng nến của ``frame`` (đúng cách ``decide`` ghép từng yếu tố)."""
    return merge_closed_reference(frame, trend, ["trend_bias"], ref_close_name="trend_close_time")


def _times(*hours: str) -> pd.Series:
    return pd.Series(pd.to_datetime([f"2026-11-16 {hour}" for hour in hours]).astype("datetime64[us]"))


def _trend(*opens: str, bias: tuple[float, ...] | None = None) -> pd.DataFrame:
    bartime = _times(*opens)
    return pd.DataFrame({"bartime": bartime, "close_time": bartime + M30, "trend_bias": list(bias or (1.0,) * len(opens))})


# =============================================================================
# 1. Đủ chưa: bảng đối chiếu nến M10 và nến M30.
# =============================================================================


@pytest.mark.parametrize(
    ("entry_open", "must_wait"),
    [("10:00", False), ("10:10", False), ("10:20", True), ("10:30", False), ("10:40", False), ("10:50", True)],
)
def test_nen_m10_chi_phai_cho_khi_gio_dong_trung_gio_dong_cua_nen_m30(entry_open: str, must_wait: bool) -> None:
    """Nến M30 mới nhất đã về mở 09:30 (đóng 10:00). Nến M10 mở 10:20 đóng 10:30 trùng giờ đóng của nến M30 mở 10:00 nên phải chờ nến đó; nến mở 10:10 đóng 10:20 thì dùng nến M30 mở 09:30."""
    entry_close = pd.Timestamp(f"2026-11-16 {entry_open}") + M10
    assert reference_ready(_trend("09:00", "09:30"), entry_close, M30) is (not must_wait)


def test_nen_trend_ve_truoc_hay_cung_luc_thi_da_du_ve_sau_thi_phai_cho() -> None:
    entry_close = pd.Timestamp("2026-11-16 10:30")
    assert reference_ready(_trend("09:30"), entry_close, M30) is False  # nến M30 mở 10:00 (đóng 10:30) chưa về: chờ
    assert reference_ready(_trend("09:30", "10:00"), entry_close, M30) is True  # đã về (trước hay cùng lúc): đủ
    assert reference_ready(_trend("09:30", "10:00", "10:30"), entry_close, M30) is True  # có cả nến mới hơn: nến đóng đúng 10:30 vẫn có


def test_bang_trend_rong_thi_chua_du() -> None:
    assert reference_ready(_trend(), pd.Timestamp("2026-11-16 10:20"), M30) is False


def test_sau_nghi_phien_nen_cuoi_cu_nhung_gio_dong_khong_trung_luoi_thi_dung_nen_do() -> None:
    assert reference_ready(_trend("09:30"), pd.Timestamp("2026-11-16 14:20"), M30) is True  # cách nến cuối 4h20 không phải bội của 30 phút: nến cuối chính là nến phải dùng


def test_gioi_han_da_biet_sau_nghi_phien_gio_dong_trung_luoi_suy_ra_thi_cho() -> None:
    """Lưới đóng được suy từ chính các nến trend đang có (không giả định giờ neo), nên sau nghỉ phiên mà giờ đóng vô tình trùng lưới suy ra thì luật chờ (chờ nhầm). Khoá lại hành vi đã biết này."""
    assert reference_ready(_trend("09:30"), pd.Timestamp("2026-11-16 14:30"), M30) is False


# =============================================================================
# 2. Chọn và ghép nến trend đã đóng.
# =============================================================================


def _entries(*opens: str) -> pd.DataFrame:
    bartime = _times(*opens)
    return pd.DataFrame({"bartime": bartime, "close_time": bartime + M10, "close": np.arange(len(bartime), dtype=float)})


def test_ghep_nen_trend_da_dong_khong_muon_hon_gio_dong_nen_trigger_bang_nhau_van_tinh() -> None:
    trend = _trend("09:30", "10:00", bias=(-1.0, 1.0))  # nến M30 đóng 10:00 (SELL) và 10:30 (BUY)
    merged = merge_trend_reference(_entries("10:10", "10:20", "10:30", "10:40"), trend)
    assert merged["bartime"].tolist() == _times("10:10", "10:20", "10:30", "10:40").tolist()
    # nến mở 10:10 (đóng 10:20) nhìn nến đóng 10:00; nến mở 10:20 (đóng 10:30) nhìn nến đóng đúng 10:30 (bằng nhau vẫn tính); các nến sau vẫn nhìn nến đóng 10:30
    assert merged["trend_bias"].tolist() == [-1.0, 1.0, 1.0, 1.0]
    assert merged["trend_close_time"].tolist() == _times("10:00", "10:30", "10:30", "10:30").tolist()


def test_nen_trigger_chua_co_nen_trend_nao_thi_bias_la_nan() -> None:
    merged = merge_trend_reference(_entries("09:00"), _trend("09:30"))
    assert np.isnan(merged["trend_bias"].iloc[0]) and pd.isna(merged["trend_close_time"].iloc[0])


def test_bang_trend_rong_thi_moi_cot_mang_sang_la_nan_va_bang_trigger_rong_thi_tra_ban_sao() -> None:
    merged = merge_closed_reference(_entries("10:00", "10:10"), _trend(), ["trend_bias"], ref_close_name="trend_close_time")
    assert merged["trend_bias"].isna().all() and merged["trend_close_time"].isna().all() and len(merged) == 2
    empty = merge_trend_reference(_entries(), _trend("09:30"))
    assert empty.empty


def test_ket_qua_sap_theo_bartime_tang_dan_du_dau_vao_xao_tron() -> None:
    merged = merge_trend_reference(_entries("10:30", "10:00", "10:20"), _trend("09:30", "10:00"))
    assert merged["bartime"].tolist() == _times("10:00", "10:20", "10:30").tolist()
