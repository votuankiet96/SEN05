# Đối chứng signal → thực thi — đợt 2026-09-05 (Combo BTCUSD/UK100 + MA Cross 8 symbol)

Mục tiêu: xác nhận cả 2 cBot **đặt lệnh / entry / SL / TP đúng theo cấu trúc
thiết kế chiến lược**, từ signal CSV tới thực thi thật trên backtest tick. Không
tối ưu tham số, không đánh giá chất lượng chiến lược — chỉ kiểm định fidelity.

## Cấu hình chung (mọi lượt)

| | |
|---|---|
| CLI | standalone `ctrader-cli 5.9.0.38`, broker `FTMO Platform`, account demo `7563609` |
| Kỳ test | 01/01/2026 → 28/02/2026 (2 tháng), `--data-mode=Ticks` (`tickDataFromServer`) |
| Vốn | USD 10,000 |
| Tham số bot | `KslLevel=2` → **SL = 1.0 × ATR**; `KtpLevel=4` → **TP = 2.618 × ATR**; `RiskPercent=1%`; `MaxMarginPercent=50%` Equity/lệnh; 3 FTMO guard OFF |
| Algo | `Combo.algo` (build 04/09), `MA Cross.algo` (build 05/09) — bản có `SlFibLevel`/`TpFibLevel` tách rời + `CapVolumeByMargin` + `ReconcileExistingExposure` |
| Commission/spread | 0 (mặc định CLI) — đây là kiểm tra THỰC THI, không phải hiệu suất đủ chi phí |

MA Cross: khung thời gian chọn ngẫu nhiên (seed 20260905) trong {m10, m20, m30, m45};
signal CSV khớp đúng khung. Combo cố định H1.

## Kết quả 10 lượt

| # | Bot | Symbol (CLI) | TF | Net P/L | ROI | PF | Lệnh đóng | Signal trong kỳ | Placed | Reject/Fail | Reversed | Margin-capped |
|--:|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | Combo | BTCUSD | H1 | -116.22 | -1.16% | 0.94 | 71 | 113 | 107 | 0 | 21 | **104** |
| 2 | Combo | UK100.cash | H1 | +150.47 | +1.50% | 1.04 | 50 | 78 | 72 | 0 | 9 | 12 |
| 3 | MA Cross | BTCUSD | m20 | +331.11 | +3.31% | 1.19 | 120 | 122 | 120 | 0 | 8 | **120** |
| 4 | MA Cross | GER40.cash (DE40) | m30 | -1363.41 | -13.63% | 0.63 | 56 | 56 | 56 | 0 | 3 | 21 |
| 5 | MA Cross | FRA40.cash (FR40) | m10 | -3226.26 | -32.26% | 0.59 | 133 | 137 | 133 | 0 | 22 | 99 |
| 6 | MA Cross | XAUUSD (GOLD) | m20 | -208.50 | -2.08% | 0.96 | 76 | 77 | 76 | 0 | 3 | 5 |
| 7 | MA Cross | JP225.cash | m45 | -71.89 | -0.72% | 0.97 | 36 | 36 | 36 | 0 | 0 | 1 |
| 8 | MA Cross | SPN35.cash (SP35) | m45 | +36.11 | +0.36% | 1.03 | 20 | 20 | 20 | 0 | 1 | 2 |
| 9 | MA Cross | US100.cash | m10 | +4491.54 | +44.92% | 1.54 | 130 | 130 | 130 | 0 | 2 | 84 |
| 10 | MA Cross | US500.cash | m20 | -1616.86 | -16.17% | 0.62 | 82 | 86 | 82 | 0 | 6 | 55 |

*(P/L chỉ là 1 cửa sổ 2 tháng, không có ý nghĩa đánh giá chiến lược. US100 m10 +44.92%
là một outlier ngắn hạn — không kết luận gì từ đó.)*

## Đối chứng fidelity (3 nguồn độc lập cho MỖI lượt)

Với từng lượt, đối chiếu **log.txt** (dòng Print của bot) ↔ **events.json** (order/fill
/exit thật của cTrader) ↔ **OnStop summary** (counter nội bộ bot). Kết quả:

- **10/10 lượt: mọi con số khớp tuyệt đối.** `placed = Σ(các Outcome đặt lệnh)`;
  `pending-expired`, `same-direction-skipped`, `reversed`, `margin-capped` từng cái
  bằng đúng số dòng log tương ứng và bằng phân rã events.json.
- **10/10 lượt: SL/TP của mọi lệnh đặt khớp công thức thiết kế** `Entry ± {1.0|2.618}×ATR`
  (Combo, giá tuyệt đối) / `{1.0|2.618}×ATR/PipSize` pips (MA Cross) — so trực tiếp với
  giá trị bot tự log, sai khác < 0.01 (hoặc < 0.1% với pips).
- **0/10 lượt có lệnh sai hướng, sai entry, hoặc signal bị bỏ sót** (`not-processed`
  chỉ gồm signal có `AvailableTime` > cuối kỳ test — hợp lệ).
- **Combo (Pending Stop)**: `Create Stop Order = Filled + Cancelled (+ ≤1 treo cuối kỳ)`;
  `Cancelled = hết-hạn-3-nến + huỷ-do-đảo-chiều`. Khớp cho cả BTCUSD và UK100.
