# Chẩn đoán: vì sao CLI tick backtest "đứng" — Claude — 2026-09-10

Trả lời câu hỏi: *"có phải do thiết kế hệ thống / chạy qua Python không?"* và *"CLI có kém hiệu quả không?"*

Tất cả số liệu dưới đây **đo trực tiếp trong phiên này**, gồm cả chạy tay `ctrader-cli`
KHÔNG qua Python để loại trừ pipeline.

---

## 0. Kết luận ngắn

**KHÔNG phải do Python / thiết kế pipeline.** Chạy tay `ctrader-cli backtest` (không có
Python) gặp **đúng** các triệu chứng. Pipeline thậm chí xử lý tốt hơn chạy tay (xem §4).

**CLI không kém hiệu quả ở phần tính toán** — khi có dữ liệu, JP225 20 tháng tick chạy sim
hết ~225s, ngang US30. Nút thắt nằm ở **hai chỗ khác**, và có **ba chế độ hỏng riêng biệt**.

---

## 1. Ba chế độ, phân tách sạch theo nguyên nhân

| Symbol | Quote | Cache tick 2025 | Kết quả | Chế độ |
|---|---|---|---|---|
| US30.cash | USD | đầy (986 file, từ 2023) | ✅ ok **239s** net +18,797 | **A** |
| XAUUSD | USD | một phần (155, thiếu 2025) | ✅ ok nhưng **1794s** net −40,623 | **B** |
| US100.cash | USD | thưa (35) | ⏳ tải tick 80% sau **37 phút** (tôi kill) | **B** |
| US500.cash | USD | **0 file** | (đang tải, batch mới) | **B** |
| BTCUSD | USD | đầy (978, **1338 MB**) | (batch) | A hoặc B (nặng) |
| JP225.cash | JPY | **đầy** (620) + USDJPY đầy | ❌ sim đông cứng | **C** |
| HK50.cash | HKD | đầy + USDHKD đầy | ❌ | **C** |
| UK100.cash | GBP | thưa + GBPUSD thưa | ❌ | **C** (+B) |
| GER40/FRA40/SPN35 | EUR | thưa + EURUSD thưa | ❌ | **C** (+B) |

### Chế độ A — chạy ngon
USD-quote + cache ấm. Không cần symbol quy đổi. 239s cho 20 tháng tick. Không có gì để sửa.

### Chế độ B — tải tick lạnh, chậm bệnh lý
`--data-mode=ticks` là chế độ **chậm nhất** (Spotware xác nhận): CLI tải từng tick từ server.
- **[đo]** US100 (cache 35 file): loading 80% sau **37 phút**, tốc độ ~1 MB/phút hiệu dụng.
- **[đo]** XAUUSD (cache 155 file): hoàn tất nhưng mất **1794s** (30 phút) — phần lớn là tải.
- Cache **có** được ghi thật (file mới nhất US30 ghi đúng lúc batch chạy). Không phải "không cache".
- `--data-dir` KHÔNG giúp lần đầu — vẫn phải tải lạnh; chỉ nhanh từ lần 2 (đã đo trước: cold
  82s → warm 22s).

### Chế độ C — BUG của CLI ở tick + symbol quy đổi
Đây là phần đáng chú ý nhất. **[đo] log chạy tay JP225 20 tháng tick:**

```
Progress | Loading JP225.cash, h1 | 0 → 100 %        (nhanh, dữ liệu có sẵn)
CBot instance started
Progress | Backtesting | 0.16 %
Progress | Loading USDJPY, h1 | 0 → 100 %            (symbol quy đổi, cũng nhanh)
Combo: loaded 4542 valid signal rows
Progress | Backtesting | 0.17 % → 99.94 %           (sim chạy hết ~225s, tính ra 584 lệnh!)
Info | CBot instance [Combo, JP225.cash, h1] stopped.
{ "NetProfit": 14757.21, "TotalTrades": 584, "ProfitFactor": 1.04 }   ← KẾT QUẢ ĐẦY ĐỦ
Progress | Backtesting | 99.94 %                    ← KẸT Ở ĐÂY
[treo 37 phút, tôi phải kill]
```

`report.json` **ĐÃ ĐƯỢC GHI** (2.2 MB, hợp lệ: net 14,757.21, testingPeriod đúng). Backtest
**xong rồi**. CLI chỉ **không thoát nổi** khỏi trạng thái "lưu report":

```
System.InvalidOperationException: Message expected
   at cTrader.Console.Infrastructure.StateMachine.Strategies
      .BacktestReportSavingStateStrategy.DoEnter()
```

Qua pipeline (batch) thì gặp biến thể khác: sim **đông cứng ngay ở 0.17%** (chưa chạy gì),
sau ~1000s bị **chính cTrader tự abort**: `Error | CBot instance ... aborted by timeout` —
đây là watchdog "not responding" nội bộ của cТrader, không phải timeout của pipeline.

