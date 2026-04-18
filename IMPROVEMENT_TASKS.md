# SEN05 Auto Trading — Kế hoạch cải tiến

> Cập nhật: 2026-04-16  
> Nguyên tắc: **Chỉ làm code thuần túy trước** — không động vào financial logic cho đến khi có quyết định từ owner.

---

## Phân loại nhiệm vụ

| Loại | Ý nghĩa | Làm khi nào |
| --- | --- | --- |
| ✅ CODE | Lỗi kỹ thuật thuần túy, không ảnh hưởng financial logic | Làm ngay |
| 🔬 FINANCIAL | Liên quan đến backtest accuracy, metrics, strategy params | Cần owner quyết định trước |

---

## Phase 1 — Code & Infrastructure (LÀM NGAY)

### Task 1.1: Setup Test Framework

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** HIGH  
**Files mới:** `tests/__init__.py`, `tests/conftest.py`, `requirements-dev.txt`

**Mô tả:**  
Project hiện có ZERO test files. `pyproject.toml` đã config pytest (line 51-56) nhưng không có `tests/` directory.

**Yêu cầu:**

1. Tạo `requirements-dev.txt`:

   ```text
   -r requirements.txt
   pytest>=8.0
   pytest-cov>=5.0
   pytest-mock>=3.14
   ```

2. Tạo `tests/__init__.py` (empty)
3. Tạo `tests/conftest.py` với fixtures cơ bản (sample OHLCV, equity series)

**Verification:** `pytest tests/ --co` không có lỗi.

---

### Task 1.2: Unit Tests cho Indicators

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** HIGH  
**Dependency:** Task 1.1  
**File mới:** `tests/test_indicators.py`  
**File source:** `modules/indicators.py`

**Test cases cần viết:**

1. SMA(5) trên known values → verify giá trị cụ thể
2. EMA trên constant series = constant
3. RSI always ∈ [0, 100]
4. ATR > 0 cho non-constant series
5. MACD histogram ≈ 0 cho constant price
6. `add_indicators()` output có đủ cột `['ma', 'atr', 'macd_h']`
7. Input DataFrame không bị mutate
8. Đầu series có NaN do warmup period

---

### Task 1.3: Unit Tests cho Execution Engine

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** HIGH  
**Dependency:** Task 1.1  
**File mới:** `tests/test_execution.py`  
**File source:** `core_python/strategies/combo/core/execution.py`

**Lưu ý:** Test hành vi **hiện tại** của code. Khi Phase 2 (financial) được làm, tests sẽ cập nhật theo.

**Test cases cần viết:**

1. Pending order fill từ bar tiếp theo (kiểm tra hành vi hiện tại)
2. Partial TP cập nhật equity đúng
3. Daily stop ngừng mở trade mới
4. Max drawdown dừng backtest
5. Trailing SL chỉ đi theo một chiều (LONG chỉ tăng, SHORT chỉ giảm)
6. Pending order hết TTL bị hủy
7. `_build_trade_record()` trả về đúng close_net và trade dict

---

### Task 1.4: Fix Empty Equity IndexError trong metrics.py

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** HIGH  
**File:** `core_python/strategies/combo/core/metrics.py` line 93-95

**Vấn đề:**  
Khi backtest không có trade nào, `eq_ts` chỉ có 1 phần tử. Code hiện tại:

```python
total_ret = (eq_ts.iloc[-1] / eq_ts.iloc[0] - 1) * 100
years     = (eq_ts.index[-1] - eq_ts.index[0]).days / 365.25
```

→ `years = 0` → ZeroDivisionError hoặc `annual_ret = inf`.

**Fix cần làm:**  
Thêm early return phòng thủ trước khi tính các metrics phụ thuộc vào eq_ts:

```python
if len(eq_ts) < 2:
    # Không đủ data để tính time-based metrics
    total_ret = annual_ret = max_dd = calmar = 0.0
    years = 0.0
else:
    # ... logic hiện tại
```

**Đây là lỗi kỹ thuật thuần túy** — không thay đổi bất kỳ công thức tài chính nào.

---

### Task 1.5: Fix DB Connection Leak trong data_loader.py

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** HIGH  
**File:** `modules/data_loader.py`

