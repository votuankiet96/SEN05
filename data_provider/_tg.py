# =============================================================================
# data_provider/_tg.py  —  Telegram notification utility (dùng chung)
# =============================================================================
# Module này được import bởi tất cả script trong data_provider/ để gửi cảnh
# báo và báo cáo lên Telegram mà không cần mỗi file tự định nghĩa lại.
#
# Cách dùng:
#   from _tg import tg_send, tg_alert
#
#   tg_send("<b>Hello</b>")                    # gửi tin thô HTML
#   tg_alert("INFO",    "Pipeline khởi động")  # ℹ️ [AUTO TRADING — INFO]
#   tg_alert("WARNING", "Có cặp bị thiếu")    # ⚠️ [AUTO TRADING — WARNING]
#   tg_alert("ERROR",   "Lỗi kết nối DB")     # 🚨 [AUTO TRADING — ERROR]
#
# Cấu hình (đọc tự động từ .env qua config.py):
#   TELEGRAM_BOT_TOKEN=123456:ABC-xyz
#   TELEGRAM_CHAT_ID=-100123456789
#
# Nếu một trong hai biến chưa được đặt thì tất cả lời gọi đều là no-op.
# =============================================================================

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_pending_threads: list[threading.Thread] = []

# ── Thêm project root vào sys.path để import config.py ──────────────────────
_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402


def tg_send(message: str) -> None:
    """
    Gửi tin nhắn HTML lên Telegram bằng Bot API.
    Không làm gì nếu chưa cấu hình TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
    Gửi trong thread daemon riêng → không block luồng chính.

    Tự retry tối đa 3 lần:
      - Nếu HTTP 429 (rate-limit): chờ theo Retry-After header
      - Nếu exception: chờ 3 giây trước khi thử lại
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    def _send() -> None:
        import requests  # import lazily — không phải module nào cũng cần requests
        url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.ok:
                    return
                if r.status_code == 429:
                    # Rate-limit: chờ theo header, tối đa 30 giây
                    wait = min(float(r.headers.get("Retry-After", 5)), 30)
                    time.sleep(wait)
                else:
                    # Lỗi server khác: thử lại sau 3 giây
                    if attempt < 2:
                        time.sleep(3)
            except Exception:
                if attempt < 2:
                    time.sleep(3)

    t = threading.Thread(target=_send, daemon=False)
    t.start()
    _pending_threads.append(t)


def tg_flush(timeout: float = 12.0) -> None:
    """Chờ tất cả tin nhắn Telegram đang pending gửi xong (tối đa timeout giây).
    Gọi trước khi script ngắn thoát để đảm bảo tin đến nơi."""
    for t in _pending_threads:
        t.join(timeout=timeout)
    _pending_threads.clear()


def tg_alert(level: str, text: str) -> None:
    """
    Gửi cảnh báo có định dạng chuẩn lên Telegram.

    level: "INFO"    → ℹ️ [AUTO TRADING — INFO]
           "WARNING" → ⚠️ [AUTO TRADING — WARNING]
           "ERROR"   → 🚨 [AUTO TRADING — ERROR]
           (khác)    → 📌 [AUTO TRADING — ...]

    text: nội dung tự do, hỗ trợ HTML (in đậm <b>, in nghiêng <i>).
    Thời gian hiện tại được thêm tự động vào cuối tin nhắn.
    """
    icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨"}
    icon  = icons.get(level, "📌")
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg   = f"{icon} <b>[AUTO TRADING — {level}]</b>\n{text}\n<i>{now}</i>"
    tg_send(msg)
