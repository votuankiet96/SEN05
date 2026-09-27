# cTrader Backtest API Plugin — Optimize Spike Record

Ngày: 2026-09-11  
Người kiểm: Codex  
Phạm vi: `bo_workflow/ctrader_api/` spike backend, không refactor `bo_workflow/pipeline/`.

## 1. Kết luận ngắn

Backtest API plugin là hướng rất đáng chọn làm backend nền cho hệ thống optimize mới.

- Plugin API chạy được backtest qua cTrader Desktop bằng `Backtesting.Start(...)`.
- Đã xác nhận chạy song song thật với `MaxConcurrentJobs=8`.
- 72 pass optimize `KSL × KTP` chạy xong trong khoảng 5 phút thay vì ước tính khoảng 36–40 phút nếu tuần tự.
- Khi config khớp, plugin có thể khớp GUI từng số trên case 9-pass `KTP` với `MaxMarginPercent=100`.
- Với GUI run mới `MaxMarginPercent=50`, plugin và GUI cùng chọn best candidate, trade count khớp 70/70 cặp đối chứng, nhưng metric chưa khớp tuyệt đối; cần chuẩn hóa thêm toàn bộ `BacktestingSettings` và làm trade-level parity gate.

## 2. Kiến trúc nên đi tiếp

Mục tiêu thật của hệ thống không phải chỉ chạy backtest, mà là xây nền tối ưu hóa chiến lược:

```text
Config / StrategyDefinition / ExperimentConfig
        ↓
PlanBuilder
        ↓
RunSpec[]
        ↓
Scheduler / Store / Resume / Retry
        ↓
Executor backend
  ├─ CLI backend
  └─ Plugin API backend
        ↓
Normalized report / trades / events
        ↓
Optimize / Walkforward / Monte Carlo
```

`bo_workflow/ctrader_api/` nên giữ là component plugin + bridge. Khi production hóa, adapter Python nằm trong core engine/pipeline và gọi component này như một executor backend.

Không nên biến plugin spike thành pipeline riêng. Source of truth vẫn nên là store/index của pipeline/core engine; bridge chỉ là transport.

## 3. Thay đổi đã thực hiện trong spike

Chỉ chỉnh trong `bo_workflow/ctrader_api/`, không đụng pipeline cũ:

- `plugin/BoBacktestRunner/BoBacktestRunner/config.json`
  - đổi parameter `MaxConcurrent` thành `MaxConcurrentJobs`
  - default `8`
- `plugin/BoBacktestRunner/BoBacktestRunner/BoBacktestRunner.cs`
  - dùng `EffectiveMaxConcurrent = Math.Clamp(MaxConcurrentJobs, 1, 8)`
  - heartbeat ghi thêm `maxConcurrent`

Lý do đổi tên parameter: cTrader có thể giữ runtime setting cũ `MaxConcurrent=1`; đổi tên giúp tránh bị reuse giá trị cũ khi test concurrency.

Build/deploy plugin bằng:

```powershell
python bo_workflow\ctrader_api\scripts\deploy_plugin.py
```

Heartbeat sau khi bật lại plugin:

```json
{
  "schema": "bo-backtest-api-heartbeat/v1",
  "status": "alive",
  "running": 0,
  "maxConcurrent": 8
}
```

## 4. Thử nghiệm optimize qua plugin

### 4.1 Setup chung

- Strategy: `Combo`
- Symbol: `US30.cash`
- Timeframe: `h1`
- Period: `2026-01-01 -> 2026-02-01`
- Data mode: `ticks`
- Balance: `100000`
- Signal CSV: `Z:\Desktop\og_program\runtime\exports\combo_US30_H1_full_history_signals.csv`
- Risk: `1`
- Protections: false
- Backend: cTrader Desktop Plugin API
- Plugin concurrency: `MaxConcurrentJobs=8`

### 4.2 KTP-only smoke/compare

Sweep:

- `KslLevel=Fib1000`
- `KtpLevel=9 mức`
- `MaxMarginPercent=100`

Kết quả:

- 9/9 plugin runs `ok`
- mỗi pass khoảng 30–32 giây
- khi đối chiếu GUI cùng config `MaxMarginPercent=100`, các metric chính khớp từng số.
- Best: `KtpLevel=Fib4618`, net `11416.40`, PF `1.74`, trades `22`.

### 4.3 KSL × KTP — MM100

Sweep:

- `KslLevel`: 8 mức
- `KtpLevel`: 9 mức
- tổng: 72 jobs
- `MaxMarginPercent=100`

Kết quả plugin:

- 72/72 `ok`
- tổng thời gian: `295.9s`
- concurrency xác nhận: heartbeat lên `running=8`, `maxConcurrent=8`
- Best:
  - `KSL=Fib0618`
  - `KTP=Fib4618`
  - net `28232.25`
  - PF `2.54`
  - trades `22`
  - max equity DD `%` `8.696188355255087`

### 4.4 KSL × KTP — MM50, đối chứng GUI mới

Sweep:

