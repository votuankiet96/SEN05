"""Side-effect-light TradingView network and browser readiness diagnostics."""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from core_engine.settings import CACHE_DIR


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


class ConnectivityProbe:
    """Bounded HTTPS preflight with a local cooldown after failures."""

    def __init__(self, *, enabled: bool, timeout_sec: float, cooldown_sec: int) -> None:
        self.enabled = enabled
        self.timeout_sec = timeout_sec
        self.cooldown_sec = cooldown_sec
        self.block_until = 0.0
        self.last_error = ""

    def check(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "preflight disabled"
        now = time.time()
        if now < self.block_until:
            remaining = max(0.0, self.block_until - now)
            return False, f"cooldown active for {remaining:.0f}s after {self.last_error}"
        try:
            response = requests.get(
                "https://www.tradingview.com/",
                headers={"User-Agent": _USER_AGENT},
                timeout=(2.0, self.timeout_sec),
                allow_redirects=True,
            )
            if response.status_code < 500:
                self.last_error = ""
                self.block_until = 0.0
                return True, f"HTTP {response.status_code}"
            reason = f"HTTP {response.status_code}"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        self.last_error = reason[:240]
        self.block_until = time.time() + self.cooldown_sec
        return False, self.last_error


def playwright_browser_status() -> tuple[bool, str]:
    """Return whether the managed Playwright Chromium executable exists."""

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return False, "playwright package is not installed"

    browser_cache = CACHE_DIR / "playwright-browsers"
    old_browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    try:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if executable.exists():
                return True, str(executable)
            return False, f"chromium executable missing: {executable}"
    except Exception as exc:
        return False, str(exc)
    finally:
        if old_browser_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = old_browser_path
