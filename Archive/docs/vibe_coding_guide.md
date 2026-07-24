# SEN05 — Vibe Coding Guide

> Tài liệu này dành cho Claude và người không biết code cùng xây dựng lại hoặc
> mở rộng hệ thống SEN05. Đọc cùng với `README.md` — README là nguồn sự thật về
> **cái gì đang tồn tại**, file này là hướng dẫn về **cách tư duy và cách làm**.

---

## 1. Mô Hình Tư Duy (Mental Model)

Hệ thống là một **luồng thông tin một chiều**. Không cần hiểu code, chỉ cần hiểu
luồng này:

```
Thế giới (giá cả thật)
    │
    ▼
[KÉO VỀ & LƯU]        data_provider/
    │  Kéo nến từ TradingView → lưu vào SQL Server
    │
    ▼
[PHÂN TÍCH]           core_python/
    │  Đọc từ SQL → tính indicators → tìm tín hiệu → thông báo Telegram/Discord
    │
    ▼
[KIỂM TRA]            backtest_optimize/
    │  Đọc signal CSV → mô phỏng lệnh → tính metrics
    │
    ▼
[THỰC THI]            cbot_calgo/
       Thực hiện lệnh thật trong cTrader (ngoài Python runtime)
```

**Nguyên tắc vàng:** Phần sau KHÔNG được build trước phần trước. Data phải có
thật trong DB trước khi build strategy. Strategy phải có thật trong CSV trước
khi build backtest.

---

## 2. Cấu Trúc Dữ Liệu Cốt Lõi

Claude cần biết những "hình dạng" dữ liệu này để không bịa ra format khác:

### Nến (OHLCV)
```python
# DataFrame — đây là format duy nhất đi qua toàn hệ thống
df.index   = bar_time  # datetime UTC naive, sorted ascending
df.columns = ['open', 'high', 'low', 'close', 'volume']
```

### Signal row
```python
# Khi strategy trả về DataFrame, các cột thêm vào:
signal  # 1 = buy, -1 = sell, 0 = không có
entry   # float, NaN khi signal == 0
sl      # stop loss, float, NaN khi signal == 0
tp      # take profit, float, NaN khi signal == 0
```

### Strategy contract (bất biến)
```
signal ở bar đóng i → vào lệnh tại open của bar i+1
Nếu signal != 0: entry/sl/tp KHÔNG được NaN
Không mutate DataFrame đầu vào
```

---

## 3. Thứ Tự Build (Không Đảo)

```
GIAI ĐOẠN 1 — NỀN TẢNG DỮ LIỆU
  1a  SQL schema          → kho chứa nến
  1b  DB connector        → Python đọc/ghi SQL
  1c  TradingView client  → kéo nến về
  1d  Pipeline            → điều phối 1b + 1c, full/gap/dry-run
  1e  WS Live             → cập nhật liên tục (N bar mới nhất)
  1f  Checker             → kiểm tra chất lượng, tự sửa
  1g  Data dashboard      → xem warehouse bằng mắt (Flask port 8050)

GIAI ĐOẠN 2 — TÍN HIỆU
  2a  Data loader         → đọc Fact_OHLCV → DataFrame
  2b  Indicators          → tính MA/MACD/ATR/KNN lên DataFrame
  2c  Strategy logic      → detect signal, add entry/sl/tp
  2d  Strategy dashboard  → xem chart + signal bằng mắt (Flask port 8516)
  2e  Signal watcher      → scan theo bar close, dedup, gửi Telegram/Discord

GIAI ĐOẠN 3 — BACKTEST
  3a  Signal loader       → đọc CSV từ raw_signals/
  3b  Execution engine    → mô phỏng vào/ra lệnh
  3c  Metrics             → win rate, expectancy R, MAE/MFE
  3d  Walk-forward        → stability check
```

---

## 4. Conventions Không Được Thay Đổi

Đây là những quyết định đã được kiểm chứng trong hệ thống đang chạy. Khi vibe
code, Claude không được tự ý chọn cách khác:

| Convention | Lý do |
|---|---|
| Bar timestamp = UTC naive | TV trả UTC, không convert để tránh DST edge case |
| Signal ở bar `i` → entry ở bar `i+1` | Ngăn look-ahead bias trong backtest |
| Staging tables `SEN.TF_*` → `usp_LoadDirect` → `Fact_OHLCV` | Tách staging/warehouse, upsert safe |
| Guest mode TradingView = hard error | Guest thiếu history, dễ nạp data không đầy đủ |
| Lock file trước mỗi thao tác ghi DB | Ngăn pipeline + ws_live + checker chạy đồng thời |
| `--dry-run` bắt buộc cho mọi script ghi dữ liệu | Kiểm tra plan trước khi thực hiện |
| State file dedup signal watcher | Restart không gửi lại signal cũ |
| `config.py` là single source of truth | Tất cả scripts import từ đây, không hardcode |

---

## 5. Quy Trình Vibe Code Từng Phần

### Công thức 5 bước (áp dụng cho mọi cấu phần)

