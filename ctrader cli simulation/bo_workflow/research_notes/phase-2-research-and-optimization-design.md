# Phase 2 — Thiết kế hệ thống nghiên cứu & tối ưu hoá (bản hợp nhất)

Ngày chốt: 2026-09-11 · Trạng thái: **CHỜ DUYỆT → giao đội coder triển khai**
Hợp nhất từ: đề xuất 7 tầng (Claude) + nghi thức chống overfit, 8 điều chỉnh và cấu trúc 10 file
(đội audit) + phản biện qua lại trong phiên 2026-09-11.
Bằng chứng nền: `ab-plugin-api-vs-cli-2026-09-11.md`, `cli-vs-gui-conversion-rate-bug-2026-09-11.md`,
`ctrader-cli-practical-issues-catalog-2026-09-10.md`, `lotsize-pipeline-reference.md`.

Quy ước: **PHẢI** = bắt buộc; **NÊN** = mặc định, đổi phải ghi lý do; **TBD** = người dùng chốt
sau, code phải nhận qua config chứ không hardcode.

---

## 0. Tóm tắt một trang

- **cBot (`Combo.algo`, `MA Cross.algo`) là bản gốc** của logic giao dịch. OF (Order Follower,
  Python, gọi cTrader Open API) sẽ là bản sao về sau. cTrader chỉ được **mượn làm engine chấm điểm**.
- **GUI cTrader là chuẩn đối chiếu.** CLI là runner batch mặc định. Plugin API đóng băng.
- **Chống overfit là nguyên tắc thiết kế**, không phải bước kiểm tra sau: experiment phải khai
  trước vùng dữ liệu, không gian tham số, luật chọn, ngân sách thử; final test bị store khoá.
- Hệ mới: `bo_workflow/research/` **đúng 10 file Python**. `pipeline/` và
  `ctrader_api/` đóng băng làm tham chiếu; logic đã kiểm chứng được **port có kiểm
  soát** sang hệ mới (không import chéo).
- Luồng: `experiment config → plan → runner (CLI) → store → evidence → selection / montecarlo →
  .cbotset + cấu hình trung lập cho OF`.

---

## 1. Hiện trạng tại thời điểm chốt (đội coder đọc trước)

### 1.1 Thay đổi bot ngày 2026-09-11: đã GỠ margin cap

- `CapVolumeByMargin` + `[Parameter] MaxMarginPercent` + bộ đếm `margin-capped/margin-blocked`
  đã gỡ khỏi **cả Combo.cs và MA Cross.cs** (quyết định người dùng). Volume giờ chỉ theo risk%
  khoảng cách SL, chuẩn hoá step, kẹp min/max broker.
- Lý do: với profile không bật tỷ giá lịch sử (§2.3), `Symbol.GetEstimatedMargin` quy đổi bằng
  **giá cuối kỳ backtest** ⇒ volume lệnh bị chặn phụ thuộc ngày kết thúc (look-ahead). Đo thật:
  cùng lệnh US30 02/01/2026, chỉ đổi endUtc → volume 14.85/15.06/15.77/14.07.
- `.algo` đã build lại 2026-09-11 15:58Z (Combo sha256 `F64715BC6E39…`, MA Cross `ABCE057EB287…`),
  metadata còn 10 tham số (9 + `SignalFilePath`). Source cũ sao lưu:
  `bo_workflow/legacy/margin-cap-removed-2026-09-11/`.
- Dòng OnStop mới (máy đọc):
  `Combo: loaded=, processed=, before-start=, not-processed=, placed=, failed=, guard-skipped=,
  pending-expired=, same-direction-skipped=, reversed=.` (MA Cross giống, không có `pending-expired`).
- `pipeline/` đã vá tương thích (registry, regex OnStop đọc được cả log cũ lẫn mới, notebook);
  35/35 test OK.

**Hệ quả PHẢI biết:**
1. **Mọi kết quả/golden tạo trước 2026-09-11 15:58Z là của bot có cap ⇒ không so được với bot
   mới.** Golden phải tạo lại (§4.4).
2. Không còn cap ⇒ lệnh vượt margin khả dụng sẽ **bị engine từ chối lúc khớp** (Combo: lệnh chờ
   biến mất im lặng — bot không đếm được; MA Cross: `failed` + dòng `market … was rejected`).
   Quyết định từ chối của engine **cũng** dùng margin quy đổi xấp xỉ ⇒ vẫn là look-ahead, chỉ ở
   mức "từ chối hay không". ⇒ gate `margin_rejections` (§5, P0).

### 1.2 Sự thật đã đo (không cần đo lại)

