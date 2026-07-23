"""hCaptcha solving for the TradingView headless-login fallback.

Only used by the headless fresh-login browser flow when
`TV_AUTH_HEADLESS_FRESH_LOGIN` is enabled and a captcha service key is
configured. No shared auth state is read or written here.
"""

from __future__ import annotations

import time

import requests

from core_engine.settings import TRADINGVIEW

from core_engine.util.logkit import get_logger

_logger = get_logger("tv_auth", console=False)


def _get_totp_code() -> str | None:
    """Tạo mã TOTP từ TRADINGVIEW.two_fa_secret; trả về None nếu thiếu secret hoặc pyotp."""
    if not TRADINGVIEW.two_fa_secret:
        return None
    try:
        import pyotp  # type: ignore

        return pyotp.TOTP(TRADINGVIEW.two_fa_secret).now()
    except ImportError:
        _logger.warning(
            "[AUTH] pyotp not installed - cannot auto-fill 2FA. Install: pip install pyotp"
        )
        return None
    except Exception as exc:
        _logger.warning("[AUTH] TOTP generation failed: %s", exc)
        return None


def _solve_via_capsolver(sitekey: str, page_url: str) -> str | None:
    """Gửi hCaptcha lên CapSolver API và poll đến khi có solution token."""
    try:
        payload = {
            "clientKey": TRADINGVIEW.captcha_api_key,
            "task": {
                "type": "HCaptchaTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }
        r = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=15)
        task_id = r.json().get("taskId")
        if not task_id:
            _logger.warning("[AUTH] CapSolver: task creation failed - %s", r.text[:200])
            return None
        for _ in range(60):
            time.sleep(1)
            r2 = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": TRADINGVIEW.captcha_api_key, "taskId": task_id},
                timeout=15,
            )
            data = r2.json()
            status = data.get("status")
            if status == "ready":
                return data.get("solution", {}).get("gRecaptchaResponse")
            if status == "failed":
                _logger.warning("[AUTH] CapSolver: task failed - %s", data)
                return None
        _logger.warning("[AUTH] CapSolver: timed out waiting for solution.")
        return None
    except Exception as exc:
        _logger.warning("[AUTH] CapSolver error: %s", exc)
        return None


def _solve_via_2captcha(sitekey: str, page_url: str) -> str | None:
    """Gửi hCaptcha lên 2Captcha API và poll đến khi có solution token."""
    try:
        r = requests.post(
            "http://2captcha.com/in.php",
            data={
                "key": TRADINGVIEW.captcha_api_key,
                "method": "hcaptcha",
                "sitekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=15,
        )
        data = r.json()
        if data.get("status") != 1:
            _logger.warning("[AUTH] 2Captcha: submit failed - %s", data)
            return None
        task_id = data["request"]
        for _ in range(30):
            time.sleep(2)
            r2 = requests.get(
                "http://2captcha.com/res.php",
                params={"key": TRADINGVIEW.captcha_api_key, "action": "get", "id": task_id, "json": 1},
                timeout=15,
            )
            data2 = r2.json()
            if data2.get("status") == 1:
                return data2["request"]
            if data2.get("request") != "CAPCHA_NOT_READY":
                _logger.warning("[AUTH] 2Captcha: error - %s", data2)
                return None
        _logger.warning("[AUTH] 2Captcha: timed out waiting for solution.")
        return None
    except Exception as exc:
        _logger.warning("[AUTH] 2Captcha error: %s", exc)
        return None


def _solve_captcha_hcaptcha(sitekey: str, page_url: str) -> str | None:
    """Chọn CapSolver hoặc 2Captcha theo TRADINGVIEW.captcha_service; trả về None nếu đang tắt."""
    if not TRADINGVIEW.captcha_api_key:
        return None
    service = (TRADINGVIEW.captcha_service or "capsolver").lower()
    if service == "2captcha":
        return _solve_via_2captcha(sitekey, page_url)
    return _solve_via_capsolver(sitekey, page_url)
