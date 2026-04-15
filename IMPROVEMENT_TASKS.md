# SEN05 Auto Trading — Kế hoạch cải tiến

> Tạo ngày: 2026-04-11  
> Tổng: 4 Phase, 13 Tasks  
> Ưu tiên: Phase 1 > 2 > 3 > 4  

---

## Phase 1: Fix Backtest Accuracy (P0 — Bắt buộc sửa trước khi live trading)

### Task 1.1: Fix Look-ahead Bias — Pending Order phải fill T+1

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/execution.py`  
**Mức độ:** CRITICAL

**Mô tả vấn đề:**  
Hiện tại, pending order fill trên **cùng bar** khi price touch entry level (line 114-118 trong `backtest_symbol()`, line 480-483 trong `backtest_fast()`). Trong thực tế, signal sinh ra ở bar close, order chỉ có thể fill từ bar **tiếp theo**. Điều này khiến backtest lạc quan hơn thực tế vì:
- Fill tại giá lý tưởng (high/low) thay vì giá open thực tế
- Bỏ qua overnight gap có thể skip entry level

**Yêu cầu thay đổi:**
1. Trong `backtest_symbol()` (line 108-142):
   - Khi pending order match điều kiện fill (high >= entry cho LONG, low <= entry cho SHORT), **KHÔNG fill ngay**
   - Đánh dấu `pending['triggered'] = True` và chờ bar tiếp theo (i+1)
   - Fill tại `next_bar['open'] + direction * cost` (cost = slippage + spread)
   - Nếu next bar open gap qua cả TP → fill tại open price (worst case slippage)
2. Áp dụng tương tự trong `backtest_fast()` (line 474-499)
3. Thêm xử lý edge case: nếu triggered ở bar cuối cùng của data → bỏ qua trade (không fill)

**Verification:**
- Chạy backtest US30 H4 trước/sau fix
- Expected: ít trades hơn, PnL thấp hơn (realistic hơn)
- So sánh average entry price: sau fix phải gần open price hơn, không còn dùng high/low

**Prompt gợi ý cho session mới:**
> Hãy fix look-ahead bias trong file `strategies/combo/core/execution.py`. Hiện tại pending order fill trên cùng bar tại line 114-118 (backtest_symbol) và line 480-483 (backtest_fast). Cần thay đổi thành T+1 fill: order chỉ fill tại open price của bar tiếp theo + slippage/spread. Xem chi tiết Task 1.1 trong file IMPROVEMENT_TASKS.md.

---

### Task 1.2: Fix MA Trailing phá Breakeven

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/execution.py`  
**Mức độ:** CRITICAL

**Mô tả vấn đề:**  
Sau khi partial TP hit, SL được set về entry price (breakeven) tại line 172. Tuy nhiên, MA trailing logic tại line 188-193 có thể kéo SL **xuống dưới entry** cho LONG (hoặc lên trên entry cho SHORT). Ví dụ:
- LONG entry 35000, SL ban đầu 34700
- Partial TP hit → SL set về 35000 (breakeven)
- Vài bar sau, MA drop xuống 34500 → SL bị kéo theo xuống 34500
- Position "đã bảo vệ" vẫn lỗ 500 pts → vi phạm logic risk management

**Yêu cầu thay đổi:**
1. Tại line 172 (sau khi set breakeven), lưu thêm: `position['breakeven_price'] = entry`
2. Tại line 190-191 (LONG trailing), thêm guard:
   ```python
   be = position.get('breakeven_price')
   if be is not None:
       new_sl = max(new_sl, be)  # Không cho SL xuống dưới breakeven
   if new_sl > position['sl']:
       position['sl'] = new_sl
   ```
3. Tại line 192-193 (SHORT trailing), thêm guard tương tự:
   ```python
   be = position.get('breakeven_price')
   if be is not None:
       new_sl = min(new_sl, be)  # Không cho SL lên trên breakeven
   if new_sl < position['sl']:
       position['sl'] = new_sl
   ```

**Verification:**
- Tạo unit test: LONG entry 35000, partial TP hit, MA drops to 34500 → assert SL >= 35000
- Tạo unit test: SHORT entry 1.2000, partial TP hit, MA rises to 1.2100 → assert SL <= 1.2000

