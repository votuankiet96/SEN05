# Triển khai core_python dashboard 24/7 trên vm-og

Đã kiểm chứng thật (2026-07-07): kết nối SQL Server trên DP6 qua FreeTDS +
SQL Authentication, load dữ liệu thật, tính tín hiệu thật, phục vụ qua
gunicorn — tất cả đã chạy đúng trên chính máy `vm-og` này.

## 1. Cài đặt

```bash
cd /home/administrator/Desktop/og_program
python3 -m venv .venv
./.venv/bin/pip install -e ".[prod]"
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

Sau refactor, unit file phải trỏ tới package mới:

```bash
systemctl --user show og-dashboard.service -p ExecStart --no-pager
```

Kỳ vọng ExecStart chứa:

```text
core_python.chart.server:create_app()
```

Nếu còn thấy `og_past.chart.server:create_app()`, cập nhật lại user service
bằng file `deploy/og-dashboard-user.service` trước lần restart/reboot tiếp
theo.

```bash
install -D -m 0644 /home/administrator/Desktop/og_program/deploy/og-dashboard-user.service \
  ~/.config/systemd/user/og-dashboard.service
systemctl --user daemon-reload
```

Hai lệnh trên chỉ cập nhật unit file trên disk; chúng không restart dashboard
đang chạy.

Khi cần chạy dashboard foreground để debug, dùng cổng phụ để không đụng service
production đang giữ cổng `8516`:

```bash
./.venv/bin/python -m core_python.main --host 127.0.0.1 --port 8517
```

## Về sau (không bắt buộc để chạy 24/7, nhưng nên cân nhắc)

- **Chỉ bind `127.0.0.1`** — dashboard hiện chỉ truy cập được từ chính máy
  `vm-og` (vd. qua SSH tunnel). Muốn truy cập từ máy khác trong mạng: đổi
  `--bind 127.0.0.1:8516` thành `--bind 0.0.0.0:8516` (hoặc IP cụ thể) trong
  file service, `sudo systemctl daemon-reload && sudo systemctl restart
  og-dashboard`. Cân nhắc thêm xác thực trước khi mở rộng ra ngoài máy này —
  hiện dashboard không có auth.
- **Không mở dashboard công khai nếu chưa có auth.** Lỗi input dự đoán được
  đã trả `400`, nhưng lỗi hạ tầng ngoài dự kiến vẫn trả JSON `500` kèm message
  để debug nội bộ. Nếu bind ra ngoài `127.0.0.1`, cần thêm xác thực và làm sạch
  error response trước.
