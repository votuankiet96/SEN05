# Script kiểm chứng `ctrader-cli backtest` — chạy tay từng lệnh (2026-09-01)

## Bối cảnh

Đã thử tự động hoá `ctrader-cli backtest` cho Combo (US30/H4) + MA Cross
(US30/M30), nhưng **CẢ 2 đều crash tại đúng 0.00% tiến trình** với lỗi:

```
Message expected
System.InvalidOperationException: Message expected
   at cTrader.Console.Infrastructure.StateMachine.Strategies.BacktestReportSavingStateStrategy.DoEnter()
```

**Đã cô lập bằng 3 thử nghiệm loại trừ** — lỗi này **không liên quan** tới:
Combo/MA Cross, `--data-mode` (thử cả `Ticks` và `M1`), độ dài khoảng ngày
(thử cả 20 tháng và 1 tháng). Kể cả **1 cBot hoàn toàn mặc định** (tạo mới
qua `ctrader-cli create cbot`, build qua `ctrader-cli build`, không cần
`--full-access`, không tham số nào) **cũng crash y hệt**.

Nghi vấn hàng đầu: **lệch phiên bản** — `ctrader-cli` standalone đang cài là
bản **5.9.0.38**, trong khi cTrader Desktop đã lên **5.9.10.52700**. Đã thử
bản `ctrader-cli.exe` đi kèm ngay trong thư mục cài Desktop (khớp đúng
5.9.10) nhưng exit ngay (exit code 3, không có output/lỗi gì) dù cTrader
Desktop đang chạy sẵn — chưa rõ nguyên nhân, chưa điều tra thêm.

**Chưa thử**: chạy qua **shell tương tác** của `ctrader-cli` (không phải chế
độ 1-lệnh qua `--flag=value` tôi đã dùng) — cú pháp shell tương tác dùng
tham số VỊ TRÍ (`backtest <algo-file> <symbol> <period> <from> <to>`), khác
hẳn cú pháp `--start=/--end=` tôi đã thử — có thể là 1 code path khác, chưa
chắc dính cùng lỗi. Đây là hướng đáng thử nhất trong script dưới đây.

## Thông tin cần có sẵn

- File mật khẩu: `C:\Users\Administrator\.ctrader-cli-pwd.txt` (đã có sẵn từ
  trước, KHÔNG cần tạo lại).
- cTID: `votuankiet96@gmail.com`
- Account number: `7563609`
- `.algo` đã build sẵn tại: `C:\Users\Administrator\Documents\cAlgo\Sources\Robots\Combo.algo`
  và `...\MA Cross.algo` (build lúc 01/09 tối, mới nhất).

## Phần A — Thử qua shell tương tác (CHƯA TEST, ưu tiên thử trước)

Mở PowerShell, gõ để vào shell tương tác (không kèm command nào phía sau):

```powershell
ctrader-cli --ctid=votuankiet96@gmail.com --pwd-file="C:\Users\Administrator\.ctrader-cli-pwd.txt" --account=7563609
```

Sau khi vào được shell (thấy prompt), gõ lần lượt (Enter sau mỗi dòng, xem
kết quả trước khi gõ dòng tiếp theo):

```
backtest "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\Combo.algo" US30 h4 01/01/2025 08/30/2026
```

Nếu lệnh trên hỏi thêm tham số (SignalFilePath, RiskPercent...) — điền:
- SignalFilePath: `Z:\Desktop\og_program\runtime\exports\combo_US30_H4_full_history_signals.csv`
- RiskPercent: `0.5`
- Các tham số khác: Enter bỏ trống để dùng default.

**Ghi lại**: có xuất hiện đúng lỗi "Message expected" giống bên dưới không,
hay ra kết quả thật/lỗi khác?

## Phần B — Nếu Phần A cũng lỗi: xác nhận lại đúng lỗi đã gặp (đối chứng)

Thoát shell tương tác (gõ `exit` hoặc Ctrl+C), chạy lại đúng lệnh 1-lệnh đã
biết là lỗi, để chắc chắn 2 bên đang nói về cùng 1 hiện tượng:

```powershell
ctrader-cli backtest "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\Combo.algo" --start="01/01/2025" --end="09/01/2026" --data-mode=M1 --ctid=votuankiet96@gmail.com --pwd-file="C:\Users\Administrator\.ctrader-cli-pwd.txt" --account=7563609 --symbol=US30 --period=h4 --full-access --SignalFilePath="Z:\Desktop\og_program\runtime\exports\combo_US30_H4_full_history_signals.csv" --RiskPercent=0.5
```

(Lưu ý: dùng `--end=09/01/2026` chứ không phải `08/30/2026` — riêng ngày đó
báo lỗi "can't be parsed" ngay từ bước đọc tham số, chưa rõ vì sao, các
ngày lân cận như 08/01, 09/01 đọc được bình thường.)

## Phần C — Nếu muốn thử bản CLI đi kèm Desktop (khớp version 5.9.10)

```powershell
& "C:\Users\Administrator\AppData\Local\Spotware\cTrader\abb70432efbee65d18af69e79fe8efe1\app_5.9.10.52700\x64\ctrader-cli.exe" --version
```

Nếu lệnh trên cũng không ra gì (exit code 3, giống tôi đã gặp) — bản này có
thể cần chạy từ trong chính cTrader Desktop (menu/công cụ nào đó) thay vì
gọi trực tiếp exe — bạn xem trong giao diện cTrader có mục nào liên quan
"CLI"/"Console"/"Command Line" không, báo lại nếu thấy.

## Báo lại kết quả

Chỉ cần copy/paste lại đúng phần output (kể cả lỗi) của từng lệnh đã thử —
không cần format lại, tôi sẽ đọc trực tiếp để chẩn đoán tiếp.
