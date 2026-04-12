# =============================================================================
# data_provider/02_ws_live.py  —  Cập nhật dữ liệu thời gian thực qua WebSocket
# Phiên bản     : V5 (Batch/Cron mode)
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Đây là module cập nhật real-time. Chạy liên tục và cập nhật các bar gần nhất.
#
# Các thông số vận hành có thể điều chỉnh:
# - BATCH_INTERVAL_MIN    : chu kỳ chạy mỗi batch (tính bằng phút)
# - BATCH_FETCH_TIMEOUT   : thời gian chờ tối đa trước khi retry/fail (giây)
# - WS_SYMBOLS_PER_CONN  : số symbol trên mỗi kết nối WebSocket
# - Giới hạn DB queue / overflow buffer
#
# Cảnh báo vận hành:
# - Đặt tần suất quá cao có thể bị TradingView rate-limit
# - Đặt tần suất quá thấp sẽ làm tăng độ trễ dữ liệu
#
# Thực hành tốt:
# - Chỉ chạy sau khi đã nạp đủ dữ liệu lịch sử qua 01_data_pipeline.py
# - Theo dõi log và cảnh báo Telegram khi có áp lực queue hoặc lỗi xác thực

#
# FILE NÀY LÀ GÌ?
#   Đây là module cập nhật dữ liệu REAL-TIME — thay vì chờ đến 22:22 UTC mỗi
#   ngày như file 01_data_pipeline.py, file này chạy liên tục 24/7 và cứ mỗi
#   5 phút lại lấy giá mới nhất từ TradingView qua giao thức WebSocket rồi
#   lưu ngay vào database.
#
#   WebSocket là gì? Là kênh kết nối 2 chiều giữa máy tính và TradingView,
#   giống như bạn đang mở tab TradingView trên trình duyệt — dữ liệu giá được
#   đẩy về liên tục theo thời gian thực.
#
# TẠI SAO DÙNG BATCH MODE (không kết nối liên tục)?
#   - Giữ WebSocket mở 24/7 sẽ bị TradingView phát hiện và ban IP
#   - Thay vào đó: mở kết nối → lấy 3 nến mới nhất → đóng kết nối → chờ 5 phút
#   - Cách này an toàn hơn, ít tốn tài nguyên hơn, ít bị rate-limit hơn
#
# CÁC TÍNH NĂNG CHÍNH:
#   1. BATCH MODE: mỗi 5 phút mở WS, lấy data, đóng — không giữ kết nối 24/7
#   2. SCHEDULER tích hợp: tự tính giờ chạy batch tiếp theo theo đúng mốc phút
#   3. COMPLETION TRACKING: biết khi nào đã nhận đủ data → đóng WS sớm
#   4. XỬ LÝ LỖI 429/500: tự retry với thời gian chờ tăng dần (exponential back-off)
#   5. XÁC THỰC 3 LỚP: Cookie → Username/Password → Guest (dự phòng từng bước)
#   6. OVERFLOW BUFFER: nếu hàng đợi ghi DB đầy, tạm giữ vào buffer riêng
#   7. GHI DB BẰNG THREAD RIÊNG: không để ghi DB chặn luồng nhận data
#   8. CẢNH BÁO TELEGRAM: tự động gửi tin khi có lỗi hoặc sự kiện quan trọng
#   9. BÁO CÁO MỖI GIỜ: gửi thống kê tình trạng hệ thống lên Telegram
#   10. WATERMARK: chỉ lưu nến mới, không bao giờ lưu nến trùng lặp
#
# CÁCH LẤY COOKIE VÀ AUTH TOKEN TỪ TRÌNH DUYỆT:
#   1. Mở Chrome, đăng nhập TradingView
#   2. Nhấn F12 → chọn tab "Network"
#   3. Reload trang → click vào bất kỳ request nào đến tradingview.com
#   4. Tìm phần "Request Headers" → copy toàn bộ giá trị "cookie"
#   5. Trong chuỗi cookie, tìm "auth_token=..." → copy phần giá trị đó
#   6. Dán vào file .env theo cấu trúc bên dưới
#
# CẤU HÌNH FILE .env CẦN CÓ:
#   TV_COOKIE=sessionid=abc123; tv_ecuid=xyz; ...   ← toàn bộ cookie header
#   TV_AUTH_TOKEN=eyJhbGci...                        ← chỉ giá trị auth_token
#   TV_USERNAME=your_username                         ← fallback nếu không có cookie
#   TV_PASSWORD=your_password                         ← fallback nếu không có cookie
#   TELEGRAM_BOT_TOKEN=123456:ABC-xyz                ← token bot Telegram
#   TELEGRAM_CHAT_ID=-100123456789                   ← ID nhóm/kênh nhận cảnh báo
#
# THỨ TỰ SỬ DỤNG:
#   Chạy file 01_data_pipeline.py TRƯỚC để load đủ lịch sử,
#   SAU ĐÓ mới chạy file này để cập nhật real-time.
#
# DỪNG CHƯƠNG TRÌNH: nhấn Ctrl + C (chương trình sẽ drain hết DB queue rồi mới tắt)
# =============================================================================


# =============================================================================
# NHẬP CÁC THƯ VIỆN CẦN THIẾT
# =============================================================================

import json  # Xử lý dữ liệu JSON — TradingView gửi/nhận lệnh dưới dạng JSON
import logging  # Framework ghi log chuẩn của Python
import math  # Hàm toán học — dùng math.ceil() để tính số nhóm WS cần tạo
import re    # Regex — dùng để parse auth_token từ HTML khi refresh session
import queue  # Hàng đợi thread-safe — dùng để truyền data từ WS thread sang DB thread
import random  # Tạo chuỗi ngẫu nhiên — dùng để sinh tên session chart
import string  # Bảng ký tự (a-z, 0-9) — kết hợp với random để tạo session ID
import sys  # Tương tác với Python runtime (thoát chương trình, thêm đường dẫn)
import threading  # Chạy nhiều luồng song song — WS, ghi DB, scheduler đều chạy độc lập
import time  # Hàm sleep và đo thời gian
from datetime import datetime, timezone  # Xử lý thời gian — ghi log, tính khoảng cách thời gian
from pathlib import Path  # Xử lý đường dẫn file theo chuẩn hiện đại (thay os.path)

# =============================================================================
# CẤU HÌNH ĐƯỜNG DẪN PROJECT
# =============================================================================

# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
_PROJ = Path(__file__).resolve().parent.parent   # data_provider/ → project root
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


# =============================================================================
# NHẬP CÁC THƯ VIỆN BÊN NGOÀI (cần cài qua pip)
# =============================================================================

import pandas as pd  # DataFrame — cấu trúc bảng dữ liệu dùng để lưu trữ nến trước khi ghi DB
import requests  # Gửi HTTP request — dùng để đăng nhập TradingView và gửi tin Telegram
import websocket  # Thư viện WebSocket client — kết nối và nhận data real-time từ TradingView
from _helpers import setup_logger  # Hàm khởi tạo logger (ghi ra file + console)

# =============================================================================
# NHẬP CÁC MODULE NỘI BỘ CỦA PROJECT
# =============================================================================
from config import (
    COMPUTED_TF_DEPS,  # Bảng phụ thuộc: bảng nguồn nào → tính TF phái sinh nào
    COMPUTED_TIMEFRAMES,  # Danh sách TF phái sinh cần tính (M10, M20, M90, H6, H8)
    LOG_FILE,  # Đường dẫn file log
    SYMBOLS,  # Toàn bộ danh sách symbol theo dõi (37 cặp)
    TELEGRAM_BOT_TOKEN,  # Token bot Telegram để gửi cảnh báo
    TELEGRAM_CHAT_ID,  # ID nhóm/kênh Telegram nhận cảnh báo
    TF_STAGING,  # Bảng ánh xạ: tf_code → tên bảng staging trong DB
    TV_AUTH_TOKEN,  # Auth token TradingView đọc từ .env
    TV_COOKIE,  # Cookie TradingView đọc từ .env
    TV_PASSWORD,  # Mật khẩu TradingView (dùng khi không có cookie)
    TV_USERNAME,  # Tên đăng nhập TradingView (dùng khi không có cookie)
)
from modules.db_connector import (
    get_connection,  # Mở kết nối tới SQL Server
    insert_staging_batch,  # Ghi batch nến vào bảng staging của DB
    run_etl_aggregate,  # Tính TF phái sinh bằng cách gộp nến từ TF nhỏ hơn
    run_etl_direct,  # Đẩy dữ liệu từ staging vào bảng chính Fact_OHLCV
    test_connection,  # Kiểm tra kết nối DB có hoạt động không
)

