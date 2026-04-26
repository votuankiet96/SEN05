"""Monte Carlo simulation utilities for trade-level robustness testing.

=======================================================================
FILE NÀY LÀM GÌ?
=======================================================================

File này kiểm tra xem chiến lược có thực sự ổn định, hay chỉ may mắn
vì gặp đúng chuỗi lệnh thuận lợi trong lịch sử.

Ý tưởng rất đơn giản:
  Lấy danh sách lệnh lịch sử → xáo trộn thứ tự nhiều lần → mỗi lần
  xáo được một "phiên bản song song" của chiến lược → xem toàn bộ
  phiên bản đó hoạt động như thế nào.

Ví dụ thực tế:
  Chiến lược thực hiện 200 lệnh. Nếu 10 lệnh thắng lớn đầu tiên xảy
  ra ở cuối thay vì đầu, drawdown sẽ lớn hơn bao nhiêu? Monte Carlo
  trả lời câu hỏi đó bằng cách thử 1000 hoán vị khác nhau.

=======================================================================
TẠI SAO CẦN MONTE CARLO?
=======================================================================

Backtest thông thường chỉ cho một kết quả duy nhất — đúng với chuỗi
lệnh lịch sử đó. Nhưng trong tương lai, thứ tự lệnh thắng/thua sẽ
khác. Monte Carlo mô phỏng hàng nghìn thứ tự có thể xảy ra, từ đó
trả lời:
  - Xác suất drawdown vượt 20% là bao nhiêu %?
  - Sharpe ratio thực sự nằm trong khoảng nào (không phải 1 con số)?
  - Kịch bản xấu nhất trông như thế nào?

=======================================================================
FILE NÀY ĐỘC LẬP — KHÔNG PHỤ THUỘC VÀO CHIẾN LƯỢC CỤ THỂ NÀO
=======================================================================

Chỉ cần truyền vào danh sách lãi/lỗ từng lệnh (trade_pnls) là chạy
được. Không quan tâm đó là chiến lược Combo hay AI Trend.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .theme import DARK


def run_monte_carlo(
    trade_pnls: list[float],
    n_iter: int = 1000,
    dd_threshold: float = 0.20,
    initial_balance: float = 0.0,
    trades_per_year: float | None = None,
    random_seed: int | None = None,
) -> dict:
    """Chạy mô phỏng Monte Carlo trên chuỗi lãi/lỗ từng lệnh.

    =======================================================================
    CÁCH HOẠT ĐỘNG
    =======================================================================

    1. Nhận danh sách PnL lịch sử: [+120, -80, +200, -50, ...]
    2. Xáo trộn thứ tự n_iter lần (mặc định 1000 lần)
    3. Mỗi lần xáo → vẽ một đường vốn (equity curve) mới
    4. Từ 1000 đường vốn → tính phân vị P5/P50/P95, drawdown, Sharpe

    Kết quả P5 = đường vốn xấu thứ 5% → kịch bản tệ nhưng không phải
    tệ nhất. P95 = kịch bản tốt. P50 = trung vị, thực tế nhất.

    =======================================================================
    PARAMETERS
    =======================================================================

    trade_pnls
        Danh sách lãi/lỗ từng lệnh theo thứ tự lịch sử gốc.
        Ví dụ: [120.5, -80.0, 200.3, -45.2, ...]
        Đơn vị tùy ý (USD, pip, %) — miễn là nhất quán.

    n_iter
        Số lần xáo trộn và mô phỏng. Mặc định 1000.
        Tăng lên 5000–10000 để kết quả chính xác hơn, nhưng chạy lâu hơn.

    dd_threshold
        Ngưỡng drawdown để tính xác suất vượt. Mặc định 0.20 = 20%.
        Ví dụ: prob_exceed_dd = 0.15 → 15% kịch bản có drawdown > 20%.

    initial_balance
        Số vốn ban đầu. Nên truyền vào số thực (ví dụ: 100_000.0).

        Tại sao quan trọng?
        Nếu để mặc định = 0, các lệnh đầu tiên có peak equity gần 0,
        dẫn đến drawdown bị phóng to rất lớn một cách giả tạo.
        Ví dụ: lệnh đầu lãi +100$, lệnh hai lỗ -50$ → drawdown thực là
        50/100 = 50%, nhưng nếu equity bắt đầu từ 0 thì tính sai hoàn toàn.

    trades_per_year
        Số lệnh trung bình mỗi năm — dùng để tính Sharpe ratio đúng.

        Tại sao không dùng con số cố định 252?
        252 là số ngày giao dịch/năm — đúng cho daily returns.
        Chiến lược H4 trung bình ~200 lệnh/năm, không phải 252.
        Dùng sai → Sharpe bị phóng to ~38 lần so với thực tế.
        Nếu không truyền vào → mặc định dùng 252 (không chính xác).

    random_seed
        Seed cho bộ sinh số ngẫu nhiên. Truyền vào một số cố định
        (ví dụ: 42) để kết quả tái lập được — hữu ích khi debug hoặc
        so sánh hai chiến lược trên cùng tập mô phỏng.

    =======================================================================
    RETURNS
    =======================================================================

    Trả về dict với các key:

    equity_p5 / equity_p50 / equity_p95
        Ba đường vốn theo phân vị 5%, 50%, 95% — tính trên 1000 lần mô phỏng.
        Bắt đầu từ initial_balance (nếu có).

    prob_exceed_dd
        Xác suất (0.0 → 1.0) drawdown vượt ngưỡng dd_threshold.
        Ví dụ: 0.12 = 12% khả năng drawdown > 20%.

    sharpe_ci_low / sharpe_ci_high
        Khoảng tin cậy 95% của Sharpe ratio.
        Ví dụ: [0.8, 1.6] → Sharpe thực sự nằm trong khoảng này
        với xác suất 95%, không phải một con số duy nhất.

    max_drawdowns / sharpe_samples / equity_paths
        Dữ liệu thô của 1000 lần mô phỏng — dùng để vẽ biểu đồ phân phối.
    """
    arr = np.asarray(trade_pnls, dtype=float)

    # Trường hợp không có lệnh nào → trả về dict rỗng, không crash.
    if arr.size == 0:
        empty = np.array([], dtype=float)
        return {
            "equity_p5":             empty,
            "equity_p50":            empty,
            "equity_p95":            empty,
            "prob_exceed_dd":        0.0,
            "sharpe_ci_low":         0.0,
            "sharpe_ci_high":        0.0,
            "max_drawdowns":         empty,
            "sharpe_samples":        empty,
            "sharpe_annualization":  float(trades_per_year) if trades_per_year else 252.0,
            "equity_paths":          np.empty((0, 0), dtype=float),
        }

    n_trades       = arr.size
    equity_paths   = np.zeros((n_iter, n_trades), dtype=float)
    max_drawdowns  = np.zeros(n_iter, dtype=float)
    sharpe_samples = np.zeros(n_iter, dtype=float)

    # Dùng default_rng thay vì np.random.seed() vì an toàn hơn trong môi
    # trường đa luồng và cho phép tái lập kết quả khi truyền random_seed.
    rng = np.random.default_rng(random_seed)

    # Hệ số annualize Sharpe: sqrt(số lệnh/năm).
    # Nếu không biết trades_per_year, dùng 252 — nhưng sẽ không chính xác
    # với chiến lược H4 (nên truyền giá trị thực từ caller).
    sharpe_annualization = float(trades_per_year) if trades_per_year else 252.0

    # -----------------------------------------------------------------------
    # TẠI SAO DÙNG BLOCK BOOTSTRAP THAY VÌ RANDOM SHUFFLE THÔNG THƯỜNG?
    # -----------------------------------------------------------------------
    # Random shuffle hoàn toàn (np.random.permutation) phá vỡ tương quan
    # tự nhiên trong chuỗi lệnh: streaks thắng/thua liên tiếp, hiệu ứng
    # mùa vụ, xu hướng thị trường. Kết quả là confidence interval quá hẹp,
    # trông có vẻ "ổn định" hơn thực tế.
    #
    # Block bootstrap giữ lại các chuỗi con liên tiếp (block) nguyên vẹn.
    # Kích thước block = sqrt(n_trades) là công thức kinh nghiệm cân bằng
    # giữa: giữ đủ correlation vs đủ ngẫu nhiên để mô phỏng đa dạng.
    # Ví dụ: 200 lệnh → block_size = 14 → giữ chuỗi 14 lệnh liên tiếp.
    block_size = max(1, int(np.sqrt(n_trades)))
    # Tạo thêm 1 block dự phòng để sau khi ghép luôn đủ n_trades phần tử.
    n_blocks = int(np.ceil(n_trades / block_size)) + 1

    for i in range(n_iter):
        # Chọn ngẫu nhiên n_blocks điểm bắt đầu trong mảng PnL gốc.
        starts = rng.integers(0, n_trades, size=n_blocks)

        # Tạo index cho từng block bằng modulo để không bao giờ vượt biên
        # mảng (block cuối sẽ "cuộn vòng" về đầu mảng thay vì bị cắt ngắn).
        indices = np.concatenate(
            [np.arange(s, s + block_size) % n_trades for s in starts]
        )[:n_trades]  # Cắt đúng n_trades phần tử.

        shuffled = arr[indices]

        # Equity bắt đầu từ initial_balance để tính drawdown đúng.
        # Xem giải thích chi tiết ở phần Parameters → initial_balance.
        equity = initial_balance + np.cumsum(shuffled)
        equity_paths[i, :] = equity

        # Trailing peak không được nhỏ hơn initial_balance — tránh drawdown
        # âm vô nghĩa khi equity tăng từ đầu và chưa bao giờ xuống dưới vốn.
        running_peak = np.maximum.accumulate(equity)
        running_peak = np.maximum(running_peak, max(initial_balance, 1e-12))
        dd = (running_peak - equity) / running_peak
        max_drawdowns[i] = float(np.max(dd))

        # Sharpe của lần mô phỏng này: mean/std × sqrt(trades_per_year).
        std = shuffled.std(ddof=1) if shuffled.size > 1 else 0.0
        sharpe_samples[i] = (
            (shuffled.mean() / std) * np.sqrt(max(sharpe_annualization, 1.0))
            if std > 0
            else 0.0
        )

    # Tính phân vị trên 1000 equity curve → 3 đường đại diện.
    equity_p5  = np.percentile(equity_paths,  5, axis=0)
    equity_p50 = np.percentile(equity_paths, 50, axis=0)
    equity_p95 = np.percentile(equity_paths, 95, axis=0)

    # Tỷ lệ lần mô phỏng có max drawdown vượt ngưỡng → xác suất rủi ro.
    prob_exceed_dd = float(np.mean(max_drawdowns > dd_threshold))

    # Khoảng tin cậy 95% của Sharpe: cắt 2.5% hai đầu phân phối.
    sharpe_ci_low, sharpe_ci_high = np.percentile(sharpe_samples, [2.5, 97.5])

    return {
        "equity_p5":             equity_p5,
        "equity_p50":            equity_p50,
        "equity_p95":            equity_p95,
        "prob_exceed_dd":        prob_exceed_dd,
        "sharpe_ci_low":         float(sharpe_ci_low),
        "sharpe_ci_high":        float(sharpe_ci_high),
        "max_drawdowns":         max_drawdowns,
        "sharpe_samples":        sharpe_samples,
        "sharpe_annualization":  sharpe_annualization,
        "equity_paths":          equity_paths,
    }


def plot_monte_carlo(mc_result: dict) -> None:
    """Vẽ 3 biểu đồ tóm tắt kết quả Monte Carlo.

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Sau khi chạy run_monte_carlo(), hàm này chuyển kết quả số thành 3
    biểu đồ trực quan để đánh giá nhanh độ ổn định của chiến lược.

    =======================================================================
    3 BIỂU ĐỒ
    =======================================================================

    Biểu đồ 1 — Đường vốn theo phân vị (Equity Percentiles):
        Vẽ 3 đường P5 (đỏ), P50 (xanh lá), P95 (xanh dương).
        Đọc như thế nào: Khoảng cách giữa P5 và P95 càng hẹp → chiến
        lược càng ổn định, ít phụ thuộc vào may mắn thứ tự lệnh.

    Biểu đồ 2 — Phân phối Max Drawdown:
        Histogram drawdown tối đa của 1000 lần mô phỏng.
        Đọc như thế nào: Đỉnh cột càng gần 0 càng tốt. Nếu phần lớn
        cột nằm bên phải 20% → rủi ro cao, nên giảm risk/trade.

    Biểu đồ 3 — Phân phối Sharpe Ratio + khoảng tin cậy 95%:
        Histogram Sharpe của 1000 lần mô phỏng, có vạch CI low/high.
        Đọc như thế nào: Nếu CI low < 0 → có kịch bản Sharpe âm → chiến
        lược không đủ ổn định để live trade.

    =======================================================================
    PARAMETERS
    =======================================================================

    mc_result
        Dict trả về từ run_monte_carlo(). Truyền thẳng vào mà không cần
        xử lý thêm.

    Returns
    -------
    None — hiển thị biểu đồ trực tiếp (plt.show()), không trả về gì.
    """
    equity_p5      = np.asarray(mc_result.get("equity_p5",      []), dtype=float)
    equity_p50     = np.asarray(mc_result.get("equity_p50",     []), dtype=float)
    equity_p95     = np.asarray(mc_result.get("equity_p95",     []), dtype=float)
    max_drawdowns  = np.asarray(mc_result.get("max_drawdowns",  []), dtype=float)
    sharpe_samples = np.asarray(mc_result.get("sharpe_samples", []), dtype=float)

    # Màu sắc theo dark theme chung của hệ thống.
    bg     = DARK["bg"]
    panel  = DARK["panel"]
    border = DARK["border"]
    text   = DARK["text"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5), facecolor=bg)
    for ax in axes:
        ax.set_facecolor(panel)
        ax.tick_params(colors=text)
        for spine in ax.spines.values():
            spine.set_color(border)

    # ── Biểu đồ 1: Đường vốn P5 / P50 / P95 ────────────────────────────────
    x = np.arange(len(equity_p50))
    axes[0].plot(x, equity_p5,  color="#FF6B6B", linewidth=1.5, label="P5  (xấu)")
    axes[0].plot(x, equity_p50, color="#6BCB77", linewidth=2.0, label="P50 (trung vị)")
    axes[0].plot(x, equity_p95, color="#00D4FF", linewidth=1.5, label="P95 (tốt)")
    axes[0].set_title("Đường vốn theo phân vị Monte Carlo", color=text)
    axes[0].set_xlabel("Số lệnh", color=text)
    axes[0].set_ylabel("Vốn", color=text)
    axes[0].grid(color=border, alpha=0.5)
    axes[0].legend(facecolor=panel, edgecolor=border, labelcolor=text)

    # ── Biểu đồ 2: Phân phối Max Drawdown ───────────────────────────────────
    if max_drawdowns.size:
        axes[1].hist(max_drawdowns, bins=40, color="#845EC2", alpha=0.8)
    axes[1].set_title("Phân phối Max Drawdown", color=text)
    axes[1].set_xlabel("Max Drawdown", color=text)
    axes[1].set_ylabel("Số lần xuất hiện", color=text)
    axes[1].grid(color=border, alpha=0.5)

    # ── Biểu đồ 3: Phân phối Sharpe + khoảng tin cậy 95% ───────────────────
    if sharpe_samples.size:
        axes[2].hist(sharpe_samples, bins=40, color="#FFD93D", alpha=0.85)
        ci_low  = mc_result.get("sharpe_ci_low",  0.0)
        ci_high = mc_result.get("sharpe_ci_high", 0.0)
        # Vạch đứt đỏ = giới hạn dưới CI, vạch đứt xanh = giới hạn trên CI.
        axes[2].axvline(ci_low,  color="#FF6B6B", linestyle="--", linewidth=1.5,
                        label=f"CI thấp:  {ci_low:.2f}")
        axes[2].axvline(ci_high, color="#00D4FF", linestyle="--", linewidth=1.5,
                        label=f"CI cao: {ci_high:.2f}")
        axes[2].legend(facecolor=panel, edgecolor=border, labelcolor=text)
    axes[2].set_title("Phân phối Sharpe Ratio", color=text)
    axes[2].set_xlabel("Sharpe", color=text)
    axes[2].set_ylabel("Số lần xuất hiện", color=text)
    axes[2].grid(color=border, alpha=0.5)

    fig.suptitle("Monte Carlo — Kiểm tra độ ổn định chiến lược", color=text, fontsize=14)
    fig.tight_layout()
    plt.show()
