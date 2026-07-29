"""Fail-closed TradingView account authentication and headless Chromium renewal."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from ..configuration import GUEST_TOKEN
from ..log import log_event
LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124"
_NEXT_REFRESH_ATTEMPT = 0.0

class AuthError(RuntimeError):
    pass


def _claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def token_seconds_remaining(token: str, *, now: float | None = None) -> float:
    """Return JWT lifetime, or -1 when the expiry cannot be decoded."""
    try:
        return float(_claims(token)["exp"]) - (time.time() if now is None else now)
    except (KeyError, TypeError, ValueError):
        return -1.0

def _authenticated(token: str, minimum_ttl: int) -> bool:
    if not token or token == GUEST_TOKEN:
        return False
    claims = _claims(token)
    identity = claims.get("user_id") or claims.get("id") or claims.get("sub")
    return bool(identity) and token_seconds_remaining(token) > minimum_ttl

def _cache_path(config: dict[str, Any]) -> Path:
    return Path(config["app"]["runtime_dir"]) / "cache" / "tradingview_auth.json"

def _load_cache(config: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(_cache_path(config).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _write_cache(config: dict[str, Any], payload: dict[str, Any]) -> None:
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)

def _save_cache(config: dict[str, Any], token: str, cookie: str, source: str) -> dict[str, Any]:
    if not _authenticated(token, 60):
        raise AuthError(f"TradingView {source} did not return an authenticated JWT")
    payload = {
        "token": token, "cookie": cookie, "source": source, "refreshed_at": time.time()
    }
    _write_cache(config, payload)
    return payload

def _cookie_list(raw: str) -> list[dict[str, Any]]:
    result = []
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name and value:
            result.append({"name": name, "value": value, "domain": ".tradingview.com",
                           "path": "/", "secure": True})
    return result

def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    valid = (
        item for item in cookies
        if item.get("name") and item.get("value")
        and "tradingview.com" in str(item.get("domain", "tradingview.com"))
    )
    return "; ".join(f"{item['name']}={item['value']}" for item in valid)

def _page_token(page: Any, cookies: list[dict[str, Any]]) -> str:
    script = """() => {
      for (const text of [document.documentElement.innerHTML, document.cookie]) {
        const m = String(text || "").match(/"auth_token"\\s*:\\s*"(eyJ[^"]+)"/);
        if (m) return decodeURIComponent(m[1]);
      }
      try { return localStorage.getItem("auth_token")
        || sessionStorage.getItem("auth_token")
        || window.__tv_initData?.auth_token || window.initData?.auth_token || "";
      } catch (_) { return ""; }
    }"""
    try:
        token = str(page.evaluate(script) or "")
    except Exception:
        token = ""
    values = (str(item.get("value") or "") for item in cookies
              if item.get("name") == "auth_token")
    return token or next(values, "")

def _http_cookie_refresh(cookie: str) -> tuple[str, str]:
    if not cookie:
        return "", ""
    headers = {"Cookie": cookie, "User-Agent": USER_AGENT, "Accept-Language": "en-US"}
    response = requests.get("https://www.tradingview.com/", headers=headers, timeout=20)
    response.raise_for_status()
    match = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', response.text)
    return (match.group(1), cookie) if match else ("", cookie)

def _http_login(username: str, password: str) -> tuple[str, str]:
    if not username or not password:
        return "", ""
    data = {"username": username, "password": password, "remember": "on"}
    headers = {"Referer": "https://www.tradingview.com/", "User-Agent": USER_AGENT}
    response = requests.post(
        "https://www.tradingview.com/accounts/signin/", data=data, headers=headers, timeout=20
    )
    response.raise_for_status()
    token = str(response.json().get("user", {}).get("auth_token") or "")
    cookie = "; ".join(f"{key}={value}" for key, value in response.cookies.items())
    return token, cookie

def _complete_browser_login(page: Any, tv: dict[str, Any]) -> None:
    username, password = tv.get("username", ""), tv.get("password", "")
    if not username or not password:
        raise AuthError("TradingView username/password are not configured")
    for text in ("Email", "Use email", "Continue with email"):
        try:
            page.get_by_text(text, exact=False).first.click(timeout=2_000)
            break
        except Exception:
            continue
    selectors = ('input[name="username"]', 'input[type="email"]', 'input[name="email"]')
    for selector in selectors:
        try:
            page.locator(selector).first.fill(username, timeout=5_000)
            break
        except Exception:
            continue
    else:
        raise AuthError("TradingView login username field was not found")
    page.locator('input[type="password"]').first.fill(password, timeout=10_000)
    try:
        page.locator('button[type="submit"]').first.click(timeout=5_000)
    except Exception:
        page.keyboard.press("Enter")
    page.wait_for_timeout(2_000)
    if tv.get("two_factor_secret"):
        import pyotp

        selectors = (
            'input[name="code"]', 'input[inputmode="numeric"]',
            'input[autocomplete="one-time-code"]',
        )
        for selector in selectors:
            try:
                code = pyotp.TOTP(tv["two_factor_secret"]).now()
                page.locator(selector).first.fill(code, timeout=4_000)
                page.keyboard.press("Enter")
                break
            except Exception:
                continue
    page.wait_for_timeout(4_000)

def _browser_refresh(config: dict[str, Any], cookie: str, *,
                     fresh_login: bool) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    tv = config["tradingview"]
    profile = Path(tv["browser_profile_dir"])
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=True, args=["--no-sandbox"], user_agent=USER_AGENT,
            locale="en-US",
        )
        try:
            if cookie:
                context.add_cookies(_cookie_list(cookie))
            page = context.pages[0] if context.pages else context.new_page()
            target = ("https://www.tradingview.com/accounts/signin/" if fresh_login
                      else "https://www.tradingview.com/")
            page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            if fresh_login:
                _complete_browser_login(page, tv)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            cookies = context.cookies()
            return _page_token(page, cookies), _cookie_header(cookies)
        finally:
            context.close()

def browser_status() -> dict[str, Any]:
    """Verify that Playwright Chromium can start and exit headlessly."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                return {"ok": False, "detail": "Chromium executable is missing"}
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            browser.close()
        return {"ok": True, "detail": "Playwright Chromium launched successfully"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _refresh(config: dict[str, Any]) -> dict[str, Any]:
    tv = config["tradingview"]
    cache = _load_cache(config)
    cookie = str(cache.get("cookie") or tv.get("cookie") or "")
    attempts = [
        ("session_cookie", lambda: _http_cookie_refresh(cookie)),
        ("browser_profile", lambda: _browser_refresh(config, cookie, fresh_login=False)),
        ("password_login", lambda: _http_login(
            tv.get("username", ""), tv.get("password", ""))),
    ]
    if tv.get("headless_fresh_login", True):
        attempts.append(
            ("headless_fresh_login", lambda: _browser_refresh(config, "", fresh_login=True))
        )
    errors = []
    for source, action in attempts:
        try:
            token, new_cookie = action()
            if _authenticated(token, 60):
                log_event(LOGGER, logging.INFO, "AUTH_REFRESHED", "NONE", component="auth", source=source)
                return _save_cache(config, token, new_cookie or cookie, source)
            errors.append(f"{source}: no authenticated token")
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
            log_event(LOGGER, logging.WARNING, "AUTH_PATH_FAILED", "MEDIUM", component="auth",
                      source=source, error_type=type(exc).__name__, error=exc,
                      action="trying next authentication path")
    raise AuthError("TradingView authentication failed: " + "; ".join(errors))


def ensure_authenticated(config: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Resolve an account JWT, refresh proactively, and never return guest access."""
    global _NEXT_REFRESH_ATTEMPT
    tv = config["tradingview"]
    cache = _load_cache(config)
    candidates = [
        ("runtime_cache", str(cache.get("token") or ""), str(cache.get("cookie") or "")),
        ("configuration", str(tv.get("auth_token") or ""), str(tv.get("cookie") or "")),
    ]
    best = next((item for item in candidates if _authenticated(item[1], 60)), None)
    minimum = int(tv["proactive_refresh_seconds"])
    retry_active = (
        time.monotonic() < _NEXT_REFRESH_ATTEMPT
        or time.time() < float(cache.get("retry_after") or 0)
    )
    if not force and best and (
        _authenticated(best[1], minimum) or retry_active
    ):
        tv.update(auth_token=best[1], cookie=best[2])
        return {"token": best[1], "cookie": best[2], "source": best[0]}
    if not force and not best and retry_active:
        raise AuthError("TradingView authentication retry cooldown is active")
    try:
        refreshed = _refresh(config)
    except AuthError:
        delay = int(tv["refresh_retry_seconds"])
        _NEXT_REFRESH_ATTEMPT = time.monotonic() + delay
        cache["retry_after"] = time.time() + delay
        _write_cache(config, cache)
        if best and not force:
            tv.update(auth_token=best[1], cookie=best[2])
            return {"token": best[1], "cookie": best[2], "source": best[0]}
        raise
    tv.update(auth_token=refreshed["token"], cookie=refreshed["cookie"])
    _NEXT_REFRESH_ATTEMPT = 0.0
    return refreshed


def auth_status(config: dict[str, Any]) -> dict[str, Any]:
    """Return a secret-free auth readiness snapshot."""
    cache = _load_cache(config)
    token = str(cache.get("token") or config["tradingview"].get("auth_token") or "")
    remaining = token_seconds_remaining(token)
    return {
        "ok": _authenticated(token, 60),
        "state": "authenticated" if _authenticated(token, 60) else "unavailable",
        "source": str(cache.get("source") or ("configuration" if token else "none")),
        "seconds_remaining": int(remaining) if remaining >= 0 else None,
        "cookie_present": bool(cache.get("cookie") or config["tradingview"].get("cookie")),
        "username_present": bool(config["tradingview"].get("username")),
        "headless_fresh_login": bool(config["tradingview"].get("headless_fresh_login")),
    }
