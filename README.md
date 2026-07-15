# OG — Order Generation (SEN05)

OG là lớp sinh tín hiệu giao dịch của hệ thống SEN05. Repo hiện được tách
thành ba package: `og_core` chứa logic chiến lược chung, `og_past` đọc SQL
để phục vụ dashboard/export lịch sử, và `og_live` đọc Redis event/state
snapshot từ DP6 để publish tín hiệu realtime.

Tên thư mục checkout không còn ảnh hưởng gì đến việc import sau khi cài đặt
editable (`pip install -e ...`).

## Kiến trúc

```text
og_core/config.py      Metadata chung: symbols, timeframes, defaults
og_core/engine.py      Runner chiến lược source-agnostic trên DataFrame OHLCV
og_core/indicators/    Chỉ báo kỹ thuật thuần (SMA/EMA/MACD/ATR, KNN AI Trend
                        Navigator, swing Dow HH/HL/LH/LL)
og_core/strategies/    4 chiến lược độc lập, cùng 1 hợp đồng StrategySpec:
                        Combo, MA Cross, AI Trend, KNN Combo
                        (đăng ký tập trung ở strategies/registry.py)
og_core/signals.py     SignalEvent + deterministic signal_id
og_past/data/          Đọc OHLCV từ SQL Server (chỉ đọc, xem CONTRACTS.md)
og_past/engine.py      Orchestration dashboard/export với date window SQL
og_past/chart/         Flask dashboard API + frontend (Lightweight Charts)
og_past/export/        Xuất CSV cho dashboard (/api/export, /api/export/bulk)
og_live/               Live Stream + Pub/Sub mechanisms -> og_core -> signal streams
tests/                 Test characterization cho indicators + 4 chiến lược
```

Xem `CONTRACTS.md` để biết chính xác OG kết nối với DP6 qua đâu: SQL Server
cho `og_past`, Redis Stream và Redis Pub/Sub cho `og_live`.

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # điền thông tin SQL Server thật
```

Extras chính:

```bash
./.venv/bin/pip install -e ".[past,live,prod,dev]"
```

## Chạy

```bash
# Dashboard
./.venv/bin/python -m og_past.main --port 8516
# hoặc, sau khi cài đặt: og-dashboard --port 8516

# Live Stream mechanism
./.venv/bin/python -m og_live.stream_mechanism.main
# smoke test một batch: ./.venv/bin/python -m og_live.stream_mechanism.main --once

# Live Pub/Sub mechanism
./.venv/bin/python -m og_live.pubsub_mechanism.main
# smoke test một message: ./.venv/bin/python -m og_live.pubsub_mechanism.main --once --timeout-seconds 60

# Test
./.venv/bin/python -m pytest
```

`/api/scan`, `/api/export` cần kết nối SQL Server thật (SQL_SERVER trong
`.env`) — không hoạt động nếu chưa cấu hình hoặc không có driver ODBC phù
hợp trên máy.

## Triển khai 24/7

Đã kiểm chứng thật trên `vm-og`: kết nối SQL Server DP6, load dữ liệu thật,
tính tín hiệu thật, phục vụ qua gunicorn. Xem `deploy/README.md` để biết
cách cài làm systemd service (cần quyền sudo).