- **MA Cross (Market Order)**: khớp ngay, không pending/expiry; `reversed` = số lần
  đóng vị thế ngược hướng trước khi vào lệnh mới. **0 lệnh `NoMoney`/`NOT_ENOUGH_MARGIN`
  trên cả 8 symbol** — khác hẳn bản cũ (pre-fix, US30 từng 251/561 reject) vì
  `CapVolumeByMargin` giờ cắt volume TRƯỚC khi gửi lệnh.

File đối chứng signal-by-signal (1 dòng/signal, Outcome + Notes tiếng Việt):

| Bot | File |
|---|---|
| Combo | `combo-BTCUSD-jan-feb2026-signal-trace-2026-09-05.csv` |
| Combo | `combo-UK100-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-BTCUSD-m20-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-DE40-m30-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-FR40-m10-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-GOLD-m20-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-JP225-m45-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-SP35-m45-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-US100-m10-jan-feb2026-signal-trace-2026-09-05.csv` |
| MA Cross | `macross-US500-m20-jan-feb2026-signal-trace-2026-09-05.csv` |

Raw artifact mỗi lượt: `research/cli_runs/{Combo,MACross}_<Tag>_<tf>_ticks_2026JanFeb_20260905-185351/`
(report.json/html, events.json, log.txt, bot-log.txt, params.cbotset, run.ps1, input.json).

## Phát hiện đáng chú ý (đều là hành vi thiết kế đúng, không phải lỗi)

1. **BTCUSD bị margin-cap gần như mọi lệnh** — Combo 104/107 (97%), MA Cross 120/120 (100%).
   Đòn bẩy hiệu lực BTCUSD trên FTMO rất thấp (~1.2) → volume risk-based 1% luôn vượt
   trần 50% Equity → bị cắt xuống, risk thực chỉ ~0.1–0.3%. Đúng như CLAUDE.md đã ghi.
   `CapVolumeByMargin` hoạt động chính xác: cắt theo tỉ lệ, log rõ risk% mới, đếm counter.
2. **US100/US500/FR40 (m10-m20) margin-cap nhiều** (55–99 lượt) — các index này ở khung
   nhỏ có ATR hẹp → SL hẹp → volume risk-based lớn → chạm trần 50%. GER40/SP35/JP225
   (m30-m45, ATR rộng hơn) hầu như không bị cap.
3. **MA Cross không còn `NoMoney`** — điểm khác biệt lớn nhất so với các lượt archive
   trước 2026-09-02. Bug overlapping-position (thiếu `ReconcileExistingExposure`) cũng
   đã sạch: không lượt nào có 2 vị thế ngược hướng mở đồng thời.
4. **GOLD (Combo, đợt trước cùng ngày) có 2 `SkippedSizeTooSmall`** — không tái xuất ở
   BTCUSD/UK100 đợt này (ATR/PipSize khác). MA Cross GOLD m20 cũng không có.

## Kết luận

Cả `Combo.cs` và `MA Cross.cs` (bản đang build) **thực thi đúng cấu trúc thiết kế**:
signal → (reconcile exposure) → tính SL/TP theo `KSL/KTP × ATR` → tính volume theo
risk% → chặn margin theo `MaxMarginPercent` → đặt lệnh (Pending Stop cho Combo, Market
cho MA Cross) → (Combo) hết hạn 3 nến nếu không khớp. Không có sai lệch giữa thiết kế
và thực thi trên toàn bộ 10 symbol/khung đã kiểm.

Sau đợt này: **Combo phủ 11/11 symbol, MA Cross phủ 11/11 symbol** (3 symbol MA Cross
cũ + 8 mới đợt này; khung mỗi symbol có thể khác nhau — xem bảng).

## File CSV tổng hợp toàn bộ

`research/reports/ALL-signal-trace-summary-2026-09-05.csv` — **1 dòng cho mỗi lượt đối
chứng** (30 dòng: 22 "dedicated" 11 Combo + 11 MA Cross, + 8 "1wk-smoke" từ file gộp
11-symbol). Cột:

| Nhóm cột | Ý nghĩa |
|---|---|
| `Strategy, Symbol, SymbolCLI, Timeframe, Scope, TestWindow` | định danh lượt (`Scope` = `dedicated` \| `1wk-smoke`) |
| `SignalsInWindow` | số signal CSV nằm trong kỳ test |
| `PlacedOK` | số lệnh đặt thành công (tổng mọi Outcome dạng đặt lệnh) |
| `RejectFail` | broker từ chối / size-too-small (đều 0 trừ GOLD Combo = 2) |
| `PendingExpired` | (Combo) huỷ sau 3 nến; `n/a` cho MA Cross (market order) |
| `Reversed` | số lần đảo chiều (đóng vị thế / huỷ lệnh ngược hướng) |
| `SameDirSkip, MarginCapped, SizeTooSmall, NotFoundEdge` | phân loại còn lại |
| `FidelityStatus` | `PASS` — mọi lượt |
| `ReconMethod` | `3-source (log+events+OnStop)` cho 13 lượt CLI đợt này; `outcome-parse` cho phần kế thừa (đã verify ở phiên trước / smoke batch) |
| `NetProfit_USD, ROI_pct, ProfitFactor, ClosedTrades, Win, Loss` | performance (trống nếu không giữ raw run) |
| `OutcomeBreakdown` | chuỗi đầy đủ `Outcome=count` |
| `SignalTraceCSV, RawRunFolder` | trỏ tới file chi tiết + folder artifact |

Kiểm tra toàn vẹn (đã chạy): `PlacedOK + SameDirSkip + SizeTooSmall + NotFoundEdge =
SignalsInWindow` đúng cho cả 30 dòng.
