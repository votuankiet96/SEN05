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

### Chạy dashboard bằng user service

Nếu không muốn/cần ghi vào `/etc/systemd/system`, có thể dùng user service:

```bash
install -D -m 0644 /home/administrator/Desktop/og_program/deploy/og-dashboard-user.service \
  ~/.config/systemd/user/og-dashboard.service
systemctl --user daemon-reload
systemctl --user enable --now og-dashboard.service
systemctl --user status og-dashboard.service
```

Trên VM này dashboard đang dùng cách user service, bind ở
`http://127.0.0.1:8516`.

## 5. Cài `og_live` làm systemd service

`og_live` hiện có 2 cơ chế live độc lập:

- Stream mechanism: đọc Redis Stream `dp:candle_snapshot:events` từ db0, GET
  `state_key`, tính tín hiệu qua `og_core`, publish signal lên db1 theo route
  `og:stream:signals:<strategy>:<symbol>:<tf>`.
- Pub/Sub mechanism: subscribe channel `dp:pubsub:candle_snapshot:events`,
  GET `state_key` từ db0, tính tín hiệu qua `og_core`, publish signal lên db2
  theo route `og:pubsub:signals:<strategy>:<symbol>:<tf>`.

Redis Pub/Sub channel không thuộc db nào; db2 chỉ dùng để lưu signal output
của Pub/Sub mechanism.

```bash
./.venv/bin/pip install -e ".[live]"   # nếu chưa cài
sudo cp /home/administrator/Desktop/og_program/deploy/og-live-stream.service /etc/systemd/system/
sudo cp /home/administrator/Desktop/og_program/deploy/og-live-pubsub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now og-live-stream.service og-live-pubsub.service
```

Kiểm tra:

```bash
systemctl status og-live-stream.service og-live-pubsub.service
journalctl -u og-live-stream.service -u og-live-pubsub.service -f
tail -f /home/administrator/Desktop/og_program/runtime/logs/og_live_stream.log
tail -f /home/administrator/Desktop/og_program/runtime/logs/og_live_pubsub.log
```

`Requires=redis-server.service` — service này sẽ không khởi động nếu Redis
local chưa chạy. `Restart=always` giúp systemd tự khởi động lại nếu tiến
trình live thoát vì bất kỳ lý do nào ngoài thao tác stop chủ động.

## 6. Trạng thái triển khai hiện tại của `og_live`

Trên VM này, `og_live` đang được cài bằng **systemd user service** để không cần
ghi vào `/etc/systemd/system`:

```bash
systemctl --user status og-live-stream.service og-live-pubsub.service
systemctl --user restart og-live-stream.service og-live-pubsub.service
journalctl --user -u og-live-stream.service -u og-live-pubsub.service -f
```

`loginctl enable-linger administrator` đã được bật (`Linger=yes`), nên user
service có thể tiếp tục chạy sau khi user logout và có thể tự khởi động cùng
user manager.

Healthcheck Redis/live pipeline cũng đã được cài bằng timer 5 phút/lần:

```bash
systemctl --user list-timers og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer
systemctl --user start og-live-stream-healthcheck.service og-live-pubsub-healthcheck.service
journalctl --user -u og-live-stream-healthcheck.service -u og-live-pubsub-healthcheck.service -n 50 --no-pager
```

Chạy thủ công trong repo:

```bash
cd /home/administrator/Desktop/og_program
./.venv/bin/python -m og_live.stream_mechanism.healthcheck
./.venv/bin/python -m og_live.pubsub_mechanism.healthcheck
```

Healthcheck kiểm tra các điểm sống còn:

- Redis còn kết nối được.
- `dp:candle_snapshot:events` còn nhận event mới cho Stream mechanism.
- consumer group `og_live_stream` có `lag=0`, `pending=0`.
- Pub/Sub channel có subscriber khi Pub/Sub mechanism service đang chạy.
- đủ state key cho các cặp watched.
- signal output db1 đọc được signal mới nhất nếu đã từng có signal.
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
- `og-dashboard.service`, `og-live-stream.service` và `og-live-pubsub.service` độc lập với nhau. Dashboard lỗi
  SQL không làm live dừng; Redis/live lỗi không làm dashboard dừng.
