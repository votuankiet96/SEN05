# Audit: song song `ctrader-cli` (N login) & bug tick-hang JP225

Ngày: 2026-09-09
Tác giả: Claude (Sonnet 5)
Phạm vi: `bo_workflow/pipeline/` — engine chạy backtest/optimize qua `ctrader-cli` standalone 5.9.0.38.

## 0. Bối cảnh

Người dùng nêu 2 phản biện sau khi pipeline được chuyển sang chạy song song và tôi báo "JP225 tick optimize không chạy được":

1. **"16 luồng không có nghĩa là 16 đăng nhập cùng lúc"** — nghi ngờ thiết kế đang mở N session dư thừa.
2. **"GUI chạy tốt thì CLI phải chạy tốt hơn chứ, dữ liệu tick đã có sẵn rồi"** — nghi ngờ kết luận "CLI treo với JP225".

Cả 2 đều đáng audit. Kết quả dưới đây dựa trên thực nghiệm trực tiếp (không suy đoán).

---

## 1. Điểm 1 — "N luồng = N đăng nhập": ĐÚNG, nhưng KHÔNG tránh được với `ctrader-cli`

### 1.1. Thiết kế hiện tại

`methods/backtest.py` dùng `ThreadPoolExecutor(max_workers=N)`. Mỗi worker gọi
`subprocess.Popen(["ctrader-cli", "backtest", <algo>, <cbotset>, "--start=…", "--end=…",
"--data-mode=…", "--balance=…", "--ctid=…", "--pwd-file=…", "--report-json=…", …])`.

→ **N worker = N tiến trình `ctrader-cli.exe` = N lần login riêng biệt** vào account <FTMO-ACCOUNT-ID>.
Người dùng nói đúng: đây không giống GUI (1 login, N worker thread nội bộ chia sẻ 1 kết nối + 1 dataset đã nạp).

### 1.2. Có cách nào 1-login-nhiều-backtest không? — Có, nhưng KHÔNG dùng được

`ctrader-cli` **interactive shell** (chạy `ctrader-cli` với credentials, không kèm command) giữ **1 login** và cho gõ nhiều lệnh tuần tự. Trong danh sách 42 lệnh của shell CÓ:

```
backtest <algo-file> <symbol> <period> <from> <to>
backtest <algo-file> <symbol> <period> <from> <to> <params>
```

**NHƯNG** — thử thật, shell `backtest` **từ chối mọi cờ `--x`**:

> `Unknown form for 'backtest'. Expected: backtest <algo-file> <symbol> <period> <from> <to> [<params>]`
> `The command was NOT launched: --name=value / -x flags are launch-time arguments and are rejected inside the shell.`
> `For cBot parameter overrides pass a single quoted positional with comma-separated Name=Value pairs`

Shell `backtest` **không nhận** `--data-mode`, `--balance`, `--report-json`, `--commission`, `--spread`.
Không set được chế độ dữ liệu (Ticks vs m1), không set vốn, không chỉ định nơi ghi report
→ **vô dụng cho pipeline** (những tham số đó là bắt buộc).

### 1.3. Vì sao GUI khác

GUI/Desktop là app tích hợp: 1 connection, engine optimize tự fork worker thread, tất cả chia sẻ
tick dataset đã nạp trong RAM. `ctrader-cli` standalone **không có lệnh `optimize`** (đã xác nhận
nhiều lần — help chỉ có `backtest`/`run`), và không expose cơ chế đó.

### 1.4. Kết luận điểm 1

**N-login là ràng buộc của `ctrader-cli`, không phải lựa chọn thiết kế.** Cách duy nhất có đủ cờ
điều khiển là gọi `ctrader-cli backtest` non-interactive, mỗi lần = 1 process = 1 login.

**Thực nghiệm cho thấy N-login KHÔNG gây lỗi:**

| Test | Login đồng thời | Kết quả |
|---|---|---|
| Grid US30, `max_parallel=16` | 16 | **16/16 OK, 0 lỗi**, wall 97s (vs ~560s tuần tự = **5.8x**) |
| 3-way / 4-way | 3–4 | OK |
| PS pipeline cũ (2026-09-02) | 10 | 100/100 tổ hợp, 0 failed |

`settings.MAX_PARALLEL` hạ từ 16 xuống **12** (thận trọng — máy còn chạy cTrader Desktop; mỗi
`ctrader-cli` ~570 MB, CPU bão hoà ~16 nên >16 không tăng throughput).

---

## 2. Điểm 2 — "GUI chạy tốt thì CLI phải chạy tốt": bug tick-hang JP225 LÀ THẬT

### 2.1. Nghi ngờ ban đầu của tôi (đã loại)

Tôi đã nghi 3 nguyên nhân "không phải bug CLI" và **loại từng cái bằng thực nghiệm**:

| Nghi ngờ | Cách loại | Kết quả |
|---|---|---|
| Session exhaustion (do tôi test 20-way trước đó) | Chạy **1 process duy nhất**, account đã yên, 0 tiến trình `ctrader-cli` khác | Vẫn treo |
| File signal `combo_J225…csv` lỗi | Chạy lại với `combo_US30_H1…csv` (đúng file GUI optimize đã dùng) | Vẫn treo y hệt |
| Tick cache lạnh / đang tải | Theo dõi `IO_ReadBytes` của process | **= 0 KB suốt** — CLI crash TRƯỚC khi đọc cache |