**Prompt gợi ý cho session mới:**
> Hãy fix MA trailing breakeven bug trong file `strategies/combo/core/execution.py`. Sau partial TP (line 172), SL set về breakeven nhưng MA trailing (line 188-193) có thể kéo SL qua breakeven. Cần thêm guard để SL không bao giờ vượt qua breakeven price sau khi partial TP đã hit. Xem chi tiết Task 1.2 trong file IMPROVEMENT_TASKS.md.

---

### Task 1.3: Fix Sharpe/Sortino Inflation

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/metrics.py`  
**Mức độ:** CRITICAL

**Mô tả vấn đề:**  
Sharpe ratio được annualize bằng `mean/std * sqrt(1512)` (line 75-76) bất kể sample size. Với H4 data chỉ 3 tháng (~378 bars), một Sharpe thực 0.5 sẽ hiển thị là ~19.4 — hoàn toàn vô nghĩa. Sortino (line 79-80) cũng bị inflate tương tự. Minimum sample check chỉ là `len(rets) > 1` (2 bars!).

**Yêu cầu thay đổi:**
1. Thêm constant: `MIN_BARS_RATIO = 0.25` (ít nhất 1/4 năm data để annualize)
2. Tại line 75-76, thay đổi Sharpe calculation:
   ```python
   bpy = _bars_per_year(tf_code)
   rets = eq_ts.pct_change().dropna()
   min_bars = int(bpy * MIN_BARS_RATIO)
   if len(rets) > 1 and rets.std() > 0:
       if len(rets) >= min_bars:
           sharpe = rets.mean() / rets.std() * np.sqrt(bpy)  # Full annualization
       else:
           sharpe = rets.mean() / rets.std() * np.sqrt(len(rets))  # Sample-period only
   else:
       sharpe = 0.0
   ```
3. Áp dụng tương tự cho Sortino (line 79-80)
4. Thêm field trong output dict: `'sharpe_type': 'annualized'` hoặc `'sample_adjusted'`

**Verification:**
- Test: 100 bars random returns → Sharpe phải < 5.0 (trước fix có thể > 15)
- Test: 1500+ bars → Sharpe vẫn dùng full annualization (behavior không đổi)
- Test: constant returns → Sharpe = 0 (std = 0)

**Prompt gợi ý cho session mới:**
> Hãy fix Sharpe và Sortino inflation trong file `strategies/combo/core/metrics.py`. Hiện tại annualize bằng sqrt(1512) bất kể sample size (line 73-80). Cần thêm minimum sample check: nếu data < 1/4 năm thì dùng sqrt(n_bars) thay vì sqrt(bpy). Xem chi tiết Task 1.3 trong file IMPROVEMENT_TASKS.md.

---

### Task 1.4: Fix Monte Carlo — Block Bootstrap

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/monte_carlo.py`  
**Mức độ:** HIGH

**Mô tả vấn đề:**  
Monte Carlo simulation dùng `np.random.permutation(arr)` (line 54) — simple random shuffle phá hủy hoàn toàn serial correlation trong trade sequence. Trading returns KHÔNG i.i.d.: trades cluster theo regime (bull/bear), volatility state, v.v. Kết quả là confidence interval quá hẹp (overconfident).

**Yêu cầu thay đổi:**
1. Thêm helper function `_block_bootstrap()`:
   ```python
   def _block_bootstrap(arr: np.ndarray, block_size: int | None = None) -> np.ndarray:
       """Stationary block bootstrap preserving serial correlation."""
       n = len(arr)
       if block_size is None:
           block_size = max(3, int(np.sqrt(n)))
       result = []
       while len(result) < n:
           start = np.random.randint(0, n)
           length = min(np.random.geometric(1 / block_size), n - len(result))
           for j in range(length):
               result.append(arr[(start + j) % n])
       return np.array(result[:n])
   ```
2. Tại line 54, thay: `shuffled = _block_bootstrap(arr)` thay vì `np.random.permutation(arr)`
3. Thêm parameter `block_size` vào `run_monte_carlo()` signature (default None = auto)

**Verification:**
- So sánh CI width (sharpe_ci_high - sharpe_ci_low): block bootstrap phải cho CI rộng hơn
- So sánh prob_exceed_dd: block bootstrap phải cho probability cao hơn (conservative hơn)
- Test edge case: 5 trades → block_size = 3 (sqrt(5) rounded), function không crash

