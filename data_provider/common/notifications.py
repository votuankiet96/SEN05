"""Discord webhook notifications cho data_provider.

Thay thế _telegram.py — tên hàm giữ nguyên cho các entrypoint đang import
tg_send, tg_alert, tg_flush, start_bot_listener và QUICK_COMMANDS_HINT.

Discord webhook là một chiều (outbound only) — không thể nhận lệnh ngược lại.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
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
    "\n-----------------\n"
    "Details: check data_provider/runtime/logs/ or run manually\n"
    "  python -m data_provider.apps.checker --dry-run\n"
    "  python -m data_provider.apps.pipeline --mode gap"
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

    icons = {"INFO": "[INFO]", "WARNING": "[WARN]", "ERROR": "[ERROR]"}
    icon  = icons.get(level, "[NOTE]")
    color = _COLORS.get(level, _COLORS["INFO"])
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    plain = re.sub(r"<[^>]+>", "", text)
    description = f"{plain}\n\n*{now}*"[:4096]

    global _pending_threads

    def _send() -> None:
        payload = {
            "embeds": [{
                "title":       f"{icon} AUTO TRADING - {level}",
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


def start_bot_listener() -> None:
    """No-op: Discord webhook không có kênh nhận lệnh ngược lại.

    Giữ hàm này để ws_live.py gọi mà không lỗi.
    Trả về None thay vì Thread — ws_live không join thread này nên an toàn.
    """
    return None
