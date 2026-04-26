"""Discord webhook notifications cho data_provider.

Thay thế _telegram.py — tên hàm giữ nguyên (tg_send, tg_alert, tg_flush,
tg_ask, start_bot_listener, QUICK_COMMANDS_HINT) để các file chỉ cần đổi
đúng 1 dòng import.

Discord webhook là một chiều (outbound only) — không thể nhận lệnh ngược lại.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import DISCORD_WEBHOOK_URL  # noqa: E402

_pending_threads: list[threading.Thread] = []

# Màu embed Discord theo mức cảnh báo
_COLORS = {
    "INFO":    3447003,   # xanh dương
    "WARNING": 16776960,  # vàng
    "ERROR":   15158332,  # đỏ
}

# Thay thế QUICK_COMMANDS_HINT của Telegram — giờ là hướng dẫn xem log
QUICK_COMMANDS_HINT = (
    "\n─────────────────\n"
    "Xem chi tiết: data_provider/logs/ hoặc chạy thủ công\n"
    "  04_checker.py --dry-run\n"
    "  01_data_pipeline.py --mode gap"
)


# =============================================================================
# HÀM GỬI CỐT LÕI
# =============================================================================

def tg_send(message: str) -> None:
    """Gửi tin nhắn plain-text qua Discord webhook trong background thread.

    Tên hàm giữ nguyên tg_send để tương thích ngược với các file import.
    Discord không render HTML — tất cả tag <b>, <i>, <pre>... sẽ bị strip.
    """
    if not DISCORD_WEBHOOK_URL:
        return

    # Strip HTML tags mà Telegram dùng
    plain = re.sub(r"<[^>]+>", "", message)

    global _pending_threads

    def _send() -> None:
        import requests

        payload = {"content": plain[:2000]}   # Discord giới hạn 2000 ký tự
        for attempt in range(3):
            try:
                resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
                if resp.status_code in (200, 204):
                    return
                if resp.status_code == 429:
                    try:
                        wait = float(resp.json().get("retry_after", 5))
                    except Exception:
                        wait = 5.0
                    time.sleep(min(wait, 30))
                elif attempt < 2:
                    time.sleep(3)
            except Exception:
                if attempt < 2:
                    time.sleep(3)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    _pending_threads = [t for t in _pending_threads if t.is_alive()]
    _pending_threads.append(thread)


def tg_flush(timeout: float = 12.0) -> None:
    """Chờ tất cả Discord sends đang pending hoàn tất."""
    global _pending_threads
    alive: list[threading.Thread] = []
    for thread in _pending_threads:
        thread.join(timeout=timeout)
        if thread.is_alive():
            alive.append(thread)
    _pending_threads = alive


# =============================================================================
# HÀM ĐỊNH DẠNG
# =============================================================================

def tg_alert(level: str, text: str) -> None:
    """Gửi Discord embed có màu theo mức cảnh báo (INFO/WARNING/ERROR).

    Tên hàm giữ nguyên tg_alert để tương thích ngược với các file import.
    """
    if not DISCORD_WEBHOOK_URL:
        return

    import requests

    icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨"}
    icon  = icons.get(level, "📌")
    color = _COLORS.get(level, _COLORS["INFO"])
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    plain = re.sub(r"<[^>]+>", "", text)
    description = f"{plain}\n\n*{now}*"[:4096]

    global _pending_threads

    def _send() -> None:
        payload = {
            "embeds": [{
                "title":       f"{icon} AUTO TRADING — {level}",
                "description": description,
                "color":       color,
            }]
        }
        for attempt in range(3):
            try:
                resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
                if resp.status_code in (200, 204):
                    return
                if resp.status_code == 429:
                    try:
                        wait = float(resp.json().get("retry_after", 5))
                    except Exception:
                        wait = 5.0
                    time.sleep(min(wait, 30))
                elif attempt < 2:
                    time.sleep(3)
            except Exception:
                if attempt < 2:
                    time.sleep(3)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    _pending_threads = [t for t in _pending_threads if t.is_alive()]
    _pending_threads.append(thread)


def tg_ask(
    title: str,
    problem_desc: str,
    options: dict,
    token: str,
    timeout_min: int = 240,
    affected_pairs: list | None = None,
) -> None:
    """Gửi thông báo một chiều thay thế cho tg_ask tương tác của Telegram.

    Discord webhook không thể nhận lệnh phản hồi — hàm chỉ gửi thông báo
    cho biết hệ thống sắp tự xử lý (auto-repair hoặc auto-skip).
    Caller trong _task_lock.request_confirm() sẽ trả về 'timeout' ngay sau khi
    hàm này trả về.
    """
    if not DISCORD_WEBHOOK_URL:
        return

    import requests

    pairs_section = ""
    if affected_pairs:
        pair_lines = "\n".join(f"  • {p}" for p in affected_pairs[:8])
        if len(affected_pairs) > 8:
            pair_lines += f"\n  (... và {len(affected_pairs) - 8} pairs khác)"
        pairs_section = f"\n\n**Pairs bị ảnh hưởng:**\n{pair_lines}"

    timeout_h = timeout_min // 60
    timeout_m = timeout_min % 60
    timeout_str = f"{timeout_h}h" if timeout_m == 0 else f"{timeout_h}h {timeout_m}m"

    plain_desc = re.sub(r"<[^>]+>", "", problem_desc)
    body = (
        f"{plain_desc}{pairs_section}\n\n"
        f"⚙️ Discord webhook là một chiều — hệ thống sẽ tự xử lý.\n"
        f"(Trước đây sẽ đợi {timeout_str} để xác nhận qua Telegram.)"
    )[:4096]

    global _pending_threads

    def _send() -> None:
        payload = {
            "embeds": [{
                "title":       f"⚠️ {title}",
                "description": body,
                "color":       _COLORS["WARNING"],
            }]
        }
        for attempt in range(3):
            try:
                resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
                if resp.status_code in (200, 204):
                    return
                if resp.status_code == 429:
                    try:
                        wait = float(resp.json().get("retry_after", 5))
                    except Exception:
                        wait = 5.0
                    time.sleep(min(wait, 30))
                elif attempt < 2:
                    time.sleep(3)
            except Exception:
                if attempt < 2:
                    time.sleep(3)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    _pending_threads = [t for t in _pending_threads if t.is_alive()]
    _pending_threads.append(thread)


def start_bot_listener() -> None:
    """No-op: Discord webhook không có kênh nhận lệnh ngược lại.

    Giữ hàm này để 02_ws_live.py gọi mà không lỗi.
    Trả về None thay vì Thread — ws_live không join thread này nên an toàn.
    """
    return None
