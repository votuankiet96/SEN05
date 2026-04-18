# =============================================================================
# data_provider/_tg_bot.py  —  Telegram bot nhận và xử lý lệnh từ xa
# =============================================================================
#
# FILE NÀY LÀM GÌ?
#   Đây là "tai nghe" của hệ thống trên Telegram — chạy nền 24/7 bên trong
#   02_ws_live.py, liên tục kiểm tra xem bạn có gửi lệnh nào không.
#   Khi nhận lệnh hợp lệ, nó tự động chạy script tương ứng.
#
# TẠI SAO CẦN FILE NÀY?
#   VPS không có màn hình. Bạn không thể ngồi trước máy tính mà gõ lệnh.
#   Thay vào đó, gõ lệnh trực tiếp vào nhóm Telegram từ điện thoại hoặc
#   máy tính bất kỳ đâu — hệ thống sẽ thực thi tương ứng.
#
# CÁCH HOẠT ĐỘNG:
#   Mỗi 20 giây (long-poll), bot gọi getUpdates để lấy tin nhắn mới từ Telegram.
#   Nếu tin nhắn bắt đầu bằng "/" và đến từ đúng chat của bạn → xử lý lệnh.
#   Các lệnh nặng (/fix, /pipeline) chạy trong thread riêng để không block bot.
#
# DANH SÁCH LỆNH HỖ TRỢ (gõ vào group Telegram):
#   /help              → liệt kê tất cả lệnh có thể dùng
#   /status            → scan nhanh chất lượng dữ liệu (chỉ đọc, không sửa)
#   /fix               → chạy Checker đầy đủ (sẽ hỏi xác nhận trước khi sửa DB)
#   /pipeline          → chạy Data Pipeline chế độ gap (bù dữ liệu còn thiếu)
#   /confirm_TOKEN     → xác nhận đồng ý sửa (TOKEN = mã 8 ký tự trong tin nhắn hỏi)
#   /skip_TOKEN        → từ chối sửa, chỉ lưu báo cáo
#
# AN TOÀN:
#   - Chỉ nhận lệnh từ đúng TELEGRAM_CHAT_ID được cấu hình trong .env
#   - Không cho phép chạy 2 tác vụ nặng đồng thời (_task_lock guard)
#   - /confirm và /skip có token ngẫu nhiên → không thể gõ nhầm lệnh cũ
# =============================================================================

import re
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        ok   = proc.returncode == 0
        icon = "✅" if ok else "⚠️"
        _reply(
            f"{icon} <b>[Bot] {name} hoàn tất</b>\n"
            f"Exit code: {proc.returncode}\n"
            f"<i>Xem log chi tiết trong thư mục logs/</i>"
        )
    except subprocess.TimeoutExpired:
        _reply(f"⏱ <b>[Bot] {name}</b> — quá 2 giờ, đã dừng.")
    except Exception as e:
        _reply(f"🚨 <b>[Bot] {name} lỗi:</b> {e}")
    finally:
        with _task_lock:
            _running_task = None


def _store_token_to_db(action: str, token: str) -> None:
    """
    Ghi kết quả token vào SEN.ActiveTask.Payload để Checker đọc được
    qua cross-process DB relay.

    Checker có thể đang chạy trong process khác và dùng embedded poll loop
    riêng → không share memory với WS bot. DB là kênh giao tiếp duy nhất.

    Silent fail nếu row không tồn tại (checker_repair chưa active).
    """
    try:
        import sys
        from pathlib import Path
        proj = Path(__file__).resolve().parent.parent
        if str(proj) not in sys.path:
            sys.path.insert(0, str(proj))
        from modules.db_connector import get_connection
        conn = get_connection()
        try:
            conn.cursor().execute(
                "UPDATE SEN.ActiveTask SET Payload = ? WHERE TaskName = 'checker_repair'",
                (f"{action}:{token}",),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # silent fail — không để DB relay làm crash bot listener


def _handle_command(text: str) -> None:
    """Xử lý lệnh nhận được từ Telegram."""
    global _running_task

    # ── Ưu tiên 1: token confirmation (/confirm_TOKEN, /skip_TOKEN) ──────────
    # Phải check TRƯỚC khi split bằng spaces vì token commands không có spaces
    m = re.match(r"^/(confirm|skip)_([A-Za-z0-9]{8})\b", text.strip(), re.IGNORECASE)
    if m:
        action = m.group(1).lower()   # chuẩn hóa về 'confirm' hoặc 'skip'
        token  = m.group(2).upper()   # chuẩn hóa về uppercase để khớp generate_token()

        # Thử xử lý trong same process (nếu Checker đang chạy trong WS process)
        try:
            from _task_lock import _handle_token_command
            _handle_token_command(text)
        except Exception:
            pass

        # Luôn ghi vào DB relay để Checker process (nếu riêng biệt) cũng nhận được
        _store_token_to_db(action, token)
        return

    # ── Ưu tiên 2: các lệnh thông thường ─────────────────────────────────────
    cmd = text.strip().lower().split()[0]

    if cmd in ("/help", "/start"):
        _reply(
            "🤖 <b>AutoTrading Bot — Lệnh hỗ trợ</b>\n\n"
            "/status    — Scan chất lượng dữ liệu (dry-run, ~20-30 phút)\n"
            "/fix       — Chạy Checker sửa dữ liệu (sẽ hỏi xác nhận)\n"
            "/pipeline  — Backfill dữ liệu còn thiếu\n"
            "/help      — Hiển thị trợ giúp này\n\n"
            "<i>Khi Checker phát hiện lỗi, bot sẽ gửi lệnh /confirm_TOKEN\n"
            "và /skip_TOKEN để bạn chọn trước khi sửa.</i>"
        )
        return

    if cmd == "/status":
        _reply("🔍 <b>[Bot]</b> Đang scan chất lượng dữ liệu (dry-run)...")
        # Checker dry-run: scan only, không ghi DB, không hỏi xác nhận
        t = threading.Thread(
            target=_run_task,
            args=("Checker Scan", [sys.executable, str(_DATA_DIR / "04_checker.py"), "--dry-run"]),
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
                _running_task = "Checker"
                task_cmd = [sys.executable, str(_DATA_DIR / "04_checker.py")]
                _reply(
                    "🔧 <b>[Bot]</b> Đang chạy Checker...\n"
                    "<i>Checker sẽ scan dữ liệu trước, sau đó hỏi bạn xác nhận\n"
                    "trước khi thực sự sửa. Vui lòng theo dõi và phản hồi khi được hỏi.</i>"
                )
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
    Gọi từ 02_ws_live.py khi khởi động.
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