**Prompt gợi ý cho session mới:**
> Hãy thay thế Monte Carlo permutation bằng block bootstrap trong file `strategies/combo/core/monte_carlo.py`. Hiện tại dùng np.random.permutation (line 54) phá hủy serial correlation. Cần implement stationary block bootstrap với geometric block length. Xem chi tiết Task 1.4 trong file IMPROVEMENT_TASKS.md.

---

## Phase 2: Walk-Forward & Overfitting Protection

### Task 2.1: Tăng IS Window & Thêm Buffer giữa IS/OOS

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/walk_forward.py`  
**Mức độ:** HIGH

**Mô tả vấn đề:**  
IS window chỉ 5000 bars (~140 trading days, ~7 tháng) quá ngắn cho H4 strategy. OOS 1250 bars (~35 ngày) cũng quá ngắn để statistically significant. IS/OOS boundary không có buffer → parameter optimization artifacts lan sang OOS.

**Yêu cầu thay đổi:**
1. Tại line 160-163, thay đổi defaults:
   - `is_bars`: 5000 → **8000** (~220 ngày, gần 1 năm)
   - `oos_bars`: 1250 → **2000** (~55 ngày, ~2.5 tháng)
   - Thêm parameter: `buffer_bars: int = 500`
2. Tại line 228-230, thêm buffer gap:
   ```python
   oos_start = is_bars + step * step_bars + buffer_bars
   oos_end   = oos_start + oos_bars
   if oos_end > n_bars:
       break
   ```
3. Log buffer info: `logger.info("IS/OOS buffer: %d bars skipped", buffer_bars)`

**Verification:**
- Chạy walk-forward US30 H4: confirm OOS windows bắt đầu sau buffer
- Expect: fewer OOS windows (do buffer + larger windows) nhưng kết quả robust hơn

**Prompt gợi ý cho session mới:**
> Hãy cải thiện walk-forward parameters trong file `strategies/combo/core/walk_forward.py`. Tăng IS window từ 5000 lên 8000 bars, OOS từ 1250 lên 2000, và thêm buffer 500 bars giữa IS/OOS boundary (line 160-163, 228-230). Xem chi tiết Task 2.1 trong file IMPROVEMENT_TASKS.md.

---

### Task 2.2: Giảm Grid Search Combinations

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/walk_forward.py`  
**Mức độ:** HIGH

**Mô tả vấn đề:**  
Grid search hiện tại: 3 values × 4 parameters = 81 combinations per window (line 209-214). Trên IS window ngắn (5000 bars), 81 combos dễ tìm được spurious correlations. `threshold_ratio = 0.5` cũng quá lỏng.

**Yêu cầu thay đổi:**
1. Tại line 209-214, giảm grid từ 3 → **2 values** per parameter:
   ```python
   return {
       'ktp':      sorted({round(ktp0, 2), round(ktp0 * 1.1, 2)}),
       'x':        sorted({round(x0, 2), round(x0 + 2.0, 2)}),
       'trailing': sorted({round(tr0, 2), round(tr0 * 1.25, 2)}),
       'ma_period': sorted({ma0, ma0 + 5}),
   }
   ```
   → 2⁴ = **16 combinations** (giảm 5x từ 81)
2. Tại line 116, tăng `threshold_ratio`: 0.5 → **0.7**
3. Tại line 138, tăng `stable_ratio` threshold: 0.6 → **0.7**

**Verification:**
- Log grid size: confirm 16 combos per window
- Expect: best params có plateau rộng hơn (robust hơn)

**Prompt gợi ý cho session mới:**
> Hãy giảm overfitting risk trong walk-forward grid search tại `strategies/combo/core/walk_forward.py`. Giảm grid từ 3 values xuống 2 values per parameter (line 209-214), tăng threshold_ratio từ 0.5 lên 0.7 (line 116), tăng stable_ratio từ 0.6 lên 0.7 (line 138). Xem chi tiết Task 2.2 trong file IMPROVEMENT_TASKS.md.

---

### Task 2.3: Thêm Parameter Stability Penalty

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `strategies/combo/core/walk_forward.py`  
**Mức độ:** MEDIUM

