"""Curated Telegram push for pilot monitoring — business-meaningful events only.

The full technical trace (every wire message, every heartbeat, every symbol load) stays file-only —
same split dp_program uses between its own log file and its Discord reporter. `configure()` is
called once at startup; every module then just calls `notify()` without needing the credentials
threaded through its own function signature.
"""

import logging
import urllib.parse
import urllib.request
from typing import Optional

from engine.log import log_event, safe_error

_LOGGER = logging.getLogger(__name__)

# Chi dung EVENT nghiep vu duoc day Telegram - toan bo trace ky thuat (connection, converter...)
# khong nam trong danh sach nay, chi vao file log.
_TELEGRAM_EVENTS = {
    "PLAN_COMPUTED", "FX_CONVERSION_APPLIED", "SIZE_TOO_SMALL", "SIZE_CAPPED",
    "ORDER_ACCEPTED", "ORDER_REJECTED", "ORDER_FILLED", "ORDER_DROPPED",
    "CLEANUP_FAILED", "POSITION_CLOSED", "EXPOSURE_DECISION", "SESSION_SUMMARY",
    "SIGNAL_SKIPPED", "STARTUP_UNRESOLVED", "STARTUP_COMPLETE", "CONNECTION_LOST",
    "SHUTDOWN", "UNEXPECTED_ERROR", "CLOSE_ALL_EXECUTED",
}

_bot_token: Optional[str] = None
_chat_id: Optional[str] = None


def configure(bot_token: str, chat_id: str) -> None:
    """Goi 1 lan luc khoi dong (main.py). Rong = tat, notify() tu no-op."""
    global _bot_token, _chat_id
    _bot_token = bot_token or None
    _chat_id = chat_id or None


def notify(event: str, text: str) -> None:
    """Best-effort — loi mang/API chi tu log lai (TELEGRAM_SEND_FAILED), khong raise, khong duoc
    chan luong dat lenh. Chi gui neu event nam trong danh sach nghiep vu da duyet."""
    if not _bot_token or not _chat_id:
        return
    if event.upper() not in _TELEGRAM_EVENTS:
        return
    url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": _chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
        log_event(_LOGGER, "INFO", "TELEGRAM_MESSAGE_SENT", "NONE", component="telegram", source_event=event)
    except Exception as exc:
        log_event(_LOGGER, "ERROR", "TELEGRAM_SEND_FAILED", "LOW", component="telegram",
                   source_event=event, error=safe_error(exc))
