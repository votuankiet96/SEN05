"""Curated Discord push for pilot monitoring — business-meaningful events only.

Thay hẳn Telegram (2026-09-29). Gửi qua Webhook, KHÔNG dùng bot đầy đủ — chỉ gửi 1 chiều, không cần
nhận phản hồi hay giữ kết nối gateway, đúng khuyến nghị đơn giản nhất cho use-case này. Đích tới là 1
thread có sẵn trong kênh của webhook (`?thread_id=` trên chính URL webhook — xác nhận từ tài liệu
chính thức: "Send a message to the specified thread within a webhook's channel", KHÔNG cần quyền gì
thêm ngoài chính webhook). `configure()` gọi 1 lần lúc khởi động; mọi module khác chỉ gọi `notify()`.
"""

import json
import logging
import re
import ssl
import urllib.request
from typing import Optional

import certifi

from engine.log import log_event, safe_error

_LOGGER = logging.getLogger(__name__)
# Xac nhan thuc te 2026-09-29: may nay goi discord.com/discordapp.com bi CERTIFICATE_VERIFY_FAILED
# (kho chung chi goc Windows thieu dung CA "Google Trust Services WE1" ky chung chi cua Discord) dù
# api.telegram.org tren CUNG may, CUNG cach goi lai xac thuc binh thuong — khong phai loi mang chung,
# chi thieu 1 CA cu the. Dung bo CA doc lap cua certifi (khong phu thuoc kho chung chi he dieu hanh)
# thay vi tat xac thuc SSL.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
# Xac nhan thuc te 2026-09-29: Cloudflare (dung truoc discord.com) tra ve 403 "error code: 1010"
# ("banned based on your browser's signature") cho User-Agent mac dinh cua urllib
# ("Python-urllib/3.12") — chan o tang Cloudflare, KHONG phai loi tu API Discord (webhook/thread deu
# hop le, xac nhan boi cung request thanh cong 204 khi doi User-Agent). Bat ky User-Agent khac mac
# dinh nao cung qua duoc (thu ca UA trinh duyet lan UA dang bot deu qua) — dat 1 chuoi rieng, trung
# thuc ve nguon goc request, khong gia lam trinh duyet.
_USER_AGENT = "SEN05-OF-Discord-Notifier/1.0"

# Chi dung EVENT nghiep vu duoc day Discord - toan bo trace ky thuat (connection, converter...) khong
# nam trong danh sach nay, chi vao file log. Da bo PLAN_COMPUTED/FX_CONVERSION_APPLIED/EXPOSURE_DECISION
# tu 2026-09-27 - day la 3 buoc TINH TOAN NOI BO, fire tren MOI tin hieu ke ca khi khong co gi xay ra.
_DISCORD_EVENTS = {
    "SIZE_TOO_SMALL", "SIZE_CAPPED",
    "ORDER_ACCEPTED", "ORDER_REJECTED", "ORDER_FILLED", "ORDER_DROPPED",
    "CLEANUP_FAILED", "POSITION_CLOSED", "SESSION_SUMMARY", "SESSION_SUMMARY_FAILED",
    "SIGNAL_SKIPPED", "STARTUP_UNRESOLVED", "STARTUP_RECOVERED", "STARTUP_COMPLETE", "CONNECTION_LOST",
    "SHUTDOWN", "UNEXPECTED_ERROR", "CLOSE_ALL_EXECUTED",
}

# Dich cac ma ly do noi bo (snake_case) sang cau tieng Anh tu nhien - chi gom nhung ma CO THE xuat
# hien trong message da day Discord (ORDER_DROPPED.reason); ma nao khong co trong day thi hien
# nguyen van thay vi bay loi - text goc van doc duoc, chi khong dep bang ban dich.
_REASON_EN = {
    "reversed_or_manually_cancelled": "reversed by a new signal, or cancelled manually",
    "expired_good_till_date": "expired before it could fill",
}

_TRADE_SIDE_LABEL = {1: "BUY", 2: "SELL"}  # model_messages.ProtoOATradeSide.BUY=1, SELL=2

# Discord markdown: \ * _ ~ ` | > deu co y nghia dac biet - escape de gia tri tu do (symbol, error
# text tu broker...) khong vo tinh tao dinh dang la hoac (voi '\\`') thoat khoi khoi <code>.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_~|>])")
_MAX_CONTENT_LENGTH = 2000  # gioi han cung cua Discord (message content), khong the vuot qua

_webhook_url: Optional[str] = None
_thread_id: Optional[str] = None


def configure(webhook_url: str, thread_id: str) -> None:
    """Goi 1 lan luc khoi dong (main.py). Rong = tat, notify() tu no-op."""
    global _webhook_url, _thread_id
    _webhook_url = webhook_url or None
    _thread_id = thread_id or None


def translate_reason(reason_code: str) -> str:
    """Cau tieng Anh tu nhien cho 1 ma ly do noi bo, hoac chinh ma do (khong raise) neu chua co
    trong bang - dung cho text hien thi Discord, KHONG dung cho log ky thuat (log giu nguyen ma goc)."""
    return _REASON_EN.get(reason_code, reason_code)


def escape_markdown(text) -> str:
    """Escape ky tu markdown dac biet cho MOI gia tri tu do (symbol, error_description tu broker,
    exception str()...) truoc khi nhet vao noi dung tin nhan - khong escape co the lam gia tri do
    vo tinh thanh **in dam**/_in nghieng_/thoat khoi `code`. KHONG dung ham nay cho chinh cu phap
    **/`` minh chu dong viet."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


def side_label(trade_side: int) -> str:
    """"BUY"/"SELL" cho 1 trade_side (model_messages.ProtoOATradeSide), hoac chinh so do neu la gia
    tri la (khong nen xay ra - cTrader chi co 2 gia tri BUY/SELL)."""
    return _TRADE_SIDE_LABEL.get(trade_side, str(trade_side))


def notify(event: str, text: str) -> None:
    """Best-effort — loi mang/API chi tu log lai (DISCORD_SEND_FAILED), khong raise, khong duoc
    chan luong dat lenh. Chi gui neu event nam trong danh sach nghiep vu da duyet. Cat bot neu vuot
    gioi han cung 2000 ky tu cua Discord thay vi de server tu choi ca tin nhan."""
    if not _webhook_url or not _thread_id:
        return
    if event.upper() not in _DISCORD_EVENTS:
        return
    if len(text) > _MAX_CONTENT_LENGTH:
        text = text[: _MAX_CONTENT_LENGTH - 3] + "..."
    url = f"{_webhook_url}?thread_id={_thread_id}"
    data = json.dumps({"content": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    )
    try:
        urllib.request.urlopen(request, timeout=5, context=_SSL_CONTEXT)
        log_event(_LOGGER, "INFO", "DISCORD_MESSAGE_SENT", "NONE", component="discord", source_event=event)
    except Exception as exc:
        log_event(_LOGGER, "ERROR", "DISCORD_SEND_FAILED", "LOW", component="discord",
                   source_event=event, error=safe_error(exc))
