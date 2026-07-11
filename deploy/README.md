# Triển khai OG dashboard 24/7 trên vm-og

Đã kiểm chứng thật (2026-07-07): kết nối SQL Server trên DP6 qua FreeTDS +
SQL Authentication, load dữ liệu thật, tính tín hiệu thật, phục vụ qua
gunicorn — tất cả đã chạy đúng trên chính máy `vm-og` này.

Việc còn lại (cài systemd service) cần quyền root mà tài khoản đang chạy
coding agent không có — chạy các lệnh dưới đây thủ công.

## 1. Cài đặt (đã làm sẵn trong session này, ghi lại để tham khảo)

```bash
cd /home/administrator/Desktop/og_program
python3 -m venv .venv
./.venv/bin/pip install -e ".[past,prod]"
cp .env.example .env   # rồi điền SQL_SERVER/SQL_UID/SQL_PWD thật
```

`.env` hiện tại trên máy này đã có sẵn credential thật — không commit vào git
(đã gitignore).

**Lưu ý bảo mật:** dashboard chỉ cần quyền đọc. Nên xin admin DP6 tạo 1
login SQL riêng chỉ có quyền `SELECT` trên `SEN05_AutoTrading`, rồi đổi
`SQL_UID`/`SQL_PWD` trong `.env` — không bắt buộc để chạy được, nhưng nên làm
trước khi để chạy 24/7 lâu dài.

## 2. Cài systemd service (cần sudo)

```bash
sudo cp /home/administrator/Desktop/og_program/deploy/og-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now og-dashboard.service
```

## 3. Kiểm tra

```bash
systemctl status og-dashboard.service
curl http://127.0.0.1:8516/health
curl "http://127.0.0.1:8516/api/scan?strategy=combo&symbol=US30&tf=H1&bars=50"
journalctl -u og-dashboard.service -f   # xem log real-time
```

## 4. Các lệnh vận hành thường dùng

```bash
sudo systemctl restart og-dashboard.service   # sau khi đổi code/.env
sudo systemctl stop og-dashboard.service
sudo systemctl disable og-dashboard.service   # tắt tự khởi động cùng máy
```

## 5. Cài `og_live` làm systemd service

`og_live` đọc stream `candle_snapshot` từ Redis, dùng 500 nến trong snapshot
để tính tín hiệu qua `og_core`, rồi publish tín hiệu mới lên
`signal_stream:<strategy>`.

```bash
./.venv/bin/pip install -e ".[live]"   # nếu chưa cài
sudo cp /home/administrator/Desktop/og_program/deploy/og-live.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now og-live.service
```

Kiểm tra:

```bash
systemctl status og-live.service
journalctl -u og-live.service -f
tail -f /home/administrator/Desktop/og_program/runtime/logs/og_live.log
```

`Requires=redis-server.service` — service này sẽ không khởi động nếu Redis
local chưa chạy. `Restart=always` giúp systemd tự khởi động lại nếu tiến
trình live thoát vì bất kỳ lý do nào ngoài thao tác stop chủ động.

## 6. Trạng thái triển khai hiện tại của `og_live`

Trên VM này, `og_live` đang được cài bằng **systemd user service** để không cần
ghi vào `/etc/systemd/system`:

```bash
systemctl --user status og-live.service
systemctl --user restart og-live.service
journalctl --user -u og-live.service -f
```

`loginctl enable-linger administrator` đã được bật (`Linger=yes`), nên user
service có thể tiếp tục chạy sau khi user logout và có thể tự khởi động cùng
user manager.

Healthcheck Redis/live pipeline cũng đã được cài bằng timer 5 phút/lần:

```bash
systemctl --user list-timers og-live-healthcheck.timer
systemctl --user start og-live-healthcheck.service
journalctl --user -u og-live-healthcheck.service -n 50 --no-pager
```

Chạy thủ công trong repo:

```bash
cd /home/administrator/Desktop/og_program
./.venv/bin/python -m og_live.healthcheck
./.venv/bin/python -m og_live.healthcheck --json --compact-json
```

Healthcheck kiểm tra các điểm sống còn:

- Redis còn kết nối được.
- `candle_snapshot` còn nhận snapshot mới.
- consumer group `og_live` có `lag=0`, `pending=0`.
- đủ 165 cặp watched: 11 symbol x 15 timeframe.
- `signal_stream:combo` đọc được signal mới nhất.
- local delivery outbox rỗng, không có signal publish lỗi đang chờ retry.

Tình trạng hiện tại có thể trả `STATUS warn` vì 3 cặp weekly chưa đủ đúng 500
nến lịch sử: `BTCUSD W` và `GOLD W` có 479 nến, `HK50 W` có 397 nến. Đây là
cảnh báo độ sâu dữ liệu lịch sử, không phải lỗi live engine: healthcheck vẫn
exit code 0 nếu các điều kiện sống còn ổn.

Redis vẫn còn consumer group cũ `og_watchers` với lag cao. Group này không được
`og_live` mới dùng; chỉ nên xóa nếu đã xác nhận không còn tiến trình cũ nào cần
đọc group đó.

## Về sau (không bắt buộc để chạy 24/7, nhưng nên cân nhắc)

- **Chỉ bind `127.0.0.1`** — dashboard hiện chỉ truy cập được từ chính máy
  `vm-og` (vd. qua SSH tunnel). Muốn truy cập từ máy khác trong mạng: đổi
  `--bind 127.0.0.1:8516` thành `--bind 0.0.0.0:8516` (hoặc IP cụ thể) trong
  file service, `sudo systemctl daemon-reload && sudo systemctl restart
  og-dashboard`. Cân nhắc thêm xác thực trước khi mở rộng ra ngoài máy này —
  hiện dashboard không có auth.
- **Lỗi API vẫn trả chi tiết exception ra ngoài** (quyết định trước đó là
  chưa cần sửa) — nên sửa nếu mở dashboard ra ngoài `127.0.0.1`.
- `og-dashboard.service` và `og-live.service` độc lập với nhau. Dashboard lỗi
  SQL không làm live dừng; Redis/live lỗi không làm dashboard dừng.