| Sự thật | Bằng chứng |
|---|---|
| Plugin `Backtesting.Start` = Desktop spawn `ctrader-cli.exe backtest` cùng bộ cờ (+`--port`) | cây tiến trình, A/B 72/72 pass + 442/442 lệnh trùng |
| CLI 8 song song ≈ plugin 8 song song (300 s vs 291 s / 72 pass) | A/B 2026-09-11 |
| GUI (bỏ tick tỷ giá lịch sử) = CLI: 21/21 metric + toàn bộ bot log | FRA40 2026-09-11 |
| CLI 5.9.16 **không** có `--precise-conversion`, `tick-csv`, `--commission-type` | probe |
| Plugin API 5.9.16 **không** có `PreciseConversion`; **có** custom data source (tick) | `cAlgo.API.xml` |
| Tỷ giá xấp xỉ (EUR) có thể trôi theo thời điểm chạy (FRA40 cách 4.5 h lệch ~$7, lệnh y hệt) | chiến dịch warm |
| `report.json` có `history.items` (từng lệnh, đủ trường) + `equity.points` **theo giờ** (`balance`, `minEquity`, `maxEquity`) | đọc report |
| Signal CSV của OG có lịch sử **từ 05/2017** (~4.4–4.5k signal H1/symbol) | đọc CSV |
| Tick FTMO đã có cache 2025-01 → nay cho 11 symbol; **trước 2025 chưa kiểm** | census cache |
| Mỗi backtest (CLI/API) tạo 1 thư mục `Documents\cAlgo\Data\cBots\<bot>\<guid>\` (~200–300 KB); đã 923 thư mục / 2.8 GB | đếm |
| `ctrader-cli build` **luôn ghi** `Sources\Robots\<Tên>.algo` bất kể csproj ở đâu | sự cố 2026-09-11 |

---

## 2. Ranh giới & nguyên tắc

### 2.1 Ranh giới
```text
cBot .algo         logic giao dịch (bản gốc). Pipeline KHÔNG tính lệnh thay cBot.
cTrader engine     dữ liệu tick FTMO, khớp lệnh, spread, swap, report. Chỉ gọi qua CLI.
research/          điều phối, lưu bằng chứng, đánh giá độ bền, chọn tham số.
OF (tương lai)     bản sao Python của cBot; nhận đầu ra cấu hình từ research/.
```

### 2.2 Nguyên tắc (PHẢI)
- P-1 **Fidelity trước tốc độ**: dữ liệu chỉ vào nghiên cứu khi qua parity gate (§4) và validity
  gates (§5).
- P-2 **Khai trước, chạy sau**: experiment khoá `experiment.json` trước run đầu tiên; đổi không
  gian tham số/luật chọn/vùng dữ liệu = experiment mới.
- P-3 **Chọn vùng ổn định, không chọn đỉnh** (§6.4).
- P-4 **Final test mở đúng một lần**, do store cưỡng chế (§6.2).
- P-5 **Bằng chứng tái lập**: snapshot đầu vào theo hash, provenance bất biến từng run.
- P-6 **Tối giản**: 10 file nguồn; không backend abstraction "chờ sẵn"; không file xuất thừa.
- P-7 **Fail-closed**: thiếu/lệch điều kiện ⇒ dừng có lý do, không chạy với default âm thầm.

### 2.3 Engine profile duy nhất của v1
`ctrader-5.9.16-ticks-approxfx-v1`: CLI 5.9.16.53348, `data_mode=ticks`, **tỷ giá lịch sử OFF**
(GUI: bỏ tick ô "Download historical data for additional symbols" ở **cả tab Backtesting lẫn
tab Optimization**), commission/spread mặc định (không truyền), balance theo experiment, ngày
kết thúc **tính vào kỳ** (CLI nhận `--end = end + 1 ngày`, định dạng `dd/MM/yyyy HH:mm`).
Profile khác (m1, bản CLI khác, precise ON khi có) = profile mới, phải parity lại.

---

## 3. Kiến trúc: `bo_workflow/research/` — đúng 10 file

```text
bo_workflow/
├─ pipeline/                 ĐÓNG BĂNG — tham chiếu, nguồn để port (không import từ hệ mới)
├─ ctrader_api/              ĐÓNG BĂNG — spike plugin
├─ research/
│  ├─ __init__.py            chỉ export API ổn định
│  ├─ models.py              dataclass bất biến
│  ├─ catalog.py             schema .algo + strategy profile (allowlist)
│  ├─ plan.py                config → RunSpec[] (grid, zones, WFO windows)
│  ├─ runner.py              RunSpec → ctrader-cli → artifact thô (+ read_metadata, dọn instance)
│  ├─ store.py               SQLite, run folder, cache, snapshot, claim, protocol guard
│  ├─ evidence.py            đọc report/log/GUI, chuẩn hoá, parity, validity flags
│  ├─ selection.py           metric, FTMO screen, plateau, tổng hợp zone/WFO (thuần)
│  ├─ montecarlo.py          block bootstrap trên timeline equity (thuần)
│  ├─ api.py                 run_grid / run_walkforward / screen / run_montecarlo / export
│  ├─ tests/                 (ngoài quy tắc 10 file) — BẮT BUỘC
│  └─ profiles/              (dữ liệu JSON có version) engine/*.json, ftmo/*.json, strategy/*.json
├─ notebooks/                (giao diện người dùng) — chỉ gọi research.api
└─ runs/research/            dữ liệu phát sinh (không phải source)
   ├─ _protocol.sqlite       holdout registry + nhật ký mở + đếm ngân sách thử (toàn cục)
   ├─ _golden/<case>/        fixture GUI bất biến
   └─ <method>/<experiment>/
```

### 3.1 Trách nhiệm & quyền sở hữu (PHẢI)

| File | Trách nhiệm duy nhất | Được làm | Không được làm |
|---|---|---|---|
| `models.py` | `RunSpec`, `EngineProfile`, `Experiment`, `Window`, `Zone`, `RunStatus`, `FailureCode` | dataclass frozen, canonical JSON | đọc file, gọi process |
| `catalog.py` | schema tham số từ metadata `.algo` (tên, kiểu, enum→index, default, min/max, thứ tự) + strategy profile (allowlist, miền quét, ý nghĩa) | validate, map enum tên↔index | tự thêm tham số vào optimize; gọi CLI (nhận JSON từ `runner.read_metadata`) |
| `plan.py` | config → `RunSpec[]`: grid, multi-symbol, zones + embargo, WFO windows | hàm thuần | I/O |
| `runner.py` | build argv, auth `-e`, chạy/timeout/kill cây PID, retry transient, bounded concurrency, `read_metadata()`, dọn instance **do chính nó tạo** | là nơi DUY NHẤT gọi `ctrader-cli` | ghi SQLite; build `.algo` |
| `store.py` | chủ duy nhất của `runs/research/` + SQLite: claim/resume, cache content-hash, snapshot input, ghi atomic, **holdout guard**, **đếm ngân sách** | ghi persistent | logic nghiệp vụ đánh giá |
| `evidence.py` | parse `report.json(.gz)`, bot log, GUI `report.html` + cbotset; chuẩn hoá ledger/equity; `bot_summary`; parity compare; validity flags (sự kiện) | read-only, thuần | ghi DB/file |
| `selection.py` | metric, min-trades, FTMO screening, plateau, tổng hợp train/validation/WFO; áp **policy** lên flags | thuần | gọi cTrader, ghi DB |
| `montecarlo.py` | block bootstrap timeline equity; phân phối DD, chuỗi thua, P(breach) | thuần, seed tường minh | gọi cTrader |
| `api.py` | nối module theo luồng; không business logic | orchestration | tính toán nghiệp vụ |

Không có `settings.py`: hằng số nằm ở module sở hữu (đường dẫn CLI/auth → `runner`, thư mục
signal → `catalog`, thư mục runs → `store`). Không thêm file thứ 11 cho config.

### 3.2 Giới hạn kích thước
Trần mềm ~300 dòng/file. `runner.py`, `store.py` được tới ~450 dòng **có ghi lý do đầu file**.
Vượt ⇒ trước hết bỏ logic thừa / làm hàm thuần, không tách file mới. **Không** được bỏ tính năng
an toàn (auth `-e`, redaction, kill đúng PID, retry transient, claim/resume) để đủ số dòng.

### 3.3 Port có kiểm soát từ `pipeline/` (không viết lại từ số 0)
Chép & cắt gọn, port test tương ứng mỗi bước. Được phép bỏ các phần đã đo là không cần:
`--data-dir`/`cold_fill_lock`, watchdog theo Progress %, export views/CSV, migration schema cũ,
nhánh backend native/optimize.

| Đích | Nguồn port |
|---|---|
| `models.py` | `core/params.py` (RunSpec, status, failure code) |
| `catalog.py` | `core/strategy.py` (`validate_metadata`, ParamDef, map symbol↔signal) |
| `plan.py` | `core/plan.py` (validate chặt, grid) |
| `runner.py` | `core/cli.py` + `core/runner.py` + `core/scheduler.py` (cắt gọn) |
| `store.py` | `core/store.py` (claim CAS, owner liveness, WAL, content-hash) |
| `evidence.py` | `core/metrics.py` (+ `parse_bot_summary`) |
| `selection.py` | `analysis/results.py` (`resolve_profit_factor`, rank) |
| `api.py` | preflight trong `methods/backtest.py` |

**Checklist bài học CLI PHẢI giữ** (nguồn: practical-issues catalog):
ngày `dd/MM/yyyy HH:mm`; `--end` +1 ngày; tên symbol đầy đủ (`US30.cash`); `.cbotset` sinh bằng
`json.dumps` có block `Chart{Symbol,Period}`; auth qua `-e`/env (không credential trong argv;
chỉ truyền **đường dẫn** file mật khẩu, không đọc nội dung); redact report/log; `--exit-on-stop`
+ `--report-json`; `--full-access` (Combo đọc CSV); chỉ kill đúng cây PID của mình; retry
`MESSAGE_EXPECTED`/`HARD_TIMEOUT` tối đa 3; timeout ≥ 1800 s cho tick dài; probe option trước khi
dùng (help không liệt kê đủ); `ctrader-cli metadata` không cần login; **không bao giờ gọi
`ctrader-cli build` từ research/**.

---

## 4. Tầng fidelity: parity contract

### 4.1 Hai mức (PHẢI tách)
- **Interface parity**: cùng engine profile, cùng dữ liệu/thiết lập ⇒ GUI và CLI cho `history.items`
  trùng **mọi trường** + metric chính trùng. Chứng minh adapter đúng.
- **Research validity**: kết quả đủ tin để chọn chiến lược không (§5). Interface parity đạt KHÔNG
  tự động nghĩa là mô phỏng đúng thực tế.

### 4.2 Provenance bất biến mỗi run (`execution.json`)
```json
{
  "schema": "bo-research-execution/v1",
  "run_id": "...", "param_hash": "...", "experiment": "...",
  "spec": { "strategy": "combo", "symbol": "US30.cash", "timeframe": "h1",
            "start": "2026-01-01", "end": "2026-01-31", "balance": 100000,
            "params": { "KslLevel": "0", "...": "..." }, "zone": "train", "window": null },
  "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
  "inputs": { "algo_sha256": "...", "signal_sha256": "..." },
  "cli": { "version": "5.9.16.53348", "sha256": "..." },
  "environment": { "account_leverage": 30, "used_symbols": [ { "symbol": "...", "swapLong": 0,
                   "swapShort": 0, "lotSize": 1, "stepVolume": 0.01, "commissions": {} } ],
                   "deposit_asset": "USD" },
  "status": "ok", "failure_code": "none", "reason": "", "attempts": [ ],
  "timing": { "started_utc": "...", "ended_utc": "...", "wall_seconds": 0 },
  "bot_summary": { "loaded": 0, "processed": 0, "placed": 0, "failed": 0, "pending_expired": 0,
                   "reversed": 0, "filled": 0, "reversal_cancels": 0, "margin_rejections": 0 },
  "validity_flags": { "...": "xem §5" }
}
```
`environment` lấy từ `report.main` + `report.usedSymbols` (broker có thể đổi swap/đòn bẩy theo
thời gian ⇒ cùng tham số, khác ngày chạy, có thể khác kết quả).

### 4.3 Snapshot đầu vào
`store.py` copy `.algo` và signal CSV vào `inputs/<sha256>.<ext>` của experiment **trước run
đầu tiên**; runner chạy từ bản snapshot (không chạy file gốc có thể bị OG ghi đè).
Lưu ý: Combo đọc CSV qua `SignalFilePath` ⇒ runner truyền đường dẫn snapshot.

### 4.4 Golden suite (người dùng tạo tay trên GUI, bất biến)
Tạo lại **với `.algo` build 2026-09-11 15:58Z**, engine profile §2.3. Mỗi case lưu
`runs/research/_golden/<case>/`: `report.html`, `parameters.cbotset`, `log.txt`, `events.json`,
`meta.json` (ngày giờ tạo, phiên bản cTrader, thiết lập GUI, người tạo, sha `.algo`).

| Case | Mục đích | Cấu hình |
|---|---|---|
| G1 | non-USD, dài | FRA40.cash H1 ticks 2025-01-01→2026-09-09, 100k, default (KSL Fib1000, KTP Fib2618, risk 1) |
| G2 | SL hẹp, volume lớn (trước đây bị cap) | US30.cash H1 ticks 2026-01-01→2026-01-31, KSL Fib0618, KTP Fib4618, risk 1 |
| G3 | có từ chối margin (kiểm gate) | như G2, risk 2 — coder xác nhận thật sự có lệnh bị từ chối; nếu không, tăng risk tới khi có |
| G4 | bot thứ hai | MA Cross US30.cash H1 ticks 2026-01, default |

**Không** tự động điều khiển GUI. CLI chạy tự động để đối chiếu fixture.

### 4.5 Parity gate
- Chạy lại mỗi case bằng CLI ⇒ so `history.items` (mọi trường), `main` metric, dòng OnStop.
- Trước khi kết luận "adapter sai", so `environment` với `meta` của golden: khác ⇒
  `env_drift` (tạo lại golden), **không** phải lỗi adapter. Symbol non-USD: golden và CLI phải
  chạy cùng ngày (tỷ giá xấp xỉ trôi).
- Kích hoạt: khi `.algo` sha đổi, CLI version đổi, engine profile đổi, hoặc trước experiment lớn.
- Kết quả gate lưu `_protocol.sqlite` (`algo_sha × cli_version × profile → pass/fail/drift`).
  Run chỉ có `parity_certified=true` khi tổ hợp đó đã pass.

---

## 5. Validity gates (evidence tính cờ, selection áp policy)

| Cờ | Cách tính | Policy v1 |
|---|---|---|
| `parity_certified` | tra `_protocol.sqlite` | false ⇒ không dùng cho selection |
| `period_ok` | `testingPeriod` khớp spec (start, end+1) | false ⇒ loại |
| `signal_ok` | `loaded>0` và `processed>0`; lỗi "signal file was not found" trong log ⇒ false | false ⇒ loại (bắt ca "0 lệnh mà ok") |
| **`margin_rejections`** (P0) | Nguồn ưu tiên: sự kiện từ chối của engine nếu có (S3 phải kiểm `events.json` của instance và stdout CLI xem có ghi lệnh bị từ chối vì margin không). Dự phòng — Combo: `placed − filled − pending_expired − reversal_cancels − pending_open_at_end` (filled = số dòng `FILLED`; reversal_cancels = số dòng `reversal - cancelling existing … pending order`; pending_open_at_end = `report.orders.items`); nếu log có `trading halted` (bảo vệ tài khoản gọi `ForceCloseAll` huỷ lệnh chờ không ghi từng lệnh) ⇒ giá trị `unknown`. MA Cross: số dòng `market … was rejected` có lỗi margin | **> 0 hoặc `unknown` ⇒ không đủ điều kiện cho WFO/selection** (look-ahead ở quyết định từ chối) |
| `approx_fx` | `quoteAsset ≠ depositAsset` | true ⇒ chỉ screening; không chứng nhận; parity cùng ngày |
| `env_drift` | `environment` ≠ golden cùng profile | true ⇒ cảnh báo, tạo lại golden |
| `min_trades` | số lệnh đóng trong zone | < ngưỡng (TBD) ⇒ loại |
| `ftmo_screen` | §6.6 | `observed_breach` ⇒ loại |

`bot_summary` (gồm các bộ đếm dẫn xuất ở trên) PHẢI được trích **cho mọi run** ngay sau khi chạy;
bot log đầy đủ chỉ giữ ở golden, run lỗi, hoặc run có cờ xấu.

Ghi chú OF: ngoài live OF PHẢI tự kiểm margin theo giá hiện tại trước khi đặt lệnh (broker cũng
sẽ từ chối) — ngoài phạm vi research/, ghi để đặc tả OF.

---

## 6. Nghi thức nghiên cứu (research protocol)

### 6.1 Experiment contract (`experiment.json`, khoá khi bắt đầu)
```json
{
  "schema": "bo-research-experiment/v1",
  "name": "combo_us30_h1_v1", "method": "grid|walkforward",
  "strategy": "combo", "strategy_profile": "combo-v1",
  "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
  "universe": { "symbols": ["US30.cash"], "timeframes": ["h1"] },
  "parameter_space": { "KslLevel": ["Fib0618", "..."], "KtpLevel": ["..."], "RiskPercent": [1.0] },
  "fixed_params": { "EnableDailyLossLimit": false, "...": "..." },
  "zones": { "train": ["YYYY-MM-DD", "YYYY-MM-DD"], "validation": ["...", "..."],
             "final_test": "ref:<holdout-id>", "embargo_days": 5 },
  "walkforward": { "is_months": 12, "oos_months": 3, "step_months": 3, "mode": "rolling" },
  "selection_rule": { "id": "plateau-v1", "objective": "TBD", "neighborhood": "chebyshev-1",
                      "stat": "p25", "min_neighbors": 4 },
  "risk_filters": { "min_trades": "TBD", "ftmo_profile": "ftmo-2step-v1", "max_dd_pct": "TBD" },
  "budget": { "max_configs": 72 },
  "seed": 20260911,
  "created_utc": "...", "locked": true
}
```
- Tham số trong `parameter_space` PHẢI thuộc allowlist của strategy profile; tham số có trong
  metadata `.algo` mà profile chưa khai ⇒ **dừng** (P-7), kể cả khi chỉ để default.
- Đổi bất kỳ trường nào sau khi `locked` ⇒ tạo experiment mới.

### 6.2 Vùng dữ liệu, embargo, final test (PHẢI)
- Ba vùng tách rời: **Train** (tìm ứng viên) → **Validation** (chọn vùng bền) → **Final test**
  (mở một lần khi thiết kế đã khoá). Final test không dùng để chọn lại tham số/luật; nếu đổi luật
  sau khi mở ⇒ vùng đó thành validation, cần final test mới.
- **Embargo** ≥ thời gian giữ lệnh tối đa quan sát (NÊN mặc định 5 ngày giao dịch cho H1) giữa
  các vùng; lệnh mở trong embargo không tính cho vùng nào.
- **Holdout guard** (`store.py` + `_protocol.sqlite`): final test đăng ký trước theo
  `(strategy, symbol, timeframe, khoảng ngày)`; mọi RunSpec giao với vùng đã đăng ký bị **từ chối**
  trừ khi experiment ở trạng thái `final_test_open` do người dùng mở; mỗi lần mở ghi nhật ký
  (ai, khi nào, experiment, sha). Mở lần hai ⇒ cảnh báo đỏ trong summary.
- **Ngân sách thử**: `_protocol.sqlite` đếm số cấu hình phân biệt đã đánh giá theo
  `(strategy, symbol, timeframe, zone)` xuyên mọi experiment — dùng cho Deflated Sharpe/đánh giá
  multiple-testing; vượt `budget` ⇒ dừng.

Dữ liệu nào còn "chưa bị nhìn" — **TBD, người dùng chốt trước khi đặt zone**:
- 2025-01 → 2026-09 đã dùng nhiều (phát triển bot, parity, lưới) ⇒ **không** dùng làm final test.
- 2017-05 → 2024-12 chưa được pipeline dùng, **nhưng** cần xác nhận OG không tinh chỉnh tín hiệu
  trên đoạn này.
- Final test sạch nhất = **dữ liệu tương lai sau khi khoá thiết kế** (forward trên demo/OF).

### 6.3 Dữ liệu trước 2025
Chưa biết server FTMO có tick sâu. Task S1-spike: chạy ticks ngắn ở 2018/2020/2022 cho 2–3
symbol. Nếu không có ⇒ zone cũ dùng profile `…-m1-…` riêng (GUI m1 ≡ CLI m1 vẫn parity được) +
gate hiệu chỉnh: đo lệch m1 vs ticks trên cùng đoạn 2025 trước khi trộn.

### 6.4 Chọn vùng ổn định (`plateau-v1`)
- Lân cận trên lưới **chỉ số thứ tự** enum (Fib không đều): cấu hình khác nhau ≤ 1 bậc ở mỗi chiều
  đang quét (Chebyshev-1).
- Điểm plateau = thống kê `stat` (NÊN P25, không dùng max) của `objective` trên lân cận (gồm chính
  nó), yêu cầu ≥ `min_neighbors` lân cận hợp lệ (qua gates).
- Ứng viên = tâm các vùng có điểm plateau cao, không phải ô đơn lẻ nổi bật; ưu tiên điểm giữa vùng.
- Xếp hạng trên Train; xác nhận trên Validation theo luật khai trước. `objective`, ngưỡng: **TBD**.
- Kiểm chéo (báo cáo, chưa làm luật loại ở v1): symbol khác, giai đoạn khác; lưu ý các chỉ số
  cùng khu vực tương quan nhau.

### 6.5 Walk-forward v1: cửa sổ độc lập
- Là **một loại plan** (`plan.py`) + tổng hợp (`selection.py`), không phải engine riêng.
- Mỗi cửa sổ: grid trên IS → áp `selection_rule` → chạy cấu hình chọn trên OOS.
- Mỗi OOS run **reset balance, vị thế, lệnh chờ** (`independent_window=true`): đo hiệu năng
  trong regime mới với giả định tái khởi động, **không** phải tài khoản live liên tục.
- Lưu `engine_start` và `evaluation_start` (hiện Combo không cần warm-up vì chỉ báo tính sẵn
  trong CSV; dành cho cấu phần trend khung lớn sau này — khi đó cần tham số bot kiểu
  `EvaluationStart`, Combo đã có sẵn khái niệm `before-start`).
- Vị thế còn mở cuối cửa sổ: tính theo equity cuối report (unrealized), ghi số lượng.
- **Boundary sensitivity**: dịch biên ±N ngày (N TBD) và báo chênh lệch; nếu lớn ⇒ cân nhắc
  lịch tham số liên tục trong bot (không làm ở v1).
- Báo cáo: OOS nối (không cộng dồn balance giữa cửa sổ — dùng lợi suất), WFE, tỉ lệ cửa sổ OOS dương.

### 6.6 FTMO screening
- `ftmo_profile` là JSON có version (`profiles/ftmo/ftmo-2step-v1.json`): loại tài khoản,
  daily loss %, max loss %, kiểu max loss (static/trailing), mốc tính (balance đầu ngày / balance
  ban đầu), **timezone reset (Europe/Prague, có DST)**, nguồn tài liệu + ngày đối chiếu. Không
  hardcode. (Account <FTMO-ACCOUNT-ID> chưa rõ loại — TBD.)
- Tính từ `equity.points` (theo giờ, `minEquity` là thấp nhất trong giờ): ngày theo mốc Prague;
  daily loss dùng `minEquity` trong ngày so với mốc profile; max loss tương tự trên toàn run.
- Phân loại: `observed_breach` (loại) · `screen_pass` (không thấy ở độ phân giải hiện có) ·
  `certified` (chưa có ở v1 — cần dữ liệu equity/event chi tiết hơn + profile đã xác minh).
- Task xác minh: 1 run bật `EnableDailyLossLimit` của bot, so thời điểm bot tự dừng với thời điểm
  screening phát hiện; xác nhận `minEquity` tính theo tick.
- **Lưu ý lệch mốc ngày**: bảo vệ tài khoản trong bot hiện reset lỗ ngày theo **ngày UTC**
  (`[Robot(TimeZone = UTC)]`, log "halted until the next UTC day"), còn FTMO reset 00:00 Prague.
  Screening của research/ PHẢI dùng mốc của `ftmo_profile`; sự lệch giữa bot và FTMO là vấn đề
  thiết kế bot (§11), không sửa trong research/.

### 6.7 Monte Carlo v1
- Đầu vào: timeline equity (`equity.points`) của cấu hình đã chọn (OOS nối hoặc run full).
- Đơn vị lấy mẫu: **khối ngày liên tiếp** theo mốc ngày FTMO (giữ nguyên lệnh chồng nhau, cụm
  regime, và đường equity trong ngày ⇒ tính được lỗ ngày). Lợi suất nhân (sizing theo % balance).
- Block bootstrap (độ dài khối TBD, NÊN 5–10 ngày), N path (NÊN 10.000), `seed` ghi trong kết quả.
- Đầu ra: phân phối max DD, chuỗi ngày thua dài nhất, P(vi phạm daily), P(vi phạm max loss),
  (tuỳ chọn) thời gian tới mục tiêu lợi nhuận.
- Không đo được: độ nhạy spread/slippage/nhiễu tick ⇒ lớp stress riêng (§6.8).

### 6.8 Stress thực thi (sau v1)
- Commission: CLI `--commission` được (profile chi phí khai trước, chạy lại ứng viên).
- Spread/slippage/nhiễu tick: chỉ qua **custom data source của Plugin API**
  (`Backtesting.DataSources.Add`, có trong 5.9.16) — lý do duy nhất để mở lại plugin. Chưa làm.

---

## 7. Cấu trúc một experiment

```text
runs/research/<method>/<experiment>/
├─ experiment.json          khoá khi bắt đầu
├─ index.sqlite             nguồn sự thật state/cache của experiment
├─ summary.json             leaderboard + gates + kết luận (do store ghi từ kết quả selection)
├─ inputs/
│  ├─ <algo-sha>.algo
│  └─ <signal-sha>.csv
└─ runs/<run-id>/
   ├─ spec.json
   ├─ execution.json        §4.2
   ├─ report.json.gz        report thô của cTrader (nén, đọc lại được)
   ├─ bot.log               CHỈ khi golden / lỗi / có cờ xấu
   └─ cli.log               CHỈ khi lỗi
```
Không tạo mặc định `index.csv`, `metrics.json`, `ledger.json`, bản copy log; export khi cần là
thao tác read-only riêng.

**Dọn instance cTrader**: `runner.py` chỉ xoá thư mục `Data\cBots\<bot>\<guid>\` mà nó chứng minh
được do chính invocation tạo ra (NÊN: `parameters.cbotset` trong đó trùng byte với cbotset runner
đã ghi + mtime nằm trong khung thời gian invocation), sau khi đã lấy file cần giữ. Không quét
theo timestamp chung, không xoá thư mục cũ (923 thư mục hiện có: người dùng tự quyết).
Retention policy: TBD.

---

## 8. Đầu ra (release)

- `.cbotset` của cấu hình chọn (enum ghi theo **chỉ số thứ tự**, có block `Chart`) — người dùng
  nạp vào GUI, chạy lại ⇒ phải trùng CLI (khép vòng parity).
- `strategy-config.json` trung lập cho OF: tên tham số theo metadata, enum theo **tên**
  (`Fib0618`) + chỉ số, đơn vị rõ ràng, `algo_sha`, `engine_profile`, experiment nguồn, zone/WFO
  đã qua, kết quả gates, ngày chốt.

---

## 9. Thứ tự triển khai & tiêu chí nghiệm thu

| Bước | Nội dung | Nghiệm thu (PHẢI đạt) |
|---|---|---|
| S0 | Duyệt doc; soạn `profiles/engine`, `profiles/ftmo`, `profiles/strategy` (combo-v1, macross-v1); **người dùng tạo golden G1–G4** | file profile có version + nguồn; 4 fixture đủ file + `meta.json` |
| S1 | `models.py`, `catalog.py`, `plan.py` + test; spike độ sâu tick trước 2025 | plan tất định (cùng config ⇒ cùng RunSpec/hash); catalog fail-closed khi metadata có tham số ngoài profile; zones không chồng, embargo đúng; báo cáo spike tick |
| S2 | `store.py`, `runner.py` + test port | 1 RunSpec chạy thật → artifact đúng §7; resume không chạy lại run ok; snapshot input dùng thật; holdout guard từ chối run giao final test; đếm ngân sách đúng; không credential trong argv/log |
| S3 | `evidence.py` + parity gate | G1, G2, G4 trùng `history.items` mọi trường; G3 cho `margin_rejections>0`; ca signal thiếu ⇒ `signal_ok=false`; `env_drift` phát hiện được khi sửa tay `meta` |
| S4 | `selection.py` | rank + min-trades + FTMO screen (có task xác minh §6.6) + plateau-v1 trên lưới 72 pass US30 |
| S5 | WFO qua `plan.py` + `api.py` | cửa sổ đúng khai báo; OOS reset đúng; báo boundary sensitivity |
| S6 | `montecarlo.py` | seed cố định ⇒ kết quả lặp lại; P(breach) khớp kiểm tay trên dữ liệu giả |
| S7 | export `.cbotset` + `strategy-config.json` | `.cbotset` nạp GUI chạy ra trùng CLI (người dùng xác nhận) |

Mỗi bước: port test tương ứng từ `pipeline/tests`, chạy toàn bộ test trước khi sang bước sau,
kèm report thực nghiệm ngắn trong `bo_workflow/reports/`.

---

## 10. Ngoài phạm vi v1
Plugin backend; stress qua custom data source; tự động hoá GUI; PreciseConversion (chờ Spotware);
lịch tham số liên tục trong bot; hiện thực OF; đa tài khoản; CPCV/Deflated Sharpe đầy đủ (v1 chỉ
cần lưu ngân sách thử để tính sau).

## 11. Quyết định còn mở (không chặn code, chặn chạy nghiên cứu thật)
1. Vùng dữ liệu "chưa bị nhìn" + OG đã tinh chỉnh trên đoạn nào (§6.2).
2. Độ sâu tick trước 2025 (§6.3, spike S1).
3. `objective`, ngưỡng min-trades/DD, độ dài khối MC, N boundary sensitivity.
4. Loại tài khoản FTMO đích + nội dung `ftmo_profile` theo tài liệu FTMO hiện hành.
5. Retention/dọn `Data\cBots` và kết quả cũ trong `runs/backtest/`.
6. Bảo vệ tài khoản trong bot reset theo ngày UTC ≠ FTMO (00:00 Prague) — có chỉnh bot không
   (thuộc thiết kế cBot, ngoài research/).

## 12. Ràng buộc bảo mật & vận hành (PHẢI)
- Không đọc/in/copy nội dung `C:\Users\Administrator\.ctrader-cli-pwd.txt` — chỉ truyền đường dẫn.
- Không sửa `Combo/`, `MA Cross/`, `*.algo` khi chưa được người dùng cho phép; research/ không
  build `.algo`.
- Chỉ kill process theo PID mình tạo; không `Stop-Process`/`taskkill` theo tên.
- Không commit/đẩy dữ liệu tài khoản; report đã redact trước khi lưu.
