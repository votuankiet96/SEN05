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
# khong nam trong danh sach nay, chi vao file log. 2026-09-27: bo PLAN_COMPUTED/FX_CONVERSION_APPLIED/
# EXPOSURE_DECISION khoi day - day la 3 buoc TINH TOAN NOI BO, fire tren MOI tin hieu ke ca khi
# khong co gi xay ra, la nguon nhieu chinh (user phan hoi: qua nhieu, kho theo doi). Van con day du
# trong file log (log_event khong doi), chi khong day Telegram nua.
_TELEGRAM_EVENTS = {
    "SIZE_TOO_SMALL", "SIZE_CAPPED",
    "ORDER_ACCEPTED", "ORDER_REJECTED", "ORDER_FILLED", "ORDER_DROPPED",
    "CLEANUP_FAILED", "POSITION_CLOSED", "SESSION_SUMMARY", "SESSION_SUMMARY_FAILED",
    "SIGNAL_SKIPPED", "STARTUP_UNRESOLVED", "STARTUP_COMPLETE", "CONNECTION_LOST",
    "SHUTDOWN", "UNEXPECTED_ERROR", "CLOSE_ALL_EXECUTED",
}

# Dich cac ma ly do noi bo (snake_case) sang cau tieng Anh tu nhien - chi gom nhung ma CO THE xuat
# hien trong message da day Telegram (ORDER_DROPPED.reason); ma nao khong co trong day thi hien
# nguyen van thay vi bay loi - text goc van doc duoc, chi khong dep bang ban dich.
_REASON_EN = {
    "reversed_or_manually_cancelled": "reversed by a new signal, or cancelled manually",
    "expired_good_till_date": "expired before it could fill",
}

_TRADE_SIDE_LABEL = {1: "BUY", 2: "SELL"}  # model_messages.ProtoOATradeSide.BUY=1, SELL=2

_bot_token: Optional[str] = None
_chat_id: Optional[str] = None


def configure(bot_token: str, chat_id: str) -> None:
    """Goi 1 lan luc khoi dong (main.py). Rong = tat, notify() tu no-op."""
    global _bot_token, _chat_id
    _bot_token = bot_token or None
    _chat_id = chat_id or None


def translate_reason(reason_code: str) -> str:
    """Cau tieng Anh tu nhien cho 1 ma ly do noi bo, hoac chinh ma do (khong raise) neu chua co
    trong bang - dung cho text hien thi Telegram, KHONG dung cho log ky thuat (log giu nguyen ma goc)."""
    return _REASON_EN.get(reason_code, reason_code)


def escape_html(text) -> str:
    """Escape 3 ky tu HTML dac biet cho MOI gia tri tu do (error_description tu broker, exception
    str()...) truoc khi nhet vao text co parse_mode=HTML - khong escape se co the lam Telegram tra
    ve 400 "can't parse entities" (vd text chua ky tu '<') hoac te hon, doi nghia thanh tag khong
    dinh truoc. KHONG dung ham nay cho chinh cac tag <b>/<code> minh chu dong viet."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def side_label(trade_side: int) -> str:
    """"BUY"/"SELL" cho 1 trade_side (model_messages.ProtoOATradeSide), hoac chinh so do neu la gia
    tri la (khong nen xay ra - cTrader chi co 2 gia tri BUY/SELL)."""
    return _TRADE_SIDE_LABEL.get(trade_side, str(trade_side))


def notify(event: str, text: str) -> None:
    """Best-effort — loi mang/API chi tu log lai (TELEGRAM_SEND_FAILED), khong raise, khong duoc
    chan luong dat lenh. Chi gui neu event nam trong danh sach nghiep vu da duyet. parse_mode=HTML
    de message co the dung <b>/<code> lam ro thong tin quan trong — text truyen vao PHAI la HTML
    hop le (escape san o call site neu co gia tri tu do khong tin cay)."""
    if not _bot_token or not _chat_id:
        return
    if event.upper() not in _TELEGRAM_EVENTS:
        return
    url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": _chat_id, "text": text, "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
        log_event(_LOGGER, "INFO", "TELEGRAM_MESSAGE_SENT", "NONE", component="telegram", source_event=event)
    except Exception as exc:
        log_event(_LOGGER, "ERROR", "TELEGRAM_SEND_FAILED", "LOW", component="telegram",
                   source_event=event, error=safe_error(exc))
