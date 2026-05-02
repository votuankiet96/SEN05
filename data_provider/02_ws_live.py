"""
Bo cap nhat du lieu gan realtime theo kieu batch WebSocket.

File nay KHONG giu mot ket noi WS mo 24/7. Thay vao do, cu moi 5 phut no:
- mo ket noi TradingView
- dang ky nhom symbol live
- nhan vai nen moi nhat
- dua vao queue / overflow buffer / SQLite spool neu can
- ghi staging, ETL vao Fact khi he thong cho phep, roi dong ket noi

Nhung diem van hanh quan trong:
- chi theo doi asset type Indice, Metal, Crypto; FOREX duoc de cho pipeline backfill
- tach "received watermark" va "committed watermark" de tranh duplicate va theo doi backlog
- neu checker dang repair, ETL vao Fact se duoc defer de tranh race condition
- khi queue day, he thong lui dan: RAM buffer -> durable SQLite spool
- auth co the roi xuong guest mode; day la trang thai can canh bao, khong nen de keo dai
"""

# =============================================================================
# data_provider/02_ws_live.py  -  Cập nhật dữ liệu thời gian thực qua WebSocket
# Phiên bản     : V5 (Batch/Cron mode)
# =============================================================================
#
# FILE NÀY LÀ GÌ-
#   Module cập nhật dữ liệu REAL-TIME - chạy liên tục 24/7, cứ mỗi 5 phút lại
#   mở WebSocket tới TradingView, lấy 3–5 nến mới nhất, lưu vào DB rồi đóng.
#
#   Bổ sung cho 01_data_pipeline.py (chạy hàng ngày lúc 22:22 UTC):
#   pipeline bù lịch sử -> ws_live giữ dữ liệu luôn mới trong ngày.
#
#   Phạm vi theo dõi: Indices, Metal, Crypto - KHÔNG theo dõi FOREX qua WS.
#   (FOREX có lịch đóng/mở cửa phức tạp, được backfill bởi 01_data_pipeline.py)
#
# ─────────────────────────────────────────────────────────────────────────────
# TẠI SAO DÙNG BATCH MODE (mở rồi đóng, không giữ kết nối liên tục)-
# ─────────────────────────────────────────────────────────────────────────────
#   Giữ WebSocket mở 24/7 dễ bị TradingView phát hiện và ban IP.
#   Thay vào đó: mở kết nối -> nhận 3–5 nến -> đóng -> chờ 5 phút -> lặp lại.
#   An toàn hơn, ít tốn tài nguyên hơn, ít bị rate-limit hơn.
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁCH CHẠY
# ─────────────────────────────────────────────────────────────────────────────
#   Bước 1: Chạy 01_data_pipeline.py trước để có đủ lịch sử trong DB
#   Bước 2: python 02_ws_live.py   (chạy liên tục 24/7, không cần tham số)
#   Dừng  : Ctrl + C  (thoát sạch - drain hết DB queue rồi mới dừng)
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC TÍNH NĂNG CHÍNH
# ─────────────────────────────────────────────────────────────────────────────
#   1. BATCH MODE        - mỗi 5 phút mở WS, lấy data, đóng (không giữ 24/7)
#   2. SCHEDULER         - tự tính thời điểm batch tiếp theo theo đúng mốc phút
#   3. COMPLETION TRACK  - biết khi nào nhận đủ data -> đóng WS sớm, không chờ timeout
#   4. RETRY + BACKOFF   - lỗi 429/500 -> retry với thời gian chờ tăng dần (30s->300s)
#   5. XÁC THỰC 3 LỚP   - thứ tự: Auth Token -> Cookie -> Guest (fallback từng bước)
#   6. OVERFLOW BUFFER   - queue DB đầy -> giữ tạm trong RAM; RAM đầy -> SQLite spool
#   7. DB THREAD RIÊNG   - thread ghi DB song song, không chặn luồng nhận WS
#   8. WATERMARK         - chỉ lưu nến timestamp > watermark, không bao giờ duplicate
#   9. BACKLOG TRACKING  - cặp miss ≥1 batch -> yêu cầu thêm bar (N_BARS_WS_BACKLOG)
#                          để bù khoảng trống; miss > MAX_BACKLOG_BATCHES -> cảnh báo
#   10. ETL DEFER        - 04_checker.py đang repair -> defer ghi Fact, giữ ở Staging
#                          để tránh race condition giữa 2 tiến trình
#   11. DISCORD ALERT    - cảnh báo khi lỗi, token hết hạn, queue áp lực
#   12. BÁO CÁO MỖI GIỜ - gửi thống kê bars_inserted / errors / queue_depth
#
# ─────────────────────────────────────────────────────────────────────────────
# THÔNG SỐ VẬN HÀNH (điều chỉnh trong phần HẰNG SỐ CẤU HÌNH bên dưới)
# ─────────────────────────────────────────────────────────────────────────────
#   BATCH_INTERVAL_MIN    - chu kỳ batch (phút), mặc định 5
#   BATCH_FETCH_TIMEOUT   - timeout mỗi batch (giây), mặc định 120
#   WS_SYMBOLS_PER_CONN   - symbol tối đa / kết nối WS, mặc định 10
#   DB_QUEUE_MAXSIZE      - giới hạn hàng đợi ghi DB, mặc định 2000
#   MAX_MISS_RETRIES      - số batch miss liên tiếp trước khi cảnh báo, mặc định 5
#
#   [WARN] Tần suất quá cao -> bị TradingView rate-limit
#      Tần suất quá thấp -> data bị trễ trong ngày
#
# ─────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH FILE .env (BẮT BUỘC)
# ─────────────────────────────────────────────────────────────────────────────
#   TV_AUTH_TOKEN=eyJhbGci...        ← auth_token lấy từ cookie TradingView
#   TV_COOKIE=sessionid=abc; ...     ← toàn bộ cookie header (fallback)
#   TV_USERNAME=your_username         ← fallback nếu không có cookie
#   TV_PASSWORD=your_password         ← fallback nếu không có cookie
#   DISCORD_WEBHOOK_URL=https://discordapp.com/api/webhooks/...  ← webhook Discord (để nhận cảnh báo)
#
# CÁCH LẤY AUTH TOKEN TỪ TRÌNH DUYỆT:
#   1. Mở Chrome, đăng nhập TradingView -> nhấn F12 -> tab "Network"
#   2. Reload trang -> click request bất kỳ tới tradingview.com
#   3. Trong "Request Headers" -> copy toàn bộ giá trị "cookie"
#   4. Tìm "auth_token=..." trong chuỗi -> copy phần giá trị
#   5. Dán vào .env: TV_AUTH_TOKEN=<giá trị vừa copy>
# =============================================================================


# =============================================================================
# NHẬP CÁC THƯ VIỆN CẦN THIẾT
# =============================================================================

import atexit
import json  # Xử lý dữ liệu JSON - TradingView gửi/nhận lệnh dưới dạng JSON
import logging  # Framework ghi log chuẩn của Python
import math  # Hàm toán học - dùng math.ceil() để tính số nhóm WS cần tạo
import pickle  # Serialize DataFrame -> BLOB để lưu vào SQLite spool
import re    # Regex - dùng để parse auth_token từ HTML khi refresh session
import queue  # Hàng đợi thread-safe - dùng để truyền data từ WS thread sang DB thread
import random  # Tạo chuỗi ngẫu nhiên - dùng để sinh tên session chart
import sqlite3  # SQLite local - dùng làm durable spool khi overflow buffer đầy
import string  # Bảng ký tự (a-z, 0-9) - kết hợp với random để tạo session ID
import sys  # Tương tác với Python runtime (thoát chương trình, thêm đường dẫn)
import threading  # Chạy nhiều luồng song song - WS, ghi DB, scheduler đều chạy độc lập
import time  # Hàm sleep và đo thời gian
import traceback  # Format stack trace khi có exception trong thread
from datetime import datetime, timezone  # Xử lý thời gian - ghi log, tính khoảng cách thời gian
from pathlib import Path  # Xử lý đường dẫn file theo chuẩn hiện đại (thay os.path)

# =============================================================================
# CẤU HÌNH ĐƯỜNG DẪN PROJECT
# =============================================================================

# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
_PROJ = Path(__file__).resolve().parent.parent   # data_provider/ -> project root
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


# =============================================================================
# NHẬP CÁC THƯ VIỆN BÊN NGOÀI (cần cài qua pip)
# =============================================================================

import pandas as pd  # DataFrame - cấu trúc bảng dữ liệu dùng để lưu trữ nến trước khi ghi DB
import requests  # Gửi HTTP request - dùng để đăng nhập TradingView và gửi Discord webhook
import websocket  # Thư viện WebSocket client - kết nối và nhận data real-time từ TradingView
from _helpers import setup_logger, _validate_ohlcv_df  # Hàm khởi tạo logger + validate OHLCV
from _discord import (
    QUICK_COMMANDS_HINT,
    start_bot_listener,
    tg_alert as _tg_alert,
    tg_send as _tg_send,
)
from _task_lock import (
    acquire as _acquire_task_lock,
    is_locked as _is_task_locked,
    release as _release_task_lock,
    renew as _renew_task_lock,
)
from _tv_coord import (
    acquire_live_batch_window,
    is_ws_live_shutdown_requested,
    release_live_batch_window,
    request_ws_live_shutdown,
)
import _tv_auth  # TradingView auth module dùng chung (token cache, refresh, bootstrap)

# =============================================================================
# NHẬP CÁC MODULE NỘI BỘ CỦA PROJECT
# =============================================================================
from config import (
    COMPUTED_TF_DEPS,  # Bảng phụ thuộc: bảng nguồn nào -> tính TF phái sinh nào
    COMPUTED_TIMEFRAMES,  # Danh sách TF phái sinh cần tính (M10, M20, M90, H6, H8)
    LOG_FILE,  # Đường dẫn file log
    SYMBOLS,  # Toàn bộ danh sách symbol theo dõi (37 cặp)
    DISCORD_WEBHOOK_URL,  # Dùng để hiển thị trạng thái "Discord: Enabled/Disabled"
    TF_STAGING,  # Bảng ánh xạ: tf_code -> tên bảng staging trong DB
    TV_AUTH_TOKEN,  # Auth token TradingView đọc từ .env
    TV_COOKIE,  # Cookie TradingView đọc từ .env
    TV_PASSWORD,  # Mật khẩu TradingView (dùng khi không có cookie)
    TV_USERNAME,  # Tên đăng nhập TradingView (dùng khi không có cookie)
)
from modules.db_connector import (
    clean_staging_transitions,  # Xóa transition bar DST / anchor drift sau insert staging
    get_connection,  # Mở kết nối tới SQL Server
    insert_staging_batch,  # Ghi batch nến vào bảng staging của DB
    run_etl_aggregate,  # Tính TF phái sinh bằng cách gộp nến từ TF nhỏ hơn
    run_etl_direct,  # Đẩy dữ liệu từ staging vào bảng chính Fact_OHLCV
    test_connection,  # Kiểm tra kết nối DB có hoạt động không
)

# =============================================================================
# LỌC DANH SÁCH SYMBOL CHO WEBSOCKET
# =============================================================================

# WebSocket chỉ theo dõi Indices, Metal, Crypto - bỏ qua FOREX
# Lý do: Forex có lịch đóng/mở cửa phức tạp theo múi giờ nên xử lý riêng
# Cú pháp: list comprehension - lọc ra các symbol có asset_type thuộc tập hợp cho trước
WS_SYMBOLS = [s for s in SYMBOLS if s["asset_type"] in {"Indice", "Metal", "Crypto"}]


# =============================================================================
# KHỞI TẠO LOGGER
# =============================================================================

# Log file riêng cho ws_live (tách khỏi pipeline.log của 01_data_pipeline.py)
# rotating=True -> RotatingFileHandler 10 MB × 5 files (~60 MB tối đa, đủ ~14 ngày)
_LOG_DIR    = Path(__file__).resolve().parent / "logs"
WS_LOG_FILE = str(_LOG_DIR / "ws_live.log")
logger = setup_logger("ws_live", WS_LOG_FILE, rotating=True)


# =============================================================================
# HẰNG SỐ CẤU HÌNH
# =============================================================================

# Địa chỉ WebSocket của TradingView
TV_BASE_URL           = "wss://data.tradingview.com/socket.io/websocket"

# Số symbol tối đa cho mỗi kết nối WebSocket
# TradingView giới hạn số chart session trên 1 kết nối - không đặt quá cao
WS_SYMBOLS_PER_CONN   = 10

# Số nến yêu cầu TradingView gửi về mỗi lần
# 5 nến: 1 nến hiện tại (chưa đóng) + 4 nến đã đóng -> chỉ lưu 4 nến đã đóng
N_BARS_WS             = 5

# Chu kỳ chạy batch: cứ mỗi 5 phút, hệ thống mở WS, lấy data, rồi đóng
BATCH_INTERVAL_MIN    = 5

# Timeout mỗi lần batch: nếu sau 90 giây vẫn chưa nhận đủ data -> coi như thất bại
BATCH_FETCH_TIMEOUT   = 120

# Số lần retry tối đa nếu batch thất bại trước khi bỏ qua batch đó
BATCH_MAX_RETRIES     = 3

# Thời gian chờ ban đầu trước khi retry lần 1 (giây)
RECONNECT_BASE_SEC    = 30

# Giới hạn tối đa thời gian chờ giữa các lần retry (5 phút)
# Áp dụng exponential back-off: 30s -> 60s -> 120s -> ... -> tối đa 300s
RECONNECT_MAX_SEC     = 300

# Giới hạn kích thước hàng đợi ghi DB
# Nếu hàng đợi đầy (>2000 mục chưa ghi xong) -> chuyển sang overflow buffer
DB_QUEUE_MAXSIZE      = 2000

# Dung lượng buffer dự phòng khi hàng đợi DB đầy
# Nếu cả overflow buffer cũng đầy (>500) -> nến bị mất hoàn toàn
OVERFLOW_BUFFER_MAX   = 500