- `KslLevel`: 8 mức
- `KtpLevel`: 9 mức
- tổng: 72 jobs
- `MaxMarginPercent=50`

Kết quả plugin:

- 72/72 `ok`
- tổng thời gian: `321.0s`
- concurrency xác nhận: heartbeat duy trì `running=8`
- Best plugin:
  - `KSL=Fib0618`
  - `KTP=Fib4618`
  - net `18695.77`
  - PF `2.37`
  - trades `22`
  - max equity DD `%` `7.09955303635658`

GUI folder mới nhất:

```text
C:\Users\Administrator\Documents\cAlgo\Data\cBots\Combo\4c4e163f-c54f-43c1-ab93-8d282a770f42-Default\Optimization
```

GUI parse:

- 72 report files tồn tại.
- 70/72 pass đúng setup `US30.cash h1`, period `1m (01/01/2026 - 01/02/2026)`, `Risk=1`, `MaxMarginPercent=50`.
- 2 pass còn stale/khác config:
  - `Fib1000/Fib1000`
  - `Fib2618/Fib1618`

Đối chứng 70 cặp hợp lệ:

- shared pairs: `70`
- trade count khớp: `70/70`
- exact metric rows: `26/70`
- max absolute net diff: `214.27`
- max PF diff: `0.01`
- max max-equity-DD diff: `0.06988424`
- best GUI cũng là `KSL=Fib0618`, `KTP=Fib4618`
  - GUI net `18481.50`
  - GUI PF `2.36`
  - GUI trades `22`
  - GUI max equity DD `%` `7.029668800015197`

Ghi chú quan trọng: GUI report mới có `main.commissions.value=30`, plugin report có `main.commissions.value=0`, dù `tradeStatistics.commissions` đều là `0`. Đây là dấu hiệu `BacktestingSettings` chưa được chuẩn hóa hoàn toàn giữa GUI và plugin. Cần làm trade-level diff và settings diff trước khi gọi là golden parity.

## 5. CPU/RAM/process khi chạy song song

Máy test:

- 32 logical CPU
- RAM khoảng 48 GB

Mẫu khi plugin đang `running=8`:

| Time | running | CPU tổng | RAM dùng | cTrader working set | Process |
|---|---:|---:|---:|---:|---|
| 01:17:07 | 8 | 29.2% | 29.6% | 2136 MB | `cTrader:1; ctrader-cli:9` |
| 01:17:14 | 8 | 65.9% | 44.1% | 2646 MB | `cTrader:1; ctrader-cli:9` |
| 01:17:21 | 8 | 81.3% | 60.1% | 5538 MB | `cTrader:1; ctrader-cli:9` |
| 01:18:54 | 8 | 86.7% | 59.2% | 5875 MB | `cTrader:1; ctrader-cli:9` |

Process ownership:

- Plugin code chạy trong `cTrader.exe`.
- Khi plugin gọi `Backtesting.Start(...)`, cTrader Desktop spawn các `ctrader-cli.exe` worker con.
- Trong test, 8 worker plugin là child trực tiếp của `cTrader.exe`.
- Có thêm 1 `ctrader-cli.exe` không thuộc plugin API: parent là `python.exe`, command là CLI pipeline cũ `bo_workflow\runs\backtest\combo_h1_2025_to_now_ticks...US500.cash...`. Đây là job CLI cũ/ngoài phạm vi plugin test.

Kết luận vận hành:

- `MaxConcurrentJobs=8` chạy được trên máy này.
- CPU/RAM chưa vượt ngưỡng, nhưng có spike CPU/RAM đáng kể.
- Nên production default ban đầu là 4 hoặc 6, sau đó benchmark 8 theo symbol/range.

## 6. Rủi ro/caveat còn lại

1. Parity chưa certified tuyệt đối.
   - MM100 9-pass khớp GUI từng số.
   - MM50 70-pass gần khớp và ranking khớp, nhưng còn lệch nhỏ.
   - Cần diff trade-level và settings-level.

2. GUI optimization folder có thể stale một phần.
   - Lần GUI mới có 70/72 pass đúng config, 2 pass vẫn là kết quả cũ.
   - Tool đối chứng phải kiểm `parameters.cbotset`, period, symbol, timeframe, risk, margin, data mode; không được tin số pass.

3. Plugin bridge hiện mới là transport.
   - Chưa tích hợp `ExperimentStore`, `param_hash`, resume, retry policy.
   - Chưa có cleanup claimed/results theo experiment.

4. `PreciseConversion` chưa được chứng nhận.
   - Local API 5.9.16 không expose trực tiếp; code đang apply reflection nếu runtime có property.
   - Cần test non-USD/non-quote USD symbols: FRA40/HK50/JP225/GER40.

5. Resource/process hygiene.
   - Plugin API spawn worker `ctrader-cli.exe` nội bộ.
   - Cần watchdog/timeout/release abandoned ở phía Python adapter.
   - Cần tool phát hiện external CLI jobs đang chạy để benchmark sạch.

## 7. Hướng triển khai đề xuất

### Phase 1 — Certify Plugin Backend

