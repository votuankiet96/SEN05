"""Unified Telegram notifications and bot listener for data_provider."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent
_pending_threads: list[threading.Thread] = []
_last_update_id: int = 0
_bot_lock = threading.Lock()
_running_task: str | None = None


def tg_send(message: str) -> None:
    """Send an HTML Telegram message in a background thread."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    global _pending_threads

    def _send() -> None:
        import requests

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=10)
                if response.ok:
                    return
                if response.status_code == 429:
                    wait = min(float(response.headers.get("Retry-After", 5)), 30)
                    time.sleep(wait)
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
    """Wait for pending Telegram sends to finish."""
    global _pending_threads

    alive_threads: list[threading.Thread] = []
    for thread in _pending_threads:
        thread.join(timeout=timeout)
        if thread.is_alive():
            alive_threads.append(thread)
    _pending_threads = alive_threads


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
    """Send an interactive Telegram confirmation message."""
    pairs_section = ""
    if affected_pairs:
        pair_lines = "\n".join(f"  • {pair}" for pair in affected_pairs[:8])
        if len(affected_pairs) > 8:
            pair_lines += f"\n  (... và {len(affected_pairs) - 8} pairs khác)"
        pairs_section = f"\n\n📌 <b>Pairs bị ảnh hưởng:</b>\n{pair_lines}"

    option_lines = []
    for key, desc in options.items():
        if key == "confirm":
            option_lines.append(f"  Gõ /confirm_{token} → {desc}")
        elif key == "skip":
            option_lines.append(f"  Gõ /skip_{token}    → {desc}")
        else:
            option_lines.append(f"  Gõ /{key}_{token}   → {desc}")

    timeout_h = timeout_min // 60
    timeout_m = timeout_min % 60
    timeout_str = f"{timeout_h} giờ" if timeout_m == 0 else f"{timeout_h} giờ {timeout_m} phút"

    msg = (
        f"⚠️ <b>{title}</b>\n\n"
        f"📋 <b>Tổng quan:</b>\n{problem_desc}"
        f"{pairs_section}\n\n"
        f"🔧 <b>Chọn phương án xử lý:</b>\n"
        + "\n".join(option_lines)
        + f"\n\n⏰ Hết hạn sau {timeout_str}. Nếu không phản hồi → tự động bỏ qua."
    )
    if len(msg) > 4096:
        msg = msg[:4090] + "\n[...]"

    tg_send(msg)


def tg_alert(level: str, text: str) -> None:
    """Send a formatted Telegram alert."""
    icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨"}
    icon = icons.get(level, "📌")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tg_send(f"{icon} <b>[AUTO TRADING — {level}]</b>\n{text}\n<i>{now}</i>")


def _get_updates() -> list[dict]:
    """Fetch new updates from Telegram via long-polling."""
    global _last_update_id
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return []
    try:
        import requests

        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 20},
            timeout=30,
        )
        if not response.ok:
            return []
        updates = response.json().get("result", [])
        if updates:
            _last_update_id = updates[-1]["update_id"]
        return updates
    except Exception:
        return []


def _reply(text: str) -> None:
    """Reply through the shared Telegram sender."""
    tg_send(text)


def _run_task(name: str, cmd: list[str]) -> None:
    """Run a heavy background task and notify completion."""
    global _running_task
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        ok = proc.returncode == 0
        icon = "✅" if ok else "⚠️"
        _reply(
            f"{icon} <b>[Bot] {name} hoàn tất</b>\n"
            f"Exit code: {proc.returncode}\n"
            f"<i>Xem log chi tiết trong thư mục logs/</i>"
        )
    except subprocess.TimeoutExpired:
        _reply(f"⏱ <b>[Bot] {name}</b> — quá 2 giờ, đã dừng.")
    except Exception as exc:
        _reply(f"🚨 <b>[Bot] {name} lỗi:</b> {exc}")
    finally:
        with _bot_lock:
            _running_task = None


