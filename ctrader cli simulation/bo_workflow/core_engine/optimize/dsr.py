"""dsr.py — Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

Trả lời: trong N trial đã thử (vd 100 tổ hợp KslLevel×KtpLevel), Sharpe Ratio
của trial THẮNG có thật sự > 0 sau khi trừ hao mức "nhiễu kỳ vọng" do đã thử
N lần độc lập hay không? Thuần hậu kỳ — không chạy backtest, không biết gì về
`facilitator`/`store`/`ExperimentStore` (giống `montecarlo.py` không biết gì
về nơi report đến từ đâu) — nơi ĐỌC N report thật + gọi vào đây là
`readout.run_dsr()`.

Công thức xác nhận qua nhiều nguồn ĐỘC LẬP (không chỉ 1 lần fetch PDF — lần
đầu fetch trực tiếp PDF gốc bị lỗi OCR/trích sai công thức, đã đối chiếu lại
qua marti.ai [có trích code thật] + ml4trading.io + tóm tắt học thuật trước
khi chốt) — xem research_notes/dsr-pbo-formula-verification.md:

  E[max Z | N]  = (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))      (γ=Euler-Mascheroni)
  SR0           = σ_SR · E[max Z]                            (ngưỡng "nhiễu kỳ vọng")
  DSR           = Φ[ (ŜR-SR0)·√(T-1) / √(1-γ̂₃·ŜR+((γ̂₄-1)/4)·ŜR²) ]

ŜR (Sharpe QUAN SÁT của trial đang xét) đi vào CẢ tử số lẫn số hạng hiệu chỉnh
skew/kurtosis; SR0 CHỈ xuất hiện ở tử số làm ngưỡng so sánh — đây là điểm dễ
nhầm nhất khi tự code lại công thức này (1 nguồn phụ tôi tra ban đầu nhầm chỗ
này, đã loại bỏ sau khi đối chiếu 2 nguồn khác đồng thuận + có code xác nhận).
"""
from __future__ import annotations

import math
from statistics import NormalDist, mean, pstdev
from typing import Any, Mapping, Sequence

_NORMAL = NormalDist()
EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(daily_returns: Sequence[float]) -> float:
    """SR theo ĐÚNG chu kỳ ngày (không annualize) — DSR chỉ cần SR/T/skew/
    kurtosis nhất quán CÙNG 1 chu kỳ, annualize chỉ là hệ số nhân không đổi
    kết luận DSR (Φ của 1 tỷ số, nhân chung tử+mẫu không đổi giá trị)."""
    if len(daily_returns) < 2:
        return 0.0
    sigma = pstdev(daily_returns)
    return mean(daily_returns) / sigma if sigma else 0.0


def _standardized_moment(values: Sequence[float], order: int, avg: float, sigma: float) -> float:
    if sigma == 0 or not values:
        return 0.0
    return sum((v - avg) ** order for v in values) / (len(values) * sigma ** order)


def skewness(daily_returns: Sequence[float]) -> float:
    avg = mean(daily_returns)
    sigma = pstdev(daily_returns)
    return _standardized_moment(daily_returns, 3, avg, sigma)


def kurtosis(daily_returns: Sequence[float]) -> float:
    """Kurtosis Pearson — KHÔNG trừ 3 (phân phối chuẩn ra đúng 3.0). Công thức
    PSR gốc dùng γ4 = kurtosis (không phải excess kurtosis) — nhầm 2 quy ước
    này sẽ lệch cả số hạng (γ4-1)/4 trong mẫu số DSR."""
    avg = mean(daily_returns)
    sigma = pstdev(daily_returns)
    return _standardized_moment(daily_returns, 4, avg, sigma)


def expected_max_sharpe(sharpe_values: Sequence[float]) -> float:
    """SR0 — ngưỡng "mức Sharpe kỳ vọng lớn nhất do THUẦN NHIỄU" khi đã thử
    N tổ hợp độc lập, không tổ hợp nào có skill thật (Bailey & López de Prado
    2014)."""
    n = len(sharpe_values)
    if n < 2:
        return 0.0
    sigma_sr = pstdev(sharpe_values)
    if sigma_sr == 0:
        return 0.0
    e_max_z = (
        (1 - EULER_MASCHERONI) * _NORMAL.inv_cdf(1 - 1 / n)
        + EULER_MASCHERONI * _NORMAL.inv_cdf(1 - 1 / (n * math.e))
    )
    return sigma_sr * e_max_z


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float,
    sample_length: int,
    skew: float,
    kurt: float,
) -> float:
    """DSR = xác suất Sharpe THẬT > `benchmark_sharpe` (thường = `SR0` ở trên),
    sau khi hiệu chỉnh skewness/kurtosis của chuỗi return. `observed_sharpe`
    đi vào CẢ tử số lẫn mẫu số (xem docstring module) — chỉ `benchmark_sharpe`
    dùng làm ngưỡng trừ ở tử số."""
    if sample_length < 2:
        return 0.0
    denom_inner = 1 - skew * observed_sharpe + ((kurt - 1) / 4) * observed_sharpe ** 2
    if denom_inner <= 0:
        return 0.0
    numerator = (observed_sharpe - benchmark_sharpe) * math.sqrt(sample_length - 1)
    return _NORMAL.cdf(numerator / math.sqrt(denom_inner))


def run(trials: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    """N trial (mỗi trial = 1 chuỗi %lời/lỗ theo ngày, key = nhãn trial) ->
    Sharpe mỗi trial -> chọn winner (SR cao nhất) -> DSR của đúng winner đó,
    hiệu chỉnh cho việc đã thử N=len(trials) trial."""
    if len(trials) < 2:
        raise ValueError("DSR cần ít nhất 2 trial để hiệu chỉnh multiple-testing có ý nghĩa")
    labels = list(trials)
    sharpes = {label: sharpe_ratio(trials[label]) for label in labels}
    winner = max(labels, key=lambda label: sharpes[label])
    winner_returns = trials[winner]
    sr0 = expected_max_sharpe(list(sharpes.values()))
    skew = skewness(winner_returns)
    kurt = kurtosis(winner_returns)
    dsr = deflated_sharpe_ratio(
        sharpes[winner],
        benchmark_sharpe=sr0,
        sample_length=len(winner_returns),
        skew=skew,
        kurt=kurt,
    )
    return {
        "schema": "bo-research-dsr/v1",
        "n_trials": len(trials),
        "winner_label": winner,
        "winner_sharpe": sharpes[winner],
        "benchmark_sharpe_e_max": sr0,
        "sample_length": len(winner_returns),
        "skewness": skew,
        "kurtosis": kurt,
        "dsr": dsr,
    }
