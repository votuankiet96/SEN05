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


# Hint gợi ý lệnh xử lý nhanh — append vào các tin báo lỗi
QUICK_COMMANDS_HINT = (
    "\n─────────────────\n"
    "💡 <b>Lệnh xử lý nhanh:</b>\n"
    "  /fix      — chạy Checker sửa dữ liệu\n"
    "  /pipeline — chạy Pipeline backfill\n"
    "  /status   — xem tình trạng hiện tại"
)


def tg_ask(
    title: str,
    problem_desc: str,
    options: dict,
    token: str,
    timeout_min: int = 240,
    affected_pairs: list | None = None,
) -> None:
    """
    Gửi tin Telegram hỏi user chọn phương án xử lý.

    KHÔNG block — chỉ gửi tin. Caller phải gọi tg_flush() ngay sau để đảm bảo
    tin đến trước khi bắt đầu poll.

    Tham số:
      title          — tiêu đề tin nhắn (ví dụ: "[Checker] Phát hiện vấn đề")
      problem_desc   — mô tả vấn đề, multiline string
      options        — {"confirm": "Sửa tất cả X pairs", "skip": "Bỏ qua"}
      token          — token 8 ký tự từ generate_token()
      timeout_min    — số phút timeout (hiển thị cho user)
      affected_pairs — danh sách pairs bị ảnh hưởng (hiển thị tối đa 8)
    """
    # Phần danh sách pairs bị ảnh hưởng
    pairs_section = ""
    if affected_pairs:
        pair_lines = "\n".join(f"  • {p}" for p in affected_pairs[:8])
        if len(affected_pairs) > 8:
            pair_lines += f"\n  (... và {len(affected_pairs) - 8} pairs khác)"
        pairs_section = f"\n\n📌 <b>Pairs bị ảnh hưởng:</b>\n{pair_lines}"

    # Phần lựa chọn
    options_lines = []
    for key, desc in options.items():
        if key == "confirm":
            options_lines.append(f"  Gõ /confirm_{token} → {desc}")
        elif key == "skip":
            options_lines.append(f"  Gõ /skip_{token}    → {desc}")
        else:
            options_lines.append(f"  Gõ /{key}_{token}   → {desc}")

    timeout_h = timeout_min // 60
    timeout_m = timeout_min % 60
    timeout_str = f"{timeout_h} giờ" if timeout_m == 0 else f"{timeout_h} giờ {timeout_m} phút"

    msg = (
        f"⚠️ <b>{title}</b>\n\n"
        f"📋 <b>Tổng quan:</b>\n{problem_desc}"
        f"{pairs_section}\n\n"
        f"🔧 <b>Chọn phương án xử lý:</b>\n"
        + "\n".join(options_lines)
        + f"\n\n⏰ Hết hạn sau {timeout_str}. Nếu không phản hồi → tự động bỏ qua."
    )

    # Giới hạn 4096 ký tự (Telegram API limit)
    if len(msg) > 4096:
        msg = msg[:4090] + "\n[…]"

    tg_send(msg)


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
