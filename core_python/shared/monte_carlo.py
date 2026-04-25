"""Monte Carlo simulation utilities for trade-level robustness testing.

Module này không phụ thuộc vào bất kỳ strategy cụ thể nào.
Chỉ cần list trade PnLs là đủ để chạy.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .theme import DARK


def run_monte_carlo(trade_pnls: list[float], n_iter: int = 1000,
                    dd_threshold: float = 0.20,
                    initial_balance: float = 0.0) -> dict:
    """Run Monte Carlo resampling on trade PnL sequence.

    Parameters
    ----------
    trade_pnls      : list[float]
        Danh sách PnL từng lệnh theo thứ tự lịch sử gốc.
    n_iter          : int, default=1000
        Số lần mô phỏng Monte Carlo.
    dd_threshold    : float, default=0.20
        Ngưỡng drawdown (tỷ lệ, ví dụ 0.20 = 20%) để tính xác suất vượt ngưỡng.
    initial_balance : float, default=0.0
        Vốn khởi đầu. Khi > 0, equity curve bắt đầu từ initial_balance thay vì 0.
        Quan trọng để tính drawdown đúng: không có vốn ban đầu, drawdown bị phóng to
        vì denominator (peak equity) rất nhỏ ở các bars đầu.

    Returns
    -------
    dict
        - equity_p5, equity_p50, equity_p95 : equity curves theo phân vị (bao gồm initial_balance)
        - prob_exceed_dd                     : xác suất max drawdown vượt dd_threshold
        - sharpe_ci_low, sharpe_ci_high      : khoảng tin cậy 95% của Sharpe ratio
        - max_drawdowns, sharpe_samples, equity_paths : phân phối nội bộ để vẽ biểu đồ
    """
    arr = np.asarray(trade_pnls, dtype=float)
    if arr.size == 0:
        empty = np.array([], dtype=float)
        return {
            'equity_p5': empty,
            'equity_p50': empty,
            'equity_p95': empty,
            'prob_exceed_dd': 0.0,
            'sharpe_ci_low': 0.0,
            'sharpe_ci_high': 0.0,
            'max_drawdowns': empty,
            'sharpe_samples': empty,
            'equity_paths': np.empty((0, 0), dtype=float),
        }

    n_trades      = arr.size
    equity_paths  = np.zeros((n_iter, n_trades), dtype=float)
    max_drawdowns = np.zeros(n_iter, dtype=float)
    sharpe_samples = np.zeros(n_iter, dtype=float)

    # Block bootstrap giữ serial correlation (streak thắng/thua liên tiếp).
    # Dùng modulo indexing để mỗi block luôn có đúng block_size phần tử, tránh
    # trường hợp block cuối bị cắt ngắn khi s+block_size vượt biên mảng.
    block_size = max(1, int(np.sqrt(n_trades)))
    n_blocks   = int(np.ceil(n_trades / block_size)) + 1  # +1 buffer

    for i in range(n_iter):
        starts   = np.random.randint(0, n_trades, size=n_blocks)
        indices  = np.concatenate(
            [np.arange(s, s + block_size) % n_trades for s in starts]
        )[:n_trades]
        shuffled = arr[indices]

        # Equity curve bắt đầu từ initial_balance để tính drawdown đúng.
        # Không có vốn ban đầu (=0) → denominator peak gần 0 ở bars đầu
        # → drawdown bị phóng đại nhiều lần so với thực tế.
        equity = initial_balance + np.cumsum(shuffled)
        equity_paths[i, :] = equity

        # Trailing peak phải không nhỏ hơn initial_balance
        running_peak = np.maximum.accumulate(equity)
        running_peak = np.maximum(running_peak, max(initial_balance, 1e-12))
        dd = (running_peak - equity) / running_peak
        max_drawdowns[i] = float(np.max(dd))

        std = shuffled.std(ddof=1) if shuffled.size > 1 else 0.0
        sharpe_samples[i] = (shuffled.mean() / std) * np.sqrt(252) if std > 0 else 0.0

    equity_p5  = np.percentile(equity_paths,  5, axis=0)
    equity_p50 = np.percentile(equity_paths, 50, axis=0)
    equity_p95 = np.percentile(equity_paths, 95, axis=0)

    prob_exceed_dd = float(np.mean(max_drawdowns > dd_threshold))
    sharpe_ci_low, sharpe_ci_high = np.percentile(sharpe_samples, [2.5, 97.5])

    return {
        'equity_p5':      equity_p5,
        'equity_p50':     equity_p50,
        'equity_p95':     equity_p95,
        'prob_exceed_dd': prob_exceed_dd,
        'sharpe_ci_low':  float(sharpe_ci_low),
        'sharpe_ci_high': float(sharpe_ci_high),
        'max_drawdowns':  max_drawdowns,
        'sharpe_samples': sharpe_samples,
        'equity_paths':   equity_paths,
    }


def plot_monte_carlo(mc_result: dict) -> None:
    """Plot Monte Carlo summary with dark theme style.

    Parameters
    ----------
    mc_result : dict
        Output dictionary from run_monte_carlo().

    Returns
    -------
    None
        Hiển thị 3 subplot:
        - Equity percentile curves (p5/p50/p95)
        - Drawdown distribution
        - Sharpe distribution (kèm CI 95%)
    """
    equity_p5 = np.asarray(mc_result.get('equity_p5', []), dtype=float)
    equity_p50 = np.asarray(mc_result.get('equity_p50', []), dtype=float)
    equity_p95 = np.asarray(mc_result.get('equity_p95', []), dtype=float)
    max_drawdowns = np.asarray(mc_result.get('max_drawdowns', []), dtype=float)
    sharpe_samples = np.asarray(mc_result.get('sharpe_samples', []), dtype=float)

    bg = DARK['bg']
    panel = DARK['panel']
    border = DARK['border']
    text = DARK['text']

    fig, axes = plt.subplots(1, 3, figsize=(18, 5), facecolor=bg)
    for ax in axes:
        ax.set_facecolor(panel)
        ax.tick_params(colors=text)
        for spine in ax.spines.values():
            spine.set_color(border)

    x = np.arange(len(equity_p50))
    axes[0].plot(x, equity_p5, color='#FF6B6B', linewidth=1.5, label='P5')
    axes[0].plot(x, equity_p50, color='#6BCB77', linewidth=2.0, label='P50')
    axes[0].plot(x, equity_p95, color='#00D4FF', linewidth=1.5, label='P95')
    axes[0].set_title('Monte Carlo Equity Percentiles', color=text)
    axes[0].set_xlabel('Trade #', color=text)
    axes[0].set_ylabel('Equity', color=text)
    axes[0].grid(color=border, alpha=0.5)
    axes[0].legend(facecolor=panel, edgecolor=border, labelcolor=text)

    if max_drawdowns.size:
        axes[1].hist(max_drawdowns, bins=40, color='#845EC2', alpha=0.8)
    axes[1].set_title('Max Drawdown Distribution', color=text)
    axes[1].set_xlabel('Max Drawdown', color=text)
    axes[1].set_ylabel('Frequency', color=text)
    axes[1].grid(color=border, alpha=0.5)

    if sharpe_samples.size:
        axes[2].hist(sharpe_samples, bins=40, color='#FFD93D', alpha=0.85)
        ci_low = mc_result.get('sharpe_ci_low', 0.0)
        ci_high = mc_result.get('sharpe_ci_high', 0.0)
        axes[2].axvline(ci_low, color='#FF6B6B', linestyle='--', linewidth=1.5,
                        label=f'CI low: {ci_low:.2f}')
        axes[2].axvline(ci_high, color='#00D4FF', linestyle='--', linewidth=1.5,
                        label=f'CI high: {ci_high:.2f}')
        axes[2].legend(facecolor=panel, edgecolor=border, labelcolor=text)
    axes[2].set_title('Sharpe Distribution', color=text)
    axes[2].set_xlabel('Sharpe', color=text)
    axes[2].set_ylabel('Frequency', color=text)
    axes[2].grid(color=border, alpha=0.5)

    fig.suptitle('Monte Carlo Robustness', color=text, fontsize=14)
    fig.tight_layout()
    plt.show()
