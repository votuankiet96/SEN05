"""
Khối xác nhận xu hướng T-STAT — một phương pháp trong trend_methods/, cùng hợp đồng với knn.py / zigzag.py.

Khối này chỉ làm MỘT việc: nhận nến của khung lớn (khung tham chiếu) rồi trả về hướng xu hướng của từng nến đó:
+1 xu hướng tăng có ý nghĩa thống kê (chỉ BUY), -1 giảm có ý nghĩa thống kê (chỉ SELL), 0 chưa có ý nghĩa hoặc chưa đủ
dữ liệu. Nó không biết chiến lược nào đang dùng nó; không lọc tín hiệu (việc của rules/trend_tf.py); không ghép với nến
vào lệnh (việc của indicator.merge_trend_reference); không đọc config — tham số kỹ thuật cố định ngay trong file này
(ngoại lệ có chủ đích, CLAUDE.md mục 2). Khung dùng và cách lọc (khung tham chiếu tra trend_tf_by_symbol như knn/zigzag,
không dời tín hiệu) là chính sách ở rules/trend_tf.POLICY, KHÔNG khai báo trong khối.

Nguồn ý tưởng: quy tắc TREND của Baltas & Kosowski (2015), "Demystifying Time-Series Momentum Strategies", §4.2 (đã đọc lại
bản gốc 2026-10-26; xem research_notes/topic_2026_trend-filter-candidates.md): hồi quy OLS log-giá theo thời gian trên cửa
sổ gần nhất, lấy t-stat Newey–West của độ dốc, +1 nếu t > +2, -1 nếu t < -2, 0 nếu không. Bài báo dùng nến NGÀY, cửa sổ 12
tháng, và không nêu số lag Newey–West; việc chuyển sang nến khung lớn của hệ này (N nến) là của riêng hệ.

Hai chế độ (tham số TSTAT_MODE), cùng một cửa sổ N nến kết thúc ĐÚNG ở nến đang xét (nhân quả) và cùng ngưỡng:
    "slope"  (đúng nguồn): t của độ dốc β trong  log(close_i) = a + β·i + e_i,  i = 0..N-1 (N nến cuối).
    "return" : t của lợi suất log trung bình trên N nến (= lợi suất N nến chia độ lệch chuẩn tích luỹ: "tín hiệu / nhiễu").
Sai số chuẩn của cả hai là Newey–West (hạt nhân Bartlett, không hiệu chỉnh cỡ mẫu nhỏ), độ trễ mặc định
floor(4·(N/100)^(2/9)) (quy tắc Newey–West 1994).

Điều cần biết (đo 2026-10-26, mô phỏng 4.000 bước ngẫu nhiên KHÔNG có xu hướng cho mỗi N; research_notes):
    - t của độ dốc log-giá là "hồi quy giả" trên chuỗi I(1): vượt ±2 ở 67–84% cửa sổ dù đã Newey–West, nên ngưỡng ±2 của
      chế độ "slope" là NGƯỠNG KINH NGHIỆM, không phải kiểm định 5%. Chính bài báo gốc cũng chỉ thấy TREND = 0 ở ~9–18% số
      hợp đồng: lợi ích "ít giao dịch" của nó đến từ độ DÍNH của tín hiệu, không phải từ việc đứng ngoài nhiều.
    - Chế độ "return" là kiểm định đúng cỡ (~5–7% cửa sổ vượt ±2 trên bước ngẫu nhiên), nên vùng trung tính thật sự lớn.
    - Hồi quy xu hướng, MA crossover, TSMOM... xấp xỉ cùng một họ bộ lọc tuyến tính (Levine & Pedersen 2016): hướng của khối
      sẽ trùng nhiều với MA/MACD trong chiến lược; phần khác biệt chính là vùng trung tính và độ dính.
    - Đo trên dữ liệu thật (t009: 5 symbol US30/GOLD/BTCUSD/EURUSD/DE40, nến H1/H2/H4 từ 2022; tín hiệu từ 2024-01-01, khung trend
      tra bảng như knn/zigzag): bộ mặc định slope / N = 100 cho hướng trung tính 23–25% số nến, đổi hướng ~51 / 25 / 12 lần mỗi năm
      ở H1 / H2 / H4 (breakout 40 / 21 / 11, zigzag 23 / 11 / 5, knn 646 / 326 / 164), đồng thuận 54–56% với knn và 76–79% với zigzag,
      giữ lại 22–38% tín hiệu raw (combo, ma_cross, macd_break; knn 20–94%, zigzag 31–50%). N = 50 đổi hướng gấp đôi, N = 200 chậm
      bằng zigzag và giữ lại ít tín hiệu macd_break (11–18%). Chế độ "return" trên dữ liệu thật: 94–95% nến TRUNG TÍNH ở mọi N 50–200
      (đúng với hiệu chuẩn ~5%: giá khung H1–H4 hầu như không có xu hướng có ý nghĩa thống kê) nên chỉ giữ lại 0–3% tín hiệu — là bộ
      kiểm định, không dùng làm bộ lọc mặc định.
    - CHƯA có bằng chứng intraday và CHƯA đánh giá lãi/lỗi (cần backtest BO20 qua một nguồn mới).

Các cột thêm vào (xem OUTPUT_COLUMNS):
    trend_tstat_t:     t-stat của chế độ đang chọn (NaN khi chưa đủ nến hoặc cửa sổ có giá không hợp lệ; ±inf nếu giá khớp đường thẳng tuyệt đối).
    trend_tstat_fit:   giá khớp OLS (đường hồi quy log-giá, đổi lại thành giá) tại nến cuối cửa sổ — chỉ để VẼ trên chart (đường
                       LSMA); cùng một cách tính ở cả hai chế độ.
    trend_bias_tstat:  hướng của khối này (+1/-1/0).

Phụ thuộc: chỉ numpy/pandas (không import gì trong src/).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

NAME = "tstat"
BIAS_COLUMN = f"trend_bias_{NAME}"
OUTPUT_COLUMNS: tuple[str, ...] = ("trend_tstat_t", "trend_tstat_fit")
# Đường vẽ trên chart khung lớn: colored_by_bias=True thì tô từng điểm theo hướng của CHÍNH khối này.
OVERLAYS: tuple[dict[str, Any], ...] = (
    {"column": "trend_tstat_fit", "label": "T-stat Trend Fit", "color": "#22c55e", "colored_by_bias": True},
)

TSTAT_MODES: tuple[str, ...] = ("slope", "return")
_MODE_ALIASES: dict[str, str] = {"1": "slope", "slope": "slope", "2": "return", "return": "return"}

TSTAT_TREND_DEFAULT_PARAMS: dict[str, Any] = {
    "TSTAT_MODE": "slope",
    "TSTAT_LOOKBACK": 100,
    "TSTAT_THRESHOLD": 2.0,
    "TSTAT_NW_LAG": None,  # None = quy tắc Newey–West: default_nw_lag(TSTAT_LOOKBACK)
}

# Số phần tử tối đa của một ma trận trung gian (cửa sổ x N): chia dải nến thành từng khối để bộ nhớ không phụ thuộc độ dài chuỗi.
_CHUNK_ELEMENTS = 2_000_000


def _positive_int(value: object, key: str) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive integer.")
    return parsed


def _non_negative_int(value: object, key: str) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative integer.") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be a non-negative integer.")
    return parsed


def _positive_float(value: object, key: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive number.") from exc
    if not np.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{key} must be a positive number.")
    return parsed


def _parse_mode(value: object) -> str:
    raw = str(value if value is not None else TSTAT_TREND_DEFAULT_PARAMS["TSTAT_MODE"]).strip().lower()
    if raw not in _MODE_ALIASES:
        raise ValueError(f"TSTAT_MODE must be one of {', '.join(TSTAT_MODES)} (or 1/2), got '{value}'.")
    return _MODE_ALIASES[raw]


def default_nw_lag(lookback: int) -> int:
    """Độ trễ Newey–West mặc định theo quy tắc của Newey & West (1994): floor(4·(N/100)^(2/9))."""
    return int(np.floor(4.0 * (lookback / 100.0) ** (2.0 / 9.0)))


def tstat_trend_indicator_params(params: dict | None) -> dict[str, Any]:
    """
    Ghép và kiểm tra bộ tham số kỹ thuật nội bộ cho khối T-STAT.

    TSTAT_MODE ("slope"/"1" hoặc "return"/"2"), TSTAT_LOOKBACK (số nến N, >= 5), TSTAT_THRESHOLD (ngưỡng |t|, > 0) và
    TSTAT_NW_LAG (độ trễ Newey–West, >= 0 và < N; None = default_nw_lag(N)).
    """
    raw = {**TSTAT_TREND_DEFAULT_PARAMS, **(params or {})}
    lookback = _positive_int(raw["TSTAT_LOOKBACK"], "TSTAT_LOOKBACK")
    if lookback < 5:
        raise ValueError("TSTAT_LOOKBACK must be at least 5.")
    lag_raw = raw["TSTAT_NW_LAG"]
    lag = default_nw_lag(lookback) if lag_raw is None else _non_negative_int(lag_raw, "TSTAT_NW_LAG")
    if lag >= lookback:
        raise ValueError("TSTAT_NW_LAG must be smaller than TSTAT_LOOKBACK.")
    return {
        "TSTAT_MODE": _parse_mode(raw["TSTAT_MODE"]),
        "TSTAT_LOOKBACK": lookback,
        "TSTAT_THRESHOLD": _positive_float(raw["TSTAT_THRESHOLD"], "TSTAT_THRESHOLD"),
        "TSTAT_NW_LAG": lag,
    }


def _bartlett_sum(score: np.ndarray, lag: int) -> np.ndarray:
    """Tổng Newey–West theo từng hàng: Σ u_t² + 2·Σ_{l=1..lag} (1 - l/(lag+1))·Σ_t u_t·u_{t-l} (hạt nhân Bartlett)."""
    total = np.einsum("ij,ij->i", score, score)
    for step in range(1, lag + 1):
        weight = 2.0 * (1.0 - step / (lag + 1.0))
        total = total + weight * np.einsum("ij,ij->i", score[:, step:], score[:, :-step])
    return total


def calc_trend_tstat(
    close: pd.Series | np.ndarray,
    *,
    mode: str,
    lookback: int,
    nw_lag: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Tính t-stat và giá khớp OLS cho từng nến; cửa sổ của nến t gồm các nến t-N+1..t (nhân quả).

    Trả về (t_stat, fit), cùng độ dài với `close`. Nến chưa đủ cửa sổ, hoặc cửa sổ có giá không dương / NaN: NaN.
    Chế độ "return" cần N lợi suất tức N+1 nến đóng cửa nên nến hợp lệ đầu tiên là nến thứ N+1; chế độ "slope" là nến thứ N.
    """
    price = np.asarray(close, dtype=float)
    size = price.size
    t_stat = np.full(size, np.nan)
    fit = np.full(size, np.nan)
    if size < lookback:
        return t_stat, fit
    with np.errstate(invalid="ignore", divide="ignore"):
        log_price = np.log(np.where(price > 0.0, price, np.nan))
    x_centered = np.arange(lookback, dtype=float) - (lookback - 1) / 2.0
    sxx = float(x_centered @ x_centered)
    windows_y = sliding_window_view(log_price, lookback)  # hàng i kết thúc ở nến i + N - 1
    windows_r = None
    if mode == "return":
        log_return = np.full(size, np.nan)
        log_return[1:] = np.diff(log_price)
        windows_r = sliding_window_view(log_return, lookback)
    chunk = max(1, _CHUNK_ELEMENTS // lookback)
    with np.errstate(invalid="ignore", divide="ignore"):
        for start in range(0, windows_y.shape[0], chunk):
            stop = min(start + chunk, windows_y.shape[0])
            y = windows_y[start:stop]
            y_mean = y.mean(axis=1)
            y_centered = y - y_mean[:, None]
            beta = (y_centered @ x_centered) / sxx
            target = slice(start + lookback - 1, stop + lookback - 1)
            fit[target] = np.exp(y_mean + beta * x_centered[-1])
            if windows_r is None:
                residual = y_centered - beta[:, None] * x_centered
                variance = _bartlett_sum(residual * x_centered, nw_lag) / (sxx * sxx)
                numerator = beta
            else:
                r = windows_r[start:stop]
                numerator = r.mean(axis=1)
                variance = _bartlett_sum(r - numerator[:, None], nw_lag) / float(lookback * lookback)
            t_stat[target] = numerator / np.sqrt(np.maximum(variance, 0.0))
    return t_stat, fit


def build(trend_bars: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Dựng các cột T-STAT cho frame nến khung lớn.

    Các cột được thêm (xem OUTPUT_COLUMNS):
        trend_tstat_t:     t-stat của chế độ đang chọn.
        trend_tstat_fit:   giá khớp OLS tại nến cuối cửa sổ (chỉ để vẽ).
        trend_bias_tstat:  +1 nếu t > +ngưỡng, -1 nếu t < -ngưỡng, 0 nếu không (kể cả NaN).
    """
    technical = tstat_trend_indicator_params(params)
    out = trend_bars.copy()
    t_stat, fit = calc_trend_tstat(
        out["close"],
        mode=technical["TSTAT_MODE"],
        lookback=technical["TSTAT_LOOKBACK"],
        nw_lag=technical["TSTAT_NW_LAG"],
    )
    out["trend_tstat_t"] = t_stat
    out["trend_tstat_fit"] = fit

    threshold = technical["TSTAT_THRESHOLD"]
    bias = np.zeros(len(out), dtype=np.int64)
    bias[t_stat > threshold] = 1  # so sánh với NaN luôn False: chưa đủ dữ liệu thì trung tính
    bias[t_stat < -threshold] = -1
    out[BIAS_COLUMN] = bias
    return out
