from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..models import daily_returns


BLOCK_METHODS = ("non_overlapping", "moving", "stationary")


@dataclass(frozen=True)
class MonteCarloConfig:
    paths: int = 10000
    block_days: int = 5
    seed: int = 20260911
    timezone: str = "Europe/Prague"
    initial_balance: float = 100000.0
    daily_loss_pct: float = 5.0
    max_loss_pct: float = 10.0


def run(
    equity_points: Iterable[Mapping[str, Any]],
    config: MonteCarloConfig,
    *,
    field: str = "balance",
    method: str = "non_overlapping",
) -> dict[str, Any]:
    days = daily_returns(equity_points, config.timezone, config.initial_balance, field_name=field)
    if not days:
        raise ValueError("equity timeline is empty")
    rng = random.Random(config.seed)
    # non_overlapping/moving rút khối CẮT SẴN (chỉ khác nhau ở vị trí cắt —
    # xem _cut_blocks); stationary không cắt sẵn gì, tự đi bộ trực tiếp trên
    # `days` mỗi path — 2 hình dạng thuật toán khác nhau, không gộp chung được
    # 1 vòng lặp. Cắt `blocks` 1 LẦN ngoài vòng lặp path (không phụ thuộc rng)
    # để không tốn công cắt lại 10.000 lần.
    blocks = _cut_blocks(days, config.block_days, method) if method != "stationary" else None
    max_dd: list[float] = []
    daily_breaches = 0
    total_breaches = 0
    for _ in range(config.paths):
        if method == "stationary":
            sampled = _resample_stationary(days, config.block_days, rng)
        else:
            sampled = []
            while len(sampled) < len(days):
                sampled.extend(rng.choice(blocks))
            sampled = sampled[:len(days)]
        balance = config.initial_balance
        peak = balance
        worst_dd = 0.0
        daily_bad = False
        total_bad = False
        for daily_return in sampled:
            day_start = balance
            balance *= 1.0 + daily_return
            peak = max(peak, balance)
            worst_dd = max(worst_dd, (peak - balance) / peak * 100.0)
            if day_start - balance > config.initial_balance * config.daily_loss_pct / 100.0:
                daily_bad = True
            if config.initial_balance - balance > config.initial_balance * config.max_loss_pct / 100.0:
                total_bad = True
        max_dd.append(worst_dd)
        daily_breaches += int(daily_bad)
        total_breaches += int(total_bad)
    max_dd.sort()
    return {
        "schema": "bo-research-montecarlo/v1",
        "return_field": field,
        "block_method": method,
        "paths": config.paths,
        "block_days": config.block_days,
        "seed": config.seed,
        "days": len(days),
        "max_dd_p50": _pct(max_dd, 0.50),
        "max_dd_p95": _pct(max_dd, 0.95),
        "max_dd_p99": _pct(max_dd, 0.99),
        "p_daily_breach": daily_breaches / config.paths,
        "p_total_breach": total_breaches / config.paths,
    }


def run_methods_compare(
    equity_points: Iterable[Mapping[str, Any]],
    config: MonteCarloConfig,
    *,
    field: str = "balance",
) -> dict[str, Any]:
    """Chạy song song 3 cách cắt/rút khối trên CÙNG dữ liệu — `non_overlapping`
    (Carlstein 1986, đang dùng mặc định), `moving` (Künsch 1989 — khối chồng
    lấn, tận dụng MỌI cụm ngày-liên-tiếp có thật thay vì chỉ đúng ranh giới cắt
    cố định), `stationary` (Politis & Romano 1994 — độ dài khối ngẫu nhiên,
    dữ liệu coi như vòng tròn, giữ đúng tính "dừng" thống kê). [2026-09-22,
    chốt người dùng] Không gộp thành 1 số duy nhất — để 3 kết quả đứng cạnh
    nhau, tự xem sai khác giữa bản đơn giản đang dùng và 2 bản nâng cao có đủ
    lớn để đổi kết luận thực hành hay không. Xem
    research_notes/montecarlo-block-bootstrap-variants.md."""
    points = list(equity_points)
    return {
        "schema": "bo-research-montecarlo-methods/v1",
        "non_overlapping": run(points, config, field=field, method="non_overlapping"),
        "moving": run(points, config, field=field, method="moving"),
        "stationary": run(points, config, field=field, method="stationary"),
    }


def _cut_blocks(days: list[float], block_days: int, method: str) -> list[list[float]]:
    if method == "non_overlapping":
        return [days[i:i + block_days] for i in range(0, len(days), block_days)]
    if method == "moving":
        # Künsch (1989): MỌI vị trí bắt đầu hợp lệ (không chỉ bội số của
        # block_days) — N = n-L+1 khối chồng lấn, thay vì n/L khối cố định.
        limit = max(1, len(days) - block_days + 1)
        return [days[i:i + block_days] for i in range(limit)]
    raise ValueError(f"unknown block method: {method!r}")


def _resample_stationary(days: list[float], mean_block_days: int, rng: random.Random) -> list[float]:
    """Politis & Romano (1994): đi bộ tuần tự trên `days` coi như 1 VÒNG TRÒN
    (hết ngày cuối quay lại ngày đầu, không bao giờ "hụt" dữ liệu giữa khối).
    Mỗi bước, sau khi lấy 1 ngày, có xác suất `1/mean_block_days` để NHẢY sang
    1 điểm bắt đầu mới hoàn toàn ngẫu nhiên — độ dài khối vì vậy là số ngẫu
    nhiên (phân phối hình học, kỳ vọng = mean_block_days), không cố định như
    2 phương pháp kia."""
    n = len(days)
    jump_probability = 1.0 / max(1, mean_block_days)
    idx = rng.randrange(n)
    sampled: list[float] = []
    for _ in range(n):
        sampled.append(days[idx])
        idx = idx + 1 if idx + 1 < n else 0
        if rng.random() < jump_probability:
            idx = rng.randrange(n)
    return sampled


def run_dual(equity_points: Iterable[Mapping[str, Any]], config: MonteCarloConfig) -> dict[str, Any]:
    """Chạy `run()` 2 lần song song trên cùng dữ liệu — 1 lần theo `balance` (vốn
    ĐÃ CHỐT, đúng chuẩn NAV để "chain" return qua từng ngày), 1 lần theo
    `minEquity` (đáy equity mỗi ngày, bắt được lỗ nổi giữa ngày — đúng luật
    FTMO thật). [2026-09-22, chốt người dùng] Không gộp 2 field vào 1 mô hình
    lai (2 khái niệm thống kê khác nhau: "điểm chốt" vs "cực trị" — xem
    research_notes/montecarlo-equity-vs-balance-daily-return-methodology.md)
    — để 2 kết quả đứng cạnh nhau, người dùng tự đối chiếu khoảng dao động
    giữa "rủi ro trên vốn đã chốt" và "rủi ro bao gồm lỗ nổi trong ngày"."""
    points = list(equity_points)
    return {
        "schema": "bo-research-montecarlo-dual/v1",
        "balance_based": run(points, config, field="balance"),
        "min_equity_based": run(points, config, field="minEquity"),
    }


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
    return values[index]
