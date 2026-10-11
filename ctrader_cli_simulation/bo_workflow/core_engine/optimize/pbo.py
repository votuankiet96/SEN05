"""pbo.py — Probability of Backtest Overfitting qua CSCV (Bailey, Borwein,
López de Prado, Zhu).

Trả lời câu hỏi KHÁC `dsr.py`: không nhìn 1 con số Sharpe cao nhất, mà nhìn
vào CHÍNH QUY TRÌNH chọn winner — nếu lặp lại "chọn theo 1 nửa dữ liệu, kiểm
trên nửa kia" theo MỌI cách chia đối xứng có thể, quy trình đó có xu hướng hệ
thống chọn ra trial TỆ trên nửa kiểm hay không? Thuần hậu kỳ, giống `dsr.py`
— không biết gì về `store`/report ở đâu, chỉ nhận chuỗi %lời/lỗ theo ngày của
N trial đã có sẵn; nơi ĐỌC N report thật là `readout.run_pbo()`.

Thuật toán xác nhận qua tham chiếu chéo giấy gốc + implementation Python thật
(esvhd/pypbo, đọc trực tiếp source) — xem
research_notes/dsr-pbo-formula-verification.md:

1. Ma trận M: T ngày (hàng) × N trial (cột) — cắt trục T thành `blocks` (S,
   số CHẴN) khối liên tiếp bằng nhau.
2. Với MỌI cách chọn đúng S/2 khối làm HỌC (còn lại S/2 khối làm KIỂM) —
   liệt kê HẾT `C(S, S/2)` cách, không phải chọn ngẫu nhiên vài cách.
3. Mỗi cách chia: tính hiệu suất từng trial trên nửa HỌC theo ĐÚNG tiêu chí
   chọn (`objective`), chọn trial cao nhất = winner. Xếp hạng winner đó trên
   nửa KIỂM (rank 1..N, tăng dần, hoà hạng lấy hạng trung bình như
   `scipy.stats.rankdata` mà pypbo dùng) → hạng tương đối ω = rank/(N+1).
4. λ = logit(ω) = ln(ω/(1-ω)) — λ≤0 nghĩa là winner rơi xuống nửa DƯỚI trung
   vị ở KIỂM (overfit tính cho đúng cách chia này).
5. PBO = tỷ lệ % số cách chia có λ≤0, trên TỔNG `C(S, S/2)` cách chia.

[2026-09-28] Tiêu chí hiệu suất: bài gốc nói rõ PBO đánh giá "how reliable a
backtest selection process is" và "can be applied to any alternative
[performance statistic]". Vì vậy tiêu chí phải là tiêu chí đang dùng để chọn
tổ hợp, không cố định 1 thứ:
- `net_profit`: lợi nhuận trung bình/ngày trên các khối (giữ nguyên cách tính
  cũ — cùng thứ tự xếp hạng với tổng lợi nhuận vì mọi trial cùng số ngày).
- `profit_factor`, `win_rate`: tính từ các LỆNH đóng trong các khối đó, nên
  cần thêm `trades` (mỗi trial, mỗi ngày: danh sách lãi/lỗ ròng các lệnh đóng
  trong ngày, cùng trục ngày với `trials`).
"""
from __future__ import annotations

import itertools
import math
from statistics import mean
from typing import Any, Callable, Mapping, Sequence

DEFAULT_BLOCKS = 16
# Số lần báo tiến độ tối đa cho cả vòng CSCV — đủ mịn cho thanh % (NN/g:
# việc >10 s cần percent-done), không ghi SQLite quá dày ở bên nhận.
PROGRESS_UPDATES = 100
OBJECTIVES = ("net_profit", "profit_factor", "win_rate")
TRADE_OBJECTIVES = frozenset({"profit_factor", "win_rate"})
SCHEMA = "bo-research-pbo/v2"


def _block_totals(
    returns: Sequence[float], day_trades: Sequence[Sequence[float]] | None,
) -> tuple[float, float, float, int, int]:
    """(tổng return, tổng lãi các lệnh thắng, tổng |lỗ| các lệnh thua, số lệnh
    thắng, số lệnh) của 1 khối — tổng hợp 1 lần, mỗi cách chia chỉ cộng các
    khối lại thay vì duyệt lại từng ngày."""
    total = math.fsum(returns)
    if day_trades is None:
        return total, 0.0, 0.0, 0, 0
    nets = [net for day in day_trades for net in day]
    gains = math.fsum(net for net in nets if net > 0)
    losses = math.fsum(-net for net in nets if net < 0)
    return total, gains, losses, sum(1 for net in nets if net > 0), len(nets)