```
1. DESCRIBE   Mô tả bằng ngôn ngữ thường, không dùng từ kỹ thuật
2. CONTRACT   "Nhận vào X → trả ra Y" (viết cụ thể, không mơ hồ)
3. VIBE       Prompt AI theo template dưới
4. VERIFY     Chạy trên ví dụ cụ thể, kiểm tra bằng mắt
5. ANCHOR     Ghi lại những gì đúng trước khi đi tiếp
```

### Template prompt mở đầu session

```
Tôi đang xây dựng hệ thống trading data + signal cá nhân.
Stack: Python, SQL Server, TradingView WebSocket, Flask, Telegram Bot.
Windows, Python 3.10+.

Hệ thống đã có:
[paste anchor points của các phần đã xong]

Phần đang build:
[mô tả bằng ngôn ngữ thường]

Contract:
  Nhận vào: [X]
  Trả ra:   [Y]
  Verify:   [cách test cụ thể]

Conventions cần giữ: [dán các dòng liên quan từ bảng Section 4]

Tôi không phải developer. Code đầy đủ, có thể chạy ngay.
Comment giải thích những chỗ không hiển nhiên.
```

### Template khi AI sai

```
# Khi lỗi runtime:
"Code báo lỗi: [paste error]. Giải thích lỗi đơn giản, sửa lại."

# Khi kết quả sai logic:
"Code chạy được nhưng output sai. Tôi nhận được [X], tôi muốn [Y].
 Điều chỉnh logic ở phần [tên phần]. Giữ nguyên phần còn lại."

# Khi bế tắc sau 2-3 lần:
"Viết lại từ đầu, đơn giản hơn, chỉ làm đúng contract này: [contract]"
```

---

## 6. Anchor Template (Ghi Sau Mỗi Phần Xong)

Paste vào đầu session tiếp theo để Claude có context:

```markdown
## Đã xong

### [Tên phần] — [ngày]
- Input thực tế: [mô tả]
- Output thực tế: [mô tả + ví dụ ngắn]
- Test đã pass: [mô tả]
- File chính: [đường dẫn]
- Lưu ý: [điều bất ngờ, edge case đã gặp]
```

---

## 7. Verify Checklist Theo Giai Đoạn

### Giai đoạn 1 xong khi

- [ ] Insert 100 nến GOLD H1, SELECT lại = 100, không trùng
- [ ] Pipeline dry-run in ra plan mà không thay đổi DB
- [ ] Pipeline gap fill chạy lần 2: log "0 rows inserted"
- [ ] WS Live chạy 5 phút: nến mới nhất khớp với TradingView
- [ ] Checker --dry-run không báo lỗi trên data sạch
- [ ] Data dashboard hiển thị chart GOLD H1 trên `http://127.0.0.1:8050`

### Giai đoạn 2 xong khi

- [ ] GOLD H1 500 bars → ít nhất 10 signals, không có NaN entry khi signal != 0
- [ ] SL < entry với signal buy; SL > entry với signal sell
- [ ] Strategy dashboard hiển thị chart + signals trên `http://127.0.0.1:8516`
- [ ] Watcher --dry-run in đúng format Telegram
- [ ] Watcher --warm-up: chạy rồi restart, không gửi lại gì

### Giai đoạn 3 xong khi

- [ ] Load signal CSV, chạy backtest, ra được metrics không NaN
- [ ] Nếu chạy cùng params 2 lần: kết quả giống hệt (deterministic)

---

## 8. Những Lỗi Hay Gặp Khi Vibe Code Hệ Thống Này

| Triệu chứng | Nguyên nhân thường gặp | Hỏi AI như thế nào |
|---|---|---|
| Backtest đẹp, live tệ | Signal dùng bar hiện tại chưa đóng | "Kiểm tra chỗ nào code đọc data trước khi bar đóng" |
| Restart watcher, Telegram bị spam | Thiếu dedup state file | "Thêm mechanism lưu signal đã gửi, đọc lại khi start" |
| Pipeline chạy chậm dần | Không có lock, nhiều process cùng ghi | "Thêm file lock trước khi ghi DB, release khi xong" |
| Data bị trùng sau pipeline | Upsert không đúng | "Dùng MERGE thay vì INSERT, key là (symbol, tf, bar_time)" |
| Signal khác nhau mỗi lần chạy | Indicator dùng future data hoặc sort sai | "Đảm bảo DataFrame sorted ascending trước khi tính indicator" |

---

## 9. Ghi Chú Cho Claude

Khi người dùng bắt đầu session với file này:

1. **Đọc README.md trước** để biết cụ thể những gì đang tồn tại (file, API, config values)
2. **File này là methodology** — không mô tả hiện trạng, mô tả cách làm
3. **Hỏi user đang ở bước nào** nếu không rõ (anchor points của họ đâu?)
4. **Không tự sáng tác convention** ngoài Section 4 — hỏi user trước
5. **Luôn đề xuất verify step** trước khi chuyển sang phần tiếp theo
6. **Ngôn ngữ**: tiếng Việt với user, code/comments bằng tiếng Anh