**Mô tả vấn đề:**  
Hiện tại không có cách đo xem optimal parameters có nhảy lung tung giữa các OOS window không. Nếu kTP tối ưu nhảy từ 2.0 → 3.0 → 1.5 giữa 3 windows → strategy không robust.

**Yêu cầu thay đổi:**
1. Thêm function `_param_stability_score()`:
   ```python
   def _param_stability_score(window_params: list[dict]) -> float:
       """1.0 = stable (params giống nhau qua các window), 0.0 = unstable."""
       if len(window_params) < 2:
           return 1.0
       diffs = []
       for i in range(1, len(window_params)):
           for k in window_params[i]:
               prev = window_params[i-1].get(k, window_params[i][k])
               curr = window_params[i][k]
               if prev != 0:
                   diffs.append(abs(curr - prev) / abs(prev))
       return max(0.0, 1.0 - np.mean(diffs))
   ```
2. Gọi sau khi tất cả OOS windows hoàn tất (khoảng line 356-376)
3. Thêm vào final report dict: `'param_stability': stability_score`
4. Log warning nếu stability < 0.5: `logger.warning("Low parameter stability: %.2f — risk of overfitting", score)`

**Verification:**
- Test: 3 windows với cùng params → stability = 1.0
- Test: 3 windows với params khác nhau 50% → stability ≈ 0.5
- Walk-forward report có field `param_stability`

**Prompt gợi ý cho session mới:**
> Hãy thêm parameter stability scoring vào walk-forward trong file `strategies/combo/core/walk_forward.py`. Cần function đo mức ổn định của optimal params qua các OOS window (1.0 = stable, 0.0 = unstable). Include trong final report và warning nếu < 0.5. Xem chi tiết Task 2.3 trong file IMPROVEMENT_TASKS.md.

---

## Phase 3: Test Infrastructure

### Task 3.1: Setup Test Framework

**Trạng thái:** `[ ]` Chưa làm  
**Files mới:**
- `tests/__init__.py`
- `tests/conftest.py`
- `requirements-dev.txt`

**Mức độ:** HIGH

**Mô tả vấn đề:**  
Hiện tại project có ZERO test files. `pyproject.toml` đã config pytest (line 51-56) nhưng không có tests/ directory. Không có dev dependencies (pytest, pytest-cov).

**Yêu cầu thay đổi:**
1. Tạo `requirements-dev.txt`:
   ```
   -r requirements.txt
   pytest>=8.0
   pytest-cov>=5.0
   pytest-mock>=3.14
   ```
2. Tạo `tests/__init__.py` (empty)
3. Tạo `tests/conftest.py` với fixtures:
   ```python
   import pytest
   import pandas as pd
   import numpy as np

   @pytest.fixture
   def sample_ohlcv():
       """100-bar sample OHLCV data for testing."""
       np.random.seed(42)
       n = 100
       close = 35000 + np.cumsum(np.random.randn(n) * 10)
       return pd.DataFrame({
           'open':   close + np.random.randn(n) * 5,
           'high':   close + abs(np.random.randn(n) * 15),
           'low':    close - abs(np.random.randn(n) * 15),
           'close':  close,
           'volume': np.random.randint(1000, 10000, n).astype(float),
       })

   @pytest.fixture
   def sample_equity_series():
       """Equity curve for metrics testing."""
       np.random.seed(42)
       returns = np.random.randn(500) * 0.001
       equity = 10000 * np.cumprod(1 + returns)
       return pd.Series(equity)

   @pytest.fixture
   def indicator_params():
       """Default indicator parameters."""
       return {
           'MA_PERIOD': 20,
           'MACD_FAST': 5,
           'MACD_SLOW': 25,
           'MACD_SIGNAL': 5,
           'ATR_PERIOD': 5,
           'KTP': 2.3,
           'MIN_RR': 1.25,
       }
   ```

**Verification:**
- `pip install -r requirements-dev.txt` thành công
- `pytest tests/ --co` (collect only) — shows 0 tests, no errors

**Prompt gợi ý cho session mới:**
> Hãy setup test framework cho project SEN05. Tạo tests/__init__.py, tests/conftest.py với fixtures (sample OHLCV, equity series, indicator params), và requirements-dev.txt với pytest dependencies. pyproject.toml đã có pytest config tại line 51-56. Xem chi tiết Task 3.1 trong file IMPROVEMENT_TASKS.md.