def _performance(parts: Sequence[tuple[float, float, float, int, int]], objective: str, days: int) -> float:
    """Hiệu suất trên 1 nửa (nhiều khối). NaN = không đo được (vd không có
    lệnh nào) — xếp dưới mọi giá trị thật, không bao giờ được chọn làm winner."""
    if objective == "net_profit":
        return math.fsum(part[0] for part in parts) / days
    gains = math.fsum(part[1] for part in parts)
    losses = math.fsum(part[2] for part in parts)
    wins = sum(part[3] for part in parts)
    count = sum(part[4] for part in parts)
    if count == 0:
        return math.nan
    if objective == "profit_factor":
        if losses == 0:
            return math.inf if gains > 0 else math.nan
        return gains / losses
    return wins / count


def _order_key(value: float) -> float:
    return -math.inf if math.isnan(value) else value


def _average_rank(values: Sequence[float], index: int) -> float:
    """Hạng 1..N tăng dần của values[index]; hoà hạng lấy hạng trung bình."""
    target = _order_key(values[index])
    below = sum(1 for value in values if _order_key(value) < target)
    equal = sum(1 for value in values if _order_key(value) == target)
    return below + (equal + 1) / 2


def run(
    trials: Mapping[str, Sequence[float]], *, blocks: int = DEFAULT_BLOCKS,
    on_progress: Callable[[int, int], None] | None = None,
    objective: str = "net_profit",
    trades: Mapping[str, Sequence[Sequence[float]]] | None = None,
) -> dict[str, Any]:
    """`on_progress(done, total)` (tuỳ chọn) được gọi theo số cách chia đã xét
    — C(16, 8) = 12.870 cách với mặc định, nên vòng này là phần chạy lâu nhất."""
    if len(trials) < 2:
        raise ValueError("PBO cần ít nhất 2 trial để so sánh")
    if blocks % 2 != 0 or blocks < 2:
        raise ValueError(f"blocks={blocks} phải là số chẵn >= 2")
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective: {objective!r}; supported: {OBJECTIVES}")
    labels = list(trials)
    n = len(labels)
    lengths = {len(trials[label]) for label in labels}
    if len(lengths) != 1:
        raise ValueError("mọi trial phải có cùng độ dài chuỗi ngày (cùng khoảng thời gian backtest)")
    total_days = lengths.pop()
    if blocks > total_days:
        raise ValueError(f"blocks={blocks} vượt quá số ngày dữ liệu ({total_days})")
    if objective in TRADE_OBJECTIVES:
        if trades is None or set(trades) != set(labels):
            raise ValueError(f"objective {objective!r} cần kết quả từng lệnh của mọi trial")
        if any(len(trades[label]) != total_days for label in labels):
            raise ValueError("kết quả từng lệnh phải cùng trục ngày với chuỗi return")

    # Cắt T ngày thành `blocks` khối liên tiếp bằng nhau — phần dư (nếu T
    # không chia hết cho blocks) bị bỏ, không đưa vào bất kỳ khối nào (đúng
    # cách implementation tham chiếu xử lý, đơn giản hơn chia không đều).
    chunk_len = total_days // blocks
    per_trade = objective in TRADE_OBJECTIVES
    totals = {
        label: [
            _block_totals(
                trials[label][i * chunk_len:(i + 1) * chunk_len],
                trades[label][i * chunk_len:(i + 1) * chunk_len] if per_trade else None,
            )
            for i in range(blocks)
        ]
        for label in labels
    }

    half = blocks // 2
    half_days = half * chunk_len
    expected_splits = math.comb(blocks, half)
    report_every = max(1, expected_splits // PROGRESS_UPDATES)
    overfit_count = 0
    total_splits = 0
    logits: list[float] = []
    for is_indices in itertools.combinations(range(blocks), half):
        is_set = set(is_indices)
        oos_indices = [i for i in range(blocks) if i not in is_set]

        is_perf = [
            _performance([totals[label][i] for i in is_indices], objective, half_days)
            for label in labels
        ]
        oos_perf = [
            _performance([totals[label][i] for i in oos_indices], objective, half_days)
            for label in labels
        ]

        winner = max(range(n), key=lambda index: _order_key(is_perf[index]))
        rank = _average_rank(oos_perf, winner)  # 1..N, 1 = tệ nhất
        omega = rank / (n + 1)
        omega = min(max(omega, 1e-9), 1 - 1e-9)  # tránh logit(0)/logit(1) vô cực
        lam = math.log(omega / (1 - omega))

        logits.append(lam)
        total_splits += 1
        if lam <= 0:
            overfit_count += 1
        if on_progress and (total_splits % report_every == 0 or total_splits == expected_splits):
            on_progress(total_splits, expected_splits)

    return {
        "schema": SCHEMA,
        "objective": objective,
        "n_trials": n,
        "sample_length": total_days,
        "blocks": blocks,
        "splits_tested": total_splits,
        "pbo": overfit_count / total_splits,
        "logit_mean": mean(logits),
    }
