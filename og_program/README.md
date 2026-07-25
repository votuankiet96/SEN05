# core_python — Strategy Core Workspace (SEN05)

`core_python` là workspace cốt lõi để phát triển và kiểm chứng chiến lược
trong SEN05. Nhiệm vụ của package này rất rõ: đọc OHLCV lịch sử từ SQL Server
(DP6), chạy chiến lược kỹ thuật, hiển thị lên dashboard chart và xuất CSV
signal để phân tích.

Package này **không** làm realtime Redis, không gửi lệnh, không quản lý Order
Follower và không gửi Discord. Các phần đó thuộc lớp hệ thống khác.

Chỉ 2 chiến lược được hỗ trợ: **Combo** (khuyến nghị H1-H4, vẫn cho phép các
timeframe hệ thống khác) và **MA Cross** (chỉ M10/M20/M30/M45, mặc định M30).

Tên thư mục checkout không còn ảnh hưởng gì đến việc import sau khi cài đặt
editable (`pip install -e ...`).

## Kiến trúc

```text
core_python/
  data/                    Đọc OHLCV từ SQL Server (chỉ đọc, xem CONTRACTS.md)
  indicators/              Chỉ báo kỹ thuật thuần (SMA/EMA/MACD/ATR)
  strategies/              Combo, MA Cross — cùng 1 hợp đồng StrategySpec
                           (đăng ký tập trung ở strategies/registry.py)
  engine.py                Runner chiến lược + orchestration SQL/date-window
  export/, export_cli.py   Xuất CSV cho dashboard và CLI
  chart/                   Flask dashboard API + frontend (Lightweight Charts)
    payloads/
      common.py              Contract chung: candles, markers, levels, stats
      registry.py            Map strategy key -> payload builder
      layouts/
        combo.py             Layout dashboard riêng cho Combo
        ma_cross.py          Layout dashboard riêng cho MA Cross
  main.py                  Dashboard entry point
  util/                    Hạ tầng hỗ trợ:
    config.py                Symbols, timeframes, defaults (DWH-backed lookup)
    settings.py               SQL Server connection settings (.env)
    logging_setup.py           Rotating file handler dùng chung
    ops.py                      CLI hỗ trợ: config/strategies/services/validate/health/smoke/exports
tests/                     Test characterization cho indicators + 2 chiến lược
```

Xem `CONTRACTS.md` để biết chính xác core_python kết nối với DP6 qua đâu
(SQL Server, chỉ đọc).

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # điền thông tin SQL Server thật
```

Cần chạy production qua gunicorn: `./.venv/bin/pip install -e ".[prod]"`.

## Chạy

```bash
# Dashboard
./.venv/bin/python -m core_python.main --port 8516
# hoặc, sau khi cài đặt: core-python-dashboard --port 8516

# CSV export
./.venv/bin/python -m core_python.export_cli single --strategy combo --symbol US30 --tf H1

# Kiểm tra / hỗ trợ vận hành
./.venv/bin/python -m core_python.util.ops config
./.venv/bin/python -m core_python.util.ops health

# Test
./.venv/bin/python -m pytest
```

`/api/scan`, `/api/export` cần kết nối SQL Server thật (SQL_SERVER trong
`.env`) — không hoạt động nếu chưa cấu hình hoặc không có driver ODBC phù
hợp trên máy.

## Triển khai 24/7

Đã kiểm chứng thật trên `vm-og`: kết nối SQL Server DP6, load dữ liệu thật,
tính tín hiệu thật, phục vụ qua gunicorn. Xem `deploy/README.md` để biết
cách cài làm systemd service.