# =============================================================================
# LỌC DANH SÁCH SYMBOL CHO WEBSOCKET
# =============================================================================

# WebSocket chỉ theo dõi Indices, Metal, Crypto — bỏ qua FOREX
# Lý do: Forex có lịch đóng/mở cửa phức tạp theo múi giờ nên xử lý riêng
# Cú pháp: list comprehension — lọc ra các symbol có asset_type thuộc tập hợp cho trước
WS_SYMBOLS = [s for s in SYMBOLS if s["asset_type"] in {"Indice", "Metal", "Crypto"}]


# =============================================================================
# KHỞI TẠO LOGGER
# =============================================================================

# Tạo logger tên "ws_live", ghi log vào file LOG_FILE và ra console
logger = setup_logger("ws_live", LOG_FILE)


# =============================================================================
# HẰNG SỐ CẤU HÌNH
# =============================================================================

# Địa chỉ WebSocket của TradingView
TV_BASE_URL           = "wss://data.tradingview.com/socket.io/websocket"

# Số symbol tối đa cho mỗi kết nối WebSocket
# TradingView giới hạn số chart session trên 1 kết nối — không đặt quá cao
WS_SYMBOLS_PER_CONN   = 10

# Số nến yêu cầu TradingView gửi về mỗi lần
# 3 nến: 1 nến hiện tại (chưa đóng) + 2 nến đã đóng → chỉ lưu 2 nến đã đóng
N_BARS_WS             = 3

# Chu kỳ chạy batch: cứ mỗi 5 phút, hệ thống mở WS, lấy data, rồi đóng
BATCH_INTERVAL_MIN    = 5

# Timeout mỗi lần batch: nếu sau 90 giây vẫn chưa nhận đủ data → coi như thất bại
BATCH_FETCH_TIMEOUT   = 90

# Số lần retry tối đa nếu batch thất bại trước khi bỏ qua batch đó
BATCH_MAX_RETRIES     = 3

# Thời gian chờ ban đầu trước khi retry lần 1 (giây)
RECONNECT_BASE_SEC    = 30

# Giới hạn tối đa thời gian chờ giữa các lần retry (5 phút)
# Áp dụng exponential back-off: 30s → 60s → 120s → ... → tối đa 300s
RECONNECT_MAX_SEC     = 300

# Giới hạn kích thước hàng đợi ghi DB
# Nếu hàng đợi đầy (>2000 mục chưa ghi xong) → chuyển sang overflow buffer
DB_QUEUE_MAXSIZE      = 2000

# Dung lượng buffer dự phòng khi hàng đợi DB đầy
# Nếu cả overflow buffer cũng đầy (>500) → nến bị mất hoàn toàn
OVERFLOW_BUFFER_MAX   = 500

# Độ trễ giữa mỗi lần đăng ký chart session trong cùng 1 kết nối WS
# Cần thiết để tránh TradingView bị quá tải khi đăng ký nhiều session liên tiếp
SESSION_THROTTLE      = 0.5

# Chu kỳ gửi báo cáo trạng thái lên Telegram (3600 giây = 1 giờ)
STATUS_INTERVAL_SEC   = 3600

# Các từ khóa trong message lỗi cho biết token đã hết hạn / không hợp lệ
TOKEN_EXPIRY_KEYWORDS = ("unauthorized", "auth_error", "not_authorized")

# Số lần miss liên tiếp tối đa trước khi gửi cảnh báo Telegram
# Nếu cặp (symbol, TF) nào không nhận được data trong MAX_MISS_RETRIES batch liên tiếp
# → hệ thống cảnh báo ngay và reset đếm (tránh spam)
MAX_MISS_RETRIES      = 5


# Bảng ánh xạ: tên TF nội bộ → chuỗi interval theo chuẩn TradingView WebSocket API
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
# Ví dụ: khi có nến M5 mới → tự động tính lại M10, M20
_SOURCE_TO_COMPUTED = COMPUTED_TF_DEPS

# Số lần retry tối đa khi HTTP trả về 429 hoặc 5xx
HTTP_MAX_RETRIES    = 4

# Thời gian chờ ban đầu giữa các lần retry (giây) — tăng gấp đôi mỗi lần retry
HTTP_BASE_DELAY_SEC = 2.0

# Thời gian chờ tối đa giữa các lần retry (giây) — giới hạn để không chờ quá lâu
HTTP_MAX_DELAY_SEC  = 120.0


# =============================================================================
# TRẠNG THÁI DÙNG CHUNG GIỮA CÁC THREAD
# =============================================================================
# Vì nhiều thread chạy đồng thời (WS, DB worker, scheduler), các biến dưới đây
# phải được bảo vệ bằng Lock để tránh race condition (ghi đồng thời gây lỗi dữ liệu).

# Lock dùng để bảo vệ _stats và _last_bar_ts khi nhiều thread cùng đọc/ghi
_state_lock    = threading.Lock()

# Watermark: lưu timestamp của nến mới nhất đã lưu cho từng cặp (symbol_id, tf_code)
# Dùng để lọc: chỉ lưu nến có timestamp > watermark (không lưu nến trùng)
_last_bar_ts: dict[tuple[int, str], float] = {}

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
# maxsize=2000 → nếu DB worker ghi chậm, tối đa chứa 2000 nến chờ
_db_queue: queue.Queue = queue.Queue(maxsize=DB_QUEUE_MAXSIZE)

# Token xác thực TradingView (được chia sẻ giữa tất cả kết nối WS)
# Mặc định là chuỗi đặc biệt "unauthorized_user_token" = chưa xác thực / guest
_auth_token: str = "unauthorized_user_token"
_auth_lock  = threading.Lock()  # Lock để đảm bảo chỉ 1 thread cập nhật token tại 1 thời điểm

# Cookie TradingView (in-memory, có thể được cập nhật khi session được refresh)
# Tách biệt với TV_COOKIE (import-time constant) để có thể thay đổi lúc runtime
_tv_cookie: str = TV_COOKIE

# Đường dẫn file .env — dùng để ghi lại credentials mới khi được refresh
_ENV_FILE: Path = _PROJ / ".env"

# Bộ đếm backfill miss: số lần LIÊN TIẾP không nhận được data cho mỗi cặp (symbol_id, tf_code)
# Khi counter đạt MAX_MISS_RETRIES → cảnh báo Telegram ngay, reset counter (tránh spam)
# Khi cặp đó nhận được data trở lại → counter tự động xóa
_missed_pairs: dict[tuple[int, str], int] = {}
_missed_lock  = threading.Lock()   # Lock riêng để không tranh chấp với _state_lock


# =============================================================================
# HTTP RETRY UTILITY
# =============================================================================