**Vấn đề:**  
Một số code path return sớm (khi `df.empty`) mà không close connection → tích lũy sau nhiều lần chạy → DB connection pool exhausted.

**Fix cần làm:**  
Đảm bảo tất cả functions dùng `get_connection()` đều có pattern:

```python
conn = get_connection()
try:
    # ... logic
finally:
    conn.close()
```

Không để `conn.close()` nằm trong nhánh `else` hoặc sau `return` sớm.

---

### Task 1.6: Pin tvdatafeed Dependency

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** MEDIUM  
**File:** `requirements.txt`

**Vấn đề:**

```text
tvdatafeed @ git+https://github.com/rongardF/tvdatafeed.git
```

Không có version tag → breaking change từ maintainer sẽ làm chết pipeline.

**Fix cần làm:**

1. Xác định commit hash hiện tại đang dùng
2. Pin: `tvdatafeed @ git+https://github.com/rongardF/tvdatafeed.git@<COMMIT_HASH>`
3. Thêm comment: `# Pinned 2026-04-16. Review quarterly.`

---

### Task 1.7: Connection Pooling cho DB

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** MEDIUM  
**File:** `modules/db_connector.py` line 48-68

**Vấn đề:**  
Mỗi operation tạo TCP connection mới → overhead đáng kể khi dashboard refresh thường xuyên.

**Fix cần làm:**  
Thread-safe pool (max 5 connections), validate trước khi reuse, release thay vì close.

---

### Task 1.8: Batch Delete trong db_connector.py

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** LOW  
**File:** `modules/db_connector.py` line 793-798

**Vấn đề:**  
`delete_ohlcv_bars()` delete từng bar một trong loop → O(n) round-trips.

**Fix cần làm:**  
Dùng `IN (...)` clause thay vì loop:

```sql
DELETE FROM DWH.Fact_OHLCV
WHERE SymbolID = ? AND TimeframeID = ? AND BarTime IN (?, ?, ?, ...)
```

---

### Task 1.9: Fix DB Clustering Index

**Trạng thái:** `[ ]` Chưa làm  
**Loại:** ✅ CODE  
**Mức độ:** MEDIUM  
**File mới:** `data_provider/00_sql/06_optimize_indexes.sql`

**Vấn đề:**  
`Fact_OHLCV` cluster trên `FactID` (insertion order) nhưng mọi query filter theo `(SymbolID, TimeframeID, BarTime)` → full scan.

**⚠️ LƯU Ý:** PHẢI backup DB trước khi chạy. Table bị lock trong quá trình migration.

---

## Phase 2 — Financial Logic (HOÃN LẠI — cần owner quyết định)

> Các task dưới đây liên quan đến **backtest accuracy**, **metrics formula**, và **strategy parameters**.  
> **Không làm** cho đến khi owner hiểu rõ ảnh hưởng và đồng ý.  
> Mỗi task cần chạy backtest so sánh trước/sau để xác nhận.

### Task 2.1: Fix Look-ahead Bias — T+1 Fill

**Loại:** 🔬 FINANCIAL  
**Mức độ:** CRITICAL  
**File:** `execution.py` line 114-118, 480-483  
**Ảnh hưởng khi fix:** Ít trades hơn, PnL thấp hơn, realistic hơn (~5-20% thay đổi)

Pending order hiện fill trên cùng bar với signal. Cần chuyển sang fill tại open của bar tiếp theo.

---

### Task 2.2: Fix MA Trailing phá Breakeven

**Loại:** 🔬 FINANCIAL  
**Mức độ:** CRITICAL  
**File:** `execution.py` line 172, 188-193  
**Ảnh hưởng khi fix:** Một số trades hiện đang lỗ sẽ đúng là breakeven

MA trailing có thể kéo SL qua breakeven price sau partial TP. Cần thêm guard.

---

### Task 2.3: Fix Sharpe/Sortino Inflation

**Loại:** 🔬 FINANCIAL  
**Mức độ:** CRITICAL  
**File:** `metrics.py` line 75-80  
**Ảnh hưởng khi fix:** Sharpe hiển thị thấp hơn nhiều (đúng hơn)

Annualize bằng sqrt(1512) bất kể sample size. Cần minimum sample check.

---

### Task 2.4: Fix Monte Carlo — Block Bootstrap