---

### Task 3.2: Unit Tests cho Indicators

**Trạng thái:** `[ ]` Chưa làm  
**File mới:** `tests/test_indicators.py`  
**File source:** `modules/indicators.py` (function `add_indicators()` line 95-137)  
**Mức độ:** HIGH

**Yêu cầu:**
Viết test cases cho:
1. `test_calc_sma_known_values()` — SMA(5) trên [1,2,3,4,5,6,7] → verify giá trị cụ thể
2. `test_calc_ema_convergence()` — EMA trên constant series = constant
3. `test_calc_rsi_boundaries()` — RSI always ∈ [0, 100]
4. `test_calc_rsi_overbought()` — Liên tục tăng → RSI gần 100
5. `test_calc_atr_positive()` — ATR > 0 cho non-constant series
6. `test_calc_macd_zero_for_constant()` — MACD histogram ≈ 0 cho constant price
7. `test_add_indicators_has_required_columns()` — Output có ['ma', 'atr', 'macd_h']
8. `test_add_indicators_no_input_mutation()` — Input DataFrame không bị thay đổi
9. `test_add_indicators_nan_warmup()` — Đầu series có NaN do warmup period

**Prompt gợi ý cho session mới:**
> Hãy viết unit tests cho indicators trong file tests/test_indicators.py. Test các function trong modules/indicators.py: SMA, EMA, RSI, ATR, MACD, và add_indicators(). Fixtures đã có trong tests/conftest.py. Xem chi tiết Task 3.2 trong file IMPROVEMENT_TASKS.md.

---

### Task 3.3: Unit Tests cho Execution Engine

**Trạng thái:** `[ ]` Chưa làm  
**File mới:** `tests/test_execution.py`  
**File source:** `strategies/combo/core/execution.py`  
**Mức độ:** HIGH  
**Dependency:** Task 1.1 và 1.2 phải hoàn thành trước

**Yêu cầu:**
Viết test cases cho:
1. `test_pending_fill_t_plus_1()` — Order fill tại next bar open, không phải current bar
2. `test_breakeven_guard_long()` — LONG: sau partial TP, SL >= entry price
3. `test_breakeven_guard_short()` — SHORT: sau partial TP, SL <= entry price
4. `test_partial_tp_pnl_calculation()` — PnL = 50% * fixed_TP_profit + 50% * trailing_profit
5. `test_daily_stop_triggers()` — Khi daily loss > 5%, không mở trade mới
6. `test_max_drawdown_stop()` — Khi equity < init * (1 - max_dd), dừng
7. `test_trailing_sl_only_improves()` — LONG SL chỉ đi lên, SHORT SL chỉ đi xuống
8. `test_pending_order_ttl_expires()` — Pending order hết hạn sau 3 bars

**Prompt gợi ý cho session mới:**
> Hãy viết unit tests cho execution engine trong file tests/test_execution.py. Test pending order T+1 fill, breakeven guard, partial TP, daily stop, max DD, trailing SL. Source: strategies/combo/core/execution.py. Xem chi tiết Task 3.3 trong file IMPROVEMENT_TASKS.md.

---

### Task 3.4: Unit Tests cho Metrics

**Trạng thái:** `[ ]` Chưa làm  
**File mới:** `tests/test_metrics.py`  
**File source:** `strategies/combo/core/metrics.py`  
**Mức độ:** MEDIUM  
**Dependency:** Task 1.3 phải hoàn thành trước

**Yêu cầu:**
Viết test cases cho:
1. `test_sharpe_short_sample_not_inflated()` — 100 bars → Sharpe < 5.0
2. `test_sharpe_long_sample_annualized()` — 1500+ bars → dùng full annualization
3. `test_sharpe_constant_returns_zero()` — Constant equity → Sharpe = 0
4. `test_sharpe_known_value()` — Known return series → verify exact Sharpe
5. `test_sortino_ignores_upside()` — Chỉ negative returns trong denominator
6. `test_profit_factor_no_losses()` — Không có losing trade → PF = inf hoặc large number
7. `test_profit_factor_known_value()` — Known wins/losses → verify PF
8. `test_max_drawdown_known_curve()` — [100, 110, 90, 95] → DD = (110-90)/110

