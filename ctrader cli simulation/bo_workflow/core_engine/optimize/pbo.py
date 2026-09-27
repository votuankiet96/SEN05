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
3. Mỗi cách chia: tính hiệu suất (mean return) từng trial trên nửa HỌC, chọn
   trial cao nhất = winner. Xếp hạng winner đó trên nửa KIỂM (rank 1..N,
   ascending) → hạng tương đối ω = rank/(N+1).
4. λ = logit(ω) = ln(ω/(1-ω)) — λ≤0 nghĩa là winner rơi xuống nửa DƯỚI trung
   vị ở KIỂM (overfit tính cho đúng cách chia này).
5. PBO = tỷ lệ % số cách chia có λ≤0, trên TỔNG `C(S, S/2)` cách chia.
"""
from __future__ import annotations

import itertools
import math
from statistics import mean
from typing import Any, Mapping, Sequence

DEFAULT_BLOCKS = 16


def _block_mean_return(values: Sequence[float]) -> float:
    return mean(values) if values else 0.0


def run(trials: Mapping[str, Sequence[float]], *, blocks: int = DEFAULT_BLOCKS) -> dict[str, Any]:
    if len(trials) < 2:
        raise ValueError("PBO cần ít nhất 2 trial để so sánh")
    if blocks % 2 != 0 or blocks < 2:
        raise ValueError(f"blocks={blocks} phải là số chẵn >= 2")
    labels = list(trials)
    n = len(labels)
    lengths = {len(trials[label]) for label in labels}
    if len(lengths) != 1:
        raise ValueError("mọi trial phải có cùng độ dài chuỗi ngày (cùng khoảng thời gian backtest)")
    total_days = lengths.pop()
    if blocks > total_days:
        raise ValueError(f"blocks={blocks} vượt quá số ngày dữ liệu ({total_days})")

    # Cắt T ngày thành `blocks` khối liên tiếp bằng nhau — phần dư (nếu T
    # không chia hết cho blocks) bị bỏ, không đưa vào bất kỳ khối nào (đúng
    # cách implementation tham chiếu xử lý, đơn giản hơn chia không đều).
    chunk_len = total_days // blocks
    chunks: list[dict[str, Sequence[float]]] = [
        {label: trials[label][i * chunk_len:(i + 1) * chunk_len] for label in labels}
        for i in range(blocks)
    ]

    half = blocks // 2
    overfit_count = 0
    total_splits = 0
    logits: list[float] = []
    for is_indices in itertools.combinations(range(blocks), half):
        is_set = set(is_indices)
        oos_indices = [i for i in range(blocks) if i not in is_set]

        is_perf = {
            label: _block_mean_return([v for i in is_indices for v in chunks[i][label]])
            for label in labels
        }
        oos_perf = {
            label: _block_mean_return([v for i in oos_indices for v in chunks[i][label]])
            for label in labels
        }

        winner = max(labels, key=lambda label: is_perf[label])
        oos_ranked = sorted(labels, key=lambda label: oos_perf[label])  # thấp -> cao
        rank = oos_ranked.index(winner) + 1  # 1..N, 1 = tệ nhất
        omega = rank / (n + 1)
        omega = min(max(omega, 1e-9), 1 - 1e-9)  # tránh logit(0)/logit(1) vô cực
        lam = math.log(omega / (1 - omega))

        logits.append(lam)
        total_splits += 1
        if lam <= 0:
            overfit_count += 1

    return {
        "schema": "bo-research-pbo/v1",
        "n_trials": n,
        "sample_length": total_days,
        "blocks": blocks,
        "splits_tested": total_splits,
        "pbo": overfit_count / total_splits,
        "logit_mean": mean(logits),
    }