# Độ trễ giữa mỗi lần đăng ký chart session trong cùng 1 kết nối WS
# Cần thiết để tránh TradingView bị quá tải khi đăng ký nhiều session liên tiếp
SESSION_THROTTLE      = 0.15

# Chu kỳ gửi báo cáo trạng thái lên Discord (3600 giây = 1 giờ)
STATUS_INTERVAL_SEC   = 3600

# Từ khóa nhận biết lỗi token - dùng chung với _tv_auth.py
TOKEN_EXPIRY_KEYWORDS = _tv_auth.TOKEN_EXPIRY_KEYWORDS

# Số lần miss liên tiếp tối đa trước khi gửi cảnh báo Discord
# Nếu cặp (symbol, TF) nào không nhận được data trong MAX_MISS_RETRIES batch liên tiếp
# -> hệ thống cảnh báo ngay và reset đếm (tránh spam)
MAX_MISS_RETRIES      = 5

# Số nến yêu cầu khi cặp (symbol, TF) đang trong backlog (đã bị miss ít nhất 1 batch)
# Đủ để phủ khoảng trống: ví dụ M5 miss 1 batch = 5 phút -> cần 30 nến = 150 phút buffer an toàn
N_BARS_WS_BACKLOG     = 30

# Số lần retry backlog tối đa trước khi coi khoảng trống là vĩnh viễn và dừng retry
# 12 batch × 5 phút = 60 phút - nếu vẫn không lấy được sau 1 giờ -> báo lỗi + bỏ
MAX_BACKLOG_BATCHES   = 12

# Số bar tối đa được phép lưu trong SQLite spool trước khi drop và alert
MAX_SPOOL_ROWS        = 100_000


# Bảng ánh xạ: tên TF nội bộ -> chuỗi interval theo chuẩn TradingView WebSocket API
# TradingView dùng chuỗi riêng để chỉ interval: "1W", "1D", "240" (=H4), v.v.
WS_TF_INTERVAL = {
    "W":   "1W",   # Tuần
    "D1":  "1D",   # Ngày
    "H4":  "240",  # 4 giờ = 240 phút
    "H3":  "180",  # 3 giờ = 180 phút
    "H2":  "120",  # 2 giờ = 120 phút
    "H1":  "60",   # 1 giờ = 60 phút
    "M45": "45",   # 45 phút
    "M30": "30",   # 30 phút
    "M15": "15",   # 15 phút
    "M5":  "5",    # 5 phút
}

# Bảng phụ thuộc TF phái sinh: dùng khi có nến mới trong bảng nguồn
# Ví dụ: khi có nến M5 mới -> tự động tính lại M10, M20
_SOURCE_TO_COMPUTED = COMPUTED_TF_DEPS

# HTTP retry constants - dùng chung với _tv_auth.py
HTTP_MAX_RETRIES    = _tv_auth.HTTP_MAX_RETRIES
HTTP_BASE_DELAY_SEC = _tv_auth.HTTP_BASE_DELAY_SEC
HTTP_MAX_DELAY_SEC  = _tv_auth.HTTP_MAX_DELAY_SEC


# =============================================================================
# TRẠNG THÁI DÙNG CHUNG GIỮA CÁC THREAD
# =============================================================================
# Vì nhiều thread chạy đồng thời (WS, DB worker, scheduler), các biến dưới đây
# phải được bảo vệ bằng Lock để tránh race condition (ghi đồng thời gây lỗi dữ liệu).

# Lock dùng để bảo vệ _stats và _last_bar_ts khi nhiều thread cùng đọc/ghi
_state_lock    = threading.Lock()

# ── ETL deferral khi Checker đang repair ─────────────────────────────────────
# Khi Checker giữ lock 'checker_repair', WS defer Steps B+C để tránh race condition.
# Value = committed watermark candidate (max bar ts của batch đã stage).
_deferred_etl: dict[tuple[int, str, str, str], float] = {}
_deferred_lock        = threading.Lock()
_checker_lock_cache: dict = {"locked": False, "checked_at": 0.0}
_CHECKER_LOCK_TTL     = 30.0           # giây - refresh cache mỗi 30s
_DEFERRED_ETL_WARN    = 2000           # log WARNING khi set đạt ngưỡng này
_DEFERRED_ETL_MAX     = 5000           # log ERROR nếu backlog defer tăng quá cao
_WRITE_DEFER_LOCKS    = ("checker_repair", "warehouse_maintenance")

# Committed watermark: chỉ nhảy lên sau khi ETL direct thành công vào Fact_OHLCV.
_last_bar_ts: dict[tuple[int, str], float] = {}
# Received watermark: bar mới nhất đã nhìn thấy/accept vào queue-spool, chỉ dùng quan sát.
_received_bar_ts: dict[tuple[int, str], float] = {}

# Bộ đếm thống kê hoạt động của hệ thống (hiển thị trong báo cáo định kỳ)
_stats = {
    "bars_inserted": 0,   # Tổng số nến đã lưu thành công vào DB
    "errors":        0,   # Tổng số lỗi phát sinh
    "events":        0,   # Tổng số gói tin WebSocket đã xử lý
    "queue_depth":   0,   # Số mục hiện đang chờ trong hàng đợi DB
    "batches_run":   0,   # Tổng số lần batch đã chạy
}

# Event dùng để ra hiệu tắt chương trình cho tất cả thread
# Khi _shutdown.set() được gọi, tất cả thread đang chờ sẽ thức dậy và dừng
_shutdown      = threading.Event()

# Buffer dự phòng: chứa các nến chưa đưa được vào hàng đợi DB (khi queue đầy)
_overflow_buf  = []
_overflow_lock = threading.Lock()  # Lock riêng để bảo vệ _overflow_buf

# Hàng đợi (FIFO) truyền data từ các WS thread sang DB worker thread
# maxsize=2000 -> nếu DB worker ghi chậm, tối đa chứa 2000 nến chờ
_db_queue: queue.Queue = queue.Queue(maxsize=DB_QUEUE_MAXSIZE)

# Auth state - quản lý bởi _tv_auth.py (dùng _tv_auth._auth_token, _tv_auth._auth_lock, v.v.)

# Đường dẫn file .env
_ENV_FILE: Path = _PROJ / ".env"

# Đường dẫn SQLite spool - durable buffer khi queue + overflow RAM đều đầy
_SPOOL_DB   = Path(__file__).parent / ".overflow_spool.db"
_spool_lock = threading.Lock()

# Bộ đếm số batch liên tiếp đang chạy ở guest mode
_consecutive_guest_batches = 0
# Ngưỡng cảnh báo: sau bao nhiêu batch guest liên tiếp thì gửi alert nặng hơn
_GUEST_ALERT_THRESHOLD     = 3

# Thống kê theo giờ - reset mỗi lần gửi hourly digest lên Discord
_hourly_stats: dict = {
    "batches":          0,  # Số batch chạy trong giờ qua
    "bars":             0,  # Tổng bars nhận được trong giờ qua
    "zero_bar_batches": 0,  # Số batch nhận được 0 bar
    "backlog_peak":     0,  # Backlog peak (số pairs) trong giờ qua
}
_hourly_lock = threading.Lock()

# Bộ đếm backfill miss: số lần LIÊN TIẾP không nhận được data cho mỗi cặp (symbol_id, tf_code)
# Khi counter đạt MAX_MISS_RETRIES -> cảnh báo Discord ngay, reset counter (tránh spam)
# Khi cặp đó nhận được data trở lại -> counter tự động xóa
_missed_pairs: dict[tuple[int, str], int] = {}
_missed_lock  = threading.Lock()   # Lock riêng để không tranh chấp với _state_lock

# Backlog: các cặp (symbol_id, tf_code) bị miss ≥1 batch -> lần sau yêu cầu N_BARS_WS_BACKLOG
# Giá trị = số lần miss liên tiếp; xóa khi cặp đó nhận được data trở lại
_backlog: dict[tuple[int, str], int] = {}
_backlog_lock = threading.Lock()   # Lock riêng để bảo vệ _backlog

# Thống kê hourly delta: reset mỗi giờ khi _status_reporter chạy
_hourly_stats: dict = {
    "batches":          0,
    "bars":             0,
    "zero_bar_batches": 0,
    "backlog_peak":     0,
    "pair_bars":        {},   # {(symbol_id, tf_code): tổng bars} trong giờ qua
}
_hourly_lock = threading.Lock()

# Buffer aggregate: (symbol_id, target_tf, src_table, tv_symbol) chưa được flush
# Được flush khi queue DB tạm rỗng - tránh gọi run_etl_aggregate per-bar
_pending_agg: set[tuple[int, str, str, str]] = set()


# =============================================================================
# AUTH FUNCTIONS - delegated to _tv_auth (shared auth module)
# All TradingView authentication logic lives in data_provider/_tv_auth.py
# =============================================================================

def _http_request_with_retry(method, url, *, max_retries=HTTP_MAX_RETRIES,
                              base_delay=HTTP_BASE_DELAY_SEC, max_delay=HTTP_MAX_DELAY_SEC,
                              **kwargs):
    """HTTP request với retry - proxy về _tv_auth._http_request_with_retry."""
    return _tv_auth._http_request_with_retry(method, url, max_retries=max_retries,
                                              base_delay=base_delay, max_delay=max_delay,
                                              **kwargs)


def _renew_auth_token() -> None:
    """Gia hạn token giữa chừng - proxy về _tv_auth.renew()."""
    _tv_auth.renew(logger)


def _check_and_maybe_refresh_token() -> None:
    """Chủ động làm mới token nếu sắp hết hạn - proxy về _tv_auth.check_and_refresh()."""
    _tv_auth.check_and_refresh(logger)


def _bootstrap_credentials() -> tuple:
    """Bootstrap credentials lúc startup - proxy về _tv_auth.bootstrap()."""
    return _tv_auth.bootstrap(logger)


def _resolve_auth_token() -> tuple:
    """Resolve auth token qua 4 lớp - proxy về _tv_auth._resolve_auth_token()."""
    return _tv_auth._resolve_auth_token(logger)


def _load_token_cache() -> dict:
    """Đọc token cache file - proxy về _tv_auth._load_token_cache()."""
    return _tv_auth._load_token_cache()


# =============================================================================
# KHỞI ĐỘNG - NẠP WATERMARK TỪ DATABASE
# =============================================================================

def _load_watermarks() -> None:
    """
    Đọc từ DB thời điểm nến mới nhất đã lưu của mỗi cặp (symbol, TF).
    Dữ liệu này được lưu vào _last_bar_ts và dùng làm "watermark".

    Watermark là gì-
        Là dấu mốc thời gian - hệ thống chỉ lưu những nến có thời gian
        SAU watermark, đảm bảo không bao giờ lưu nến trùng lặp vào DB.

    Tại sao cần load từ DB khi khởi động-
        Khi chương trình restart, _last_bar_ts bị reset về rỗng.
        Nếu không nạp lại từ DB, hệ thống sẽ lưu lại toàn bộ nến cũ.
    """
    logger.info("[INIT] Loading watermarks from DWH.Fact_OHLCV...")
    loaded = 0
    try:
        conn   = get_connection()
        cursor = conn.cursor()

        # 1 query trên Fact_OHLCV thay vì 10 query riêng trên staging tables.
        # Lý do: staging bị purge sau 7 ngày -> watermark về 0 khi restart nếu dùng staging.
        # Fact_OHLCV lưu vĩnh viễn nên watermark luôn chính xác dù restart bao nhiêu lần.
        ws_tf_codes  = list(WS_TF_INTERVAL.keys())   # 10 TF trực tiếp WS cần theo dõi
        placeholders = ",".join("?" * len(ws_tf_codes))
        cursor.execute(f"""
            SELECT f.SymbolID, tf.Code, MAX(f.BarTime)
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE tf.Code IN ({placeholders})
              AND f.BarTime < DATEADD(minute, 1, GETUTCDATE())
            GROUP BY f.SymbolID, tf.Code
        """, ws_tf_codes)
        for symbol_id, tf_code, max_bt in cursor.fetchall():
            if max_bt is not None:
                # Chuyển datetime sang Unix timestamp (số giây từ 1970)
                # để dễ so sánh hơn với timestamp từ TradingView
                _last_bar_ts[(symbol_id, tf_code)] = max_bt.timestamp()
                loaded += 1

        conn.close()
    except Exception as exc:
        # Nếu không kết nối được DB -> bắt đầu từ watermark = 0 (không có gì)
        logger.warning("[INIT] Watermark load failed (starting from zero): %s", exc)

    logger.info("[INIT] Watermarks loaded: %d entries.", loaded)

    # Kiểm tra watermark cũ - cảnh báo nếu có cặp dữ liệu stale khi khởi động.
    # Stale = khoảng trống > 3× chu kỳ TF (ví dụ: H1 stale nếu data cũ hơn 3 giờ).
    # Cảnh báo này không chặn startup, chỉ nhắc operator cân nhắc chạy backfill trước.
    from _helpers import TF_MINUTES  # import cục bộ tránh circular ở module level
    now_ts = datetime.now(timezone.utc).timestamp()
    stale = [
        (sym_id, tf_code, (now_ts - wm_ts) / 60)
        for (sym_id, tf_code), wm_ts in _last_bar_ts.items()
        if (now_ts - wm_ts) / 60 > TF_MINUTES.get(tf_code, 60) * 3
    ]
    if stale:
        worst = max(stale, key=lambda x: x[2])
        logger.warning(
            "[STARTUP] Found %d old data pairs - worst: SymbolID=%d %s (%.0f minutes old). "
            "Suggested fix: run 01_data_pipeline.py --mode gap first.",
            len(stale), worst[0], worst[1], worst[2],
        )
        _tg_alert(
            "WARNING",
            f"[WARN] Startup found {len(stale)} old data pairs.\n"
            f"Worst: SymbolID={worst[0]} {worst[1]} ({worst[2]:.0f} minutes old)\n"
            f"Consider running backfill before live mode."
        )


def _future_cutoff_ts() -> float:
    """Grace period nhỏ để tránh false-positive với bar vừa đóng."""
    return datetime.now(timezone.utc).timestamp() + 60.0