**Prompt gợi ý cho session mới:**
> Hãy viết unit tests cho metrics trong file tests/test_metrics.py. Test Sharpe (short sample guard, annualization), Sortino, Profit Factor, Max Drawdown. Source: strategies/combo/core/metrics.py. Xem chi tiết Task 3.4 trong file IMPROVEMENT_TASKS.md.

---

## Phase 4: Database & Infrastructure Optimization

### Task 4.1: Fix Fact_OHLCV Clustering Index

**Trạng thái:** `[ ]` Chưa làm  
**File mới:** `data_provider/00_sql/06_optimize_indexes.sql`  
**File liên quan:** `data_provider/00_sql/02_core_tables.sql` (line 271, 287-289)  
**Mức độ:** MEDIUM

**Mô tả vấn đề:**  
`Fact_OHLCV` cluster trên `FactID` (surrogate key, insertion order) — line 271. Nhưng mọi query đều filter theo `(SymbolID, TimeframeID, BarTime)`. Nonclustered covering index đã có (line 287-289) nhưng vẫn thua clustered index về performance cho range scans.

**Yêu cầu thay đổi:**
Tạo migration script `06_optimize_indexes.sql`:
```sql
-- BACKUP TRƯỚC KHI CHẠY! Operation sẽ lock table.
-- Estimated time: vài phút cho 10M+ rows

BEGIN TRANSACTION;

-- 1. Drop existing clustered PK
ALTER TABLE DWH.Fact_OHLCV DROP CONSTRAINT PK_Fact_OHLCV;

-- 2. Add FactID as non-clustered unique (giữ surrogate key)
ALTER TABLE DWH.Fact_OHLCV ADD CONSTRAINT UQ_Fact_FactID UNIQUE NONCLUSTERED (FactID);

-- 3. Create new clustered index on query pattern
CREATE CLUSTERED INDEX CIX_Fact_Sym_TF_Time 
    ON DWH.Fact_OHLCV (SymbolID, TimeframeID, BarTime);

-- 4. Drop old nonclustered covering index (now redundant)
DROP INDEX IF EXISTS IX_Fact_Sym_TF_Time ON DWH.Fact_OHLCV;

COMMIT;
```

**LƯU Ý QUAN TRỌNG:**
- PHẢI backup database trước khi chạy
- Chạy ngoài giờ trading (table bị lock)
- Test trên test DB trước

**Verification:**
- Query `SELECT TOP 5000 ... WHERE SymbolID=10 AND TimeframeID=11 ORDER BY BarTime DESC` — measure execution time trước/sau
- Expected: 2-5x speedup cho range queries

**Prompt gợi ý cho session mới:**
> Hãy tạo SQL migration script data_provider/00_sql/06_optimize_indexes.sql để đổi Fact_OHLCV từ clustered trên FactID sang clustered trên (SymbolID, TimeframeID, BarTime). Xem chi tiết Task 4.1 trong file IMPROVEMENT_TASKS.md.

---

### Task 4.2: Thêm Connection Pooling

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `modules/db_connector.py` (line 48-68: `get_connection()`)  
**Mức độ:** MEDIUM

**Mô tả vấn đề:**  
Mỗi operation tạo connection mới qua `pyodbc.connect()`. Với 37 symbols × 15 TFs và dashboard refresh thường xuyên, overhead TCP connection setup đáng kể.

**Yêu cầu thay đổi:**
1. Thêm module-level pool (thread-safe):
   ```python
   import threading
   _pool_lock = threading.Lock()
   _pool: list[pyodbc.Connection] = []
   _POOL_MAX = 5
   ```
2. Sửa `get_connection()` để check pool trước:
   - Lấy connection từ pool nếu có
   - Validate bằng `conn.execute("SELECT 1")` trước khi return
   - Nếu pool rỗng hoặc connection stale → tạo mới (giữ retry logic hiện tại)
3. Thêm `release_connection(conn)`:
   - Trả connection về pool nếu chưa đầy
   - Close nếu pool đã max
4. Sửa tất cả functions dùng `get_connection()` để gọi `release_connection()` trong finally block thay vì `conn.close()`

**Verification:**
- Log pool hit/miss ratio
- Benchmark 100 consecutive queries: confirm connection reuse (no 100 TCP handshakes)

