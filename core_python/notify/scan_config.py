"""
Cấu hình các nhóm scan cho vòng lặp giám sát 24/7.

Mô tả:
    Định nghĩa danh sách SCAN_GROUPS — mỗi nhóm chỉ định:
    - Chiến lược cần chạy
    - Danh sách symbol cần theo dõi
    - Khung thời gian
    - Số bar tải về
    - Channel Telegram riêng (tùy chọn)

    Người dùng chỉ cần sửa file này để thêm/bớt symbol hoặc TF,
    không cần thay đổi logic watcher.

    Nếu dùng CLI --symbols/--tf, file này sẽ bị bỏ qua hoàn toàn.
"""

from __future__ import annotations


# Khoảng thời gian poll (giây) mặc định cho từng khung thời gian.
# Các giá trị này KHÔNG còn được dùng trong logic bar-close-aligned hiện tại
# (signal_watcher.py dùng _next_bar_close_utc() thay vì fixed interval).
# Giữ lại để dùng khi override bằng CLI --symbols/--tf qua _build_groups_from_args().
TF_POLL_SECONDS: dict[str, int] = {
    "M5": 60,
    "M10": 120,
    "M15": 120,
    "M20": 180,
    "M30": 180,
    "M45": 240,
    "H1": 300,
    "M90": 360,
    "H2": 600,
    "H3": 900,
    "H4": 900,
    "H6": 1800,
    "H8": 1800,
    "D1": 3600,
    "W": 3600,
}

# Tất cả symbol Indice có trong DB hiện tại.
INDICE_SYMBOLS = ["FR40", "DE40", "HK50", "J225", "SP35", "UK100", "US500", "US100", "US30"]

# Mỗi dict trong SCAN_GROUPS định nghĩa một nhóm scan độc lập:
#
#   strategy:     Key chiến lược ("combo" hoặc "ma_cross").
#   symbols:      List symbol cần scan trong nhóm này.
#   tf:           Mã khung thời gian ("H1", "H2", ...).
#   bars:         Số bar tải về — phải đủ lớn cho warmup indicators + lịch sử cần thiết.
#   poll_seconds: Dự phòng khi không dùng bar-close-aligned scheduling.
#   chat_id:      Telegram chat/group ID riêng (None = dùng TELEGRAM_CHAT_ID env).
#   overrides:    Dict tham số chiến lược override (rỗng = dùng defaults).
#
# Lưu ý về bars và TTL:
#   H1: 500 bars × 60min  = 20.8 ngày < TTL_DAYS=60 ✓
#   H2: 500 bars × 120min = 41.7 ngày < TTL_DAYS=60 ✓
#   H3: 400 bars × 180min = 50.0 ngày < TTL_DAYS=60 ✓
#   H4: 300 bars × 240min = 50.0 ngày < TTL_DAYS=60 ✓
#   → TTL=60 đảm bảo không bao giờ prune key của bar còn trong cửa sổ.
SCAN_GROUPS: list[dict] = [
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H1",
        "bars": 500,
        "poll_seconds": TF_POLL_SECONDS["H1"],
        "chat_id": None,   # None → dùng TELEGRAM_CHAT_ID env var
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H2",
        "bars": 500,
        "poll_seconds": TF_POLL_SECONDS["H2"],
        "chat_id": None,
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H3",
        "bars": 400,
        "poll_seconds": TF_POLL_SECONDS["H3"],
        "chat_id": None,
        "overrides": {},
    },
    {
        "strategy": "combo",
        "symbols": INDICE_SYMBOLS,
        "tf": "H4",
        "bars": 300,
        "poll_seconds": TF_POLL_SECONDS["H4"],
        "chat_id": None,
        "overrides": {},
    },
]