- Giữ `bo_workflow/ctrader_api/` là component riêng.
- Thêm script/tool parity:
  - parse GUI Optimization/Backtesting folder
  - parse plugin results
  - compare by `(symbol,timeframe,start,end,params)`
  - fail nếu settings mismatch
  - compare trade-level nếu report có đủ `positions/items`

### Phase 2 — Adapter vào core engine

Không rewrite toàn bộ pipeline ngay. Thêm abstraction executor:

```text
core_engine/backends/
  cli/
    executor.py
  plugin_api/
    executor.py
```

`RunSpec` vẫn là atom. Scheduler/store/analysis dùng chung.

Plugin adapter làm:

```text
RunSpec -> BacktestApiJob -> wait result -> JsonReport -> metrics/store
```

Bridge files chỉ là mailbox, không là source of truth.

### Phase 3 — Optimize

Implement optimize như grid planner + scheduler:

```text
param grid -> RunSpec[] -> plugin executor -> ExperimentStore -> rank/filter
```

Sau đó mới thêm:

- staged search
- coarse m1 screening -> ticks confirmation
- early rejection
- top-N portfolio selection

### Phase 4 — Walkforward

Walkforward là generator của nhiều train/test windows:

```text
window planner -> optimize train -> select top candidates -> test OOS -> aggregate
```

Không cần engine riêng; dùng cùng executor/store.

### Phase 5 — Monte Carlo

Monte Carlo không nên gọi cTrader lại nếu không cần.

Nên dùng:

- positions/trades/events từ completed run
- resample trade sequence
- randomize slippage/spread nếu có model
- tính risk-of-ruin/DD distribution offline

## 8. Prompt bàn giao cho Claude

```text
Bạn tiếp nhận từ record:
bo_workflow/reports/ctrader-backtest-api-plugin-optimize-record-2026-09-11.md

Mục tiêu chiến lược:
Xây hệ thống tối ưu hóa strategy dựa trên cTrader Desktop Backtesting API plugin làm backend chính, sau đó phát triển optimize, walkforward, monte carlo. CLI pipeline hiện tại giữ lại như backend phụ/headless, không xóa.

Nguyên tắc kiến trúc:
- Không biến plugin spike thành pipeline riêng.
- Giữ một core engine chung: config -> RunSpec -> scheduler/store -> executor backend -> normalized result -> analysis.
- Backend chỉ là lớp thực thi một RunSpec: CLI hoặc Plugin API.
- Bridge folder của plugin chỉ là mailbox, không phải source of truth.
- Source of truth vẫn là ExperimentStore/index/manifest/run folder của core engine.
- Code phải lean, tách chức năng rõ ràng, ít file nhưng đúng vai trò, dễ mở rộng.

Hiện trạng đã test:
- Plugin đã chạy được `Backtesting.Start(...)`.
- `MaxConcurrentJobs=8` đã được bật và xác nhận heartbeat `running=8`.
- 72-pass KSL×KTP qua plugin chạy OK.
- MM100: 72 pass xong 295.9s, best `KSL=Fib0618`, `KTP=Fib4618`, net `28232.25`.
- MM50: 72 pass xong 321.0s, best `KSL=Fib0618`, `KTP=Fib4618`, net `18695.77`.
- GUI MM50 mới có 70/72 pass đúng config; best cũng `Fib0618/Fib4618`, net `18481.50`.
- 70 cặp GUI/plugin: trade count khớp 70/70, PF/DD lệch nhỏ, net lệch tối đa 214.27.
- Cần settings-level và trade-level parity trước khi gọi backend là certified.

Việc cần làm ngay:
1. Viết tool parity trong `bo_workflow/ctrader_api/` hoặc report utility:
   - parse plugin result JSON
   - parse GUI report.html + parameters.cbotset
   - match by params
   - compare settings/report metrics/trade-level positions
   - báo missing/stale GUI passes
2. Chuẩn hóa BacktestingSettings plugin:
   - commission, commission type, spread, precise conversion, balance, data mode
   - đảm bảo report `main.commissions`, `spread`, `data`, `testingPeriod` khớp GUI
3. Thiết kế adapter production:
   - `PluginBacktestExecutor`
   - input: `RunSpec`
   - output: normalized result giống CLI backend
   - vẫn dùng ExperimentStore/param_hash/scheduler chung
4. Sau parity, triển khai optimize method dựa trên plugin executor:
   - grid params -> RunSpec[] -> scheduler -> store -> rank
5. Sau optimize mới triển khai walkforward và monte carlo:
   - walkforward = window planner + optimize train + OOS test
   - monte carlo = offline resampling từ trades/events, không gọi cTrader lại nếu không cần

Ràng buộc:
- Không đụng `Combo/Combo/Combo.cs`, `MA Cross/MA Cross/MA Cross.cs`, hoặc `.algo` nếu không được yêu cầu.
- Không phá pipeline CLI cũ.
- Không copy/read password file.
- Giữ code clean, tối giản, tách rõ chức năng.
- Mỗi bước phải có test nhỏ và report kết quả thực nghiệm.
```