### 2.2. Ma trận thực nghiệm JP225.cash / h1

| Kỳ | `--data-mode=Ticks` | `--data-mode=m1` |
|---|---|---|
| 1 tuần (01–08/01/2025) | ✅ 30s | — |
| 1 tháng (01–31/01/2025) | ✅ 110s (Progress bò chậm 3%→30%→96%) | — |
| **3 tháng (01/01–01/04/2025)** | **❌ Progress kẹt 1%, CPU idle, IO=0, crash ~50s, KHÔNG ra report** | ✅ **30s** |
| 1 năm (optimize) | ❌ treo | (chưa thử — dự kiến OK theo m1 3 tháng) |

Đối chứng: **US30.cash + Ticks + 8 tháng → ✅ 130s** (khớp GUI từng số — xem `lotsize-sizing-work-summary`).
→ Bug **đặc thù JP225.cash + Ticks + kỳ ≥ 3 tháng**, không phải lỗi tick engine chung.

### 2.3. Vì sao GUI không dính

- `ctrader-cli` standalone cài qua winget: **5.9.0.38**.
- cTrader Desktop / GUI: **5.9.10**.
- **Hai codebase/engine khác nhau.** CLI có bug ở đường tick-backtest cho một số symbol.
- Đã có tiền lệ: `leverage-pipvalue-crosscheck-2026-09-04` ghi "**HK50** — cả 2 lần thử đều bị chính
  `ctrader-cli` tự abort / crash (`System.InvalidOperationException: Message expected`) — không phải
  lỗi máy này". Nay xác nhận thêm JP225.
- **"Tick data đã cache" không giúp** vì CLI crash ở giai đoạn setup (Progress 0–1%), trước khi chạm
  file cache (IO read = 0).

### 2.4. Xử lý trong code

Thêm `RunStatus.STALLED` + watchdog trong `runner._run_cli`: nếu `Progress %` không nhích trong
**240s** → `_kill_tree(pid)`, đánh dấu `stalled`, pool chạy tiếp tổ hợp khác (không treo cả mẻ).
`reason` gợi ý `data_mode='m1'`.

---

## 3. Pipeline làm được gì / không làm được gì

| Kịch bản | Pipeline |
|---|---|
| Backtest/optimize **US30, US500, US100, GOLD, BTC, GER40, FR40, SP35, UK100** — Ticks, kỳ dài | ✅ (đã verify US30 khớp GUI; các symbol khác cần test tick-hang từng cái) |
| Backtest/optimize **JP225, HK50** — Ticks | ❌ CLI treo với kỳ ≥ ~3 tháng → **chạy trên GUI** |
| Mọi symbol — `data_mode='m1'` | ✅ nhanh, song song 12–16, **nhưng fill theo nến M1** → số KHÔNG khớp GUI-ticks (chấp nhận được cho việc XẾP HẠNG tổ hợp trong optimize) |
| Song song | ✅ 12 (mặc định), 16 verified OK; >16 vô ích (CPU-bound) |
| Resume / chống chạy trùng | ✅ `param_hash` |
| So sánh chéo / lưới KSL×KTP | ✅ `analysis.grid()` / `rank()` |

---

## 4. Khuyến nghị

1. **Optimize JP225 khớp GUI (ticks):** chạy trên GUI. `ctrader-cli` standalone không làm được.
   Đọc kết quả GUI optimize qua filesystem như thường (`Data\cBots\Combo\<instance>\Optimization\<n>\`).
2. **Optimize JP225 qua pipeline (chấp nhận sai lệch):** `data_mode='m1'`, `max_parallel=12`,
   `notebooks/optimize.ipynb` (đã đổi sẵn). ~72 tổ hợp / 12 luồng × ~30–60s ≈ 5–10 phút.
   Dùng để LỌC nhanh vùng tham số, rồi verify top vài tổ hợp trên GUI-ticks.
3. **Các symbol US-index / GOLD / BTC:** pipeline + Ticks + `max_parallel=12–16` — chạy nền.
   Test 1 backtest 3-tháng/symbol trước để chắc không dính tick-hang.
4. **Không tăng `max_parallel` quá 16** — CPU đã bão hoà, chỉ chậm thêm.

---

## 5. Nhật ký bằng chứng (2026-09-09)

- 16-way US30 grid: `runs/backtest/` (đã xoá sau test), 16/16 `ok`, wall 97s.
- Shell `backtest` từ chối cờ: output `"--name=value flags are rejected inside the shell"`.
- JP225 3-month Ticks, run sạch: Progress 0%→1.06%→(crash), `IO_ReadBytes` delta = 0 KB/15s, CPU +1s/15s.
- JP225 3-month Ticks + `combo_US30_H1` CSV: treo y hệt (Progress kẹt 1% sau 105s).
- JP225 3-month **m1**: `OK 30s, period=3m, net=-2351.96`.
- US30 8-month Ticks: `OK 130s` (phiên trước, khớp GUI).
- `ctrader-cli --commands`: shell có lệnh `backtest`, `run`, `candles`, `metadata`; KHÔNG có `optimize`.

Phiên bản: `ctrader-cli.exe` 5.9.0.38 (`C:\Users\Administrator\AppData\Local\Programs\cTrader CLI\`);
cTrader Desktop 5.9.10. Máy VM-BO20: 2× Xeon E5-2696 v4 (32 logical CPU), 48 GB RAM.
