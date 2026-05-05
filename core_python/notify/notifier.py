"""Notification backends for signal alerts."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests


@dataclass(frozen=True)
class NotifyResult:
    backend: str
    sent: bool
    detail: str


class Notifier:
    """Send signal messages through Telegram or Discord."""

    def __init__(self, backend: str = "auto", dry_run: bool = False) -> None:
        self.backend = backend.lower().strip()
        self.dry_run = dry_run
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")

    def send(self, message: str, chat_id: str | None = None) -> NotifyResult:
        """Send a notification or print it in dry-run mode.

        chat_id overrides the default TELEGRAM_CHAT_ID for per-group routing.
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
        if self.backend in {"telegram", "discord", "none"}:
            return self.backend
        if self.telegram_token and self.telegram_chat_id:
            return "telegram"
        if self.discord_webhook:
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
        response = requests.post(url, json=payload, timeout=15)
        if response.ok:
            return NotifyResult("telegram", True, "sent")
        return NotifyResult("telegram", False, f"HTTP {response.status_code}: {response.text[:200]}")

    def _send_discord(self, message: str) -> NotifyResult:
        if not self.discord_webhook:
            return NotifyResult("discord", False, "missing DISCORD_WEBHOOK_URL")
        # Strip HTML tags for Discord (plain text)
        import re
        plain = re.sub(r"<[^>]+>", "", message)
        response = requests.post(self.discord_webhook, json={"content": plain[:2000]}, timeout=15)
        if response.status_code in {200, 204}:
            return NotifyResult("discord", True, "sent")
        return NotifyResult("discord", False, f"HTTP {response.status_code}: {response.text[:200]}")
