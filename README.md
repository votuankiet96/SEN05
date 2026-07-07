# OG — Order Generation (SEN05)

OG là lớp sinh tín hiệu giao dịch của hệ thống SEN05: đọc OHLCV từ SQL Data
Warehouse trên **DP6** (VM lưu trữ dữ liệu + chạy pipeline lấy dữ liệu), tính
chỉ báo kỹ thuật, phát hiện tín hiệu theo chiến lược, và hiển thị qua
dashboard + xuất CSV.

Package Python: `og_core` (xem `pyproject.toml`). Tên thư mục checkout không
còn ảnh hưởng gì đến việc import — `og_core` luôn import được sau khi cài
đặt, bất kể repo nằm ở đâu hay tên gì.

## Kiến trúc

```text
og_core/data/          Đọc OHLCV từ SQL Server (chỉ đọc, xem CONTRACTS.md)
og_core/indicators/    Chỉ báo kỹ thuật thuần (SMA/EMA/MACD/ATR, KNN AI Trend
                        Navigator, swing Dow HH/HL/LH/LL)
og_core/strategies/    4 chiến lược độc lập, cùng 1 hợp đồng StrategySpec:
                        Combo, MA Cross, AI Trend, KNN Combo
                        (đăng ký tập trung ở strategies/registry.py)
og_core/engine.py      Orchestration dùng chung cho dashboard + export
og_core/chart/         Flask dashboard API + frontend (Lightweight Charts)
og_core/export/        Xuất CSV (dashboard export + tiện ích export tín hiệu)
tests/                 Test characterization cho indicators + 4 chiến lược
```

**Đã gỡ tạm thời:** hệ thống notification/watcher 24/7 (Telegram/Discord,
Redis signal relay tới hệ "OF" downstream) từng nằm ở `notify/`. Vẫn còn
nguyên trong lịch sử git, sẽ được thiết kế lại trong một giai đoạn phát
triển riêng — xem `strategies/*/ARCHITECTURE.md` để biết phần logic tín
hiệu nào từng phục vụ notify (nay đã lược bỏ phần alert-formatting, giữ lại
phần signal rule vẫn còn dùng).

Xem `CONTRACTS.md` để biết chính xác OG kết nối với DP6 qua đâu (SQL Server
— hợp đồng hạ tầng duy nhất còn lại, không phải Python import).

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # điền thông tin SQL Server thật
```

## Chạy

```bash
# Dashboard
./.venv/bin/python -m og_core.main --port 8516
# hoặc, sau khi cài đặt: og-dashboard --port 8516

# Test
./.venv/bin/python -m pytest
```

`/api/scan`, `/api/export` cần kết nối SQL Server thật (SQL_SERVER trong
`.env`) — không hoạt động nếu chưa cấu hình hoặc không có driver ODBC phù
hợp trên máy.
