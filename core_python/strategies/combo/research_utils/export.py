"""Export helpers for Combo research outputs."""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .dashboard import metrics_to_frame, trades_to_frame

def export_result_bundle(
    name: str,
    *,
    metrics: Mapping[str, Any] | None = None,
    trades: list[dict[str, Any]] | pd.DataFrame | None = None,
    equity: pd.Series | pd.DataFrame | None = None,
    base_dir: str | Path = "reports/combo",
) -> Path:
    """Xuất metrics/trades/equity ra CSV để lưu lại một lần chạy.

    Hàm này không tự chạy trong notebook; bạn gọi ở cell cuối khi muốn lưu kết
    quả. Thư mục được tạo theo `base_dir/name`.
    """
    out_dir = Path(base_dir) / _safe_name(name)
    out_dir.mkdir(parents=True, exist_ok=True)

    if metrics:
        metrics_to_frame(metrics).to_csv(out_dir / "metrics.csv", index=False, encoding="utf-8-sig")
        monthly = metrics.get("monthly_pnl_table")
        if isinstance(monthly, pd.DataFrame) and not monthly.empty:
            monthly.to_csv(out_dir / "monthly_pnl.csv", encoding="utf-8-sig")

    trades_df = trades_to_frame(trades)
    if not trades_df.empty:
        trades_df.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")

    if isinstance(equity, pd.Series):
        equity.rename("equity").to_csv(out_dir / "equity.csv", encoding="utf-8-sig")
    elif isinstance(equity, pd.DataFrame) and not equity.empty:
        equity.to_csv(out_dir / "equity.csv", encoding="utf-8-sig")

    print(f"Đã export kết quả vào: {out_dir}")
    return out_dir


__all__ = ["export_result_bundle"]