**Điểm chốt phân tách chế độ C:**
- Mọi run hỏng đều **load một symbol quy đổi** (JP225→USDJPY, HK50→USDHKD, UK100→GBPUSD, GER40→EURUSD).
- Mọi run chạy được (US30, XAUUSD) **không** load symbol quy đổi (quote = USD = account currency).
- Không phải do cache: JP225 cache **đầy đủ 2025** vẫn hỏng; XAUUSD cache **thiếu** vẫn chạy.
- Không phải do độ dài: chạy tay JP225 **3 tháng** = 40s sạch, không treo. Chỉ range dài mới lộ.
- **Flaky**: cùng lệnh, probe lần 1 đông cứng ở 397s → retry lần 2 chạy xong 130s.

→ **Bug trong engine backtest của CLI khi tick-mode phải nội suy giá symbol quy đổi.**
CLI mới ra ~1 tháng (8/2026); changelog 5.9.10/5.9.11 toàn vá đúng loại này ("fixed hangs",
"native algo host exit errors reported instead of hanging", "freeze after backtest").

---

## 2. Vì sao GUI chạy được mà CLI không

| | GUI Desktop | CLI |
|---|---|---|
| Phiên | 1 login sống lâu, dữ liệu nằm trong RAM | mỗi backtest = 1 process + 1 login mới |
| Tải dữ liệu | có UI riêng, chờ bao lâu cũng được | có watchdog "not responding" tự giết chính nó |
| Cache tick trên máy này | **do GUI tạo** suốt nhiều tuần anh dùng | CLI hưởng ké — symbol nào GUI chưa mở kỹ thì CLI phải tự tải |
| Engine xử lý symbol quy đổi | ổn định nhiều năm | code mới, có bug (chế độ C) |
| `optimize` | có, đa lõi, 1 phiên | **chưa có** (cần CLI 5.10) |

CLI được thiết kế để **tự động hoá chạy hàng loạt**, không phải để thay khâu **tải dữ liệu**
của GUI. Trên máy này GUI đã làm sẵn khâu tải cho các symbol USD chính → CLI chạy tốt đúng
những symbol đó.

---

## 3. Nên làm gì

### Phân công theo đúng thế mạnh

| Việc | Công cụ | Lý do |
|---|---|---|
| Screening / quét lưới đa biến (mục đích chính của pipeline) | **CLI + `data_mode='m1'`** | m1 chạy sạch 11/11 symbol, ~30s/run, không dính chế độ B lẫn C. Đã là mặc định notebook. |
| Fidelity tick — symbol USD, cache ấm (US30, US100*, US500*, XAUUSD*, BTCUSD) | **CLI + ticks** | chế độ A. (*sau khi warm 1 lần) |
| Fidelity tick — symbol non-USD (JP225, HK50, UK100, GER40, FRA40, SPN35) | **GUI**, hoặc CLI + range ngắn ≤3 tháng + retry | chế độ C: CLI bug; GUI không dính. Range ngắn né được (đã đo 3 tháng = 40s sạch). |
| Warm cache tick cho symbol USD còn thiếu (US100, US500) | chạy 1 lần, chấp nhận 30–45 phút/symbol | chế độ B là phí 1 lần; sau đó nhanh như US30 |

### Đang chạy (background)
Batch `combo_h1_2025_to_now_ticks` **chỉ còn USD** (US100, US500, BTCUSD — US30/XAUUSD đã
xong, skip). Tuần tự, timeout 5400s, retry 2. Sẽ warm cache cho chúng. Báo từng cái khi xong.

### Non-USD: chờ anh chốt
1. **`m1`** cho tất cả — nhanh, chấp nhận fill khác GUI-ticks một chút (khuyến nghị).
2. **Chia khúc ≤3 tháng** + retry — đúng số GUI-ticks nhưng ~5 phút/symbol, code thêm 1 lớp
   date-splitter.
3. **Chạy trên GUI** cho shortlist cuối (vài bộ tham số, không phải cả lưới).
4. **Báo bug cho Spotware** + chờ CLI update (5.9.16 đã mới hơn lúc bắt đầu phiên).

---

## 4. Pipeline đã sửa (phiên này)

Chế độ C **flaky** → retry là cách đúng. Trước đây pipeline chỉ retry `MESSAGE_EXPECTED` 1 lần.

- `transient_retries` nới trần **1 → 3**; retry **nhiều vòng** (retry của retry).
- Retry thêm cho `PROGRESS_STALLED` + `HARD_TIMEOUT` (không chỉ `MESSAGE_EXPECTED`) — cùng là
  đông cứng tạm thời. `AUTH_FAILED`/`ACCESS_REQUIRED`/`PERIOD_MISMATCH` vẫn không retry.
- Watchdog "report.json hợp lệ → kill sau grace 10s → OK" **đã có sẵn** và xử lý đúng chế độ
  C "treo sau khi xong" (chạy tay treo 37 phút vì không có lớp này).
- Test: 36 → **37, 37/37 PASS**.

Không đề xuất đổi kiến trúc pipeline — nó không phải nguyên nhân.
