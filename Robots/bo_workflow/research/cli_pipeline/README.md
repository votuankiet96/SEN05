# Pipeline backtest cTrader CLI (không cần GUI)

File chính: `Invoke-CliBacktest.ps1`. Pipeline hiện chạy trên
`ctrader-cli 5.9.0.38`, account demo FTMO `7563609`, broker được chỉ rõ là
`FTMO Platform`.

## Luồng chuẩn

```text
Input có cấu trúc
  -> kiểm tra không còn ctrader-cli.exe cũ
  -> kiểm tra .algo, ngày, timeframe
  -> Resolve-CliSymbol (không đoán tên symbol)
  -> đóng gói tham số cBot vào params.cbotset
  -> chạy backtest tuần tự với --full-access
  -> poll report.json
  -> parse JSON + đối chiếu testingPeriod
  -> lưu raw artifact và run-summary.json
  -> grid / so sánh CSV + JSON + Markdown
```

Tín hiệu hoàn tất đáng tin cậy duy nhất là `report.json` đã xuất hiện, parse
được và `testingPeriod` khớp đúng ngày yêu cầu. Trạng thái PowerShell job, CPU
đứng yên hoặc dòng progress không được dùng để kết luận backtest đã xong.
`ctrader-cli.exe` đôi khi không tự thoát dù report đã được ghi xong; pipeline
sẽ thu log rồi dọn đúng tiến trình của lượt đó.

## Input của một lượt backtest

| Input | Ý nghĩa |
|---|---|
| `AlgoPath` | File `.algo` đã được build trong cTrader IDE |
| `Symbol` | Tên hoặc từ khóa; luôn được xác nhận bằng `Resolve-CliSymbol` |
| `Period` | Timeframe cTrader, ví dụ `h4`, `m30` |
| `StartDate`, `EndDate` | Nên truyền ISO `yyyy-MM-dd`; pipeline đổi sang `dd/MM/yyyy` cho CLI |
| `DataMode` | `Ticks` hoặc `M1` |
| `Params` | Hashtable tham số cBot, gồm `SignalFilePath`, `RiskPercent`... |
| `TimeoutMinutes` | Trần chờ `report.json`; dữ liệu tick dài cần đặt đủ lớn |
| `PollSeconds` | Chu kỳ kiểm tra report/tiến trình |

Combo và MA Cross dùng `AccessRights.FullAccess` để đọc CSV ngoài workspace,
do đó pipeline luôn truyền `--full-access`. CSV signal vẫn là input của cBot;
CLI cung cấp dữ liệu giá/tick từ server FTMO theo `DataMode`.

Ví dụ một lượt:

```powershell
. "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\research\cli_pipeline\Invoke-CliBacktest.ps1"

$run = Invoke-CliBacktest `
    -AlgoPath ".\Combo.algo" `
    -Symbol "US30.cash" `
    -Period "h4" `
    -StartDate "2024-01-01" `
    -EndDate "2026-09-01" `
    -DataMode "Ticks" `
    -Params @{
        SignalFilePath = "Z:\Desktop\og_program\runtime\exports\combo_US30_H4_full_history_signals.csv"
        RiskPercent = 1.0
    } `
    -TimeoutMinutes 90

$run.Success
$run.RunSummaryPath
```

Không chạy lượt mới nếu còn bất kỳ `ctrader-cli.exe` nào. Pipeline cũng tự
kiểm tra điều này ở cả bước resolve symbol và trước khi backtest.

## Artifact của từng lượt

Mỗi lượt dùng folder riêng có timestamp đến giây, không ghi đè:

```text
research/cli_runs/<Bot>_<Symbol>_<TF>_<yyyyMMdd-HHmmss>/
    params.cbotset    tham số override đã truyền
    command.txt       toàn bộ argument CLI để audit/tái lập
    log.txt           console/log cBot và lỗi runtime
    report.json       báo cáo raw của cTrader, gồm tổng hợp và history.items
    run-summary.json  index chuẩn hóa input, hash .algo/CSV, kỳ thực tế, metrics
```

`run-summary.json` lưu SHA-256 của `.algo` và CSV signal (nếu file tồn tại),
nhờ đó hai lượt dùng file cùng tên nhưng nội dung khác nhau vẫn được phát hiện.
Nội dung file password không được đọc hay sao chép vào artifact.

