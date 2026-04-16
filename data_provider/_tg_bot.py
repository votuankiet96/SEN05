# =============================================================================
# data_provider/_tg_bot.py  —  Telegram bot command handler
# =============================================================================
# Module này chạy trong background thread (gọi từ 03_ws_live.py) và lắng nghe
# lệnh từ Telegram. Khi nhận lệnh, tự động spawn subprocess để thực hiện.
#
# Lệnh hỗ trợ (gõ vào group Telegram):
#   /status      → chạy reconcile 24h, báo cáo ngay
#   /fix         → chạy gap_fill 2 ngày, vá lỗ hổng
#   /pipeline    → chạy data_pipeline --mode gap (backfill)
#   /help        → liệt kê các lệnh
# =============================================================================

import subprocess
import sys
import threading
import time
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent

# Theo dõi update_id cuối cùng đã xử lý (tránh xử lý lại tin cũ)
_last_update_id: int = 0

# Khoá để tránh chạy 2 lệnh nặng đồng thời
_task_lock = threading.Lock()
_running_task: str | None = None

# ─────────────────────────────────────────────────────────────────────────────

def _get_updates() -> list[dict]:
    """Lấy danh sách update mới từ Telegram (getUpdates long-poll 20s)."""
    global _last_update_id
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return []
    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 20},
            timeout=30,
        )
        if not r.ok:
            return []
        updates = r.json().get("result", [])
        if updates:
            _last_update_id = updates[-1]["update_id"]
        return updates
    except Exception:
        return []


def _reply(text: str) -> None:
    """Gửi tin trả lời lên Telegram (đơn giản, không cần retry)."""
    from _tg import tg_send
    tg_send(text)


def _run_task(name: str, cmd: list[str]) -> None:
    """Spawn subprocess, gửi kết quả về Telegram khi xong."""
    global _running_task
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        ok   = proc.returncode == 0
        icon = "✅" if ok else "⚠️"
        _reply(
            f"{icon} <b>[Bot] {name} hoàn tất</b>\n"
            f"Exit code: {proc.returncode}\n"
            f"<i>Xem log chi tiết trong thư mục logs/</i>"
        )
    except subprocess.TimeoutExpired:
        _reply(f"⏱ <b>[Bot] {name}</b> — quá 30 phút, đã dừng.")
    except Exception as e:
        _reply(f"🚨 <b>[Bot] {name} lỗi:</b> {e}")
    finally:
        with _task_lock:
            _running_task = None


def _handle_command(text: str) -> None:
    """Xử lý lệnh nhận được từ Telegram."""
    global _running_task
    cmd = text.strip().lower().split()[0]

    if cmd in ("/help", "/start"):
        _reply(
            "🤖 <b>AutoTrading Bot — Lệnh hỗ trợ</b>\n\n"
            "/status   — Kiểm tra chất lượng dữ liệu 24h (reconcile)\n"
            "/fix       — Vá lỗ hổng dữ liệu (gap_fill 2 ngày)\n"
            "/pipeline  — Backfill dữ liệu còn thiếu (data_pipeline)\n"
            "/help      — Hiển thị trợ giúp này\n\n"
            "<i>Lưu ý: /fix và /pipeline cần ~5-30 phút để hoàn tất.</i>"
        )
        return

    if cmd == "/status":
        _reply("🔍 <b>[Bot]</b> Đang kiểm tra chất lượng dữ liệu...")
        # Reconcile là read-only, chạy thẳng không cần lock
        t = threading.Thread(
            target=_run_task,
            args=("Reconcile 24h", [sys.executable, str(_DATA_DIR / "05_reconcile.py"), "--lookback", "24"]),
            daemon=True,
        )
        t.start()
        return

    if cmd in ("/fix", "/pipeline"):
        with _task_lock:
            if _running_task:
                _reply(f"⏳ <b>[Bot]</b> Đang chạy <b>{_running_task}</b>, vui lòng chờ xong.")
                return
            if cmd == "/fix":
                _running_task = "Gap Fill"
                task_cmd = [sys.executable, str(_DATA_DIR / "02_gap_fill.py"), "--lookback", "2"]
                _reply("🔧 <b>[Bot]</b> Đang chạy Gap Fill (lookback 2 ngày)...")
            else:
                _running_task = "Data Pipeline"
                task_cmd = [sys.executable, str(_DATA_DIR / "01_data_pipeline.py"), "--mode", "gap"]
                _reply("🚀 <b>[Bot]</b> Đang chạy Data Pipeline (mode: gap)...")

        t = threading.Thread(target=_run_task, args=(_running_task, task_cmd), daemon=True)
        t.start()
        return

    # Lệnh không nhận ra — bỏ qua (tránh reply rác khi có tin nhắn bình thường)


def _is_from_our_chat(update: dict) -> tuple[bool, str]:
    """Kiểm tra update có phải từ channel/group của mình và có text không."""
    for key in ("message", "channel_post"):
        msg = update.get(key, {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "")
        if chat_id == str(TELEGRAM_CHAT_ID) and text.startswith("/"):
            return True, text
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────

def start_bot_listener() -> threading.Thread:
    """
    Khởi động bot listener trong background thread.
    Gọi từ 03_ws_live.py khi khởi động.
    Trả về thread object.
    """
    def _loop():
        # Lấy hết update cũ trước khi bắt đầu lắng nghe (skip backlog)
        _get_updates()

        while True:
            try:
                updates = _get_updates()
                for update in updates:
                    ok, text = _is_from_our_chat(update)
                    if ok:
                        _handle_command(text)
            except Exception:
                pass
            time.sleep(2)  # Ngắn vì getUpdates đã long-poll 20s

    t = threading.Thread(target=_loop, daemon=True, name="tg-bot-listener")
    t.start()
    return t
