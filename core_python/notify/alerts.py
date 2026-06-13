"""Human alert formatting and delivery for realtime signals."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import requests

from core_python.strategies.combo.realtime import format_combo_raw_signal_message
from core_python.strategies.ma_cross.realtime import format_signal_message

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_TELEGRAM_MIN_INTERVAL_SECONDS = 1.1
_TELEGRAM_MAX_RETRIES = 2


@dataclass(frozen=True)
class NotifyResult:
    backend: str
    sent: bool
    detail: str


class Notifier:
    """Send human-facing alerts through Telegram or Discord."""

    def __init__(self, backend: str = "auto", dry_run: bool = False) -> None:
        self.backend = backend.lower().strip()
        self.dry_run = dry_run
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.signal_discord_webhook = (
            os.environ.get("SIGNAL_DISCORD_WEBHOOK_URL", "")
            or os.environ.get("DISCORD_WEBHOOK_URL", "")
        )
        self.discord_webhook = self.signal_discord_webhook
        self._last_telegram_send_at = 0.0

    def send(
        self,
        message: str,
        chat_id: str | None = None,
        *,
        backend: str | None = None,
        discord_webhook: str | None = None,
    ) -> NotifyResult:
        if self.dry_run:
            print(message)
            return NotifyResult("dry-run", True, "printed")

        resolved_backend = self._resolve_backend(backend)
        if resolved_backend == "telegram":
            return self._send_telegram(message, chat_id=chat_id)
        if resolved_backend == "discord":
            return self._send_discord(message, webhook_url=discord_webhook)
        return NotifyResult("none", False, "no notifier credentials configured")

    def _resolve_backend(self, backend: str | None = None) -> str:
        selected = (backend or self.backend).lower().strip()
        if self.backend == "none":
            return "none"
        if selected in {"telegram", "discord", "none"}:
            return selected
        if self.telegram_token and self.telegram_chat_id:
            return "telegram"
        if self.signal_discord_webhook:
            return "discord"
        return "none"

    def _send_telegram(self, message: str, chat_id: str | None = None) -> NotifyResult:
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
                time.sleep(_telegram_retry_after(response))
                continue
            return NotifyResult("telegram", False, f"HTTP {response.status_code}: {response.text[:200]}")
        return NotifyResult("telegram", False, "telegram retry loop exhausted")

    def _throttle_telegram(self) -> None:
        elapsed = time.monotonic() - self._last_telegram_send_at
        remaining = _TELEGRAM_MIN_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _send_discord(self, message: str, webhook_url: str | None = None) -> NotifyResult:
        effective_webhook = webhook_url or self.signal_discord_webhook
        if not effective_webhook:
            return NotifyResult("discord", False, "missing SIGNAL_DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_URL")
        plain = re.sub(r"<[^>]+>", "", message)
        try:
            response = requests.post(effective_webhook, json={"content": plain[:2000]}, timeout=15)
        except requests.exceptions.RequestException as exc:
            return NotifyResult("discord", False, f"network error: {exc}")
        if response.status_code in {200, 204}:
            return NotifyResult("discord", True, "sent")
        return NotifyResult("discord", False, f"HTTP {response.status_code}: {response.text[:200]}")


def _telegram_retry_after(response: requests.Response) -> float:
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
