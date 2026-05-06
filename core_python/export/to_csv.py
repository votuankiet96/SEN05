"""
Xuất tín hiệu giao dịch ra file CSV để import vào công cụ backtest.

Mô tả:
    Lọc các dòng có tín hiệu (signal != 0) từ DataFrame đã enriched,
    chọn các cột cần thiết và ghi ra file CSV với timestamp trong tên file.
    Định dạng CSV phù hợp để import vào cTrader hoặc công cụ backtest khác.

Đầu ra:
    File CSV gồm các cột [bartime, atr, signal] tại thư mục core_python/exports/
    (hoặc output_dir tùy chỉnh).

Phụ thuộc ngoài:
    Không có — chỉ dùng stdlib và pandas.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


# Các cột xuất ra CSV — bartime để định vị thời gian, atr để tính size,
# signal để phân biệt hướng lệnh (1=BUY, -1=SELL).
EXPORT_COLUMNS = ["bartime", "atr", "signal"]


def export_signals(
    df: pd.DataFrame,
    *,
    symbol: str,
    strategy: str,
    output_dir: str | Path | None = None,
) -> Path:
    """
    Xuất các dòng có signal != 0 ra file CSV.

    Quy trình:
        1. Lọc dòng có signal != 0 (bỏ dòng không có tín hiệu).
        2. Chọn các cột EXPORT_COLUMNS.
        3. Chuẩn hóa định dạng: bartime → chuỗi "YYYY-MM-DD HH:MM:SS".
        4. Ghi CSV với encoding utf-8-sig (hỗ trợ Excel đọc tiếng Việt).
        5. Tên file: {SYMBOL}_{strategy}_{YYYYMMDD_HHMMSS}.csv

    Args:
        df: DataFrame đã enriched đầy đủ (phải có cột bartime, atr, signal).
        symbol: Tên symbol — dùng trong tên file (uppercase tự động).
        strategy: Tên chiến lược — dùng trong tên file.
        output_dir: Thư mục đích. Mặc định: core_python/exports/.

    Returns:
        Path tới file CSV đã tạo.

    Raises:
        ValueError: Nếu df thiếu bất kỳ cột nào trong EXPORT_COLUMNS.

    Side Effects:
        Tạo thư mục output_dir nếu chưa tồn tại.
        Ghi file CSV mới (không ghi đè file cũ vì tên có timestamp).

    Giả định giao dịch:
        Timestamp bartime trong CSV là UTC-naive (theo chuẩn DB loader).
        ATR dùng để tính position size trong backtest/cTrader.
        Signal: 1 = BUY, -1 = SELL.
    """
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[1] / "exports"
    base_dir.mkdir(parents=True, exist_ok=True)

    missing = [col for col in EXPORT_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing export columns: {missing}")

    # Lọc dòng có tín hiệu, loại dòng signal=0 (không có hành động giao dịch)
    out = df.loc[df["signal"].fillna(0).astype(int).ne(0), EXPORT_COLUMNS].copy()
    out["bartime"] = pd.to_datetime(out["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["atr"] = pd.to_numeric(out["atr"], errors="coerce")
    out["signal"] = out["signal"].astype(int)

    # Timestamp trong tên file là UTC wall-clock lúc xuất — chỉ để phân biệt file
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{symbol.upper()}_{strategy}_{timestamp}.csv"
    path = base_dir / filename
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path