def _set_received_watermark(key: tuple[int, str], max_ts: float) -> None:
    """Theo dõi bar mới nhất đã được accept vào queue/overflow/spool."""
    with _state_lock:
        _received_bar_ts[key] = max(max_ts, _received_bar_ts.get(key, 0.0))


def _set_committed_watermark(key: tuple[int, str], max_ts: float) -> None:
    """Chỉ cập nhật watermark filter sau khi Fact commit xong."""
    with _state_lock:
        _last_bar_ts[key] = max(max_ts, _last_bar_ts.get(key, 0.0))


# =============================================================================
# OVERFLOW BUFFER - Xử lý khi hàng đợi DB đầy
# =============================================================================

def _flush_overflow_to_queue() -> None:
    """
    Thử chuyển các nến đang chờ trong overflow buffer vào hàng đợi DB.
    Được gọi định kỳ bởi DB worker khi hàng đợi có chỗ trống.
    """
    with _overflow_lock:
        if not _overflow_buf:
            return  # Buffer trống -> không cần làm gì

        recharged = 0   # Đếm số nến chuyển thành công
        remaining = []  # Những nến vẫn chưa đưa được vào queue

        for item in _overflow_buf:
            try:
                # put_nowait: thêm vào queue không chờ đợi
                # Nếu queue vẫn đầy -> ném exception queue.Full
                _db_queue.put_nowait(item)
                recharged += 1
            except queue.Full:
                # Queue vẫn đầy -> giữ nến này lại trong buffer
                remaining.append(item)

        # Cập nhật buffer: chỉ giữ lại các nến chưa đưa được vào queue
        _overflow_buf[:] = remaining

        if recharged:
            logger.info("[DB ] Recharged %d bar(s) from overflow buffer.", recharged)

    # Flush thêm từ SQLite spool khi queue còn chỗ
    if not _db_queue.full():
        _spool_flush_to_queue()


# =============================================================================
# DURABLE SPOOL - SQLite backup khi cả queue lẫn overflow RAM đều đầy
# =============================================================================

