"""Fail-closed TradingView account authentication and headless Chromium renewal."""

from __future__ import annotations

import base64, json, logging, re, time
from pathlib import Path
from typing import Any

import requests

from ..configuration import GUEST_TOKEN
from ..log import log_event
from .spool import InterprocessLockTimeout, atomic_write_text, interprocess_lock
LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124"
_NEXT_REFRESH_ATTEMPT = 0.0

# File này lo phần đăng nhập TradingView cho live và backfill.
# Mục tiêu: luôn dùng tài khoản thật. Nếu không đăng nhập được thì dừng, không chạy guest.
# Thứ tự xử lý đi từ nhẹ tới nặng: dùng token/cookie cũ, refresh cookie, rồi mới đăng nhập lại.


class AuthError(RuntimeError):
    pass


# Nhóm JWT: đọc token để biết token có thuộc user thật và còn hạn không.
# Nhóm này không gọi TradingView, nên kiểm tra rất nhanh.
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


# Nhóm cache: lưu token/cookie đã dùng được để lần sau không phải đăng nhập lại.
# Nếu token trong cache không hợp lệ, hệ thống vẫn dừng thay vì chạy guest.
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
    atomic_write_text(path, json.dumps(payload))
def _save_cache(config: dict[str, Any], token: str, cookie: str, source: str) -> dict[str, Any]:
    if not _authenticated(token, 60):
        raise AuthError(f"TradingView {source} did not return an authenticated JWT")
    payload = {
        "token": token, "cookie": cookie, "source": source, "refreshed_at": time.time()
    }
    _write_cache(config, payload)
    return payload


# Nhóm cookie: đổi cookie qua lại giữa dạng text và dạng browser cần dùng.
# Chỉ nhận cookie của TradingView.
def _cookie_list(raw: str) -> list[dict[str, Any]]:
    result = []
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name and value:
            result.append({"name": name, "value": value,
                           "domain": ".tradingview.com", "path": "/", "secure": True})
    return result
def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    valid = (item for item in cookies if item.get("name") and item.get("value")
             and re.fullmatch(r"(?:.+\.)?tradingview\.com",
                              str(item.get("domain", "tradingview.com")).lower().lstrip(".")))
    return "; ".join(f"{item['name']}={item['value']}" for item in valid)


# Sau khi mở trang TradingView, tìm token ở các nơi TradingView có thể lưu.
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
    values = (str(item.get("value") or "") for item in cookies if item.get("name") == "auth_token")
    return token or next(values, "")


# Cách nhẹ nhất: dùng cookie đang có để xin lại token mới.
# Nếu cách này chạy được thì không cần mở browser hay nhập password.
def _http_cookie_refresh(cookie: str) -> tuple[str, str]:
    if not cookie:
        return "", ""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US"}
    with requests.Session() as session:
        for item in _cookie_list(cookie): session.cookies.set(**item)
        response = session.get("https://www.tradingview.com/", headers=headers, timeout=20)
        response.raise_for_status()
        renewed_cookie = _cookie_header([vars(item) for item in session.cookies])
    match = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', response.text)
    return (match.group(1), renewed_cookie or cookie) if match else ("", renewed_cookie or cookie)


# Đăng nhập thẳng bằng username/password qua HTTP.
# Nếu TradingView chặn hoặc đổi cách đăng nhập, hệ thống sẽ thử cách bằng browser.
def _http_login(username: str, password: str) -> tuple[str, str]:
    if not username or not password:
        return "", ""
    data = {"username": username, "password": password, "remember": "on"}
    headers = {"Referer": "https://www.tradingview.com/", "User-Agent": USER_AGENT}
    response = requests.post("https://www.tradingview.com/accounts/signin/",
                             data=data, headers=headers, timeout=20)
    response.raise_for_status()
    token = str(response.json().get("user", {}).get("auth_token") or "")
    cookie = "; ".join(f"{key}={value}" for key, value in response.cookies.items())
    return token, cookie


# Điền form đăng nhập trong browser ẩn.
# Cách này giống thao tác người dùng hơn, và có thể nhập mã 2FA nếu đã cấu hình.
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
    selectors = ('input[name="id_username"]', 'input[name="username"]',
                 'input[type="email"]', 'input[name="email"]')
    for selector in selectors:
        try:
            page.locator(selector).first.fill(username, timeout=5_000)
            break
        except Exception:
            continue
    else:
        raise AuthError("TradingView login username field was not found")
    page.locator('input[name="id_password"], input[type="password"]').first.fill(password, timeout=10_000)
    try:
        page.locator('button[type="submit"]').first.click(timeout=5_000)
    except Exception:
        page.keyboard.press("Enter")
    page.wait_for_timeout(2_000)
    if tv.get("two_factor_secret"):
        import pyotp

        selectors = ('input[name="code"]', 'input[inputmode="numeric"]', 'input[autocomplete="one-time-code"]')
        for selector in selectors:
            try:
                code = pyotp.TOTP(tv["two_factor_secret"]).now()
                page.locator(selector).first.fill(code, timeout=4_000)
                page.keyboard.press("Enter")
                break
            except Exception:
                continue
    page.wait_for_timeout(4_000)