**Loại:** 🔬 FINANCIAL  
**Mức độ:** HIGH  
**File:** `monte_carlo.py` line 54  
**Ảnh hưởng khi fix:** Confidence interval rộng hơn, conservative hơn

Random shuffle phá serial correlation. Cần block bootstrap.

---

### Task 2.5: Walk-forward — Tăng IS/OOS Window

**Loại:** 🔬 FINANCIAL  
**Mức độ:** HIGH  
**File:** `walk_forward.py` line 160-163  
**Ảnh hưởng khi fix:** Ít OOS windows hơn nhưng robust hơn

IS 5000 bars → 8000, OOS 1250 → 2000, thêm buffer 500 bars giữa IS/OOS.

---

### Task 2.6: Walk-forward — Giảm Grid Search

**Loại:** 🔬 FINANCIAL  
**Mức độ:** HIGH  
**File:** `walk_forward.py` line 209-214  
**Ảnh hưởng khi fix:** Ít overfitting hơn, params robust hơn

81 combinations → 16, tăng threshold_ratio 0.5 → 0.7.

---

### Task 2.7: Walk-forward — Parameter Stability Score

**Loại:** 🔬 FINANCIAL  
**Mức độ:** MEDIUM  
**File:** `walk_forward.py`

Đo mức ổn định của optimal params qua các OOS window. Warning nếu params nhảy lung tung.

---

## Checklist tổng hợp

| # | Task | Loại | Mức độ | File chính | Trạng thái |
| --- | --- | --- | --- | --- | --- |
| 1.1 | Setup test framework | ✅ CODE | HIGH | tests/conftest.py | `[ ]` |
| 1.2 | Unit tests — indicators | ✅ CODE | HIGH | tests/test_indicators.py | `[ ]` |
| 1.3 | Unit tests — execution | ✅ CODE | HIGH | tests/test_execution.py | `[ ]` |
| 1.4 | Fix empty equity IndexError | ✅ CODE | HIGH | metrics.py | `[ ]` |
| 1.5 | Fix DB connection leak | ✅ CODE | HIGH | data_loader.py | `[ ]` |
| 1.6 | Pin tvdatafeed | ✅ CODE | MEDIUM | requirements.txt | `[ ]` |
| 1.7 | Connection pooling | ✅ CODE | MEDIUM | db_connector.py | `[ ]` |
| 1.8 | Batch delete | ✅ CODE | LOW | db_connector.py | `[ ]` |
| 1.9 | Fix DB clustering index | ✅ CODE | MEDIUM | 06_optimize_indexes.sql | `[ ]` |
| — | — | — | — | — | — |
| 2.1 | Fix look-ahead bias | 🔬 FINANCIAL | CRITICAL | execution.py | `[HOLD]` |
| 2.2 | Fix MA trailing breakeven | 🔬 FINANCIAL | CRITICAL | execution.py | `[HOLD]` |
| 2.3 | Fix Sharpe/Sortino inflation | 🔬 FINANCIAL | CRITICAL | metrics.py | `[HOLD]` |
| 2.4 | Fix Monte Carlo bootstrap | 🔬 FINANCIAL | HIGH | monte_carlo.py | `[HOLD]` |
| 2.5 | Walk-forward IS/OOS window | 🔬 FINANCIAL | HIGH | walk_forward.py | `[HOLD]` |
| 2.6 | Walk-forward grid search | 🔬 FINANCIAL | HIGH | walk_forward.py | `[HOLD]` |
| 2.7 | Parameter stability score | 🔬 FINANCIAL | MEDIUM | walk_forward.py | `[HOLD]` |

---

## Đã hoàn thành

| Task | Mô tả | Ngày |
| --- | --- | --- |
| execution.py refactor | Thêm `_build_trade_record()` — gom 3 đoạn code trùng | 2026-04-16 |
| _scan_shared.py | Module docstring đúng vị trí trước imports | 2026-04-16 |

---

## Thứ tự làm tiếp theo (Phase 1)

```text
1.4 → 1.5 (fix bugs ngay, không cần setup trước)
1.1 → 1.2 → 1.3 (setup test, viết tests)
1.6 (pin dependency — 5 phút)
1.8 (batch delete — đơn giản)
1.7 → 1.9 (infra lớn hơn, để cuối)
```