def _store_token_to_db(action: str, token: str) -> None:
    """Persist token commands so another process can observe them."""
    try:
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
        pass


def _handle_command(text: str) -> None:
    """Handle supported Telegram slash commands."""
    global _running_task

    match = re.match(r"^/(confirm|skip)_([A-Za-z0-9]{8})\b", text.strip(), re.IGNORECASE)
    if match:
        action = match.group(1).lower()
        token = match.group(2).upper()
        try:
            from _task_lock import _handle_token_command

            _handle_token_command(text)
        except Exception:
            pass
        _store_token_to_db(action, token)
        return

    cmd = text.strip().lower().split()[0]

    if cmd in ("/help", "/start"):
        _reply(
            "🤖 <b>AutoTrading Bot — Lệnh hỗ trợ</b>\n\n"
            "/status    — Scan chất lượng dữ liệu (dry-run, ~20-30 phút)\n"
            "/fix       — Chạy Checker sửa dữ liệu (auto-repair nếu không dùng manual-confirm)\n"
            "/pipeline  — Backfill dữ liệu còn thiếu\n"
            "/help      — Hiển thị trợ giúp này\n\n"
            "<i>Checker chỉ hỏi /confirm_TOKEN khi chạy với --manual-confirm.\n"
            "Mặc định hiện tại là scan xong sẽ tự repair ngay.</i>"
        )
        return

    if cmd == "/status":
        with _bot_lock:
            if _running_task:
                _reply(f"⏳ <b>[Bot]</b> Đang chạy <b>{_running_task}</b>, vui lòng chờ xong.")
                return
            _running_task = "Checker Scan"
        _reply("🔍 <b>[Bot]</b> Đang scan chất lượng dữ liệu (dry-run)...")
        threading.Thread(
            target=_run_task,
            args=("Checker Scan", [sys.executable, str(_DATA_DIR / "04_checker.py"), "--dry-run"]),
            daemon=True,
        ).start()
        return

    if cmd in ("/fix", "/pipeline"):
        with _bot_lock:
            if _running_task:
                _reply(f"⏳ <b>[Bot]</b> Đang chạy <b>{_running_task}</b>, vui lòng chờ xong.")
                return
            if cmd == "/fix":
                _running_task = "Checker"
                task_cmd = [sys.executable, str(_DATA_DIR / "04_checker.py")]
                _reply(
                    "🔧 <b>[Bot]</b> Đang chạy Checker...\n"
                    "<i>Checker sẽ scan trước rồi tự repair ngay theo cấu hình hiện tại.\n"
                    "Chỉ các run với --manual-confirm mới chờ bạn /confirm.</i>"
                )
            else:
                _running_task = "Data Pipeline"
                task_cmd = [sys.executable, str(_DATA_DIR / "01_data_pipeline.py"), "--mode", "gap"]
                _reply("🚀 <b>[Bot]</b> Đang chạy Data Pipeline (mode: gap)...")

        threading.Thread(target=_run_task, args=(_running_task, task_cmd), daemon=True).start()
        return


def _is_from_our_chat(update: dict) -> tuple[bool, str]:
    """Check whether an update comes from the configured Telegram chat."""
    for key in ("message", "channel_post"):
        msg = update.get(key, {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if chat_id == str(TELEGRAM_CHAT_ID) and text.startswith("/"):
            return True, text
    return False, ""


def start_bot_listener() -> threading.Thread:
    """Start the Telegram command listener in a daemon thread."""

    def _loop() -> None:
        _get_updates()
        while True:
            try:
                for update in _get_updates():
                    ok, text = _is_from_our_chat(update)
                    if ok:
                        _handle_command(text)
            except Exception:
                pass
            time.sleep(2)

    thread = threading.Thread(target=_loop, daemon=True, name="telegram-bot-listener")
    thread.start()
    return thread