# Mở Chromium ẩn để lấy lại session TradingView.
# Có thể dùng profile cũ, hoặc đăng nhập mới từ đầu.
def _browser_refresh(config: dict[str, Any], cookie: str, *, fresh_login: bool) -> tuple[str, str]:
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


# Kiểm tra máy có mở được Chromium ẩn không.
# Không đăng nhập và không sửa cache.
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


# Ghi log cho từng cách đăng nhập, nhưng không ghi token/cookie/password.
def _log_path(level: int, event: str, risk: str, source: str, started: float,
              **fields: Any) -> None:
    log_event(LOGGER, level, event, risk, component="auth", source=source,
              duration_seconds=round(time.monotonic() - started, 3), **fields)
def _refresh(config: dict[str, Any]) -> dict[str, Any]:
    # Thử từng cách đăng nhập, từ ít rủi ro nhất đến nặng nhất.
    # Cách nào lấy được token thật thì lưu cache và dùng ngay.
    tv = config["tradingview"]
    cache = _load_cache(config)
    original_cookie = cookie = str(cache.get("cookie") or tv.get("cookie") or "")
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
        started = time.monotonic()
        try:
            token, new_cookie = action()
            cookie = new_cookie or cookie
            if _authenticated(token, 60):
                saved = _save_cache(config, token, cookie, source)
                _log_path(logging.INFO, "AUTH_REFRESHED", "NONE", source, started,
                          session_material_changed=cookie != original_cookie)
                return saved
            errors.append(f"{source}: no authenticated token")
            _log_path(logging.INFO, "AUTH_PATH_NO_TOKEN", "LOW", source, started,
                      action="trying next authentication path")
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
            _log_path(logging.WARNING, "AUTH_PATH_FAILED", "MEDIUM", source, started,
                      error_type=type(exc).__name__, error=exc,
                      action="trying next authentication path")
    raise AuthError("TradingView authentication failed: " + "; ".join(errors))


# Chọn token/cookie tốt nhất đang có.
# Ưu tiên cache runtime vì đó là lần đăng nhập mới nhất.
def _best_material(config: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, str, str] | None]:
    tv = config["tradingview"]
    cache = _load_cache(config)
    candidates = [
        ("runtime_cache", str(cache.get("token") or ""), str(cache.get("cookie") or "")),
        ("configuration", str(tv.get("auth_token") or ""), str(tv.get("cookie") or "")),
    ]
    return cache, next((item for item in candidates if _authenticated(item[1], 60)), None)
def _activate(tv: dict[str, Any], material: tuple[str, str, str]) -> dict[str, Any]:
    tv.update(auth_token=material[1], cookie=material[2])
    return {"token": material[1], "cookie": material[2], "source": material[0]}


# Đảm bảo đăng nhập khi đã giữ khóa.
# Khóa này ngăn live và backfill cùng refresh auth một lúc.
def _ensure_authenticated_locked(config: dict[str, Any], *, force: bool) -> dict[str, Any]:
    global _NEXT_REFRESH_ATTEMPT
    tv = config["tradingview"]
    cache, best = _best_material(config)
    minimum = int(tv["proactive_refresh_seconds"])
    retry_active = (
        time.monotonic() < _NEXT_REFRESH_ATTEMPT
        or time.time() < float(cache.get("retry_after") or 0)
    )
    if not force and best and (
        _authenticated(best[1], minimum) or retry_active
    ):
        return _activate(tv, best)
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
            return _activate(tv, best)
        raise
    tv.update(auth_token=refreshed["token"], cookie=refreshed["cookie"])
    _NEXT_REFRESH_ATTEMPT = 0.0
    return refreshed


# Hàm chính mà live/backfill gọi trước khi kết nối TradingView.
# Token còn hạn thì dùng ngay. Hết hạn thì refresh có khóa bảo vệ.
def ensure_authenticated(config: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Resolve account auth while serializing refresh/cache/profile mutation."""
    tv = config["tradingview"]
    _cache, best = _best_material(config)
    if not force and best and _authenticated(best[1], int(tv["proactive_refresh_seconds"])):
        return _activate(tv, best)
    try:
        with interprocess_lock(config, "auth_refresh", timeout_seconds=0):
            return _ensure_authenticated_locked(config, force=force)
    except InterprocessLockTimeout:
        if best and not force:
            return _activate(tv, best)
    try:
        with interprocess_lock(config, "auth_refresh", timeout_seconds=240):
            return _ensure_authenticated_locked(config, force=force)
    except InterprocessLockTimeout as exc:
        raise AuthError("TradingView authentication refresh lock timed out") from exc


# Trả trạng thái đăng nhập cho status/doctor, không lộ secret.
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
