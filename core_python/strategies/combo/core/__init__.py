"""Gói logic lõi cho chiến lược Combo.

Nhiệm vụ của thư mục core:
- Chứa config chiến lược và helper dùng chung.
- Chứa scanner/pipeline/backtest dùng cho notebook và dashboard.

Lưu ý quản trị:
- Khi cần đổi hành vi chiến lược, ưu tiên chỉnh tham số ở strategy_config.py.
- Chỉ sửa code logic khi bạn chủ động muốn thay đổi luật giao dịch.

Cấu trúc module:
- backtest_engine : load data + tạo chỉ báo + detect signals (+ re-exports).
- execution       : backtest_symbol(), backtest_fast().
- walk_forward    : check_plateau_stability(), walk_forward_backtest().
- metrics         : calc_metrics(), in_bao_cao().
"""

from .backtest_engine import (
    load_backtest_data,
    load_backtest_full,
    add_backtest_indicators,
    session_mask,
    detect_signals,
)
from .execution import backtest_symbol, backtest_fast
from .walk_forward import walk_forward_backtest, check_plateau_stability
from .metrics import calc_metrics, in_bao_cao

__all__ = [
    # Data loading & prep
    "load_backtest_data",
    "load_backtest_full",
    "add_backtest_indicators",
    "session_mask",
    "detect_signals",
    # Execution
    "backtest_symbol",
    "backtest_fast",
    # Walk-forward
    "walk_forward_backtest",
    "check_plateau_stability",
    # Metrics & reporting
    "calc_metrics",
    "in_bao_cao",
]