## Quét tham số tuần tự

CLI 5.9 không có optimizer batch tích hợp trong command hiện có. Hàm
`Invoke-CliBacktestGrid` tạo Cartesian product, chạy từng tổ hợp tuần tự và
checkpoint sau mỗi lượt. `MaxRuns` là chốt an toàn chống tạo grid quá lớn.

```powershell
$grid = Invoke-CliBacktestGrid `
    -AlgoPath ".\Combo.algo" `
    -Symbol "UK100.cash" -Period "h4" `
    -StartDate "2025-01-01" -EndDate "2025-04-01" `
    -DataMode Ticks `
    -BaseParams @{
        SignalFilePath = "Z:\Desktop\og_program\runtime\exports\combo_UK100_H4_full_history_signals.csv"
    } `
    -ParameterGrid @{
        RiskPercent = @(0.5, 1.0)
        KslLevel = @("Fib0500", "Fib0618")
    } `
    -MaxRuns 4
```

Kết quả grid nằm tại:

```text
research/cli_batches/<Bot>_<Symbol>_<TF>_grid_<timestamp>/
    grid-summary.csv
    grid-summary.json
```

Mỗi row trỏ ngược về folder raw trong `cli_runs`. `BestRun` hiện xếp theo
`NetProfit`; đây mới là parameter grid, chưa phải Walk-Forward Optimization.

## So sánh kết quả

`Export-CliBacktestComparison` nhận ít nhất hai folder `cli_runs`, đọc trực
tiếp `report.json`, chuẩn hóa và xếp theo Net Profit:

```powershell
$comparison = Export-CliBacktestComparison `
    -RunDirectories @(
        "research\cli_runs\Combo_US30.cash_h4_<run-1>",
        "research\cli_runs\Combo_US30.cash_h4_<run-2>"
    ) `
    -Name "Combo_US30_before_after"
```

Output:

```text
research/cli_comparisons/<name>_<timestamp>/
    comparison.csv
    comparison.json
    comparison.md
```

Pipeline chỉ đánh dấu `Comparable=YES` khi bot, symbol, timeframe, ngày bắt
đầu/kết thúc, data mode và vốn đầu kỳ đều giống nhau. Bảng gồm Net Profit,
ROI, Profit Factor, số lệnh, win rate và max equity drawdown.

Đây là lớp so sánh performance. Kiểm định fidelity chiến lược
`signal -> đặt lệnh -> fill/reject/expire -> đóng vị thế` phải đọc thêm CSV,
`log.txt` và các mảng `history/items` trong report bằng parser riêng cho từng
cBot, vì log Combo (pending) và MA Cross (market) có semantics khác nhau.

## Sự cố UK100 đã được giải quyết

Hiện tượng UK100 dừng ở `03/01/2025 21:00 / 71.88%` không phải thiếu tick,
deadlock hay route nhầm broker. Pipeline cũ format ngày theo `MM/dd/yyyy`,
trong khi CLI yêu cầu `dd/MM/yyyy`: `--end=04/01/2025` bị hiểu là 4/1/2025,
không phải 1/4/2025. Vì vậy nến cuối ngày 3/1 là đúng lịch phiên.

Pipeline đã sửa sang `dd/MM/yyyy`, bỏ hoàn toàn retry cộng/trừ một ngày và
kiểm tra kỳ thực tế trong report. Lượt xác minh UK100 H4 từ 1/1 đến 1/4/2025
đã tạo report hợp lệ, kết thúc đúng kỳ và log đến 1/4/2025.

Cache dưới namespace `Spotware` không chứng minh lệnh chạy qua tài khoản
Spotware: cache US30 do CLI tạo cũng nằm trong namespace đó. Account được
CLI xác nhận là FTMO `7563609`, và mọi lệnh hiện truyền rõ
`--broker="FTMO Platform"`.

## Phần chưa hoàn thiện

- Walk-Forward đầy đủ: chia rolling IS/OOS, chọn tham số trên IS, chạy OOS và
  nối equity OOS.
- Parser fidelity tự động riêng cho Combo và MA Cross.
- Chính sách ranking đa mục tiêu (lợi nhuận, drawdown, độ ổn định), thay cho
  `BestRun` chỉ theo Net Profit.
