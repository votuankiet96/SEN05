"""
Backend gửi thông báo tín hiệu qua Telegram hoặc Discord.

Mô tả:
    Notifier hỗ trợ 3 chế độ:
    - "telegram": Gửi qua Telegram Bot API (HTML parse mode).
    - "discord":  Gửi qua Discord Webhook (plain text, strip HTML tags).
    - "auto":     Tự detect dựa trên biến môi trường (ưu tiên Telegram).
    - "dry-run":  In ra stdout, không gửi thực sự.

Biến môi trường cần thiết:
    TELEGRAM_BOT_TOKEN: Token của Telegram Bot.
    TELEGRAM_CHAT_ID:   ID group/channel Telegram nhận thông báo.
    DISCORD_WEBHOOK_URL: URL webhook của Discord channel.

Xử lý lỗi và rate limit:
    Mọi lỗi mạng hoặc HTTP error được trả về qua NotifyResult (sent=False)
    thay vì raise exception — watcher tiếp tục chạy dù một lần gửi thất bại.
    Telegram: tự động retry khi nhận HTTP 429 (rate limit) với sleep theo retry_after.
    Throttle: tối thiểu 1.1 giây giữa hai lần gửi Telegram liên tiếp.

Phụ thuộc ngoài:
    requests (HTTP client), python-dotenv (load .env file).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests


# Khoảng cách tối thiểu giữa hai lần gửi Telegram liên tiếp (giây).
# Telegram API giới hạn ~30 tin nhắn/giây cho một bot; 1.1s = an toàn cho 1 bot.
_TELEGRAM_MIN_INTERVAL_SECONDS = 1.1

# Số lần retry tối đa khi nhận HTTP 429 (Too Many Requests).
_TELEGRAM_MAX_RETRIES = 2


@dataclass(frozen=True)
class NotifyResult:
    """
    Kết quả của một lần gửi thông báo.

    Thuộc tính:
        backend: Tên backend đã dùng ("telegram", "discord", "dry-run", "none").
        sent:    True nếu gửi thành công (hoặc dry-run in thành công).
        detail:  Mô tả kết quả: "sent", "printed", "network error: ...", "HTTP 429: ...".
    """
    backend: str
    sent: bool
    detail: str


class Notifier:
    """
    Gửi tin nhắn tín hiệu qua Telegram hoặc Discord.

    State:
        backend:                 Backend được chọn ("auto", "telegram", "discord", "none").
        dry_run:                 Nếu True, in ra stdout thay vì gửi thực sự.
        telegram_token:          Lấy từ TELEGRAM_BOT_TOKEN env var.
        telegram_chat_id:        Lấy từ TELEGRAM_CHAT_ID env var.
        discord_webhook:         Lấy từ DISCORD_WEBHOOK_URL env var.
        _last_telegram_send_at:  Timestamp lần gửi Telegram cuối (time.monotonic()).

    Lifecycle:
        Tạo một instance duy nhất khi khởi động watcher.
        Dùng lại instance đó cho mọi lần gửi — không tạo mới mỗi lần.
    """

    def __init__(self, backend: str = "auto", dry_run: bool = False) -> None:
        self.backend = backend.lower().strip()
        self.dry_run = dry_run
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._last_telegram_send_at = 0.0

    def send(self, message: str, chat_id: str | None = None) -> NotifyResult:
        """
        Gửi thông báo hoặc in ra console trong chế độ dry-run.

        Args:
            message: Nội dung tin nhắn (HTML cho Telegram, plain text cho Discord).
            chat_id: Override TELEGRAM_CHAT_ID cho lần gửi này.
                     Dùng khi mỗi nhóm scan có channel Telegram riêng.
                     None = dùng TELEGRAM_CHAT_ID từ env.

        Returns:
            NotifyResult mô tả kết quả.

        Side Effects:
            Gửi HTTP request đến Telegram/Discord API (nếu không phải dry-run).
            In ra stdout (nếu dry-run).
        """
        if self.dry_run:
            print(message)
            return NotifyResult("dry-run", True, "printed")

        backend = self._resolve_backend()
        if backend == "telegram":
            return self._send_telegram(message, chat_id=chat_id)
        if backend == "discord":
            return self._send_discord(message)
        return NotifyResult("none", False, "no notifier credentials configured")

    def _resolve_backend(self) -> str:
        """
        Xác định backend thực sự sẽ dùng.

        Nếu backend = "auto": ưu tiên Telegram nếu có token+chat_id, ngược lại Discord.

        Returns:
            "telegram", "discord", hoặc "none".
        """
        if self.backend in {"telegram", "discord", "none"}:
            return self.backend
        if self.telegram_token and self.telegram_chat_id:
            return "telegram"
        if self.discord_webhook:
            return "discord"
        return "none"

    def _send_telegram(self, message: str, chat_id: str | None = None) -> NotifyResult:
        """
        Gửi tin nhắn qua Telegram Bot API với retry khi rate-limit.

        Quy trình:
            1. Throttle: đợi nếu chưa đủ _TELEGRAM_MIN_INTERVAL_SECONDS kể từ lần gửi trước.
            2. POST đến sendMessage API, timeout 15s.
            3. Nếu 200 OK: trả về sent=True.
            4. Nếu 429 (rate limit) và còn retry: sleep theo retry_after, thử lại.
            5. Các lỗi khác: trả về sent=False.

        Args:
            message: Nội dung HTML.
            chat_id: Override chat ID (None = dùng TELEGRAM_CHAT_ID env).

        Returns:
            NotifyResult. sent=True nếu API trả về 200 OK.

        Side Effects:
            Cập nhật self._last_telegram_send_at sau mỗi lần gửi.
            Có thể sleep khi throttle hoặc retry.
        """
        if not self.telegram_token:
            return NotifyResult("telegram", False, "missing TELEGRAM_BOT_TOKEN")
        effective_chat_id = chat_id or self.telegram_chat_id
        if not effective_chat_id:
            return NotifyResult("telegram", False, "missing TELEGRAM_CHAT_ID")
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": effective_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(_TELEGRAM_MAX_RETRIES + 1):
            self._throttle_telegram()
            try:
                response = requests.post(url, json=payload, timeout=15)
            except requests.exceptions.RequestException as exc:
                return NotifyResult("telegram", False, f"network error: {exc}")
            self._last_telegram_send_at = time.monotonic()
            if response.ok:
                return NotifyResult("telegram", True, "sent")
            if response.status_code == 429 and attempt < _TELEGRAM_MAX_RETRIES:
                # Rate limited: lấy retry_after từ response body nếu có, mặc định 5s
                retry_after = _telegram_retry_after(response)
                time.sleep(retry_after)
                continue
            return NotifyResult("telegram", False, f"HTTP {response.status_code}: {response.text[:200]}")
        return NotifyResult("telegram", False, "telegram retry loop exhausted")

    def _throttle_telegram(self) -> None:
        """
        Đợi nếu chưa đủ thời gian tối thiểu giữa hai lần gửi Telegram.

        Tính elapsed kể từ lần gửi cuối; sleep phần còn lại nếu cần.
        Không làm gì nếu đã đủ khoảng cách.
        """
        elapsed = time.monotonic() - self._last_telegram_send_at
        remaining = _TELEGRAM_MIN_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _send_discord(self, message: str) -> NotifyResult:
        """
        Gửi tin nhắn qua Discord Webhook (plain text, strip HTML).

        Discord không hỗ trợ HTML tags nên phải strip trước khi gửi.
        Nội dung bị cắt ở 2000 ký tự (giới hạn Discord message length).

        Args:
            message: Nội dung HTML (sẽ được strip thành plain text).

        Returns:
            NotifyResult. sent=True nếu Discord trả về 200 hoặc 204.

        Side Effects:
            Gửi HTTP POST đến Discord webhook URL. Timeout 15 giây.
        """
        if not self.discord_webhook:
            return NotifyResult("discord", False, "missing DISCORD_WEBHOOK_URL")
        # Strip HTML tags để hiển thị đúng trên Discord
        import re
        plain = re.sub(r"<[^>]+>", "", message)
        try:
            response = requests.post(self.discord_webhook, json={"content": plain[:2000]}, timeout=15)
        except requests.exceptions.RequestException as exc:
            return NotifyResult("discord", False, f"network error: {exc}")
        if response.status_code in {200, 204}:
            return NotifyResult("discord", True, "sent")
        return NotifyResult("discord", False, f"HTTP {response.status_code}: {response.text[:200]}")


def _telegram_retry_after(response: requests.Response) -> float:
    """
    Trích xuất giá trị retry_after từ response 429 của Telegram.

    Telegram trả về {"parameters": {"retry_after": N}} khi rate limit.
    Trả về 5.0 giây nếu không parse được.

    Args:
        response: HTTP response từ Telegram API (status 429).

    Returns:
        Số giây cần đợi trước khi thử lại (ít nhất 1.0 giây, cộng thêm 1s buffer).
    """
    try:
        data = response.json()
    except ValueError:
        return 5.0
    parameters = data.get("parameters") if isinstance(data, dict) else None
    retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
    try:
        return max(1.0, float(retry_after) + 1.0)
    except (TypeError, ValueError):
        return 5.0