def _init_spool_db() -> None:
    """
    Tạo bảng spool trong SQLite local (tạo file nếu chưa có).
    Gọi một lần khi khởi động. An toàn khi gọi nhiều lần (CREATE IF NOT EXISTS).
    """
    with _spool_lock:
        with sqlite3.connect(_SPOOL_DB) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS spool (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol_id     INTEGER NOT NULL,
                    tf_code       TEXT    NOT NULL,
                    staging_table TEXT    NOT NULL,
                    tv_symbol     TEXT    NOT NULL,
                    bar_data      BLOB    NOT NULL,
                    created_at    TEXT    DEFAULT (datetime('now'))
                )
            """)
            con.commit()
    logger.info("[SPOOL] Persistent spool ready: %s", _SPOOL_DB)


def _spool_write(item: tuple) -> bool:
    """
    Serialize 1 bar và ghi vào SQLite spool.
    Được gọi khi cả DB queue lẫn overflow buffer RAM đều đầy.
    Bar sẽ được đọc lại và đưa vào queue khi có chỗ trống.
    Drop và alert khi spool vượt MAX_SPOOL_ROWS.
    Trả về True nếu ghi thành công, False nếu spool đã đầy và bar bị từ chối.
    """
    symbol_id, tf_code, staging_table, tv_symbol, df = item
    blob = pickle.dumps(df)
    with _spool_lock:
        with sqlite3.connect(_SPOOL_DB) as con:
            count = con.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
            if count >= MAX_SPOOL_ROWS:
                logger.error(
                    "[SPOOL] Offline spool is full (%d bars) - dropping bar %s %s.",
                    MAX_SPOOL_ROWS, tv_symbol, tf_code,
                )
                try:
                    _tg_alert(
                        "CRITICAL",
                        f"[ERROR] WS spool is full ({MAX_SPOOL_ROWS} rows) - data is being dropped.\n"
                        f"Check the DB connection now. Dropped bar: {tv_symbol}/{tf_code}",
                    )
                except Exception:
                    pass
                return False
            con.execute(
                "INSERT INTO spool (symbol_id,tf_code,staging_table,tv_symbol,bar_data) VALUES (?,?,?,?,?)",
                (symbol_id, tf_code, staging_table, tv_symbol, blob),
            )
            con.commit()
    return True


def _spool_flush_to_queue() -> int:
    """
    Đọc các bar từ SQLite spool và đưa vào DB queue khi có chỗ trống.
    Xóa khỏi SQLite sau khi đưa vào queue thành công.
    Trả về số bar đã chuyển được.
    """
    flushed = 0
    with _spool_lock:
        with sqlite3.connect(_SPOOL_DB) as con:
            rows = con.execute(
                "SELECT id,symbol_id,tf_code,staging_table,tv_symbol,bar_data "
                "FROM spool ORDER BY id LIMIT 200"
            ).fetchall()
            for row_id, sym_id, tf_code, stg_tbl, tv_sym, blob in rows:
                try:
                    df   = pickle.loads(blob)
                    item = (sym_id, tf_code, stg_tbl, tv_sym, df)
                    _db_queue.put_nowait(item)
                    con.execute("DELETE FROM spool WHERE id=?", (row_id,))
                    flushed += 1
                except queue.Full:
                    break  # Queue vẫn đầy -> giữ lại phần còn trong SQLite
            con.commit()
    if flushed:
        logger.info("[SPOOL] Recovered %d bar(s) from persistent spool.", flushed)
    with _state_lock:
        _stats["queue_depth"] = _db_queue.qsize()
    return flushed


def _safe_spool_count() -> int | None:
    """Best-effort spool depth snapshot for diagnostics; never raise to callers."""
    try:
        with _spool_lock:
            with sqlite3.connect(_SPOOL_DB) as con:
                row = con.execute("SELECT COUNT(*) FROM spool").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None


def _spool_cleanup_old() -> int:
    """Xóa entries trong spool cũ hơn 48 giờ. Gọi mỗi giờ từ _status_reporter."""
    try:
        with _spool_lock:
            with sqlite3.connect(_SPOOL_DB) as con:
                con.execute("DELETE FROM spool WHERE created_at < datetime('now', '-48 hours')")
                deleted = con.total_changes
                con.commit()
        if deleted:
            logger.info("[SPOOL] Cleaned up %d stale entries (>48h).", deleted)
        return deleted
    except Exception as exc:
        logger.warning("[SPOOL] cleanup_old failed: %s", exc)
        return 0


def _enqueue_or_buffer(item: tuple, group_id: int, tv_symbol: str, tf_code: str) -> str:
    """
    Thêm 1 nến vào hàng đợi DB.
    Nếu hàng đợi đầy -> chuyển sang overflow buffer.
    Nếu cả 2 đều đầy -> nến bị mất hoàn toàn (cảnh báo ngay).

    item: tuple gồm (symbol_id, tf_code, staging_table, tv_symbol, dataframe)
    """
    try:
        # Thử thêm nến vào hàng đợi DB ngay lập tức
        _db_queue.put_nowait(item)
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()
        return "queued"
    except queue.Full:
        # Hàng đợi đầy -> chuyển sang overflow buffer
        with _overflow_lock:
            buf_len = len(_overflow_buf)
            if buf_len < OVERFLOW_BUFFER_MAX:
                _overflow_buf.append(item)
                new_len = buf_len + 1
                logger.warning(
                    "[G%d] Queue full - buffered: %s %s (overflow: %d)",
                    group_id, tv_symbol, tf_code, new_len,
                )
                # Cảnh báo sớm khi buffer đạt 80% dung lượng (tránh mất dữ liệu)
                warn_threshold = int(OVERFLOW_BUFFER_MAX * 0.8)
                if new_len >= warn_threshold and buf_len < warn_threshold:
                    logger.error(
                        "[OVERFLOW] Buffer near capacity: %d/%d (80%%) - DB worker may be stuck",
                        new_len, OVERFLOW_BUFFER_MAX,
                    )
                    _tg_alert(
                        "WARNING",
                        f"[WARN] Overflow buffer is almost full: {new_len}/{OVERFLOW_BUFFER_MAX}\n"
                        f"The DB worker may be slow. Check the DB connection."
                    )
                with _state_lock:
                    _stats["queue_depth"] = _db_queue.qsize()
                return "buffered"
            else:
                # Cả queue lẫn overflow RAM đều đầy -> ghi vào SQLite spool (durable)
                try:
                    if not _spool_write(item):
                        with _state_lock:
                            _stats["errors"] += 1
                            _stats["queue_depth"] = _db_queue.qsize()
                        return "rejected"
                    logger.warning(
                        "[G%d] Queue+overflow full - spooled to disk: %s %s",
                        group_id, tv_symbol, tf_code,
                    )
                    with _state_lock:
                        _stats["queue_depth"] = _db_queue.qsize()
                    return "spooled"
                except Exception as exc:
                    # SQLite cũng fail -> chỉ khi đĩa đầy hoặc quyền ghi bị chặn
                    logger.error(
                        "[G%d] Spool write failed - bar dropped: %s %s: %s",
                        group_id, tv_symbol, tf_code, exc,
                    )
                    _tg_alert(
                        "ERROR",
                        f"Queue, overflow buffer, and SQLite spool all failed.\n"
                        f"Lost bar: {tv_symbol} {tf_code}\n"
                        f"Check database writer and disk space now."
                        + QUICK_COMMANDS_HINT
                    )
                    with _state_lock:
                        _stats["errors"] += 1
                        _stats["queue_depth"] = _db_queue.qsize()
                    return "rejected"
    except Exception as exc:
        logger.exception(
            "[G%d] Unexpected enqueue failure - bar DROPPED: %s %s: %s",
            group_id, tv_symbol, tf_code, exc,
        )
        with _state_lock:
            _stats["errors"] += 1
            _stats["queue_depth"] = _db_queue.qsize()
        return "rejected"


# =============================================================================
# THREAD GHI DATABASE (DB Worker)
# =============================================================================

def _checker_is_repairing() -> bool:
    """
    Kiểm tra Checker có đang giữ lock 'checker_repair' không.

    Có cache 30 giây để tránh DB hammering trong _db_worker() tight loop.
    Fail-open: nếu DB lỗi -> trả về False (không defer vô hạn, ETL tiếp tục bình thường).
    """
    now = time.monotonic()
    if now - _checker_lock_cache["checked_at"] < _CHECKER_LOCK_TTL:
        return _checker_lock_cache["locked"]
    try:
        from _task_lock import is_locked
        result = any(is_locked(task_name) for task_name in _WRITE_DEFER_LOCKS)
    except Exception:
        result = False   # fail-open
    _checker_lock_cache.update({"locked": result, "checked_at": now})
    return result


def _db_worker() -> None:
    """
    Luồng chạy độc lập, liên tục lấy nến từ hàng đợi và ghi vào database.

    Tại sao cần thread riêng-
        Ghi DB mất thời gian (vài ms đến vài trăm ms). Nếu để WS thread ghi DB,
        trong thời gian đó WS không xử lý được gói tin mới -> mất data.
        Giải pháp: WS thread chỉ đưa nến vào queue, thread riêng lấy ra và ghi.

    Luồng này KHÔNG phải daemon -> chương trình sẽ chờ nó xử lý hết queue trước khi thoát.
    """
    logger.info("[DB ] Worker started.")

    # Vòng lặp: tiếp tục chạy cho đến khi _shutdown được set VÀ queue/overflow/spool đều rỗng.
    while True:
        # Tranh thủ mỗi vòng lặp: thử chuyển nến từ overflow buffer/spool vào queue.
        _flush_overflow_to_queue()

        if _shutdown.is_set() and _db_queue.empty():
            with _overflow_lock:
                overflow_pending = len(_overflow_buf)
            spool_pending = _safe_spool_count() or 0
            if overflow_pending == 0 and spool_pending == 0:
                break

        # Lấy 1 nến từ hàng đợi, chờ tối đa 1 giây
        # Nếu sau 1 giây queue vẫn rỗng -> tiếp tục vòng lặp (không block mãi)
        try:
            item = _db_queue.get(timeout=1.0)
            with _state_lock:
                _stats["queue_depth"] = _db_queue.qsize()
        except queue.Empty:
            continue  # Queue rỗng -> quay lại đầu vòng lặp

        # Giải nén thông tin từ tuple
        symbol_id, tf_code, staging_table, tv_symbol, df = item
        key = (symbol_id, tf_code)

        # Lọc bars sai alignment (DST artifact) - cùng logic với 01_data_pipeline
        df, _ = _validate_ohlcv_df(df, tv_symbol, tf_code, logger)
        if df.empty:
            _db_queue.task_done()
            continue

        # BƯỚC A: Ghi nến vào bảng staging trong DB (có retry 3 lần khi DB tạm lỗi)
        _DB_WORKER_RETRIES = 3
        inserted = 0
        _staging_ok = False
        for _attempt in range(1, _DB_WORKER_RETRIES + 1):
            try:
                inserted = insert_staging_batch(df, symbol_id, staging_table)
                _staging_ok = True
                break
            except Exception as exc:
                if _attempt == _DB_WORKER_RETRIES:
                    logger.error(
                        "[DB] Saving bar to staging failed after %d tries - data was lost. %s %s: %s",
                        _DB_WORKER_RETRIES, tv_symbol, tf_code, exc,
                    )
                    with _state_lock:
                        _stats["errors"] += 1
                    _tg_alert(
                        "ERROR",
                        f"[ERROR] Staging FAILED: DB worker staging failed for {tv_symbol}/{tf_code} after "
                        f"{_DB_WORKER_RETRIES} tries - the bar was lost.\n`{exc}`",
                    )
                else:
                    logger.warning(
                        "[DB] Try %d/%d failed - retrying in 5s... %s %s: %s",
                        _attempt, _DB_WORKER_RETRIES, tv_symbol, tf_code, exc,
                    )
                    _shutdown.wait(5)
        if not _staging_ok:
            _db_queue.task_done()
            continue

        # BƯỚC A2: Dọn transition bar DST / anchor drift khỏi staging
        if tf_code in ('M45', 'H2', 'H3', 'H4'):
            from config import TF_MINUTES
            n_cleaned = clean_staging_transitions(symbol_id, staging_table, TF_MINUTES[tf_code])
            if n_cleaned > 0:
                logger.info("[DB ] ANCHOR_CLEAN %s %s: removed %d transition bar(s)",
                            tv_symbol, tf_code, n_cleaned)

        if inserted > 0:
            with _state_lock:
                _stats["bars_inserted"] += inserted
            logger.info("[DB ] %s %s: +%d bar(s) staged.", tv_symbol, tf_code, inserted)

        # Dù inserted=0 vẫn phải ETL/defer, vì bars có thể đã nằm sẵn ở staging
        # từ lần fail trước và đang chờ được đẩy vào Fact.
        max_committed_ts = max(ts.timestamp() for ts in df.index)

        if _checker_is_repairing():
            with _deferred_lock:
                defer_key = (symbol_id, tf_code, staging_table, tv_symbol)
                n_deferred = len(_deferred_etl)
                if n_deferred >= _DEFERRED_ETL_MAX:
                    logger.error(
                        "[DB ] Deferred ETL backlog is high (%d/%d) - %s %s will stay in staging.",
                        n_deferred, _DEFERRED_ETL_MAX, tv_symbol, tf_code,
                    )
                    _tg_alert(
                        "WARNING",
                        f"Deferred ETL backlog is high: {n_deferred}/{_DEFERRED_ETL_MAX}\n"
                        f"Data for {tv_symbol} {tf_code} is staged and waiting for checker to release the lock."
                    )
                else:
                    if n_deferred >= _DEFERRED_ETL_WARN:
                        logger.warning(
                            "[DB ] Deferred ETL set %d/%d - checker lock is taking a long time",
                            n_deferred, _DEFERRED_ETL_MAX,
                        )
                _deferred_etl[defer_key] = max(
                    max_committed_ts,
                    _deferred_etl.get(defer_key, 0.0),
                )
            logger.info(
                "[DB ] Checker lock active - deferred ETL for %s %s",
                tv_symbol, tf_code,
            )
        else:
            # BƯỚC B: Đẩy nến từ staging vào bảng chính Fact_OHLCV (ETL direct)
            try:
                run_etl_direct(symbol_id, tf_code, staging_table)
            except Exception as exc:
                logger.error("[DB ] ETL direct error - %s %s: %s", tv_symbol, tf_code, exc)
                with _state_lock:
                    _stats["errors"] += 1
            else:
                _set_committed_watermark(key, max_committed_ts)

            # BƯỚC C: Thêm vào _pending_agg thay vì tính ngay
            # Tránh gọi run_etl_aggregate per-bar - flush khi queue tạm rỗng
            if staging_table in _SOURCE_TO_COMPUTED:
                for target_tf, src_table in _SOURCE_TO_COMPUTED[staging_table]:
                    _pending_agg.add((symbol_id, target_tf, src_table, tv_symbol))

        # Báo cho queue biết đã xử lý xong item này (cần thiết cho queue.join())
        _db_queue.task_done()

        # Cập nhật thống kê số lượng nến còn đang chờ trong queue
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()

        # Flush aggregates khi queue tạm rỗng - gộp tất cả cùng symbol/TF lại
        # Guard: không flush khi Checker đang repair (tránh race condition)
        if _pending_agg and _db_queue.empty() and not _checker_is_repairing():
            for sym_id, tgt_tf, src_tbl, sym_name in list(_pending_agg):
                try:
                    run_etl_aggregate(sym_id, tgt_tf, src_tbl)
                    logger.info("[DB ] %s -> computed %s.", sym_name, tgt_tf)
                except Exception as exc:
                    logger.error("[DB ] ETL aggregate error - %s %s: %s", sym_name, tgt_tf, exc)
                    with _state_lock:
                        _stats["errors"] += 1
            _pending_agg.clear()

        # Retry deferred ETL khi Checker đã release lock
        with _deferred_lock:
            if _deferred_etl and not _checker_is_repairing():
                logger.info("[DB ] Processing %d deferred ETL item(s)...", len(_deferred_etl))
                # Invalidate cache để lần check tiếp theo query DB thật
                _checker_lock_cache["checked_at"] = 0.0
                still_deferred: dict[tuple[int, str, str, str], float] = {}
                for (sym_id, tf_c, stg_tbl, sym_nm), max_ts in list(_deferred_etl.items()):
                    try:
                        run_etl_direct(sym_id, tf_c, stg_tbl)
                        logger.info("[DB ] Deferred ETL done: %s %s", sym_nm, tf_c)
                        _set_committed_watermark((sym_id, tf_c), max_ts)
                        if stg_tbl in _SOURCE_TO_COMPUTED:
                            for tgt_tf, src_tbl in _SOURCE_TO_COMPUTED[stg_tbl]:
                                _pending_agg.add((sym_id, tgt_tf, src_tbl, sym_nm))
                    except Exception as exc:
                        logger.error(
                            "[DB ] Deferred ETL error - %s %s: %s", sym_nm, tf_c, exc
                        )
                        with _state_lock:
                            _stats["errors"] += 1
                        still_deferred[(sym_id, tf_c, stg_tbl, sym_nm)] = max_ts
                _deferred_etl.clear()
                _deferred_etl.update(still_deferred)

    # Retry deferred ETL một lần cuối nếu checker đã release lock trong lúc shutdown.
    with _deferred_lock:
        if _deferred_etl and not _checker_is_repairing():
            logger.info("[DB ] Processing %d deferred ETL item(s) before shutdown...", len(_deferred_etl))
            still_deferred: dict[tuple[int, str, str, str], float] = {}
            for (sym_id, tf_c, stg_tbl, sym_nm), max_ts in list(_deferred_etl.items()):
                try:
                    run_etl_direct(sym_id, tf_c, stg_tbl)
                    logger.info("[DB ] Deferred ETL done before shutdown: %s %s", sym_nm, tf_c)
                    _set_committed_watermark((sym_id, tf_c), max_ts)
                    if stg_tbl in _SOURCE_TO_COMPUTED:
                        for tgt_tf, src_tbl in _SOURCE_TO_COMPUTED[stg_tbl]:
                            _pending_agg.add((sym_id, tgt_tf, src_tbl, sym_nm))
                except Exception as exc:
                    logger.error(
                        "[DB ] Deferred ETL error (shutdown) - %s %s: %s", sym_nm, tf_c, exc
                    )
                    with _state_lock:
                        _stats["errors"] += 1
                    still_deferred[(sym_id, tf_c, stg_tbl, sym_nm)] = max_ts
            _deferred_etl.clear()
            _deferred_etl.update(still_deferred)

    # Flush các aggregate còn lại trước khi tắt (queue đã rỗng nhưng pending chưa chạy).
    if _pending_agg and not _checker_is_repairing():
        logger.info("[DB ] Flushing %d pending aggregate(s) before shutdown...", len(_pending_agg))
        for sym_id, tgt_tf, src_tbl, sym_name in list(_pending_agg):
            try:
                run_etl_aggregate(sym_id, tgt_tf, src_tbl)
            except Exception as exc:
                logger.error("[DB ] ETL aggregate error (shutdown flush) - %s %s: %s",
                             sym_name, tgt_tf, exc)
        _pending_agg.clear()
    elif _pending_agg:
        logger.warning(
            "[DB ] Skipping %d pending aggregate(s) on shutdown because checker lock is active.",
            len(_pending_agg),
        )

    logger.info("[DB ] Worker stopped.")


# =============================================================================
# CÁC HÀM TIỆN ÍCH
# =============================================================================

def _gen_id(prefix: str) -> str:
    """
    Tạo một ID ngẫu nhiên với định dạng: prefix_xxxxxxxxxxxx
    Dùng để tạo tên chart session duy nhất cho mỗi cặp (symbol, TF).

    Tại sao cần ID ngẫu nhiên-
        TradingView yêu cầu mỗi chart session có ID riêng biệt.
        Nếu dùng ID cố định, có thể bị xung đột khi mở nhiều session.
    """
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}_{suffix}"


def _send(ws, msg: list) -> None:
    """
    Đóng gói và gửi lệnh theo giao thức độc quyền của TradingView WebSocket.

    Giao thức TradingView có định dạng: ~m~{LEN}~m~{JSON_PAYLOAD}
        - ~m~ là ký hiệu phân cách của TradingView (không phải JSON chuẩn)
        - LEN là độ dài của JSON payload tính theo byte
        - JSON payload có cấu trúc: { "m": "tên_lệnh", "p": [tham_số_1, tham_số_2, ...] }

    Ví dụ lệnh xác thực: _send(ws, ["set_auth_token", "eyJhbGci..."])
    -> Gửi: ~m~45~m~{"m":"set_auth_token","p":["eyJhbGci..."]}
    """
    method  = msg[0]        # Tên lệnh (ví dụ: "set_auth_token", "chart_create_session")
    params  = list(msg[1:]) # Các tham số của lệnh
    payload = json.dumps({"m": method, "p": params})  # Chuyển thành JSON string
    ws.send(f"~m~{len(payload)}~m~{payload}")          # TradingView wire format


def _parse_packets(raw: str) -> list[str]:
    """
    Tách chuỗi gói tin ghép theo giao thức TradingView.

    TradingView có thể gửi nhiều gói tin trong 1 message WebSocket, ghép lại với nhau:
        ~m~10~m~{"m":"du"}~m~15~m~{"m":"heartbeat"}

    Hàm này đọc từng gói theo thứ tự, tách ra thành danh sách các JSON string riêng.
    Trả về: ["{"m":"du"}", "{"m":"heartbeat"}"]
    """
    packets, pos = [], 0
    while pos < len(raw):
        # Kiểm tra có phải bắt đầu gói tin không (phải có ký hiệu ~m~)
        if raw[pos: pos + 3] != "~m~":
            break
        pos += 3

        # Tìm ký hiệu ~m~ tiếp theo để xác định vị trí kết thúc của phần độ dài
        sep = raw.find("~m~", pos)
        if sep == -1:
            break

        # Lấy chuỗi độ dài (ví dụ: "45") và chuyển thành số nguyên
        length_str = raw[pos:sep]
        pos = sep + 3
        try:
            length = int(length_str)
            # Cắt đúng số ký tự = length để lấy nội dung gói tin
            packets.append(raw[pos: pos + length])
            pos += length
        except ValueError:
            break  # Gặp dữ liệu không hợp lệ -> dừng lại

    return packets


def _bars_to_df(bars: list) -> pd.DataFrame:
    """
    Chuyển danh sách nến nhận từ TradingView sang DataFrame chuẩn.

    Dữ liệu nến từ TradingView có dạng:
        { "v": [timestamp, open, high, low, close, volume] }
        - v[0]: timestamp Unix (số giây từ 1970)
        - v[1]: open  (giá mở cửa)
        - v[2]: high  (giá cao nhất)
        - v[3]: low   (giá thấp nhất)
        - v[4]: close (giá đóng cửa)
        - v[5]: volume (khối lượng giao dịch)

    Trả về DataFrame với index là datetime UTC, các cột là OHLCV.
    Trả về DataFrame rỗng nếu không có nến hợp lệ.
    """
    records = []
    for bar in bars:
        v = bar.get("v", [])
        if len(v) < 6:
            continue  # Bỏ qua nến thiếu dữ liệu

        # Chuyển timestamp Unix -> datetime UTC, bỏ timezone info để lưu vào DB
        ts = datetime.fromtimestamp(v[0], tz=timezone.utc).replace(tzinfo=None)
        records.append({
            "__ts__": ts,
            "open":   float(v[1]),
            "high":   float(v[2]),
            "low":    float(v[3]),
            "close":  float(v[4]),
            # v[5] == v[5] là cách check NaN (NaN != NaN trong Python)
            # Nếu volume là NaN -> lưu None thay vì giá trị vô nghĩa
            "volume": float(v[5]) if v[5] == v[5] else None,
        })

    if not records:
        return pd.DataFrame()  # Không có nến hợp lệ -> trả về DataFrame rỗng

    # Tạo DataFrame, đặt cột thời gian làm index
    df = pd.DataFrame(records).set_index("__ts__")
    df.index.name = None  # Bỏ tên index để gọn hơn khi lưu vào DB
    return df


def _is_token_error(msg_type: str, data: str) -> bool:
    """
    Kiểm tra xem gói tin từ TradingView có phải lỗi xác thực không.
    Nếu đúng -> cần gia hạn token.
    """
    if msg_type in ("error", "critical_error"):
        # Tìm các từ khóa lỗi xác thực trong nội dung gói tin (không phân biệt hoa thường)
        return any(kw in data.lower() for kw in TOKEN_EXPIRY_KEYWORDS)
    return False


def _update_missed_pairs(
    received: set[tuple[int, str]],
    missed:   set[tuple[int, str]],
) -> None:
    """
    Cập nhật bộ đếm miss liên tiếp sau mỗi batch.

    Logic:
    ┌──────────────────────────────────────────────────────────────────────────┐
    │ Cặp (symbol_id, tf_code) nhận được data  -> xóa khỏi bộ đếm (reset = 0) │
    │ Cặp bị miss lần này                      -> tăng counter +1              │
    │ Counter >= MAX_MISS_RETRIES               -> cảnh báo Discord, reset     │
    └──────────────────────────────────────────────────────────────────────────┘

    Tham số:
        received : tập hợp (symbol_id, tf_code) đã nhận được response từ TradingView
        missed   : tập hợp (symbol_id, tf_code) đã đăng ký nhưng không nhận được response
    """
    alerts: list[tuple[tuple[int, str], int]] = []

    with _missed_lock:
        # Xóa counter cho các cặp đã nhận được data - chúng đang hoạt động bình thường
        for key in received:
            _missed_pairs.pop(key, None)

        # Tăng counter cho các cặp bị miss lần này
        for key in missed:
            count = _missed_pairs.get(key, 0) + 1
            _missed_pairs[key] = count

            if count >= MAX_MISS_RETRIES:
                alerts.append((key, count))
                # Reset về 0 để tránh spam: sẽ cảnh báo lại sau MAX_MISS_RETRIES lần tiếp theo
                _missed_pairs[key] = 0

    # Gửi cảnh báo ngoài lock để không block thread khác
    for (symbol_id, tf_code), count in alerts:
        # Tra tên symbol để thông báo dễ đọc hơn
        sym_name = next(
            (s["tv_symbol"] for s in WS_SYMBOLS if s["symbol_id"] == symbol_id),
            str(symbol_id),
        )
        logger.warning(
            "[MISS] %s [%s] missed %d batch(es) in a row - sending alert.",
            sym_name, tf_code, count,
        )
        _tg_alert(
            "WARNING",
            f"[WARN] <b>Repeated backfill miss</b>\n"
            f"Symbol : {sym_name}\n"
            f"TF     : {tf_code}\n"
            f"Count  : {count} batches in a row\n"
            f"Check the TradingView connection, or check whether the symbol was delisted.",
        )


# =============================================================================
# CLASS BatchFetcher - Một kết nối WebSocket cho một nhóm symbol
# =============================================================================

class BatchFetcher:
    """
    Đại diện cho 1 kết nối WebSocket phụ trách một nhóm symbol (tối đa 10 symbol).
    Mỗi lần gọi fetch() sẽ:
        1. Mở kết nối WebSocket mới đến TradingView
        2. Xác thực bằng auth token
        3. Đăng ký chart session cho từng cặp (symbol × TF)
        4. Chờ nhận data từ tất cả sessions (hoặc đến khi timeout)
        5. Đóng kết nối WebSocket

    Không giữ kết nối liên tục - mỗi lần fetch() là 1 vòng đời kết nối hoàn chỉnh.
    """

    def __init__(self, group_id: int, symbols: list) -> None:
        self.group_id  = group_id   # Số thứ tự nhóm (0, 1, 2, ...) để phân biệt trong log
        self.symbols   = symbols    # Danh sách symbol (tối đa 10) mà nhóm này quản lý

        # Bảng ánh xạ: chart session ID -> (symbol_id, tf_code, staging_table, tv_symbol)
        # Dùng để biết gói tin từ session nào là của symbol/TF nào
        self._cs_map: dict[str, tuple[int, str, str, str]] = {}

        # Tập hợp ID của các session đã đăng ký xong (đã gửi lệnh đăng ký thành công)
        self._expected: set[str] = set()

        # Tập hợp ID của các session đã nhận được phản hồi data từ TradingView
        self._received: set[str] = set()

        # Đếm số nến thực sự mới (chưa có trong DB) nhận được trong batch này
        self._new_bars_count = 0

        # Số nến mới per cặp (symbol_id, tf_code) - dùng để in bảng tóm tắt cuối batch
        self._pair_new_bars: dict[tuple[int, str], int] = {}

        # Flag: True khi _on_open đang trong vòng đăng ký chart sessions
        # Mục đích: chặn _on_message kích hoạt _done.set() sớm khi _expected chưa đầy đủ
        # (Race condition: TV có thể gửi data về ngay session đầu tiên, trước khi
        #  các session còn lại được đăng ký xong)
        self._registering: bool = False

        # Event dùng để báo hiệu batch đã hoàn thành (nhận đủ data hoặc timeout)
        self._done     = threading.Event()

        # Lock riêng của BatchFetcher để bảo vệ _expected, _received, _new_bars_count
        self._lock     = threading.Lock()

        # Tham chiếu đến WebSocketApp hiện tại (None khi không có kết nối)
        self._ws: websocket.WebSocketApp | None = None

    # ─── TẠO HEADER HTTP CHO KẾT NỐI WEBSOCKET ───────────────────────────────

    def _build_headers(self) -> list[str]:
        """
        Tạo danh sách HTTP header gửi kèm khi mở kết nối WebSocket.
        Cần giả lập trình duyệt Chrome để TradingView không từ chối kết nối.
        Nếu có cookie thì đính kèm để được xác thực với tài khoản Premium.
        """
        headers = [
            "Origin: https://www.tradingview.com",    # Báo nguồn gốc request
            "Referer: https://www.tradingview.com/",  # Trang tham chiếu
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36",  # Giả lập Chrome 124
        ]
        active_cookie = _tv_auth._tv_cookie   # Ưu tiên cookie đã được refresh (từ _tv_auth)
        if active_cookie:
            # Đính kèm cookie để TradingView nhận ra đây là tài khoản Premium
            headers.append(f"Cookie: {active_cookie}")
        return headers

    # ─── CÁC HÀM XỬ LÝ SỰ KIỆN WEBSOCKET ───────────────────────────────────

    def _on_open(self, ws) -> None:
        """
        Được gọi TỰ ĐỘNG khi kết nối WebSocket mở thành công.
        Khởi động thread đăng ký sessions trong background để không block WS.
        """
        logger.info("[G%d] Connected - registering sessions...", self.group_id)
        # Chạy đăng ký sessions trong thread riêng vì đăng ký nhiều session mất thời gian
        threading.Thread(
            target=self._register_sessions, args=(ws,),
            daemon=True, name=f"reg-g{self.group_id}",
        ).start()

    def _register_sessions(self, ws) -> None:
        """
        Đăng ký chart session cho từng cặp (symbol × TF) lên TradingView.
        Gọi theo thứ tự: set_auth_token -> chart_create_session -> resolve_symbol -> create_series

        Mỗi lệnh tương ứng với:
            - set_auth_token:       Xác thực kết nối
            - chart_create_session: Tạo "tab chart" ảo trên TradingView
            - resolve_symbol:       Báo cho chart biết sẽ xem symbol nào
            - create_series:        Yêu cầu TradingView gửi dữ liệu nến về
        """
        # Hàm nội bộ kiểm tra WS còn sống không (tránh gửi lệnh vào kết nối đã đóng)
        def _ws_alive() -> bool:
            try:
                return ws.sock is not None and ws.sock.connected
            except Exception:
                return False

        # Reset trạng thái từ batch trước
        self._cs_map.clear()
        self._expected.clear()
        self._received.clear()

        # Kiểm tra WS còn hoạt động trước khi bắt đầu đăng ký
        if not _ws_alive():
            logger.warning("[G%d] WS closed before registration.", self.group_id)
            self._done.set()
            return

        # BƯỚC 1: Xác thực kết nối bằng auth token
        try:
            _send(ws, ["set_auth_token", _tv_auth._auth_token])
        except Exception as exc:
            logger.warning("[G%d] Auth send failed: %s", self.group_id, exc)
            self._done.set()
            return

        # Chờ 0.5 giây để TradingView xử lý xác thực trước khi tiếp tục
        time.sleep(0.5)

        # BƯỚC 2: Lặp qua từng symbol và từng TF để đăng ký chart session
        # Đặt cờ _registering TRƯỚC khi bắt đầu vòng lặp để ngăn _on_message
        # kích hoạt _done.set() sớm khi _expected chưa đầy đủ (race condition)
        with self._lock:
            self._registering = True

        for sym in self.symbols:
            for tf_code, interval in WS_TF_INTERVAL.items():
                # Kiểm tra điều kiện dừng giữa chừng
                if _shutdown.is_set() or not _ws_alive():
                    with self._lock:
                        self._registering = False
                    self._done.set()
                    return

                # Tạo ID ngẫu nhiên cho chart session này
                cs            = _gen_id("cs")  # Ví dụ: "cs_ab3k9mxp1qzr"
                staging_table = TF_STAGING[tf_code]  # Bảng DB tương ứng với TF này

                try:
                    # Lệnh 1: Tạo chart session với ID vừa tạo
                    _send(ws, ["chart_create_session", cs, ""])
                    time.sleep(0.1)

                    # Lệnh 2: Gắn symbol vào chart session
                    # sym_json chứa tên symbol và chế độ điều chỉnh (splits)
                    sym_json = json.dumps({
                        "symbol":     f"{sym['tv_exchange']}:{sym['tv_symbol']}",
                        "adjustment": "splits",  # Điều chỉnh dữ liệu khi có split cổ phiếu
                    })
                    _send(ws, ["resolve_symbol", cs, "sds_sym_1", f"={sym_json}"])
                    time.sleep(0.1)

                    # Lệnh 3: Yêu cầu TradingView gửi nến mới nhất
                    # Nếu cặp đang trong backlog (đã miss ≥1 batch) -> yêu cầu nhiều hơn
                    # để lấp khoảng trống dữ liệu bị thiếu; ngược lại chỉ cần N_BARS_WS
                    with _backlog_lock:
                        n_req = (N_BARS_WS_BACKLOG
                                 if (sym["symbol_id"], tf_code) in _backlog
                                 else N_BARS_WS)
                    _send(ws, ["create_series", cs, "sds_1", "sds_sym_1",
                               "sds_sym_1", interval, n_req, ""])

                    # Lưu ánh xạ: session ID -> thông tin symbol/TF
                    self._cs_map[cs] = (sym["symbol_id"], tf_code, staging_table, sym["tv_symbol"])

                    # Đánh dấu session này cần nhận được phản hồi
                    self._expected.add(cs)

                    # Nghỉ SESSION_THROTTLE giây trước khi đăng ký session tiếp theo
                    # Tránh gửi quá nhiều lệnh cùng lúc -> TradingView từ chối
                    time.sleep(SESSION_THROTTLE)

                except Exception as exc:
                    logger.warning("[G%d] Session register error: %s", self.group_id, exc)
                    with self._lock:
                        self._registering = False
                    self._done.set()
                    return

        # Đăng ký xong -> tắt cờ và kiểm tra completion ngay
        # (phòng trường hợp tất cả sessions đã nhận data trong khi đang đăng ký)
        with self._lock:
            self._registering = False
            already_done = self._expected and self._received >= self._expected

        if already_done:
            logger.info(
                "[G%d] All %d sessions received (post-registration check) - closing.",
                self.group_id, len(self._expected),
            )
            self._done.set()
            try:
                ws.close()
            except Exception:
                pass

        # Log tóm tắt sau khi đăng ký xong tất cả sessions
        sym_names = ", ".join(s["tv_symbol"] for s in self.symbols)
        tf_names  = ", ".join(WS_TF_INTERVAL.keys())
        logger.info(
            "[G%d] %d sessions registered | Symbols: [%s] | TFs: [%s] - waiting for data...",
            self.group_id, len(self._expected), sym_names, tf_names,
        )

    def _on_message(self, ws, raw: str) -> None:
        """
        Được gọi TỰ ĐỘNG mỗi khi có gói tin từ TradingView.
        Xử lý: heartbeat, lỗi xác thực, và gói tin chứa dữ liệu nến.
        """
        with _state_lock:
            _stats["events"] += 1  # Đếm tổng số sự kiện WebSocket

        # Tách chuỗi gói tin ghép thành từng gói riêng lẻ
        for data in _parse_packets(raw):

            # TRƯỜNG HỢP 1: Heartbeat - TradingView ping để giữ kết nối
            # Phải echo lại đúng gói tin đó nếu không kết nối bị đóng
            if data.startswith("~h~"):
                try:
                    ws.send(f"~m~{len(data)}~m~{data}")
                except Exception:
                    pass
                continue

            # Phân tích JSON của gói tin
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue  # Bỏ qua gói tin không phải JSON hợp lệ

            if not isinstance(msg, dict):
                continue  # Bỏ qua nếu JSON không phải dạng object

            msg_type = msg.get("m", "")  # Loại message (ví dụ: "du", "timescale_update", "error")
            p        = msg.get("p", [])  # Danh sách tham số kèm theo

            # TRƯỜNG HỢP 2: Lỗi xác thực - token hết hạn hoặc bị thu hồi
            if _is_token_error(msg_type, data):
                logger.warning("[G%d] Auth error detected - triggering token renewal.", self.group_id)
                # Reset token về chuỗi đặc biệt để báo hiệu cần gia hạn
                _tv_auth.set_current_token(_tv_auth.GUEST_TOKEN)
                # Khởi động gia hạn token trong thread riêng (không block WS)
                threading.Thread(target=_renew_auth_token, daemon=True).start()
                self._done.set()
                try:
                    ws.close()
                except Exception:
                    pass
                return

            # TRƯỜNG HỢP 3: Gói tin chứa dữ liệu nến (du = data update, timescale_update)
            # p[0] = chart session ID, p[1] = dict chứa dữ liệu series
            if msg_type in ("du", "timescale_update") and len(p) >= 2:
                self._handle_series(p[0], p[1], ws)

    def _handle_series(self, cs: str, series_data: dict, ws) -> None:
        """
        Xử lý dữ liệu nến từ một chart session:
            1. Lọc chỉ lấy nến đã đóng (bỏ nến đang mở)
            2. So sánh với watermark để chỉ lấy nến mới
            3. Đưa nến mới vào hàng đợi DB
            4. Đánh dấu session này đã nhận xong
        """
        # Bỏ qua nếu session ID không có trong danh sách đã đăng ký
        if cs not in self._cs_map:
            return

        # Lấy thông tin của session: symbol, TF, bảng DB, tên TV
        symbol_id, tf_code, staging_table, tv_symbol = self._cs_map[cs]

        # Kiểm tra gói tin có chứa dữ liệu series thực sự không
        # TradingView đôi khi gửi timescale_update xác nhận session nhưng chưa có data
        # -> Phải kiểm tra key "sds_1" trước khi xử lý
        sds = series_data.get("sds_1")
        if sds is None:
            return  # Không có dữ liệu nến -> bỏ qua

        # Lấy danh sách nến, lọc bỏ nến thiếu dữ liệu
        bars = [b for b in sds.get("s", []) if len(b.get("v", [])) >= 6]

        # Đánh dấu session này đã nhận được data (dù có nến mới hay không)
        with self._lock:
            self._received.add(cs)

        _new_count = 0
        if bars:
            # Sắp xếp nến theo thời gian tăng dần (timestamp tăng dần)
            bars.sort(key=lambda b: b["v"][0])

            # Bỏ nến cuối cùng (nến đang mở - chưa đóng nên giá có thể thay đổi)
            # Chỉ lưu các nến đã đóng hoàn toàn
            closed_bars = bars[:-1]

            if closed_bars:
                key = (symbol_id, tf_code)
                with _state_lock:
                    # Lấy watermark: timestamp của nến mới nhất đã lưu trong DB
                    last_ts = _last_bar_ts.get(key, 0.0)

                # Backlog mode: hạ watermark để phủ gap - staging MERGE + Fact NOT EXISTS chặn duplicate
                with _backlog_lock:
                    miss_count = _backlog.get(key, 0)

                if miss_count > 0:
                    from config import TF_MINUTES as _TF_MIN
                    tf_min = _TF_MIN.get(tf_code, 5)
                    effective_wm = max(0.0, last_ts - miss_count * tf_min * 60 * 2)
                    logger.debug(
                        "[G%d] %s [%s] backlog gap-fill - watermark lowered by %dm",
                        self.group_id, tv_symbol, tf_code, miss_count * tf_min * 2,
                    )
                else:
                    effective_wm = last_ts

                # Lọc: chỉ giữ nến sau effective watermark
                # Normal: effective_wm = last_ts (không thay đổi)
                # Backlog: effective_wm thấp hơn để lấp gap bars bị bỏ lỡ
                new_bars = [b for b in closed_bars if b["v"][0] > effective_wm]

                if new_bars:
                    # Chuyển danh sách nến thành DataFrame
                    df = _bars_to_df(new_bars)
                    if not df.empty:
                        future_cutoff = _future_cutoff_ts()
                        safe_new_bars = [b for b in new_bars if b["v"][0] <= future_cutoff]
                        if not safe_new_bars:
                            logger.warning(
                                "[G%d] %s [%s] only future bars received - ignored",
                                self.group_id, tv_symbol, tf_code,
                            )
                            return
                        if len(safe_new_bars) != len(new_bars):
                            logger.warning(
                                "[G%d] %s [%s] dropped %d future bar(s) before enqueue",
                                self.group_id, tv_symbol, tf_code,
                                len(new_bars) - len(safe_new_bars),
                            )
                            df = _bars_to_df(safe_new_bars)
                            if df.empty:
                                return

                        # Đưa vào hàng đợi TRƯỚC - đảm bảo bar được accept vào
                        # queue/overflow/spool trước khi watermark nhảy lên.
                        # Tránh tình huống watermark advance nhưng bar chưa persist.
                        item = (symbol_id, tf_code, staging_table, tv_symbol, df)
                        enqueue_status = _enqueue_or_buffer(
                            item, self.group_id, tv_symbol, tf_code
                        )
                        if enqueue_status is False or enqueue_status == "rejected":
                            with _overflow_lock:
                                overflow_depth = len(_overflow_buf)
                            spool_depth = _safe_spool_count()
                            logger.error(
                                "[G%d] %s [%s] queue/spool reject - watermark unchanged "
                                "(queue=%d overflow=%d spool=%s)",
                                self.group_id,
                                tv_symbol,
                                tf_code,
                                _db_queue.qsize(),
                                overflow_depth,
                                "n/a" if spool_depth is None else spool_depth,
                            )
                            return
                        _set_received_watermark(key, safe_new_bars[-1]["v"][0])

                        with self._lock:
                            self._new_bars_count += len(safe_new_bars)
                            self._pair_new_bars[(symbol_id, tf_code)] = len(safe_new_bars)
                        _new_count = len(safe_new_bars)

                        with _state_lock:
                            _stats["queue_depth"] = _db_queue.qsize()

        # Ghi log kết quả của session này: nhận bao nhiêu nến, có bao nhiêu cái mới
        logger.info(
            "[G%d] %s [%s] - %d bar(s) received, %d new",
            self.group_id, tv_symbol, tf_code, len(bars), _new_count,
        )

        # Kiểm tra điều kiện hoàn thành: đã nhận đủ data từ tất cả sessions chưa-
        with self._lock:
            # Guard: không đóng WS khi _on_open vẫn đang đăng ký sessions
            # (_expected chưa đầy đủ -> so sánh sẽ cho kết quả sai)
            if not self._registering and self._expected and self._received >= self._expected:
                # Tất cả sessions đều đã gửi data -> đóng WS sớm, không cần chờ timeout
                logger.info("[G%d] All %d sessions received - closing.", self.group_id, len(self._expected))
                self._done.set()
                try:
                    ws.close()
                except Exception:
                    pass

    def _on_error(self, _ws, error) -> None:
        """Được gọi TỰ ĐỘNG khi có lỗi WebSocket (kết nối bị ngắt đột ngột, v.v.)."""
        logger.error("[G%d] WS error: %s", self.group_id, error)
        with _state_lock:
            _stats["errors"] += 1
        self._done.set()  # Báo hiệu batch đã kết thúc (dù là kết thúc do lỗi)

    def _on_close(self, _ws, status_code, _msg) -> None:
        """Được gọi TỰ ĐỘNG khi kết nối WebSocket đóng (dù do ta chủ động hay bị ngắt)."""
        logger.info("[G%d] Disconnected (code=%s).", self.group_id, status_code)
        self._done.set()  # Đảm bảo _done luôn được set khi kết nối đóng

    # ─── ĐIỂM VÀO CHÍNH - GỌI HÀM NÀY ĐỂ THỰC HIỆN 1 LẦN BATCH ─────────────

    def fetch(self, timeout: int = BATCH_FETCH_TIMEOUT) -> bool:
        """
        Thực hiện 1 lần batch fetch đầy đủ:
            Mở WS -> xác thực -> đăng ký sessions -> chờ data -> đóng WS.

        Trả về:
            True  - nếu nhận đủ data từ tất cả sessions trước khi timeout
            False - nếu hết timeout mà chưa nhận đủ (một số session bị thiếu)
        """
        # Reset trạng thái từ batch trước
        self._done.clear()
        self._new_bars_count = 0
        self._pair_new_bars.clear()

        # Tạo URL kết nối với timestamp hiện tại (TradingView yêu cầu tham số date)
        ts  = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
        url = f"{TV_BASE_URL}?from=chart%2F&date={ts}"

        # Tạo WebSocketApp với các handler sự kiện đã định nghĩa
        ws = websocket.WebSocketApp(
            url,
            header=self._build_headers(),
            on_open=self._on_open,       # Gọi khi kết nối thành công
            on_message=self._on_message, # Gọi khi nhận được gói tin
            on_error=self._on_error,     # Gọi khi có lỗi kết nối
            on_close=self._on_close,     # Gọi khi kết nối đóng
        )
        self._ws = ws

        # Chạy WebSocket trong thread riêng (ws.run_forever() là blocking)
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True, name=f"ws-g{self.group_id}")
        ws_thread.start()

        # Chờ _done event được set, tối đa timeout giây
        completed = self._done.wait(timeout=timeout)

        if not completed:
            # Timeout: liệt kê các session chưa nhận được data để log
            missing = [
                f"{self._cs_map[cs][3]} {self._cs_map[cs][1]}"
                for cs in (self._expected - self._received)
                if cs in self._cs_map
            ]
            logger.warning(
                "[G%d] Fetch timeout (%ds) - received %d/%d sessions. Missing: %s",
                self.group_id, timeout, len(self._received), len(self._expected),
                ", ".join(missing) if missing else "none",
            )
            # Gửi cảnh báo Discord để người vận hành biết có vấn đề
            _tg_alert(
                "WARNING",
                f"Group {self.group_id} batch timed out after {timeout}s.\n"
                f"Received {len(self._received)}/{len(self._expected)} sessions.\n"
                + (f"Missing: {', '.join(missing)}" if missing else "")
            )
            try:
                ws.close()  # Chủ động đóng WS để giải phóng tài nguyên
            except Exception:
                pass

        # Chờ WS thread thực sự kết thúc (tối đa 5 giây)
        ws_thread.join(timeout=5)
        self._ws = None  # Xóa tham chiếu để tránh memory leak

        # ─── BACKFILL SAFETY: cập nhật bộ đếm miss ──────────────────────────────
        # Tính tập hợp (symbol_id, tf_code) đã nhận và bị miss trong batch này
        # Snapshot _cs_map ngay tại đây - tránh race nếu thread khác đang reset
        cs_map_snapshot = dict(self._cs_map)
        received_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]                   # lấy (symbol_id, tf_code)
            for cs in self._received
            if cs in cs_map_snapshot
        }
        missed_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]
            for cs in (self._expected - self._received)
            if cs in cs_map_snapshot
        }
        _update_missed_pairs(received_pairs, missed_pairs)

        # ─── BACKLOG: ghi nhớ pair bị miss, yêu cầu nhiều bars hơn ở batch tiếp theo ─
        _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}
        with _backlog_lock:
            # Cặp đã nhận được data -> xóa khỏi backlog (không cần retry nữa)
            for pair in received_pairs:
                if pair in _backlog:
                    logger.info("[BACKLOG] %s [%s] data recovered - removed from backlog.",
                                _sym_name.get(pair[0], str(pair[0])), pair[1])
                _backlog.pop(pair, None)
            # Cặp bị miss lần này -> thêm/tăng counter trong backlog
            for pair in missed_pairs:
                count = _backlog.get(pair, 0) + 1
                if count <= MAX_BACKLOG_BATCHES:
                    _backlog[pair] = count
                    logger.info("[BACKLOG] %s [%s] miss #%d - next batch requests %d bars.",
                                _sym_name.get(pair[0], str(pair[0])), pair[1],
                                count, N_BARS_WS_BACKLOG)
                    logger.info("[AUDIT] miss sym=%s tf=%s count=%d",
                                _sym_name.get(pair[0], str(pair[0])), pair[1], count)
                else:
                    # Đã miss quá nhiều lần liên tiếp -> khoảng trống có thể là vĩnh viễn
                    logger.error(
                        "[BACKLOG] %s [%s] missed %d batches in a row - data gap permanent, removing from backlog.",
                        _sym_name.get(pair[0], str(pair[0])), pair[1], count,
                    )
                    _tg_alert(
                        "ERROR",
                        f"Data gap vinh vien: {_sym_name.get(pair[0], str(pair[0]))} [{pair[1]}]\n"
                        f"Da miss {count} batch lien tiep (~{count * 5} phut).\n"
                        f"Chay 04_checker.py de quet va sua."
                        + QUICK_COMMANDS_HINT
                    )
                    _backlog.pop(pair, None)

        # ─── BẢNG TÓM TẮT BATCH ─────────────────────────────────────────────────
        # Xây dựng danh sách tất cả (symbol, TF) trong nhóm này theo thứ tự đã đăng ký
        # rồi in thành bảng: Symbol | TF | New bars | Latest bar | Status
        with _backlog_lock:
            backlog_snap = dict(_backlog)

        pair_new_bars_snap: dict[tuple[int, str], int]
        with self._lock:
            pair_new_bars_snap = dict(self._pair_new_bars)

        # Thu thập tất cả cặp (symbol_id, tf_code, tv_symbol) theo cs_map
        all_pairs = []
        seen: set[tuple[int, str]] = set()
        for cs, (sym_id, tf_code, _, tv_sym) in cs_map_snapshot.items():
            key = (sym_id, tf_code)
            if key not in seen:
                seen.add(key)
                all_pairs.append((sym_id, tf_code, tv_sym))
        # Sắp xếp: symbol trước, TF sau
        all_pairs.sort(key=lambda x: (x[2], x[1]))

        hdr = f"[G{self.group_id}] {'Symbol':<14} {'TF':<5} {'New':>4}  {'Latest bar UTC':<20}  Status"
        sep = f"[G{self.group_id}] " + "-" * (len(hdr) - len(f"[G{self.group_id}] "))
        logger.info(sep)
        logger.info(hdr)
        logger.info(sep)
        for sym_id, tf_code, tv_sym in all_pairs:
            key = (sym_id, tf_code)
            new_bars = pair_new_bars_snap.get(key, 0)
            with _state_lock:
                wm_ts = _last_bar_ts.get(key)
            if wm_ts:
                latest = datetime.fromtimestamp(wm_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            else:
                latest = "-"
            if key in missed_pairs:
                miss_count = backlog_snap.get(key, 1)
                status = f"MISS #{miss_count} -> retry {N_BARS_WS_BACKLOG}bars"
            elif new_bars > 0:
                status = f"OK  +{new_bars} bar{'s' if new_bars > 1 else ''}"
            else:
                status = "ok  (stale)"
            logger.info(
                "[G%d] %-14s %-5s %4d  %-20s  %s",
                self.group_id, tv_sym, tf_code, new_bars, latest, status,
            )
        logger.info(sep)
        logger.info(
            "[G%d] sessions=%d/%d  new_bars=%d  missed=%d",
            self.group_id, len(self._received), len(self._expected),
            self._new_bars_count, len(missed_pairs),
        )
        logger.info(
            "[AUDIT] G%d sessions=%d/%d new_bars=%d missed=%d ts=%s",
            self.group_id, len(self._received), len(self._expected),
            self._new_bars_count, len(missed_pairs),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        )
        logger.info(sep)

        with self._lock:
            expected_count = len(self._expected)
            received_count = len(self._received)
        return completed and expected_count > 0 and received_count >= expected_count


# =============================================================================
# BATCH RUNNER - Chạy tất cả nhóm song song với cơ chế retry
# =============================================================================

def _update_guest_mode_counter(is_guest: bool) -> None:
    """
    Theo dõi số batch liên tiếp đang chạy ở guest mode.
    Gửi cảnh báo nặng hơn khi vượt ngưỡng _GUEST_ALERT_THRESHOLD.
    Reset về 0 khi auth phục hồi.
    """
    global _consecutive_guest_batches
    if is_guest:
        _consecutive_guest_batches += 1
        if _consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.error(
                "[AUTH] Guest mode for %d batches in a row - data quality may be lower.",
                _consecutive_guest_batches,
            )
            _tg_alert(
                "WARNING",
                f"[WARN] Guest mode for {_consecutive_guest_batches} batches in a row.\n"
                f"Data may be limited or missing. Check TradingView auth now."
            )
    else:
        if _consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.info("[AUTH] Auth recovered after %d guest batch(es).", _consecutive_guest_batches)
        _consecutive_guest_batches = 0


def _run_batch(groups: list[BatchFetcher]) -> None:
    """
    Chạy fetch() cho TẤT CẢ nhóm CÙNG LÚC (song song).
    Mỗi nhóm có cơ chế retry riêng với exponential back-off:
        - Lần 1 thất bại -> chờ 30 giây -> thử lại
        - Lần 2 thất bại -> chờ 60 giây -> thử lại
        - Lần 3 thất bại -> bỏ qua, chờ batch tiếp theo
    """
    # Kiểm tra auth mode ngay đầu batch - theo dõi prolonged guest mode
    with _tv_auth._auth_lock:
        is_guest = (_tv_auth._auth_token == _tv_auth.GUEST_TOKEN)
    _update_guest_mode_counter(is_guest)

    batch_start = datetime.now().strftime("%H:%M:%S")
    logger.info("[SCHED] === Batch start %s - %d groups ===", batch_start, len(groups))
    live_batch_lock = acquire_live_batch_window(logger)

    def _fetch_with_retry(group: BatchFetcher) -> None:
        """Hàm fetch với retry - chạy trong thread riêng cho mỗi nhóm."""
        delay = RECONNECT_BASE_SEC  # Thời gian chờ ban đầu trước khi retry

        for attempt in range(1, BATCH_MAX_RETRIES + 1):
            if _shutdown.is_set():
                return  # Hệ thống đang tắt -> dừng ngay

            try:
                success = group.fetch()
                if success:
                    return  # Thành công -> không cần retry
                logger.warning("[G%d] Fetch incomplete (attempt %d/%d).", group.group_id, attempt, BATCH_MAX_RETRIES)
            except Exception as exc:
                logger.error("[G%d] Fetch exception (attempt %d/%d): %s", group.group_id, attempt, BATCH_MAX_RETRIES, exc)

            # Nếu chưa hết số lần retry -> chờ rồi thử lại
            if attempt < BATCH_MAX_RETRIES:
                logger.info("[G%d] Retry in %ds...", group.group_id, delay)
                # Dùng _shutdown.wait thay vì time.sleep để có thể dừng ngay khi cần
                _shutdown.wait(delay)
                # Exponential back-off: nhân đôi thời gian chờ sau mỗi lần retry
                # Nhưng không vượt quá RECONNECT_MAX_SEC (300 giây)
                delay = min(delay * 2, RECONNECT_MAX_SEC)

    # Tạo một thread cho mỗi nhóm và chạy tất cả cùng lúc
    threads = [
        threading.Thread(target=_fetch_with_retry, args=(g,), daemon=True, name=f"batch-g{g.group_id}")
        for g in groups
    ]
    try:
        for t in threads:
            t.start()  # Khởi động tất cả thread

        for t in threads:
            t.join()   # Chờ tất cả thread hoàn thành trước khi tiếp tục
    finally:
        release_live_batch_window(live_batch_lock)

    # Cập nhật bộ đếm tổng số batch đã chạy
    with _state_lock:
        _stats["batches_run"] += 1

    # Tổng bars mới toàn bộ batch (gộp tất cả group)
    total_new = sum(g._new_bars_count for g in groups)

    # Gộp per-pair bars từ tất cả groups
    batch_pair_bars: dict[tuple[int, str], int] = {}
    for g in groups:
        for key, cnt in g._pair_new_bars.items():
            batch_pair_bars[key] = batch_pair_bars.get(key, 0) + cnt

    # Backlog summary: danh sách cặp đang chờ retry
    _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}
    with _backlog_lock:
        backlog_snap = dict(_backlog)

    _on_batch_complete(_stats["batches_run"], total_new, backlog_snap, batch_pair_bars)

    logger.info("[SCHED] === Batch #%d done - new_bars=%d  backlog=%d ===",
                _stats["batches_run"], total_new, len(backlog_snap))
    logger.info(
        "[AUDIT] batch=%d new_bars=%d backlog=%d ts=%s",
        _stats["batches_run"], total_new, len(backlog_snap),
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
    )
    if backlog_snap:
        entries = ", ".join(
            f"{_sym_name.get(sid, str(sid))}/{tf}(#{cnt})"
            for (sid, tf), cnt in sorted(backlog_snap.items(), key=lambda x: -x[1])
        )
        logger.info("[SCHED] Backlog retry next batch (%d bars each): %s", N_BARS_WS_BACKLOG, entries)
    elif total_new == 0:
        logger.warning("[SCHED] No new bars and no backlog - check WebSocket/auth; sessions may not have registered.")
    else:
        logger.info("[SCHED] No backlog - all pairs OK this batch.")


def _on_batch_complete(
    batch_num: int,
    total_new: int,
    backlog_snap: dict,
    pair_bars: dict[tuple[int, str], int] | None = None,
) -> None:
    """
    Gọi sau mỗi batch: cập nhật hourly stats, gửi Discord alert nếu bất thường.
    - Batch #1: luôn gửi thông báo khởi động thành công.
    - Batch 0-bar: gửi cảnh báo ngay.
    - Batch bình thường: im lặng (digest mỗi giờ qua _status_reporter).
    """
    with _hourly_lock:
        _hourly_stats["batches"] += 1
        _hourly_stats["bars"]    += total_new
        if total_new == 0:
            _hourly_stats["zero_bar_batches"] += 1
        bl = len(backlog_snap)
        if bl > _hourly_stats["backlog_peak"]:
            _hourly_stats["backlog_peak"] = bl
        for key, cnt in (pair_bars or {}).items():
            prev = _hourly_stats["pair_bars"].get(key, 0)
            _hourly_stats["pair_bars"][key] = prev + cnt

    if batch_num == 1:
        _tg_alert(
            "INFO",
            f"[OK] <b>First batch finished</b> - Batch #{batch_num}\n"
            f"New bars: {total_new}  |  Backlog: {bl} pairs",
        )
    elif total_new == 0:
        _tg_alert(
            "WARNING",
            f"[WARN] <b>Batch #{batch_num} had no new bars</b>\n"
            f"Backlog: {bl} pairs  |  Check WebSocket connection or auth",
        )


# =============================================================================
# SCHEDULER - Tự động tính giờ và chạy batch theo đúng mốc thời gian
# =============================================================================

def _seconds_until_next_boundary(interval_minutes: int) -> float:
    """
    Tính số giây cần chờ đến mốc phút tiếp theo của interval.

    Mục đích: đảm bảo batch luôn chạy vào đúng các mốc cố định (mỗi 5 phút),
    không bị trượt thời gian theo kiểu "5 phút sau khi lần trước kết thúc".

    Ví dụ với interval = 5 phút:
        - Hiện tại 10:02:30 -> boundary tiếp theo là 10:05:00 -> chờ 2m30s = 150s
        - Hiện tại 10:05:02 -> vừa qua boundary < 5s -> chờ thêm 1 interval = 300s
    """
    now     = datetime.now()
    # Tính số giây đã trôi qua trong interval hiện tại
    elapsed = (now.minute % interval_minutes) * 60 + now.second + now.microsecond / 1_000_000
    # Số giây còn lại đến boundary tiếp theo
    wait    = interval_minutes * 60 - elapsed
    # Nếu elapsed < 5s (vừa bước qua boundary) -> chờ thêm 1 interval đầy đủ
    # Tránh chạy batch 2 lần liên tiếp khi vừa qua mốc
    return wait if wait > 5 else interval_minutes * 60


def _scheduler_loop(groups: list[BatchFetcher]) -> None:
    """
    Vòng lặp chính của scheduler: chạy batch ngay khi khởi động,
    sau đó cứ đến mốc BATCH_INTERVAL_MIN phút thì chạy lại.
    """
    # Chạy ngay 1 lần đầu khi khởi động để có data sớm nhất có thể
    if not _shutdown.is_set():
        _check_and_maybe_refresh_token()
        _run_batch(groups)

    # Lặp vô hạn cho đến khi có lệnh tắt
    while not _shutdown.is_set():
        # Tính thời gian chờ đến batch tiếp theo
        wait = _seconds_until_next_boundary(BATCH_INTERVAL_MIN)
        logger.info("[SCHED] Next batch in %.0f s (interval: %dmin)", wait, BATCH_INTERVAL_MIN)

        # Chờ đúng thời gian đó (hoặc ít hơn nếu có lệnh tắt)
        _shutdown.wait(wait)

        if _shutdown.is_set():
            break  # Có lệnh tắt -> thoát vòng lặp

        # Kiểm tra và làm mới token chủ động trước mỗi batch
        _check_and_maybe_refresh_token()
        _run_batch(groups)


# =============================================================================
# STATUS REPORTER - Báo cáo trạng thái định kỳ
# =============================================================================

def _status_reporter() -> None:
    """
    Thread chạy liên tục, cứ mỗi STATUS_INTERVAL_SEC giây (1 giờ) thì:
        1. Thu thập số liệu thống kê hiện tại
        2. Ghi vào log
        3. Gửi báo cáo lên Discord

    Dùng _shutdown.wait(timeout) thay vì sleep để có thể dừng ngay khi cần.
    """
    while not _shutdown.wait(STATUS_INTERVAL_SEC):
        # Thu thập snapshot của số liệu thống kê (copy để tránh race condition)
        with _state_lock:
            s = dict(_stats)

        # Đếm số nến đang chờ trong overflow buffer
        with _overflow_lock:
            overflow = len(_overflow_buf)

        # Đếm số cặp (symbol, TF) đang bị miss ít nhất 1 lần liên tiếp
        with _missed_lock:
            n_miss_active = sum(1 for v in _missed_pairs.values() if v > 0)

        # Freshness metrics: độ "tươi" của dữ liệu theo từng cặp (symbol, TF)
        # Dùng dict() copy nhanh - GIL của Python đảm bảo an toàn cho thao tác này
        from _helpers import TF_MINUTES as _TF_MIN
        now_ts       = datetime.now(timezone.utc).timestamp()
        wm_snapshot  = dict(_last_bar_ts)
        stale_count  = 0
        max_age_h    = 0.0
        for (_, tf_code), wm_ts in wm_snapshot.items():
            age_h = (now_ts - wm_ts) / 3600
            if age_h > _TF_MIN.get(tf_code, 60) * 3 / 60:
                stale_count += 1
            if age_h > max_age_h:
                max_age_h = age_h

        # Dọn spool entries cũ >48h trước khi đếm
        _spool_cleanup_old()

        # Đếm số bar trong SQLite spool
        try:
            with sqlite3.connect(_SPOOL_DB) as _con:
                spool_count = _con.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
        except Exception:
            spool_count = 0

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # ── Hourly delta snapshot + reset ─────────────────────────────────────
        with _hourly_lock:
            h = dict(_hourly_stats)
            _hourly_stats["batches"]          = 0
            _hourly_stats["bars"]             = 0
            _hourly_stats["zero_bar_batches"] = 0
            _hourly_stats["backlog_peak"]     = 0
            _hourly_stats["pair_bars"]        = {}

        # Per-symbol và per-TF breakdown từ hourly pair_bars
        _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}
        sym_totals: dict[int, int] = {}
        tf_totals:  dict[str, int] = {}
        for (sid, tf), cnt in h["pair_bars"].items():
            sym_totals[sid] = sym_totals.get(sid, 0) + cnt
            tf_totals[tf]   = tf_totals.get(tf, 0) + cnt

        sym_line = "  ".join(
            f"{_sym_name.get(sid, str(sid))}:{cnt}"
            for sid, cnt in sorted(sym_totals.items())
        ) or "-"
        tf_order = ["M5", "M15", "M30", "M45", "H1", "H2", "H3", "H4", "D1", "W"]
        tf_line = "  ".join(
            f"{tf}:{tf_totals[tf]}" for tf in tf_order if tf in tf_totals
        ) or "-"

        # ── Auth info ────────────────────────────────────────────────────────
        with _tv_auth._auth_lock:
            current_token = _tv_auth._auth_token
        is_guest = (current_token == _tv_auth.GUEST_TOKEN)
        token_secs = _tv_auth._jwt_expires_in(current_token)
        if is_guest:
            auth_info = f"Guest ({_consecutive_guest_batches} batches in a row)"
        elif token_secs > 0:
            th = int(token_secs) // 3600
            tm = (int(token_secs) % 3600) // 60
            auth_info = f"Premium | Token expires in: {th}h{tm:02d}m"
        elif token_secs < 0:
            # JWT không decode được (cached/opaque token) - vẫn đang hoạt động bình thường
            auth_info = "Premium (active)"
        else:
            # token_secs == 0: token vừa hết hạn, chờ auto-renew
            auth_info = "Premium (token expired - renewing)"

        # ── Health level (GREEN / YELLOW / RED) ──────────────────────────────
        if s["errors"] > 0 or stale_count > 3 or spool_count > 0:
            health_level = "RED"
            health_emoji = "[RED]"
        elif n_miss_active > 0 or stale_count > 0 or is_guest:
            health_level = "YELLOW"
            health_emoji = "[YELLOW]"
        else:
            health_level = "GREEN"
            health_emoji = "[GREEN]"

        # ── Top issues (max 3) ───────────────────────────────────────────────
        issues = []
        if spool_count:
            issues.append(f"Temporary buffer has {spool_count} bars waiting - database writes are slow")
        if stale_count > 3:
            issues.append(f"{stale_count} pairs have outdated data")
        if n_miss_active:
            issues.append(f"{n_miss_active} pairs are missing data")
        if is_guest:
            issues.append(f"Running as guest - {_consecutive_guest_batches} batches in a row")
        elif stale_count:
            issues.append(f"{stale_count} pairs are running behind")

        # ── Ghi log ──────────────────────────────────────────────────────────
        logger.info(
            "HEALTH [%s] %s  auth=%s  bars=%d  errors=%d  batches=%d  "
            "queue=%d  overflow=%d  spool=%d  miss=%d  stale=%d  max_age=%.1fh",
            now, health_level, auth_info,
            s["bars_inserted"], s["errors"], s["batches_run"],
            s["queue_depth"], overflow, spool_count,
            n_miss_active, stale_count, max_age_h,
        )

        # h đã được snapshot và _hourly_stats đã được reset ở block đầu (line ~2046).
        # Không snapshot lại ở đây — làm vậy sẽ ghi đè h bằng dict toàn số 0.
        hourly_parts = [f"{h['batches']} batches  |  {h['bars']} new bars"]
        if h["zero_bar_batches"]:
            hourly_parts.append(f"[WARN] {h['zero_bar_batches']} empty batches (no new bars)")
        if h["backlog_peak"]:
            hourly_parts.append(f"backlog peak: {h['backlog_peak']} pairs")
        hourly_summary = "  |  ".join(hourly_parts)

        # ── Gửi Discord ──────────────────────────────────────────────────────
        issues_text = ("\n".join(f"  [WARN] {x}" for x in issues[:3])
                       if issues else "  [OK] Everything looks normal.")
        _tg_send(
            f"{health_emoji} <b>System status: {health_level}</b> [{now}]\n"
            f"Auth: {auth_info}\n"
            f"<b>Last hour:</b> {hourly_summary}\n"
            f"<b>Total:</b> {s['batches_run']} batches  |  {s['bars_inserted']:,} bars  |  {s['errors']} errors\n"
            f"DB queue: {s['queue_depth']}  |  RAM buffer: {overflow}  |  Offline spool: {spool_count}\n"
            f"Oldest not updated: {max_age_h:.1f}h  |  Late pairs: {stale_count}  |  Missing pairs: {n_miss_active}\n"
            f"{'-' * 30}\n"
            f"<b>Last 1 hour:</b> {h['batches']} batches  |  {h['bars']:,} new bars\n"
            f"  (empty batches: {h['zero_bar_batches']}  |  backlog peak: {h['backlog_peak']} pairs)\n"
            f"By pair: {sym_line}\n"
            f"By TF:  {tf_line}\n"
            f"{'-' * 30}\n"
            f"{issues_text}"
        )


# =============================================================================
# HÀM MAIN - Điểm vào của chương trình
# =============================================================================

def main() -> None:
    """
    Hàm được gọi đầu tiên khi chạy file này.
    Thứ tự thực hiện:
        0. Kiểm tra kết nối DB
        1. Xác thực TradingView
        2. Nạp watermark từ DB
        3. Tạo các nhóm BatchFetcher
        4. Khởi động các thread (DB worker, status reporter, scheduler)
        5. Chờ lệnh tắt (Ctrl+C)
        6. Graceful shutdown (xử lý hết queue trước khi thoát)
    """
    # Đảm bảo stdout dùng UTF-8 để tiếng Việt hiển thị đúng trên mọi terminal
    import sys as _sys
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Tính số nhóm WS cần tạo dựa trên tổng số symbol và giới hạn symbol/kết nối
    # math.ceil: làm tròn lên (ví dụ: 25 symbol / 10 = 3 nhóm, không phải 2.5)
    n_groups = math.ceil(len(WS_SYMBOLS) / WS_SYMBOLS_PER_CONN)

    # In banner thông tin tổng quan khi khởi động
    print("=" * 65)
    print("  REAL-TIME PRICE UPDATE SYSTEM (WS Live)")
    print("=" * 65)
    print(f"  Watched pairs    : {len(WS_SYMBOLS)} pairs (indices, metals, crypto)")
    print(f"  Timeframes       : {len(WS_TF_INTERVAL)} direct + {len(COMPUTED_TIMEFRAMES)} computed")
    print(f"  Connection groups: {n_groups} groups (~{WS_SYMBOLS_PER_CONN} pairs/group)")
    print(f"  Update interval  : every {BATCH_INTERVAL_MIN} minutes, aligned to clock time")
    print(f"  Connect timeout  : {BATCH_FETCH_TIMEOUT} seconds per connection")
    print(f"  Login method     : {'Cookie + Token' if TV_COOKIE else 'Username / Password'}")
    print(f"  Discord alerts   : {'On' if DISCORD_WEBHOOK_URL else 'Off'}")
    print(f"  Started at       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 0: Kiểm tra kết nối Database
    # Làm điều này đầu tiên vì toàn bộ hệ thống vô nghĩa nếu không ghi được DB
    # -----------------------------------------------------------------------
    print("\n[Step 1/4] Checking database connection...")
    if not test_connection():
        print("ERROR: Could not connect to the database. The system did not start.")
        sys.exit(1)  # Thoát với mã lỗi 1 (lỗi nghiêm trọng)

    # Dọn lock hết hạn trước (dead-man switch: process bị kill mà không release)
    from _task_lock import cleanup_expired as _cleanup_expired
    _cleanup_expired()

    ws_lock = _acquire_task_lock("ws_live_runtime", duration_min=60)
    if not ws_lock:
        # Lock còn hiệu lực → có instance thật đang chạy.
        # Gửi tín hiệu tắt graceful; chờ tối đa 60 giây để instance cũ thoát.
        logger.info("[LOCK] Old instance is running - sending shutdown signal...")
        request_ws_live_shutdown(logger)
        logger.info("[LOCK] Waiting up to 60 seconds for old instance to exit...")
        for _wait_i in range(60):
            if not _is_task_locked("ws_live_runtime"):
                break
            time.sleep(1)

        # Dọn lại lock hết hạn rồi thử acquire. Không force-delete lock còn hiệu lực:
        # nếu instance cũ chưa thoát, startup phải abort để tránh chạy 2 ws_live song song.
        _cleanup_expired()
        ws_lock = _acquire_task_lock("ws_live_runtime", duration_min=60)
        if not ws_lock:
            logger.error(
                "[LOCK] Existing ws_live_runtime lock is still active after handoff wait - startup aborted."
            )
            sys.exit(1)
        logger.info("[LOCK] ws_live_runtime lock acquired successfully after handoff.")
    atexit.register(_release_task_lock, "ws_live_runtime")

    ws_lock_stop = threading.Event()

    def _ws_lock_heartbeat() -> None:
        while not ws_lock_stop.wait(900):
            renewed = _renew_task_lock("ws_live_runtime", duration_min=60)
            if not renewed:
                logger.warning("[LOCK] Could not renew session lock - the system may think this process stopped.")

    threading.Thread(target=_ws_lock_heartbeat, name="ws-live-lock-heartbeat", daemon=True).start()

    # -----------------------------------------------------------------------
    # BƯỚC 1: Xác thực TradingView
    # Lấy token sẽ dùng cho tất cả kết nối WebSocket.
    # Nếu .env không có token hợp lệ -> bootstrap tự động lấy token mới.
    # -----------------------------------------------------------------------
    print("\n[Step 2/4] Logging in to TradingView...")
    # auth state managed by _tv_auth module

    # Ưu tiên: token cache > .env static > bootstrap
    _cache_token = _tv_auth._load_token_cache().get("TV_AUTH_TOKEN", "")
    _has_valid_token = (
        (_cache_token and _cache_token != _tv_auth.GUEST_TOKEN)
        or (TV_AUTH_TOKEN and TV_AUTH_TOKEN != _tv_auth.GUEST_TOKEN)
    )
    if not _has_valid_token:
        # Không có token nào hợp lệ -> bootstrap: thử session refresh -> headless -> HTTP POST
        print("  No valid token found - getting a new token automatically...")
        _tv_auth._auth_token, token_source = _bootstrap_credentials()
    else:
        # Có token (cache hoặc .env) -> _resolve_auth_token sẽ chọn đúng lớp
        _tv_auth._auth_token, token_source = _resolve_auth_token()

    print(f"  Login source    : {token_source}")
    if _tv_auth._auth_token == _tv_auth.GUEST_TOKEN:
        print("  WARNING: Guest mode is active - data may be limited.")
        _tg_alert("WARNING", "[WARN] Started in guest mode. Data may have fewer bars. Check TradingView login.")

    # -----------------------------------------------------------------------
    # BƯỚC 2: Nạp watermark từ Database
    # Đọc timestamp nến mới nhất của từng (symbol, TF) để không lưu trùng
    # -----------------------------------------------------------------------
    print("\n[Step 3/4] Loading latest data timestamps...")
    _load_watermarks()

    # -----------------------------------------------------------------------
    # BƯỚC 2b: Khởi tạo SQLite spool và phục hồi bars từ lần chạy trước
    # -----------------------------------------------------------------------
    print("\n[Step 3b] Starting offline safety buffer...")
    _init_spool_db()
    recovered = _spool_flush_to_queue()
    if recovered:
        print(f"  Restored {recovered} bars left from the previous run.")

    # -----------------------------------------------------------------------
    # BƯỚC 3: Tạo các nhóm BatchFetcher
    # Mỗi nhóm quản lý tối đa WS_SYMBOLS_PER_CONN (10) symbol
    # Chia đều WS_SYMBOLS thành các lát slice, mỗi slice là 1 nhóm
    # -----------------------------------------------------------------------
    groups = [
        BatchFetcher(i, WS_SYMBOLS[i * WS_SYMBOLS_PER_CONN: (i + 1) * WS_SYMBOLS_PER_CONN])
        for i in range(n_groups)
    ]

    # -----------------------------------------------------------------------
    # BƯỚC 4: Khởi động các thread nền
    # -----------------------------------------------------------------------
    print("\n[Step 4/4] Starting background workers...\n")

    # Bắt exception chưa xử lý trong bất kỳ thread nào -> gửi Discord alert
    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb))
        tname = getattr(args.thread, "name", "unknown")
        logger.critical("[THREAD CRASH] %s: %s\n%s", tname, args.exc_value, tb)
        _tg_alert(
            "ERROR",
            f"[ERROR] <b>Thread crash: {tname}</b>\n"
            f"{args.exc_type.__name__}: {args.exc_value}\n"
            f"<pre>{tb[-800:]}</pre>",
        )

    threading.excepthook = _thread_excepthook

    # Thread ghi DB - daemon=False để chương trình chờ nó xử lý hết queue trước khi thoát
    db_thread = threading.Thread(target=_db_worker, name="db-worker", daemon=False)
    db_thread.start()

    # Thread báo cáo trạng thái - daemon=True: tự tắt khi chương trình chính tắt
    threading.Thread(target=_status_reporter, name="status", daemon=True).start()

    # Thread scheduler - chạy các batch theo lịch, daemon=True
    sched_thread = threading.Thread(
        target=_scheduler_loop, args=(groups,),
        name="scheduler", daemon=True,
    )
    sched_thread.start()

    # Ghi log và gửi Discord thông báo hệ thống đã khởi động thành công
    logger.info(
        "V5 started - %d groups, %d sessions/batch, interval=%dmin, auth=%s.",
        n_groups, len(WS_SYMBOLS) * len(WS_TF_INTERVAL), BATCH_INTERVAL_MIN, token_source,
    )
    _tg_alert(
        "INFO",
        f"[START] <b>WS Live started</b>\n"
        f"{len(WS_SYMBOLS)} watched pairs  |  {len(WS_TF_INTERVAL)} direct timeframes\n"
        f"Updates every {BATCH_INTERVAL_MIN} minutes  |  Login: {token_source}\n"
        f"Status report will be sent to Discord every hour"
    )

    # Khởi động bot command listener (lắng nghe /fix, /status, /pipeline)
    start_bot_listener()
    logger.info("[Notice] Discord webhook is ready (one-way only, no command listener).")

    print("System is running. Press Ctrl+C to stop.\n")

    # -----------------------------------------------------------------------
    # VÒNG LẶP CHỜ - Giữ chương trình chạy, chờ lệnh tắt từ người dùng
    # -----------------------------------------------------------------------
    _shutdown_check_counter = 0
    try:
        while True:
            time.sleep(1)
            _shutdown_check_counter += 1
            if _shutdown_check_counter >= 30:
                _shutdown_check_counter = 0
                if is_ws_live_shutdown_requested():
                    logger.info(
                        "[LIVE] A new instance requested a graceful shutdown. Stopping now."
                    )
                    _shutdown.set()
                    break
    except KeyboardInterrupt:
        # Người dùng nhấn Ctrl+C -> bắt đầu shutdown
        print("\n\nStopping - waiting for all queued data to be written to the database. Please wait...")
        _shutdown.set()  # Ra hiệu cho tất cả thread: dừng lại
    except Exception as exc:
        tb = traceback.format_exc()
        logger.critical("[MAIN CRASH] %s", exc)
        _tg_alert(
            "ERROR",
            f"[ERROR] <b>ws_live crashed in the main thread</b>\n"
            f"{type(exc).__name__}: {exc}\n"
            f"<pre>{tb[-800:]}</pre>",
        )
        _shutdown.set()

    # -----------------------------------------------------------------------
    # GRACEFUL SHUTDOWN - Chờ hết dữ liệu trong queue trước khi thoát
    # -----------------------------------------------------------------------
    # Chờ DB worker xử lý hết tất cả nến còn trong queue
    # (queue.join() block cho đến khi mọi task_done() đã được gọi)
    _db_queue.join()

    # Chờ DB worker thread thực sự dừng (tối đa 30 giây)
    db_thread.join(timeout=30)

    # Thu thập số liệu cuối cùng để in báo cáo kết thúc
    with _state_lock:
        s = dict(_stats)

    # Ghi log tổng kết
    logger.info(
        "Stopped cleanly. bars_inserted=%d  errors=%d  events=%d  batches=%d",
        s["bars_inserted"], s["errors"], s["events"], s["batches_run"],
    )

    # Gửi thông báo tắt lên Discord
    _tg_alert(
        "INFO",
        f"[STOP] System stopped\n"
        f"Bars saved: {s['bars_inserted']}\n"
        f"Errors: {s['errors']}  |  Batches: {s['batches_run']}"
    )
    ws_lock_stop.set()
    _release_task_lock("ws_live_runtime")

    # In tóm tắt ra console
    print(f"\n  Bars saved    : {s['bars_inserted']:,}")
    print(f"  Errors        : {s['errors']}")
    print(f"  WS events     : {s['events']:,}")
    print(f"  Batches run   : {s['batches_run']}")


# =============================================================================
# ĐIỂM KHỞI ĐỘNG
# =============================================================================
# Chỉ gọi main() khi file được chạy trực tiếp (không phải khi được import).
# Đây là quy ước chuẩn của Python để phân biệt "chạy" vs "import".
if __name__ == "__main__":
    main()