def _http_request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = HTTP_MAX_RETRIES,
    base_delay: float = HTTP_BASE_DELAY_SEC,
    max_delay: float = HTTP_MAX_DELAY_SEC,
    **kwargs,
) -> requests.Response:
    """
    Gửi HTTP request với cơ chế retry tự động cho các lỗi tạm thời.

    Phân biệt 3 loại lỗi:
    ┌──────────┬────────────────────────────────────────────────────────────────┐
    │ HTTP 429 │ Too Many Requests — đọc header Retry-After nếu có, nếu không  │
    │          │ dùng exponential back-off + jitter để tránh thundering herd.   │
    ├──────────┼────────────────────────────────────────────────────────────────┤
    │ HTTP 5xx │ Server Error — retry ngay với jitter nhỏ.                      │
    ├──────────┼────────────────────────────────────────────────────────────────┤
    │ Network  │ Timeout / ConnectionError — retry với exponential back-off.    │
    └──────────┴────────────────────────────────────────────────────────────────┘
    HTTP 4xx khác (400/401/403/404 ...): KHÔNG retry — lỗi từ phía client.

    Khi hết max_retries → raise exception gốc để caller xử lý.

    Ví dụ dùng:
        resp = _http_request_with_retry("POST", url, json=payload, timeout=15)
        resp = _http_request_with_retry("GET",  url, headers=hdrs, timeout=20, max_retries=2)
    """
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(max_retries + 1):   # attempt 0 = lần thử đầu tiên (không phải retry)
        try:
            resp = requests.request(method, url, **kwargs)

            # ── Thành công (2xx hoặc 3xx redirect) ──
            if resp.status_code < 400:
                return resp

            # ── HTTP 429: Too Many Requests ──
            if resp.status_code == 429:
                if attempt < max_retries:
                    retry_after_hdr = resp.headers.get("Retry-After", "")
                    try:
                        # Telegram, TradingView đều dùng giá trị số giây trong header
                        wait = float(retry_after_hdr)
                        wait = min(wait, max_delay)   # Không chờ quá max_delay dù server yêu cầu
                    except (ValueError, TypeError):
                        # Không có Retry-After hợp lệ → tính theo exponential back-off
                        wait = min(base_delay * (2 ** attempt), max_delay)
                    # Thêm jitter [0.5, 2.0)s để tránh nhiều thread cùng retry một lúc
                    wait += random.uniform(0.5, 2.0)
                    logger.warning(
                        "[HTTP] 429 Too Many Requests (%s) — retry %d/%d sau %.1fs.",
                        url, attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                    last_exc = requests.HTTPError(
                        f"429 Too Many Requests (attempt {attempt + 1})", response=resp
                    )
                    continue
                raise requests.HTTPError(
                    f"429 Too Many Requests — đã hết {max_retries} lần retry ({url})", response=resp
                )

            # ── HTTP 5xx: Server Error ──
            if resp.status_code >= 500:
                if attempt < max_retries:
                    wait = min(base_delay * (2 ** attempt), max_delay)
                    wait += random.uniform(0.5, 2.0)
                    logger.warning(
                        "[HTTP] %d Server Error (%s) — retry %d/%d sau %.1fs.",
                        resp.status_code, url, attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                    last_exc = requests.HTTPError(
                        f"{resp.status_code} Server Error (attempt {attempt + 1})", response=resp
                    )
                    continue
                raise requests.HTTPError(
                    f"{resp.status_code} Server Error — đã hết {max_retries} lần retry ({url})",
                    response=resp,
                )

            # ── HTTP 4xx khác (400/401/403/404 ...): không retry ──
            raise requests.HTTPError(
                f"HTTP {resp.status_code} Client Error — không retry ({url})", response=resp
            )

        except (requests.ConnectionError, requests.Timeout) as exc:
            # ── Lỗi mạng: retry với exponential back-off ──
            last_exc = exc
            if attempt < max_retries:
                wait = min(base_delay * (2 ** attempt), max_delay)
                wait += random.uniform(0.5, 2.0)
                logger.warning(
                    "[HTTP] Network error %s (%s) — retry %d/%d sau %.1fs.",
                    type(exc).__name__, url, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
            raise   # Hết retry → re-raise exception gốc

    raise last_exc  # Không bao giờ đến đây, nhưng giữ cho type-checker hài lòng


# =============================================================================
# CẢNH BÁO TELEGRAM
# =============================================================================

def _tg_send(message: str) -> None:
    """
    Gửi tin nhắn lên Telegram bằng Bot API.
    Nếu chưa cấu hình bot token hoặc chat ID thì bỏ qua.
    Gửi trong thread riêng để không làm chậm luồng chính.
    """
    # Nếu chưa cấu hình Telegram thì thoát ngay, không làm gì
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    def _send():
        try:
            # Gọi Telegram Bot API để gửi tin nhắn HTML
            # max_retries=2: Telegram chỉ cần 2 retry (notification — không critical)
            _http_request_with_retry(
                "POST",
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
                max_retries=2,
            )
        except Exception as exc:
            # Lỗi gửi Telegram chỉ cần ghi log, không cần crash chương trình
            logger.warning("[TG] Failed to send Telegram alert: %s", exc)

    # Chạy hàm gửi trong thread daemon riêng để không block luồng chính
    threading.Thread(target=_send, daemon=True).start()


def _tg_alert(level: str, text: str) -> None:
    """
    Tạo và gửi cảnh báo Telegram có định dạng chuẩn.
    level: "INFO", "WARNING", hoặc "ERROR" → tương ứng với icon ℹ️ ⚠️ 🚨
    """
    icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨"}
    icon  = icons.get(level, "📌")  # Nếu level không khớp thì dùng icon mặc định

    # Lấy thời điểm hiện tại để đính vào cuối tin nhắn
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Định dạng tin nhắn HTML: icon + tiêu đề in đậm + nội dung + thời gian in nghiêng
    msg   = f"{icon} <b>[AUTO TRADING — {level}]</b>\n{text}\n<i>{now}</i>"
    _tg_send(msg)


# =============================================================================
# XÁC THỰC TRADINGVIEW — 3 LỚP DỰ PHÒNG
# =============================================================================
# Lý do cần 3 lớp: token TradingView có thể hết hạn, cookie có thể bị thu hồi.
# Hệ thống tự động thử từng phương thức theo thứ tự ưu tiên:
#   Lớp 1: Token tĩnh từ .env (tốt nhất — không cần gửi request)
#   Lớp 2: Đăng nhập bằng Username/Password (cần gửi HTTP POST)
#   Lớp 3: Guest token (ít data nhất, dễ bị rate-limit, chỉ dùng khi không còn cách nào)

def _fetch_auth_token_from_credentials(username: str, password: str) -> str:
    """
    Đăng nhập TradingView bằng username/password để lấy auth token.
    Mô phỏng hành vi trình duyệt khi đăng nhập (gửi POST form).
    Trả về token nếu thành công, hoặc chuỗi "unauthorized_user_token" nếu thất bại.
    """
    try:
        r = _http_request_with_retry(
            "POST",
            "https://www.tradingview.com/accounts/signin/",
            # Gửi form đăng nhập (giống như điền form trên web)
            data={"username": username, "password": password, "remember": "on"},
            # Header giả lập trình duyệt Chrome để tránh bị từ chối
            headers={
                "Referer":    "https://www.tradingview.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
            },
            timeout=15,  # Chờ tối đa 15 giây
        )
        # TradingView trả về JSON có cấu trúc: { "user": { "auth_token": "..." } }
        token = r.json()["user"]["auth_token"]
        logger.info("[AUTH] Token obtained via username/password.")
        return token
    except Exception as exc:
        # Đăng nhập thất bại (sai mật khẩu, mạng lỗi, API thay đổi, v.v.)
        logger.warning("[AUTH] Credential login failed: %s", exc)
        return "unauthorized_user_token"  # Giá trị đặc biệt báo hiệu thất bại


def _resolve_auth_token() -> tuple[str, str]:
    """
    Thử lần lượt 4 lớp xác thực và trả về (token, tên_phương_thức).

    Lớp 1  : Static token từ .env                   — nhanh nhất, không cần network
    Lớp 1.5: Refresh qua session cookie (HTTP GET)   — nhanh, không cần browser
    Lớp 2  : HTTP POST username/password             — chỉ dùng được cho native TV account
    Lớp 2.5: Headless Chromium với session cookie    — xử lý cả Google/social login
    Lớp 3  : Guest token                             — cuối cùng, dữ liệu bị giới hạn
    """
    global _tv_cookie

    # LỚP 1: Static token từ .env — dùng ngay nếu còn hợp lệ
    if TV_AUTH_TOKEN and TV_AUTH_TOKEN != "unauthorized_user_token":
        logger.info("[AUTH] Using static TV_AUTH_TOKEN from .env.")
        return TV_AUTH_TOKEN, "static_token"

    # LỚP 1.5: Refresh token qua session cookie (HTTP GET — nhanh, không cần browser)
    current_cookie = _tv_cookie or TV_COOKIE
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, current_cookie)
            return token, "session_refresh"

    # LỚP 2: Đăng nhập bằng username/password (HTTP POST — chỉ dùng cho native TV account)
    if TV_USERNAME and TV_PASSWORD:
        token = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, current_cookie)
            return token, "username/password"

    # LỚP 2.5: Headless Chromium — xử lý Google/social login khi HTTP không đủ
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "headless_chromium"

    # LỚP 3: Guest — cuối cùng, bị giới hạn dữ liệu và dễ bị rate-limit
    logger.warning("[AUTH] Falling back to guest token — Premium data may be unavailable.")
    _tg_alert("WARNING", "Không thể xác thực TradingView — đang dùng guest token.")
    return "unauthorized_user_token", "guest"


def _renew_auth_token() -> None:
    """
    Gia hạn token khi TradingView báo lỗi xác thực trong lúc đang chạy.
    Dùng Lock để đảm bảo chỉ 1 thread thực hiện gia hạn, các thread khác chờ.

    Thứ tự thử:
        1. _bootstrap_credentials() — thử tất cả lớp (session refresh → headless → HTTP POST)
        2. Nếu bootstrap thất bại → _resolve_auth_token() như thường
    """
    global _auth_token
    with _auth_lock:
        # Kiểm tra lại: nếu token đã được gia hạn bởi thread khác rồi thì bỏ qua
        if _auth_token != "unauthorized_user_token":
            return

        logger.warning("[AUTH] Token expired mid-session — attempting renewal via bootstrap...")
        _tg_alert("WARNING", "Token TradingView hết hạn — đang tự động gia hạn...")

        # Thử bootstrap trước (xử lý cả Google login qua headless Chromium)
        new_token, source = _bootstrap_credentials()

        # Nếu bootstrap thất bại → thử resolve bình thường (phòng trường hợp có static token mới)
        if new_token == "unauthorized_user_token":
            new_token, source = _resolve_auth_token()

        _auth_token = new_token  # Cập nhật token toàn cục

        if new_token != "unauthorized_user_token":
            logger.info("[AUTH] Token renewed successfully (source: %s).", source)
            _tg_alert("INFO", f"✅ Token TradingView đã được gia hạn thành công.\nNguồn: {source}")
        else:
            logger.error("[AUTH] Token renewal failed — all groups will use guest access.")
            _tg_alert("ERROR", "❌ Gia hạn token thất bại.\nHệ thống đang dùng guest access.")


# =============================================================================
# AUTH — CÁC HÀM HỖ TRỢ REFRESH CREDENTIALS TỰ ĐỘNG
# =============================================================================

def _refresh_token_via_cookie(cookie_str: str) -> str:
    """
    Lớp 1.5 — Làm mới auth_token bằng cách GET homepage TradingView với sessionid cookie.

    TradingView nhúng auth_token hiện tại vào HTML của homepage khi user đã đăng nhập.
    Cách này không cần trình duyệt — nhanh (~1-2s), không để lại dấu vết automation.

    Trả về token mới, hoặc "unauthorized_user_token" nếu thất bại.
    """
    if not cookie_str:
        return "unauthorized_user_token"
    try:
        resp = _http_request_with_retry(
            "GET",
            "https://www.tradingview.com/",
            headers={
                "Cookie":     cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20,
            allow_redirects=True,
        )
        # TradingView nhúng auth_token dạng JSON trong HTML của trang
        # Tìm pattern: "auth_token":"eyJ..."
        m = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', resp.text)
        if m:
            token = m.group(1)
            logger.info("[AUTH] Token refreshed via session cookie (HTTP GET).")
            return token
        # Kiểm tra nếu server trả về redirect về login page → session hết hạn
        if "sign-in" in resp.url or resp.status_code in (401, 403):
            logger.warning("[AUTH] Session cookie expired (redirect to sign-in).")
        else:
            logger.warning("[AUTH] Cookie valid but auth_token not found in page HTML.")
    except Exception as exc:
        logger.warning("[AUTH] Cookie refresh via HTTP failed: %s", exc)
    return "unauthorized_user_token"


def _headless_refresh(cookie_str: str) -> tuple[str, str]:
    """
    Lớp 2.5 — Dùng headless Chromium (Playwright) để load TradingView và extract credentials.

    Đáng tin cậy hơn HTTP GET vì Playwright thực thi JavaScript đầy đủ.
    Phù hợp khi TV dùng JS để set token thay vì nhúng trong HTML server-side.

    Trả về (token, cookie_str_mới) hoặc ("unauthorized_user_token", "") nếu thất bại.

    CÀI ĐẶT (chỉ cần làm 1 lần):
        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        logger.warning("[AUTH] Playwright chưa được cài — bỏ qua headless refresh.")
        logger.warning("[AUTH] Cài bằng: pip install playwright && playwright install chromium")
        return "unauthorized_user_token", ""

    def _parse_cookie_list(raw: str) -> list[dict]:
        """Chuyển chuỗi 'name=val; name2=val2' sang list dict theo chuẩn Playwright."""
        result = []
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                result.append({
                    "name":   name.strip(),
                    "value":  value.strip(),
                    "domain": ".tradingview.com",
                    "path":   "/",
                    "secure": True,
                })
        return result

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )

            # Inject cookies hiện có trước khi load trang
            if cookie_str:
                ctx.add_cookies(_parse_cookie_list(cookie_str))

            page = ctx.new_page()
            page.goto("https://www.tradingview.com/", wait_until="networkidle", timeout=45_000)

            # Thử lấy auth_token từ DOM (nhiều cách khác nhau để tăng độ tin cậy)
            token: str = page.evaluate(
                """() => {
                    // Cách 1: tìm trong HTML source
                    try {
                        const m = document.documentElement.innerHTML.match(/"auth_token":"(eyJ[^"]+)"/);
                        if (m) return m[1];
                    } catch(e) {}
                    // Cách 2: tìm trong __tv_initData (biến JS global của TV)
                    try {
                        if (window.__tv_initData && window.__tv_initData.auth_token)
                            return window.__tv_initData.auth_token;
                    } catch(e) {}
                    // Cách 3: tìm trong initData (tên biến cũ hơn)
                    try {
                        if (window.initData && window.initData.auth_token)
                            return window.initData.auth_token;
                    } catch(e) {}
                    return null;
                }"""
            ) or ""

            # Lấy tất cả cookies hiện tại sau khi trang load (có thể được refresh)
            all_cookies = ctx.cookies()
            cookie_out = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)
            browser.close()

            if token:
                logger.info("[AUTH] Token refreshed via headless Chromium.")
                return token, cookie_out
            logger.warning("[AUTH] Headless load xong nhưng không tìm thấy auth_token — session có thể đã hết hạn.")
            return "unauthorized_user_token", cookie_out

    except Exception as exc:
        logger.warning("[AUTH] Headless refresh thất bại: %s", exc)
        return "unauthorized_user_token", ""


def _save_credentials_to_env(token: str, cookie: str) -> None:
    """
    Ghi TV_AUTH_TOKEN và TV_COOKIE mới vào file .env một cách an toàn.

    - Chỉ thay đổi 2 dòng liên quan, KHÔNG động đến SQL_UID, Telegram, v.v.
    - Nếu key chưa có → append vào cuối file
    - Cập nhật cả biến in-memory _auth_token và _tv_cookie
    """
    global _auth_token, _tv_cookie

    def _replace_or_append(text: str, key: str, value: str) -> str:
        pattern = rf"^{re.escape(key)}\s*=.*$"
        new_line = f"{key}={value}"
        if re.search(pattern, text, flags=re.MULTILINE):
            return re.sub(pattern, new_line, text, flags=re.MULTILINE)
        return text.rstrip("\n") + f"\n{new_line}\n"

    try:
        original = _ENV_FILE.read_text(encoding="utf-8") if _ENV_FILE.exists() else ""
    except OSError:
        original = ""

    updated = original
    if token and token != "unauthorized_user_token":
        updated = _replace_or_append(updated, "TV_AUTH_TOKEN", token)
        _auth_token = token          # Cập nhật in-memory ngay lập tức
    if cookie:
        updated = _replace_or_append(updated, "TV_COOKIE", cookie)
        _tv_cookie = cookie          # Cập nhật in-memory ngay lập tức

    try:
        _ENV_FILE.write_text(updated, encoding="utf-8")
        logger.info("[AUTH] Credentials đã được lưu vào .env.")
    except OSError as exc:
        logger.warning("[AUTH] Không thể ghi .env: %s", exc)


def _bootstrap_credentials() -> tuple[str, str]:
    """
    Đảm bảo credentials hợp lệ trước khi WebSocket khởi động.
    Gọi lúc startup khi token trống hoặc hết hạn.

    Thứ tự thử:
        1. Refresh token qua HTTP GET (dùng sessionid cookie hiện có)
        2. Refresh token qua headless Chromium (dùng sessionid cookie)
        3. Đăng nhập bằng username/password qua HTTP POST (chỉ dùng được cho tài khoản native TV)

    Lưu credentials mới vào .env để lần restart tiếp theo dùng được ngay.
    Trả về (token, tên_phương_thức).
    """
    global _tv_cookie
    logger.info("[AUTH] Bootstrapping credentials...")

    # ── Bước 1: Refresh token từ session cookie hiện có (HTTP GET, không cần browser) ──
    current_cookie = _tv_cookie or TV_COOKIE
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, current_cookie)
            return token, "session_refresh"
        logger.info("[AUTH] HTTP cookie refresh thất bại — thử headless Chromium...")

    # ── Bước 2: Headless Chromium với session cookie hiện có ──
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "headless_chromium"

    # ── Bước 3: Đăng nhập bằng user/password (HTTP POST — chỉ dùng cho native TV account) ──
    if TV_USERNAME and TV_PASSWORD:
        logger.info("[AUTH] Thử đăng nhập bằng username/password (HTTP POST)...")
        token = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != "unauthorized_user_token":
            _save_credentials_to_env(token, current_cookie)
            return token, "http_post_login"

    logger.warning("[AUTH] Tất cả phương thức bootstrap đều thất bại.")
    return "unauthorized_user_token", "guest"


# =============================================================================
# KHỞI ĐỘNG — NẠP WATERMARK TỪ DATABASE
# =============================================================================

def _load_watermarks() -> None:
    """
    Đọc từ DB thời điểm nến mới nhất đã lưu của mỗi cặp (symbol, TF).
    Dữ liệu này được lưu vào _last_bar_ts và dùng làm "watermark".

    Watermark là gì?
        Là dấu mốc thời gian — hệ thống chỉ lưu những nến có thời gian
        SAU watermark, đảm bảo không bao giờ lưu nến trùng lặp vào DB.

    Tại sao cần load từ DB khi khởi động?
        Khi chương trình restart, _last_bar_ts bị reset về rỗng.
        Nếu không nạp lại từ DB, hệ thống sẽ lưu lại toàn bộ nến cũ.
    """
    logger.info("[INIT] Loading watermarks from staging tables...")
    loaded = 0
    try:
        conn   = get_connection()
        cursor = conn.cursor()

        # Duyệt qua từng TF và bảng staging tương ứng
        for tf_code, staging_table in TF_STAGING.items():
            try:
                # Lấy timestamp nến cuối cùng của từng symbol trong bảng staging này
                cursor.execute(
                    f"SELECT SymbolID, MAX(BarTime) FROM {staging_table} GROUP BY SymbolID"
                )
                for symbol_id, max_bt in cursor.fetchall():
                    if max_bt is not None:
                        # Chuyển datetime sang Unix timestamp (số giây từ 1970)
                        # để dễ so sánh hơn với timestamp từ TradingView
                        _last_bar_ts[(symbol_id, tf_code)] = max_bt.timestamp()
                        loaded += 1
            except Exception as exc:
                # Nếu bảng chưa tồn tại hoặc có lỗi → bỏ qua bảng đó, tiếp tục
                logger.warning("[INIT] Watermark skipped for %s: %s", staging_table, exc)

        conn.close()
    except Exception as exc:
        # Nếu không kết nối được DB → bắt đầu từ watermark = 0 (không có gì)
        logger.warning("[INIT] Watermark load failed (starting from zero): %s", exc)

    logger.info("[INIT] Watermarks loaded: %d entries.", loaded)


# =============================================================================
# OVERFLOW BUFFER — Xử lý khi hàng đợi DB đầy
# =============================================================================

def _flush_overflow_to_queue() -> None:
    """
    Thử chuyển các nến đang chờ trong overflow buffer vào hàng đợi DB.
    Được gọi định kỳ bởi DB worker khi hàng đợi có chỗ trống.
    """
    with _overflow_lock:
        if not _overflow_buf:
            return  # Buffer trống → không cần làm gì

        recharged = 0   # Đếm số nến chuyển thành công
        remaining = []  # Những nến vẫn chưa đưa được vào queue

        for item in _overflow_buf:
            try:
                # put_nowait: thêm vào queue không chờ đợi
                # Nếu queue vẫn đầy → ném exception queue.Full
                _db_queue.put_nowait(item)
                recharged += 1
            except queue.Full:
                # Queue vẫn đầy → giữ nến này lại trong buffer
                remaining.append(item)

        # Cập nhật buffer: chỉ giữ lại các nến chưa đưa được vào queue
        _overflow_buf[:] = remaining

        if recharged:
            logger.info("[DB ] Recharged %d bar(s) from overflow buffer.", recharged)


def _enqueue_or_buffer(item: tuple, group_id: int, tv_symbol: str, tf_code: str) -> None:
    """
    Thêm 1 nến vào hàng đợi DB.
    Nếu hàng đợi đầy → chuyển sang overflow buffer.
    Nếu cả 2 đều đầy → nến bị mất hoàn toàn (cảnh báo ngay).

    item: tuple gồm (symbol_id, tf_code, staging_table, tv_symbol, dataframe)
    """
    try:
        # Thử thêm nến vào hàng đợi DB ngay lập tức
        _db_queue.put_nowait(item)
    except queue.Full:
        # Hàng đợi đầy → chuyển sang overflow buffer
        with _overflow_lock:
            if len(_overflow_buf) < OVERFLOW_BUFFER_MAX:
                _overflow_buf.append(item)
                logger.warning(
                    "[G%d] Queue full — buffered: %s %s (overflow: %d)",
                    group_id, tv_symbol, tf_code, len(_overflow_buf),
                )
            else:
                # Cả queue lẫn overflow đều đầy → nến bị mất
                # Đây là tình huống nghiêm trọng cần kiểm tra ngay
                logger.error("[G%d] Queue + overflow full — bar DROPPED: %s %s", group_id, tv_symbol, tf_code)
                _tg_alert(
                    "ERROR",
                    f"Queue và overflow buffer đều đầy!\n"
                    f"Nến bị mất: {tv_symbol} {tf_code}\n"
                    f"Kiểm tra DB worker ngay."
                )
                with _state_lock:
                    _stats["errors"] += 1


# =============================================================================
# THREAD GHI DATABASE (DB Worker)
# =============================================================================

def _db_worker() -> None:
    """
    Luồng chạy độc lập, liên tục lấy nến từ hàng đợi và ghi vào database.

    Tại sao cần thread riêng?
        Ghi DB mất thời gian (vài ms đến vài trăm ms). Nếu để WS thread ghi DB,
        trong thời gian đó WS không xử lý được gói tin mới → mất data.
        Giải pháp: WS thread chỉ đưa nến vào queue, thread riêng lấy ra và ghi.

    Luồng này KHÔNG phải daemon → chương trình sẽ chờ nó xử lý hết queue trước khi thoát.
    """
    logger.info("[DB ] Worker started.")

    # Vòng lặp: tiếp tục chạy cho đến khi _shutdown được set VÀ queue rỗng hoàn toàn
    while not (_shutdown.is_set() and _db_queue.empty()):

        # Tranh thủ mỗi vòng lặp: thử chuyển nến từ overflow buffer vào queue
        _flush_overflow_to_queue()

        # Lấy 1 nến từ hàng đợi, chờ tối đa 1 giây
        # Nếu sau 1 giây queue vẫn rỗng → tiếp tục vòng lặp (không block mãi)
        try:
            item = _db_queue.get(timeout=1.0)
        except queue.Empty:
            continue  # Queue rỗng → quay lại đầu vòng lặp

        # Giải nén thông tin từ tuple
        symbol_id, tf_code, staging_table, tv_symbol, df = item

        # BƯỚC A: Ghi nến vào bảng staging trong DB
        try:
            inserted = insert_staging_batch(df, symbol_id, staging_table)
        except Exception as exc:
            logger.error("[DB ] Staging error — %s %s: %s", tv_symbol, tf_code, exc)
            with _state_lock:
                _stats["errors"] += 1
            _db_queue.task_done()  # Báo cho queue biết đã xử lý xong item này
            continue

        # Chỉ xử lý tiếp nếu thực sự có nến mới được ghi (inserted > 0)
        if inserted > 0:
            with _state_lock:
                _stats["bars_inserted"] += inserted
            logger.info("[DB ] %s %s: +%d bar(s) committed.", tv_symbol, tf_code, inserted)

            # BƯỚC B: Đẩy nến từ staging vào bảng chính Fact_OHLCV (ETL direct)
            try:
                run_etl_direct(symbol_id, tf_code, staging_table)
            except Exception as exc:
                logger.error("[DB ] ETL direct error — %s %s: %s", tv_symbol, tf_code, exc)
                with _state_lock:
                    _stats["errors"] += 1

            # BƯỚC C: Nếu TF vừa cập nhật là nguồn của TF phái sinh → tính lại TF phái sinh
            # Ví dụ: M5 vừa có nến mới → tự động tính lại M10 (vì M10 = gộp 2 nến M5)
            if staging_table in _SOURCE_TO_COMPUTED:
                for target_tf, src_table in _SOURCE_TO_COMPUTED[staging_table]:
                    try:
                        run_etl_aggregate(symbol_id, target_tf, src_table)
                        logger.info("[DB ] %s → computed %s.", tv_symbol, target_tf)
                    except Exception as exc:
                        logger.error("[DB ] ETL aggregate error — %s %s: %s", tv_symbol, target_tf, exc)
                        with _state_lock:
                            _stats["errors"] += 1

        # Báo cho queue biết đã xử lý xong item này (cần thiết cho queue.join())
        _db_queue.task_done()

        # Cập nhật thống kê số lượng nến còn đang chờ trong queue
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()

    logger.info("[DB ] Worker stopped.")


# =============================================================================
# CÁC HÀM TIỆN ÍCH
# =============================================================================

def _gen_id(prefix: str) -> str:
    """
    Tạo một ID ngẫu nhiên với định dạng: prefix_xxxxxxxxxxxx
    Dùng để tạo tên chart session duy nhất cho mỗi cặp (symbol, TF).

    Tại sao cần ID ngẫu nhiên?
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
    → Gửi: ~m~45~m~{"m":"set_auth_token","p":["eyJhbGci..."]}
    """
    method  = msg[0]        # Tên lệnh (ví dụ: "set_auth_token", "chart_create_session")
    params  = list(msg[1:]) # Các tham số của lệnh
    payload = json.dumps({"m": method, "p": params})  # Chuyển thành JSON string
    ws.send(f"~m~{len(payload)}~m~{payload}")          # Gửi theo định dạng TradingView


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
            break  # Gặp dữ liệu không hợp lệ → dừng lại

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

        # Chuyển timestamp Unix → datetime UTC, bỏ timezone info để lưu vào DB
        ts = datetime.fromtimestamp(v[0], tz=timezone.utc).replace(tzinfo=None)
        records.append({
            "__ts__": ts,
            "open":   float(v[1]),
            "high":   float(v[2]),
            "low":    float(v[3]),
            "close":  float(v[4]),
            # v[5] == v[5] là cách check NaN (NaN != NaN trong Python)
            # Nếu volume là NaN → lưu None thay vì giá trị vô nghĩa
            "volume": float(v[5]) if v[5] == v[5] else None,
        })

    if not records:
        return pd.DataFrame()  # Không có nến hợp lệ → trả về DataFrame rỗng

    # Tạo DataFrame, đặt cột thời gian làm index
    df = pd.DataFrame(records).set_index("__ts__")
    df.index.name = None  # Bỏ tên index để gọn hơn khi lưu vào DB
    return df


def _is_token_error(msg_type: str, data: str) -> bool:
    """
    Kiểm tra xem gói tin từ TradingView có phải lỗi xác thực không.
    Nếu đúng → cần gia hạn token.
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
    │ Cặp (symbol_id, tf_code) nhận được data  → xóa khỏi bộ đếm (reset = 0) │
    │ Cặp bị miss lần này                      → tăng counter +1              │
    │ Counter >= MAX_MISS_RETRIES               → cảnh báo Telegram, reset    │
    └──────────────────────────────────────────────────────────────────────────┘

    Tham số:
        received : tập hợp (symbol_id, tf_code) đã nhận được response từ TradingView
        missed   : tập hợp (symbol_id, tf_code) đã đăng ký nhưng không nhận được response
    """
    alerts: list[tuple[tuple[int, str], int]] = []

    with _missed_lock:
        # Xóa counter cho các cặp đã nhận được data — chúng đang hoạt động bình thường
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
            "[MISS] %s [%s] missed %d batch(es) in a row — sending alert.",
            sym_name, tf_code, count,
        )
        _tg_alert(
            "WARNING",
            f"⚠️ <b>Backfill miss liên tiếp!</b>\n"
            f"Symbol : {sym_name}\n"
            f"TF     : {tf_code}\n"
            f"Số lần : {count} batch liên tiếp\n"
            f"Kiểm tra kết nối TradingView hoặc symbol bị huỷ niêm yết.",
        )


# =============================================================================
# CLASS BatchFetcher — Một kết nối WebSocket cho một nhóm symbol
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

    Không giữ kết nối liên tục — mỗi lần fetch() là 1 vòng đời kết nối hoàn chỉnh.
    """

    def __init__(self, group_id: int, symbols: list) -> None:
        self.group_id  = group_id   # Số thứ tự nhóm (0, 1, 2, ...) để phân biệt trong log
        self.symbols   = symbols    # Danh sách symbol (tối đa 10) mà nhóm này quản lý

        # Bảng ánh xạ: chart session ID → (symbol_id, tf_code, staging_table, tv_symbol)
        # Dùng để biết gói tin từ session nào là của symbol/TF nào
        self._cs_map: dict[str, tuple[int, str, str, str]] = {}

        # Tập hợp ID của các session đã đăng ký xong (đã gửi lệnh đăng ký thành công)
        self._expected: set[str] = set()

        # Tập hợp ID của các session đã nhận được phản hồi data từ TradingView
        self._received: set[str] = set()

        # Đếm số nến thực sự mới (chưa có trong DB) nhận được trong batch này
        self._new_bars_count = 0

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
        active_cookie = _tv_cookie or TV_COOKIE   # Ưu tiên cookie đã được refresh
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
        logger.info("[G%d] Connected — registering sessions...", self.group_id)
        # Chạy đăng ký sessions trong thread riêng vì đăng ký nhiều session mất thời gian
        threading.Thread(
            target=self._register_sessions, args=(ws,),
            daemon=True, name=f"reg-g{self.group_id}",
        ).start()

    def _register_sessions(self, ws) -> None:
        """
        Đăng ký chart session cho từng cặp (symbol × TF) lên TradingView.
        Gọi theo thứ tự: set_auth_token → chart_create_session → resolve_symbol → create_series

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
            _send(ws, ["set_auth_token", _auth_token])
        except Exception as exc:
            logger.warning("[G%d] Auth send failed: %s", self.group_id, exc)
            self._done.set()
            return

        # Chờ 0.5 giây để TradingView xử lý xác thực trước khi tiếp tục
        time.sleep(0.5)

        # BƯỚC 2: Lặp qua từng symbol và từng TF để đăng ký chart session
        for sym in self.symbols:
            for tf_code, interval in WS_TF_INTERVAL.items():
                # Kiểm tra điều kiện dừng giữa chừng
                if _shutdown.is_set() or not _ws_alive():
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

                    # Lệnh 3: Yêu cầu TradingView gửi N_BARS_WS nến mới nhất
                    # "sds_1" là tên series — TradingView sẽ gửi data với ID này
                    _send(ws, ["create_series", cs, "sds_1", "sds_sym_1",
                               "sds_sym_1", interval, N_BARS_WS, ""])

                    # Lưu ánh xạ: session ID → thông tin symbol/TF
                    self._cs_map[cs] = (sym["symbol_id"], tf_code, staging_table, sym["tv_symbol"])

                    # Đánh dấu session này cần nhận được phản hồi
                    self._expected.add(cs)

                    # Nghỉ SESSION_THROTTLE giây trước khi đăng ký session tiếp theo
                    # Tránh gửi quá nhiều lệnh cùng lúc → TradingView từ chối
                    time.sleep(SESSION_THROTTLE)

                except Exception as exc:
                    logger.warning("[G%d] Session register error: %s", self.group_id, exc)
                    self._done.set()
                    return

        # Log tóm tắt sau khi đăng ký xong tất cả sessions
        sym_names = ", ".join(s["tv_symbol"] for s in self.symbols)
        tf_names  = ", ".join(WS_TF_INTERVAL.keys())
        logger.info(
            "[G%d] %d sessions registered | Symbols: [%s] | TFs: [%s] — waiting for data...",
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

            # TRƯỜNG HỢP 1: Heartbeat — TradingView ping để giữ kết nối
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

            # TRƯỜNG HỢP 2: Lỗi xác thực — token hết hạn hoặc bị thu hồi
            if _is_token_error(msg_type, data):
                logger.warning("[G%d] Auth error detected — triggering token renewal.", self.group_id)
                # Reset token về chuỗi đặc biệt để báo hiệu cần gia hạn
                globals()["_auth_token"] = "unauthorized_user_token"
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
        # → Phải kiểm tra key "sds_1" trước khi xử lý
        sds = series_data.get("sds_1")
        if sds is None:
            return  # Không có dữ liệu nến → bỏ qua

        # Lấy danh sách nến, lọc bỏ nến thiếu dữ liệu
        bars = [b for b in sds.get("s", []) if len(b.get("v", [])) >= 6]

        # Đánh dấu session này đã nhận được data (dù có nến mới hay không)
        with self._lock:
            self._received.add(cs)

        _new_count = 0
        if bars:
            # Sắp xếp nến theo thời gian tăng dần (timestamp tăng dần)
            bars.sort(key=lambda b: b["v"][0])

            # Bỏ nến cuối cùng (nến đang mở — chưa đóng nên giá có thể thay đổi)
            # Chỉ lưu các nến đã đóng hoàn toàn
            closed_bars = bars[:-1]

            if closed_bars:
                key = (symbol_id, tf_code)
                with _state_lock:
                    # Lấy watermark: timestamp của nến mới nhất đã lưu trong DB
                    last_ts = _last_bar_ts.get(key, 0.0)

                # Lọc: chỉ giữ lại nến có timestamp SAU watermark (nến thực sự mới)
                new_bars = [b for b in closed_bars if b["v"][0] > last_ts]

                if new_bars:
                    # Chuyển danh sách nến thành DataFrame
                    df = _bars_to_df(new_bars)
                    if not df.empty:
                        # Cập nhật watermark: timestamp của nến mới nhất vừa lấy được
                        with _state_lock:
                            _last_bar_ts[key] = new_bars[-1]["v"][0]

                        # Đưa vào hàng đợi để DB worker ghi vào database
                        item = (symbol_id, tf_code, staging_table, tv_symbol, df)
                        _enqueue_or_buffer(item, self.group_id, tv_symbol, tf_code)

                        with self._lock:
                            self._new_bars_count += len(new_bars)
                        _new_count = len(new_bars)

                        with _state_lock:
                            _stats["queue_depth"] = _db_queue.qsize()

        # Ghi log kết quả của session này: nhận bao nhiêu nến, có bao nhiêu cái mới
        logger.info(
            "[G%d] %s [%s] — %d bar(s) received, %d new",
            self.group_id, tv_symbol, tf_code, len(bars), _new_count,
        )

        # Kiểm tra điều kiện hoàn thành: đã nhận đủ data từ tất cả sessions chưa?
        with self._lock:
            if self._expected and self._received >= self._expected:
                # Tất cả sessions đều đã gửi data → đóng WS sớm, không cần chờ timeout
                logger.info("[G%d] All %d sessions received — closing.", self.group_id, len(self._expected))
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

    # ─── ĐIỂM VÀO CHÍNH — GỌI HÀM NÀY ĐỂ THỰC HIỆN 1 LẦN BATCH ─────────────

    def fetch(self, timeout: int = BATCH_FETCH_TIMEOUT) -> bool:
        """
        Thực hiện 1 lần batch fetch đầy đủ:
            Mở WS → xác thực → đăng ký sessions → chờ data → đóng WS.

        Trả về:
            True  — nếu nhận đủ data từ tất cả sessions trước khi timeout
            False — nếu hết timeout mà chưa nhận đủ (một số session bị thiếu)
        """
        # Reset trạng thái từ batch trước
        self._done.clear()
        self._new_bars_count = 0

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
                "[G%d] Fetch timeout (%ds) — received %d/%d sessions. Missing: %s",
                self.group_id, timeout, len(self._received), len(self._expected),
                ", ".join(missing) if missing else "none",
            )
            # Gửi cảnh báo Telegram để người vận hành biết có vấn đề
            _tg_alert(
                "WARNING",
                f"Batch nhóm {self.group_id} timeout sau {timeout}s.\n"
                f"Nhận được {len(self._received)}/{len(self._expected)} sessions.\n"
                + (f"Thiếu: {', '.join(missing)}" if missing else "")
            )
            try:
                ws.close()  # Chủ động đóng WS để giải phóng tài nguyên
            except Exception:
                pass

        # Chờ WS thread thực sự kết thúc (tối đa 5 giây)
        ws_thread.join(timeout=5)
        self._ws = None  # Xóa tham chiếu để tránh memory leak

        # Log tóm tắt batch: nhận được bao nhiêu session, có bao nhiêu nến mới
        sym_names = ", ".join(s["tv_symbol"] for s in self.symbols)
        logger.info(
            "[G%d] Batch done — sessions=%d/%d  new_bars=%d | Symbols: [%s]",
            self.group_id, len(self._received), len(self._expected), self._new_bars_count, sym_names,
        )

        # ─── BACKFILL SAFETY: cập nhật bộ đếm miss ──────────────────────────────
        # Tính tập hợp (symbol_id, tf_code) đã nhận và bị miss trong batch này
        # Snapshot _cs_map ngay tại đây — tránh race nếu thread khác đang reset
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
        if missed_pairs:
            logger.warning(
                "[G%d] %d pair(s) missed this batch — tracking for backfill safety.",
                self.group_id, len(missed_pairs),
            )
        _update_missed_pairs(received_pairs, missed_pairs)

        return completed


# =============================================================================
# BATCH RUNNER — Chạy tất cả nhóm song song với cơ chế retry
# =============================================================================

def _run_batch(groups: list[BatchFetcher]) -> None:
    """
    Chạy fetch() cho TẤT CẢ nhóm CÙNG LÚC (song song).
    Mỗi nhóm có cơ chế retry riêng với exponential back-off:
        - Lần 1 thất bại → chờ 30 giây → thử lại
        - Lần 2 thất bại → chờ 60 giây → thử lại
        - Lần 3 thất bại → bỏ qua, chờ batch tiếp theo
    """
    batch_start = datetime.now().strftime("%H:%M:%S")
    logger.info("[SCHED] === Batch start %s — %d groups ===", batch_start, len(groups))

    def _fetch_with_retry(group: BatchFetcher) -> None:
        """Hàm fetch với retry — chạy trong thread riêng cho mỗi nhóm."""
        delay = RECONNECT_BASE_SEC  # Thời gian chờ ban đầu trước khi retry

        for attempt in range(1, BATCH_MAX_RETRIES + 1):
            if _shutdown.is_set():
                return  # Hệ thống đang tắt → dừng ngay

            try:
                success = group.fetch()
                if success:
                    return  # Thành công → không cần retry
                logger.warning("[G%d] Fetch incomplete (attempt %d/%d).", group.group_id, attempt, BATCH_MAX_RETRIES)
            except Exception as exc:
                logger.error("[G%d] Fetch exception (attempt %d/%d): %s", group.group_id, attempt, BATCH_MAX_RETRIES, exc)

            # Nếu chưa hết số lần retry → chờ rồi thử lại
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
    for t in threads:
        t.start()  # Khởi động tất cả thread

    for t in threads:
        t.join()   # Chờ tất cả thread hoàn thành trước khi tiếp tục

    # Cập nhật bộ đếm tổng số batch đã chạy
    with _state_lock:
        _stats["batches_run"] += 1

    logger.info("[SCHED] === Batch done — total batches: %d ===", _stats["batches_run"])


# =============================================================================
# SCHEDULER — Tự động tính giờ và chạy batch theo đúng mốc thời gian
# =============================================================================

def _seconds_until_next_boundary(interval_minutes: int) -> float:
    """
    Tính số giây cần chờ đến mốc phút tiếp theo của interval.

    Mục đích: đảm bảo batch luôn chạy vào đúng các mốc cố định (mỗi 5 phút),
    không bị trượt thời gian theo kiểu "5 phút sau khi lần trước kết thúc".

    Ví dụ với interval = 5 phút:
        - Hiện tại 10:02:30 → boundary tiếp theo là 10:05:00 → chờ 2m30s = 150s
        - Hiện tại 10:05:02 → vừa qua boundary < 5s → chờ thêm 1 interval = 300s
    """
    now     = datetime.now()
    # Tính số giây đã trôi qua trong interval hiện tại
    elapsed = (now.minute % interval_minutes) * 60 + now.second + now.microsecond / 1_000_000
    # Số giây còn lại đến boundary tiếp theo
    wait    = interval_minutes * 60 - elapsed
    # Nếu elapsed < 5s (vừa bước qua boundary) → chờ thêm 1 interval đầy đủ
    # Tránh chạy batch 2 lần liên tiếp khi vừa qua mốc
    return wait if wait > 5 else interval_minutes * 60


def _scheduler_loop(groups: list[BatchFetcher]) -> None:
    """
    Vòng lặp chính của scheduler: chạy batch ngay khi khởi động,
    sau đó cứ đến mốc BATCH_INTERVAL_MIN phút thì chạy lại.
    """
    # Chạy ngay 1 lần đầu khi khởi động để có data sớm nhất có thể
    if not _shutdown.is_set():
        _run_batch(groups)

    # Lặp vô hạn cho đến khi có lệnh tắt
    while not _shutdown.is_set():
        # Tính thời gian chờ đến batch tiếp theo
        wait = _seconds_until_next_boundary(BATCH_INTERVAL_MIN)
        logger.info("[SCHED] Next batch in %.0f s (interval: %dmin)", wait, BATCH_INTERVAL_MIN)

        # Chờ đúng thời gian đó (hoặc ít hơn nếu có lệnh tắt)
        _shutdown.wait(wait)

        if _shutdown.is_set():
            break  # Có lệnh tắt → thoát vòng lặp

        _run_batch(groups)


# =============================================================================
# STATUS REPORTER — Báo cáo trạng thái định kỳ
# =============================================================================

def _status_reporter() -> None:
    """
    Thread chạy liên tục, cứ mỗi STATUS_INTERVAL_SEC giây (1 giờ) thì:
        1. Thu thập số liệu thống kê hiện tại
        2. Ghi vào log
        3. Gửi báo cáo lên Telegram

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

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Ghi báo cáo vào file log
        logger.info(
            "STATUS [%s]  bars=%d  errors=%d  events=%d  queue=%d  overflow=%d  batches=%d  miss_active=%d",
            now, s["bars_inserted"], s["errors"], s["events"],
            s["queue_depth"], overflow, s["batches_run"], n_miss_active,
        )

        # Gửi báo cáo lên Telegram (dạng HTML có emoji để dễ đọc)
        _tg_send(
            f"📊 <b>Báo cáo hệ thống</b> [{now}]\n"
            f"✅ Nến đã lưu: {s['bars_inserted']}\n"
            f"❌ Lỗi: {s['errors']}\n"
            f"📥 Queue: {s['queue_depth']}  |  Buffer: {overflow}\n"
            f"🔄 Batches: {s['batches_run']}\n"
            + (f"⚠️ Cặp đang miss: {n_miss_active}" if n_miss_active else "✅ Không có miss")
        )


# =============================================================================
# HÀM MAIN — Điểm vào của chương trình
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
    # Tính số nhóm WS cần tạo dựa trên tổng số symbol và giới hạn symbol/kết nối
    # math.ceil: làm tròn lên (ví dụ: 25 symbol / 10 = 3 nhóm, không phải 2.5)
    n_groups = math.ceil(len(WS_SYMBOLS) / WS_SYMBOLS_PER_CONN)

    # In banner thông tin tổng quan khi khởi động
    print("=" * 65)
    print("  AUTO TRADING — WS LIVE UPDATER  (V5 — Batch/Cron Mode)")
    print("  Batch mode  |  Multi-connection  |  Async DB writes")
    print(f"  Symbols        : {len(WS_SYMBOLS)}")
    print(f"  TFs (direct)   : {len(WS_TF_INTERVAL)}")
    print(f"  TFs (computed) : {len(COMPUTED_TIMEFRAMES)}")
    print(f"  WS connections : {n_groups}  (~{WS_SYMBOLS_PER_CONN} symbols/conn)")
    print(f"  Chart sessions : {len(WS_SYMBOLS) * len(WS_TF_INTERVAL)}")
    print(f"  Batch interval : every {BATCH_INTERVAL_MIN} min (aligned to boundary)")
    print(f"  Fetch timeout  : {BATCH_FETCH_TIMEOUT}s per batch")
    print(f"  Auth method    : {'Cookie+Token' if TV_COOKIE else 'Username/Password'}")
    print(f"  Telegram alert : {'Enabled' if TELEGRAM_BOT_TOKEN else 'Disabled'}")
    print(f"  Started        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 0: Kiểm tra kết nối Database
    # Làm điều này đầu tiên vì toàn bộ hệ thống vô nghĩa nếu không ghi được DB
    # -----------------------------------------------------------------------
    print("\n[Step 0] Checking database connection...")
    if not test_connection():
        print("ABORT: Cannot reach database.")
        sys.exit(1)  # Thoát với mã lỗi 1 (lỗi nghiêm trọng)

    # -----------------------------------------------------------------------
    # BƯỚC 1: Xác thực TradingView
    # Lấy token sẽ dùng cho tất cả kết nối WebSocket.
    # Nếu .env không có token hợp lệ → bootstrap tự động lấy token mới.
    # -----------------------------------------------------------------------
    print("\n[Step 1] Authenticating with TradingView...")
    global _auth_token, _tv_cookie  # Khai báo global vì sẽ được cập nhật

    _creds_missing = not (TV_AUTH_TOKEN and TV_AUTH_TOKEN != "unauthorized_user_token")
    if _creds_missing:
        # Không có token tĩnh → bootstrap: thử session refresh → headless → HTTP POST
        print("  [!] No valid static token in .env — bootstrapping credentials...")
        _auth_token, token_source = _bootstrap_credentials()
    else:
        # Có token tĩnh → dùng luôn (fast path)
        _auth_token, token_source = _resolve_auth_token()

    print(f"  Token source   : {token_source}")
    if _auth_token == "unauthorized_user_token":
        print("  [WARNING] Running as guest — data quality may be reduced.")
        _tg_alert("WARNING", "Khởi động với guest token — dữ liệu có thể bị giới hạn.")

    # -----------------------------------------------------------------------
    # BƯỚC 2: Nạp watermark từ Database
    # Đọc timestamp nến mới nhất của từng (symbol, TF) để không lưu trùng
    # -----------------------------------------------------------------------
    print("\n[Step 2] Loading watermarks...")
    _load_watermarks()

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
    print("\n[Step 4] Starting DB worker and scheduler...\n")

    # Thread ghi DB — daemon=False để chương trình chờ nó xử lý hết queue trước khi thoát
    db_thread = threading.Thread(target=_db_worker, name="db-worker", daemon=False)
    db_thread.start()

    # Thread báo cáo trạng thái — daemon=True: tự tắt khi chương trình chính tắt
    threading.Thread(target=_status_reporter, name="status", daemon=True).start()

    # Thread scheduler — chạy các batch theo lịch, daemon=True
    sched_thread = threading.Thread(
        target=_scheduler_loop, args=(groups,),
        name="scheduler", daemon=True,
    )
    sched_thread.start()

    # Ghi log và gửi Telegram thông báo hệ thống đã khởi động thành công
    logger.info(
        "V5 started — %d groups, %d sessions/batch, interval=%dmin, auth=%s.",
        n_groups, len(SYMBOLS) * len(WS_TF_INTERVAL), BATCH_INTERVAL_MIN, token_source,
    )
    _tg_alert(
        "INFO",
        f"🚀 Hệ thống đã khởi động (V5 — Batch Mode)\n"
        f"Symbols: {len(SYMBOLS)}  |  TFs: {len(WS_TF_INTERVAL)}\n"
        f"Connections: {n_groups}  |  Interval: {BATCH_INTERVAL_MIN}min  |  Auth: {token_source}"
    )

    print("[Running] Press Ctrl+C to stop.\n")

    # -----------------------------------------------------------------------
    # VÒNG LẶP CHỜ — Giữ chương trình chạy, chờ lệnh tắt từ người dùng
    # -----------------------------------------------------------------------
    try:
        while True:
            time.sleep(1)  # Ngủ 1 giây, lặp lại — chỉ để giữ main thread sống
    except KeyboardInterrupt:
        # Người dùng nhấn Ctrl+C → bắt đầu shutdown
        print("\n\nStopping — draining DB queue (please wait)...")
        _shutdown.set()  # Ra hiệu cho tất cả thread: dừng lại

    # -----------------------------------------------------------------------
    # GRACEFUL SHUTDOWN — Chờ hết dữ liệu trong queue trước khi thoát
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

    # Gửi thông báo tắt lên Telegram
    _tg_alert(
        "INFO",
        f"🛑 Hệ thống đã tắt\n"
        f"Nến đã lưu: {s['bars_inserted']}\n"
        f"Lỗi: {s['errors']}  |  Batches: {s['batches_run']}"
    )

    # In tóm tắt ra console
    print(f"\n  bars_inserted : {s['bars_inserted']}")
    print(f"  errors        : {s['errors']}")
    print(f"  events        : {s['events']}")
    print(f"  batches_run   : {s['batches_run']}")


# =============================================================================
# ĐIỂM KHỞI ĐỘNG
# =============================================================================
# Chỉ gọi main() khi file được chạy trực tiếp (không phải khi được import).
# Đây là quy ước chuẩn của Python để phân biệt "chạy" vs "import".
if __name__ == "__main__":
    main()