**Prompt gợi ý cho session mới:**
> Hãy thêm connection pooling vào modules/db_connector.py. Hiện tại mỗi operation tạo connection mới (line 48-68). Cần pool thread-safe (max 5), validate trước khi return, và release thay vì close. Xem chi tiết Task 4.2 trong file IMPROVEMENT_TASKS.md.

---

### Task 4.3: Pin tvdatafeed Dependency

**Trạng thái:** `[ ]` Chưa làm  
**File cần sửa:** `requirements.txt`  
**Mức độ:** LOW-MEDIUM

**Mô tả vấn đề:**  
`tvdatafeed` được install từ GitHub branch không có version tag:
```
tvdatafeed @ git+https://github.com/rongardF/tvdatafeed.git
```
Nếu maintainer push breaking change → toàn bộ data pipeline chết, không có fallback.

**Yêu cầu thay đổi:**
1. Xác định commit hash hiện tại đang dùng:
   ```bash
   pip show tvdatafeed | grep -i location
   # Kiểm tra git log trong site-packages
   ```
2. Pin vào commit hash:
   ```
   tvdatafeed @ git+https://github.com/rongardF/tvdatafeed.git@<COMMIT_HASH>
   ```
3. Thêm comment:
   ```
   # Pinned 2026-04-11. Review for updates quarterly.
   ```

**Verification:**
- `pip install -r requirements.txt` trong fresh venv → thành công
- `python -c "from tvDatafeed import TvDatafeed; print('OK')"` → OK

**Prompt gợi ý cho session mới:**
> Hãy pin tvdatafeed dependency trong requirements.txt vào commit hash cụ thể. Hiện tại install từ GitHub branch không có version tag — rủi ro breaking change. Xem chi tiết Task 4.3 trong file IMPROVEMENT_TASKS.md.

---

## Checklist tổng hợp

| # | Task | Phase | Mức độ | File chính | Trạng thái |
|---|------|-------|--------|-----------|------------|
| 1.1 | Fix look-ahead bias (T+1 fill) | P1 | CRITICAL | execution.py | `[ ]` |
| 1.2 | Fix MA trailing breakeven | P1 | CRITICAL | execution.py | `[ ]` |
| 1.3 | Fix Sharpe/Sortino inflation | P1 | CRITICAL | metrics.py | `[ ]` |
| 1.4 | Fix Monte Carlo bootstrap | P1 | HIGH | monte_carlo.py | `[ ]` |
| 2.1 | Tăng IS window + buffer | P2 | HIGH | walk_forward.py | `[ ]` |
| 2.2 | Giảm grid combinations | P2 | HIGH | walk_forward.py | `[ ]` |
| 2.3 | Parameter stability penalty | P2 | MEDIUM | walk_forward.py | `[ ]` |
| 3.1 | Setup test framework | P3 | HIGH | tests/conftest.py | `[ ]` |
| 3.2 | Test indicators | P3 | HIGH | tests/test_indicators.py | `[ ]` |
| 3.3 | Test execution (sau 1.1, 1.2) | P3 | HIGH | tests/test_execution.py | `[ ]` |
| 3.4 | Test metrics (sau 1.3) | P3 | MEDIUM | tests/test_metrics.py | `[ ]` |
| 4.1 | Fix DB clustering index | P4 | MEDIUM | 06_optimize_indexes.sql | `[ ]` |
| 4.2 | Connection pooling | P4 | MEDIUM | db_connector.py | `[ ]` |
| 4.3 | Pin tvdatafeed | P4 | LOW-MEDIUM | requirements.txt | `[ ]` |

---

## Lưu ý khi thực hiện

1. **Luôn chạy backtest so sánh trước/sau** mỗi thay đổi ở Phase 1 & 2
2. **Task 3.3 và 3.4 phụ thuộc vào Phase 1** — viết test dựa trên behavior đã fix
3. **Task 4.1 (DB index) phải backup trước** — không thể rollback nếu sai
4. **Sau Phase 1 hoàn tất**: chạy full backtest US30 H4 2020-2025, so sánh metrics cũ/mới
5. **Sau Phase 3 hoàn tất**: `pytest tests/ -v --cov=modules --cov=strategies` để xem coverage
