"""
Quan ly xac thuc TradingView dung chung cho pipeline, ws_live va checker.

Thu tu fallback hien tai:
1. runtime cache trong `.tv_token_cache`
2. token / cookie co san trong `.env`
3. refresh token qua cookie session (HTTP GET)
4. dang nhap bang username/password -> lay token + cookie moi (Fix A)
5. headless browser refresh voi cookie cu
6. headless browser dang nhap tu dau bang credentials -> lay token + cookie moi (Fix B)
7. guest token nhu phuong an cuoi cung

Muc tieu cua module nay khong chi la "dang nhap duoc", ma la:
- tranh khoi dong batch voi token sap het han
- co the refresh giua chung khi TradingView bao auth loi
- giu trang thai auth nhat quan cho tat ca process dung chung
- canh bao ro rang khi he thong bi roi xuong guest mode
- tu dong cap nhat cookie khi cookie het han (khong can can thiep thu cong)
"""

# =============================================================================
# data_provider/_tv_auth.py  -  Xác thực TradingView (module dùng chung)
# =============================================================================
#
# FILE NÀY LÀM GÌ-
#   TradingView yêu cầu phải đăng nhập mới xem được dữ liệu đầy đủ.
#   File này quản lý toàn bộ quá trình "lấy chứng minh đã đăng nhập" (auth token)
#   và đảm bảo token luôn còn hiệu lực khi các script khác cần dùng.
#
# AUTH TOKEN LÀ GÌ-
#   Sau khi đăng nhập TradingView, server cấp 1 chuỗi dài (JWT token) như:
#     eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...
#   Chuỗi này là "thẻ thông hành" - khi gửi kèm mỗi request, TradingView
#   biết bạn là ai và cho phép tải đủ dữ liệu (không bị giới hạn như Guest).
#
# HỆ THỐNG DỰ PHÒNG 5 LỚP (thử lần lượt từ trên xuống):
#
#   Lớp 0 - Token cache (.tv_token_cache):
#     Lần trước đã đăng nhập thành công -> lưu token vào file cục bộ.
#     Lần này dùng lại, không cần request mạng. Nhanh nhất.
#
#   Lớp 1 - Token tĩnh từ .env (TV_AUTH_TOKEN):
#     Người dùng tự copy token từ trình duyệt và lưu vào file .env.
#     Không cần mạng, nhưng token có thể hết hạn sau vài ngày.
#
#   Lớp 1.5 - Refresh qua cookie (HTTP GET):
#     Gửi HTTP request đến TradingView kèm cookie (sessionid=...).
#     TradingView nhúng token mới vào HTML trả về -> extract ra.
#     Nhanh (~1-2 giây) và đáng tin cậy nếu cookie còn hiệu lực.
#
#   Lớp 2 - Đăng nhập username/password (HTTP POST):
#     Gửi POST form giả lập như đăng nhập trên trình duyệt.
#     Chỉ hoạt động với tài khoản TradingView gốc (không phải Google login).
#
#   Lớp 2.5 - Headless Chromium với cookie cũ (Playwright):
#     Chạy trình duyệt Chrome ẩn để tải trang TV với session cookie hiện có.
#     Hỗ trợ Google/social login. Lấy được token + cookie mới từ browser.
#
#   Lớp 2.6 - Headless Chromium đăng nhập từ đầu (Playwright):
#     Điều hướng đến trang /signin/, điền username/password, submit form.
#     Không cần cookie cũ - lấy token + cookie hoàn toàn mới sau khi login.
#     Dùng khi cookie đã hết hạn hoàn toàn và lớp 2.5 không thể dùng.
#
#   Lớp 3 - Guest token:
#     Dùng tài khoản khách - dữ liệu bị giới hạn (ít lịch sử hơn, chậm hơn).
#     Là phương án cuối cùng, không nên chạy lâu với guest.
#
# CÁC SCRIPT DÙNG FILE NÀY:
#   02_ws_live.py       - WebSocket live      (bootstrap, get_current_token, renew)
#   01_data_pipeline.py - Backfill hàng ngày  (get_valid_tv_connection, refresh_mid_run)
#   04_checker.py       - Kiểm tra dữ liệu   (get_valid_tv_connection, refresh_mid_run)
#
# CẤU HÌNH CẦN THIẾT TRONG FILE .env:
#   TV_AUTH_TOKEN=eyJhbGci...   ← copy từ trình duyệt (F12 > Network > cookie)
#   TV_COOKIE=sessionid=abc...  ← copy toàn bộ cookie header từ trình duyệt
#   TV_USERNAME=your@email.com  ← chỉ cần nếu dùng username/password
#   TV_PASSWORD=your_password   ← chỉ cần nếu dùng username/password
# =============================================================================

import json
import logging
import random
import re
import threading
import time
from pathlib import Path

import requests

# Bootstrap: thêm project root vào path
import os
import sys
_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import TV_AUTH_TOKEN, TV_COOKIE, TV_PASSWORD, TV_USERNAME  # noqa: E402
from _discord import tg_alert as _tg_alert  # noqa: E402

# Module-level logger (dùng khi caller không truyền logger vào)
_logger = logging.getLogger("tv_auth")

# =============================================================================
# CONSTANTS
# =============================================================================

# Chuỗi đặc biệt báo hiệu "chưa xác thực / guest"
GUEST_TOKEN = "unauthorized_user_token"

# Từ khóa trong message lỗi cho biết token đã hết hạn / không hợp lệ
TOKEN_EXPIRY_KEYWORDS = ("unauthorized", "auth_error", "not_authorized")

# HTTP retry parameters
HTTP_MAX_RETRIES    = 4
HTTP_BASE_DELAY_SEC = 2.0
HTTP_MAX_DELAY_SEC  = 120.0
STARTUP_MIN_TOKEN_TTL_SEC = 10 * 60

# =============================================================================
# GLOBAL AUTH STATE
# =============================================================================

# Token xác thực TradingView (chia sẻ giữa tất cả kết nối)
_auth_token: str = GUEST_TOKEN
_auth_lock  = threading.Lock()

# Cookie TradingView (runtime, có thể được cập nhật khi refresh)
_tv_cookie: str = TV_COOKIE

# Đường dẫn token cache (lưu runtime credentials tách khỏi .env)
_TOKEN_CACHE = Path(__file__).parent / ".tv_token_cache"


# =============================================================================
# PUBLIC API - dùng bởi ws_live.py
# =============================================================================

def get_current_token() -> str:
    """Trả về token hiện tại (thread-safe read)."""
    with _auth_lock:
        return _auth_token


def get_auth_mode() -> str:
    """
    Trả về chế độ xác thực hiện tại: "guest" hoặc "authenticated".
    Dùng để phát hiện guest mode trước khi chạy checker/pipeline.
    """
    with _auth_lock:
        return "guest" if _auth_token == GUEST_TOKEN else "authenticated"


def set_current_token(token: str) -> None:
    """Ghi token mới vào global state (thread-safe)."""
    global _auth_token
    with _auth_lock:
        _auth_token = token


def check_and_refresh(lg: logging.Logger | None = None) -> None:
    """
    Chủ động làm mới token nếu sắp hết hạn (< 10 phút).
    Gọi trước mỗi batch WS để không bao giờ bắt đầu batch với token hết hạn.
    """
    _check_and_maybe_refresh_token(lg)


def renew(lg: logging.Logger | None = None) -> None:
    """Gia hạn token khi TradingView báo lỗi xác thực giữa chừng."""
    _renew_auth_token(lg)


def bootstrap(lg: logging.Logger | None = None) -> tuple[str, str]:
    """
    Đảm bảo credentials hợp lệ trước khi khởi động.
    Trả về (token, tên_phương_thức).
    """
    return _bootstrap_credentials(lg)


# =============================================================================
# PUBLIC API - dùng bởi pipeline / gap_fill
# =============================================================================

def get_valid_tv_connection(lg: logging.Logger | None = None) -> tuple:
    """
    Thay thế get_tv_connection() từ config.py.
    Thử toàn bộ 4 lớp xác thực, trả về (tv_object, auth_mode).

    Khác với get_tv_connection(): không dừng ở .env token mà còn
    thử cache -> cookie refresh -> headless -> username/password -> guest.
    """
    log = lg or _logger
    token, source = _resolve_auth_token(log)
    _update_global_token(token)

    from tvDatafeed import TvDatafeed  # type: ignore
    tv = TvDatafeed()
    tv.token = token
    log.info("[AUTH] TV connection ready (source: %s).", source)
    return tv, source


def refresh_mid_run(tv, lg: logging.Logger | None = None) -> bool:
    """
    Gọi khi phát hiện nhiều failures liên tiếp trong pipeline/gap_fill.
    Thử lấy token mới, cập nhật tv.token.
    Trả về True nếu lấy được token hợp lệ (không phải guest).
    """
    log = lg or _logger
    log.warning("[AUTH] Mid-run token refresh triggered...")

    # Reset để buộc resolve lại từ đầu (bỏ qua cache cũ)
    global _auth_token
    with _auth_lock:
        _auth_token = GUEST_TOKEN

    new_token, source = _bootstrap_credentials(log)
    if new_token == GUEST_TOKEN:
        new_token, source = _resolve_auth_token(log)

    _update_global_token(new_token)

    if new_token != GUEST_TOKEN:
        tv.token = new_token
        log.info("[AUTH] Mid-run refresh OK (source: %s) - updated tv.token.", source)
        _tg_alert("INFO", f"[OK] TradingView token was refreshed during the run.\nSource: {source}")
        return True
    else:
        log.error("[AUTH] Mid-run refresh FAILED - continuing with guest token.")
        _tg_alert("ERROR", "[FAIL] Mid-run token refresh failed - using guest access.\nCheck credentials in .env")
        return False


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _update_global_token(token: str) -> None:
    """Cập nhật _auth_token global nếu token hợp lệ."""
    global _auth_token
    with _auth_lock:
        if token and token != GUEST_TOKEN:
            _auth_token = token


def _http_request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = HTTP_MAX_RETRIES,
    base_delay: float = HTTP_BASE_DELAY_SEC,
    max_delay: float = HTTP_MAX_DELAY_SEC,
    **kwargs,
) -> requests.Response:
    """
    Gửi HTTP request với cơ chế retry tự động cho các lỗi tạm thời.
    HTTP 429 / 5xx -> retry với exponential back-off + jitter.
    HTTP 4xx khác (400/401/403/404) -> không retry (lỗi từ client).
    """
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)

            if resp.status_code < 400:
                return resp

            if resp.status_code == 429:
                if attempt < max_retries:
                    retry_after_hdr = resp.headers.get("Retry-After", "")
                    try:
                        wait = float(retry_after_hdr)
                        wait = min(wait, max_delay)
                    except (ValueError, TypeError):
                        wait = min(base_delay * (2 ** attempt), max_delay)
                    wait += random.uniform(0.5, 2.0)
                    _logger.warning("[HTTP] 429 Too Many Requests - retry %d/%d in %.1fs.", attempt + 1, max_retries, wait)
                    time.sleep(wait)
                    last_exc = requests.HTTPError(f"429 Too Many Requests (attempt {attempt + 1})", response=resp)
                    continue
                raise requests.HTTPError(f"429 Too Many Requests - retry limit reached after {max_retries} attempts ({url})", response=resp)

            if resp.status_code >= 500:
                if attempt < max_retries:
                    wait = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0.5, 2.0)
                    _logger.warning("[HTTP] %d Server Error - retry %d/%d in %.1fs.", resp.status_code, attempt + 1, max_retries, wait)
                    time.sleep(wait)
                    last_exc = requests.HTTPError(f"{resp.status_code} Server Error (attempt {attempt + 1})", response=resp)
                    continue
                raise requests.HTTPError(f"{resp.status_code} Server Error - retry limit reached after {max_retries} attempts ({url})", response=resp)

            raise requests.HTTPError(f"HTTP {resp.status_code} Client Error - not retrying ({url})", response=resp)

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0.5, 2.0)
                _logger.warning("[HTTP] Network error %s - retry %d/%d in %.1fs.", type(exc).__name__, attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            raise

    raise last_exc


def _fetch_auth_token_from_credentials(username: str, password: str) -> tuple[str, str]:
    """
    Đăng nhập TradingView bằng username/password để lấy auth token và cookie mới.
    Mô phỏng hành vi trình duyệt Chrome (gửi POST form).

    log.info("[AUTH] Cookie expired - trying headless fresh login...")
    Cả hai đều được extract và trả về để caller lưu vào cache.

    Trả về (token, cookie_str) hoặc (GUEST_TOKEN, "") nếu thất bại.
    """
    try:
        r = _http_request_with_retry(
            "POST",
            "https://www.tradingview.com/accounts/signin/",
            data={"username": username, "password": password, "remember": "on"},
            headers={
                "Referer":    "https://www.tradingview.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
            },
            timeout=15,
        )
        token = r.json()["user"]["auth_token"]
        new_cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items()) if r.cookies else ""
        _logger.info("[AUTH] New token and cookie received through username/password.")
        return token, new_cookie
    except Exception as exc:
        _logger.warning("[AUTH] Credential login failed: %s", exc)
        return GUEST_TOKEN, ""


def _resolve_auth_token(lg: logging.Logger | None = None) -> tuple[str, str]:
    """
    Hàm nội bộ: thử lần lượt từng lớp xác thực, trả về (token, tên_phương_thức).

    Đây là "dispatcher" - không tự xác thực mà gọi các hàm chuyên biệt.
    Thứ tự thử được thiết kế để ưu tiên cách nhanh nhất trước, cách chậm nhất sau.

    Lớp 0  : File cache .tv_token_cache (không cần mạng - nhanh nhất)
    Lớp 1  : Token tĩnh từ .env (không cần mạng)
    Lớp 1.5: Refresh qua HTTP GET + cookie (~1-2s)
    Lớp 2  : Đăng nhập username/password qua HTTP POST (~2-5s)
    Lớp 2.5: Headless Chromium (~5-10s, cần Playwright)
    Lớp 3  : Guest token (luôn thành công nhưng dữ liệu hạn chế)
    """
    global _tv_cookie
    log = lg or _logger

    # LỚP 0: Runtime token cache
    cache = _load_token_cache()
    cached_token = cache.get("TV_AUTH_TOKEN", "")
    if cached_token and cached_token != GUEST_TOKEN:
        cached_cookie = cache.get("TV_COOKIE", "")
        if cached_cookie:
            _tv_cookie = cached_cookie
        if _is_token_reusable_for_startup(cached_token, "cached_token", log):
            log.info("[AUTH] Using cached token from .tv_token_cache.")
            return cached_token, "cached_token"

    # LỚP 1: Static token từ .env
    if TV_AUTH_TOKEN and TV_AUTH_TOKEN != GUEST_TOKEN:
        if _is_token_reusable_for_startup(TV_AUTH_TOKEN, "static_token", log):
            log.info("[AUTH] Using static TV_AUTH_TOKEN from .env.")
            return TV_AUTH_TOKEN, "static_token"

    # LỚP 1.5: Refresh qua session cookie
    current_cookie = _tv_cookie or TV_COOKIE
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, current_cookie)
            return token, "session_refresh"

    # LỚP 2: username/password - lấy token + cookie mới từ response HTTP POST
    if TV_USERNAME and TV_PASSWORD:
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "username/password"

    # LỚP 2.5: Headless Chromium với cookie cũ
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "headless_chromium"

    # LỚP 2.6: Headless Chromium đăng nhập từ đầu (không cần cookie cũ)
    # Dùng khi cookie đã hết hạn hoàn toàn - lấy token + cookie hoàn toàn mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Cookie expired - trying headless fresh login...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie)
            return token, "headless_fresh_login"

    # LỚP 3: Guest
    log.warning("[AUTH] Falling back to guest token - Premium data may be unavailable.")
    _tg_alert("WARNING", "Could not authenticate TradingView - using guest token.")
    return GUEST_TOKEN, "guest"


def _renew_auth_token(lg: logging.Logger | None = None) -> None:
    """
    Gia hạn token khi TradingView báo lỗi xác thực giữa chừng.
    Dùng Lock để đảm bảo chỉ 1 thread thực hiện gia hạn.
    """
    global _auth_token
    log = lg or _logger
    with _auth_lock:
        if _auth_token != GUEST_TOKEN:
            return  # Thread khác đã gia hạn xong

        log.warning("[AUTH] All bootstrap methods failed.")
        _tg_alert("WARNING", "TradingView token expired - renewing automatically...")

        new_token, source = _bootstrap_credentials(log)
        if new_token == GUEST_TOKEN:
            new_token, source = _resolve_auth_token(log)

        _auth_token = new_token

        if new_token != GUEST_TOKEN:
            log.info("[AUTH] Token renewed successfully (source: %s).", source)
            _tg_alert("INFO", f"[OK] TradingView token was refreshed during the run.\nSource: {source}")
        else:
            log.error("[AUTH] Token renewal failed - all groups will use guest access.")
            _tg_alert("ERROR", "[FAIL] Token renewal failed.\nThe system is using guest access.")


def _jwt_expires_in(token: str) -> float:
    """
    Giải mã JWT để lấy trường 'exp', trả về số giây còn lại.
    Trả về -1.0 nếu không thể giải mã (không phải JWT).
    """
    import base64 as _b64
    try:
        part = token.split(".")[1]
        part += "=" * (4 - len(part) % 4)
        payload = json.loads(_b64.b64decode(part).decode())
        return float(payload["exp"]) - time.time()
    except Exception:
        return -1.0


def _is_token_reusable_for_startup(
    token: str,
    source: str,
    log: logging.Logger,
    *,
    min_remaining_sec: int = STARTUP_MIN_TOKEN_TTL_SEC,
) -> bool:
    """
    Reject clearly expired or nearly-expired tokens at startup.

    This prevents pipeline/checker from declaring the TradingView connection
    ready with a cached/static token that will fail immediately on the first
    historical pull.
    """
    remaining = _jwt_expires_in(token)
    if remaining == -1.0:
        log.info("[AUTH] Could not decode %s expiry; using token as-is.", source)
        return True
    if remaining <= 0:
        log.warning("[AUTH] Ignoring expired %s (expired %.0fs ago).", source, abs(remaining))
        return False
    if remaining < min_remaining_sec:
        log.info(
            "[AUTH] Skipping %s because token expires in %.0fs; trying refresh paths.",
            source,
            remaining,
        )
        return False
    return True


def _check_and_maybe_refresh_token(lg: logging.Logger | None = None) -> None:
    """
    Chủ động làm mới token nếu sắp hết hạn (< 10 phút).
    Gọi trước mỗi batch WS để không bao giờ bắt đầu batch với token hết hạn.
    """
    global _auth_token
    log = lg or _logger
    need_refresh = False
    with _auth_lock:
        remaining = _jwt_expires_in(_auth_token)
        if 0 < remaining < 600:
            log.info("[AUTH] Token expiring in %.0fs - proactive refresh.", remaining)
            _auth_token = GUEST_TOKEN
            need_refresh = True
    if need_refresh:
        _renew_auth_token(log)


def _refresh_token_via_cookie(cookie_str: str) -> str:
    """
    Lớp 1.5 - Làm mới auth_token bằng HTTP GET homepage TradingView.
    TradingView nhúng auth_token vào HTML khi user đã đăng nhập với sessionid cookie.
    Trả về token mới, hoặc GUEST_TOKEN nếu thất bại.
    """
    if not cookie_str:
        return GUEST_TOKEN
    try:
        resp = _http_request_with_retry(
            "GET",
            "https://www.tradingview.com/",
            headers={
                "Cookie":          cookie_str,
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20,
            allow_redirects=True,
        )
        m = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', resp.text)
        if m:
            token = m.group(1)
            _logger.info("[AUTH] Token refreshed via session cookie (HTTP GET).")
            return token
        if "sign-in" in resp.url or resp.status_code in (401, 403):
            _logger.warning("[AUTH] Session cookie expired (redirect to sign-in).")
        else:
            _logger.warning("[AUTH] Cookie valid but auth_token not found in page HTML.")
    except Exception as exc:
        _logger.warning("[AUTH] Cookie refresh via HTTP failed: %s", exc)
    return GUEST_TOKEN


def _headless_refresh(cookie_str: str) -> tuple[str, str]:
    """
    Lớp 2.5 - Dùng headless Chromium (Playwright) để load TradingView và extract credentials.
    Đáng tin cậy hơn HTTP GET vì Playwright thực thi JavaScript đầy đủ.
    Hỗ trợ Google/social login khi HTTP GET không đủ.

    Trả về (token, cookie_str_mới) hoặc (GUEST_TOKEN, "") nếu thất bại.

    Cài đặt (chỉ cần làm 1 lần):
        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        _logger.warning("[AUTH] Playwright is not installed - skipping headless refresh.")
        _logger.warning("[AUTH] Install with: pip install playwright && playwright install chromium")
        return GUEST_TOKEN, ""

    def _parse_cookie_list(raw: str) -> list[dict]:
        result = []
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                result.append({
                    "name":   name.strip(),
                    "value":  value.strip(),
                    "domain": ".tradingview.com",
                    "path":   "/",
                    "secure": True,
                })
        return result

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            if cookie_str:
                ctx.add_cookies(_parse_cookie_list(cookie_str))

            page = ctx.new_page()
            page.goto("https://www.tradingview.com/", wait_until="networkidle", timeout=45_000)

            token: str = page.evaluate(
                """() => {
                    try {
                        const m = document.documentElement.innerHTML.match(/"auth_token":"(eyJ[^"]+)"/);
                        if (m) return m[1];
                    } catch(e) {}
                    try {
                        if (window.__tv_initData && window.__tv_initData.auth_token)
                            return window.__tv_initData.auth_token;
                    } catch(e) {}
                    try {
                        if (window.initData && window.initData.auth_token)
                            return window.initData.auth_token;
                    } catch(e) {}
                    return null;
                }"""
            ) or ""

            all_cookies = ctx.cookies()
            cookie_out = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)
            browser.close()

            if token:
                _logger.info("[AUTH] Token refreshed via headless Chromium.")
                return token, cookie_out
            _logger.warning("[AUTH] Headless load finished but auth_token was not found - session may be expired.")
            return GUEST_TOKEN, cookie_out

    except Exception as exc:
        _logger.warning("[AUTH] Headless refresh failed: %s", exc)
        return GUEST_TOKEN, ""


def _headless_login_fresh(username: str, password: str) -> tuple[str, str]:
    """
    Lớp 2.6 - Đăng nhập TradingView từ đầu bằng Playwright (không cần cookie cũ).

    Khác với _headless_refresh() (chỉ load trang với cookie sẵn có):
    - Hàm này điều hướng đến trang /signin/, điền form username/password, submit.
    - Không phụ thuộc vào cookie cũ - phù hợp khi cookie đã hết hạn hoàn toàn.
    - Kết quả: token + cookie hoàn toàn mới, hệ thống hoạt động tiếp mà không cần
      người dùng vào trình duyệt copy cookie thủ công.

    Giới hạn: chỉ hoạt động với tài khoản TradingView gốc (email/password).
    Không hỗ trợ Google login / SSO - dùng _headless_refresh() cho các loại đó.

    Trả về (token, cookie_str) hoặc (GUEST_TOKEN, "") nếu thất bại.

    Yêu cầu: pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        _logger.warning("[AUTH] Playwright is not installed - skipping headless fresh login.")
        _logger.warning("[AUTH] Install with: pip install playwright && playwright install chromium")
        return GUEST_TOKEN, ""

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = ctx.new_page()

            # Mở trang đăng nhập TradingView
            page.goto("https://www.tradingview.com/signin/", wait_until="domcontentloaded", timeout=45_000)

            # Điền email - thử các selector phổ biến theo thứ tự
            filled = False
            for sel in ['input[name="username"]', 'input[type="email"]', 'input[name="email"]']:
                try:
                    page.wait_for_selector(sel, timeout=5_000)
                    page.fill(sel, username)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                _logger.warning("[AUTH] Headless fresh login: username/email input was not found.")
                browser.close()
                return GUEST_TOKEN, ""

            # Điền password
            page.fill('input[type="password"]', password)

            # Submit form - thử click button hoặc nhấn Enter
            try:
                page.click('button[type="submit"]')
            except Exception:
                page.keyboard.press("Enter")

            # Chờ chuyển về trang chủ sau khi đăng nhập thành công
            try:
                page.wait_for_url("**/tradingview.com/**", timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass  # Tiếp tục extract dù timeout - token có thể đã có

            # Extract auth token từ HTML hoặc JS globals
            token: str = page.evaluate(
                """() => {
                    try {
                        const m = document.documentElement.innerHTML.match(/"auth_token":"(eyJ[^"]+)"/);
                        if (m) return m[1];
                    } catch(e) {}
                    try {
                        if (window.__tv_initData && window.__tv_initData.auth_token)
                            return window.__tv_initData.auth_token;
                    } catch(e) {}
                    try {
                        if (window.initData && window.initData.auth_token)
                            return window.initData.auth_token;
                    } catch(e) {}
                    return null;
                }"""
            ) or ""

            all_cookies = ctx.cookies()
            cookie_out = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)
            browser.close()

            if token:
                _logger.info("[AUTH] Headless fresh login succeeded - new token and cookie received.")
                return token, cookie_out

            _logger.warning("[AUTH] Headless fresh login finished but auth_token was not found.")
            return GUEST_TOKEN, cookie_out

    except Exception as exc:
        _logger.warning("[AUTH] Headless fresh login failed: %s", exc)
        return GUEST_TOKEN, ""


def _load_token_cache() -> dict:
    """Đọc runtime token/cookie từ file .tv_token_cache (JSON)."""
    try:
        if _TOKEN_CACHE.exists():
            return json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_token_cache(token: str, cookie: str) -> None:
    """Lưu runtime token/cookie vào .tv_token_cache (JSON)."""
    data: dict = {}
    if token and token != GUEST_TOKEN:
        data["TV_AUTH_TOKEN"] = token
    if cookie:
        data["TV_COOKIE"] = cookie
    try:
        _TOKEN_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        _logger.warning("[AUTH] Could not write token cache: %s", exc)


def _save_credentials_to_env(token: str, cookie: str) -> None:
    """
    Cập nhật runtime credentials:
    - In-memory: _auth_token và _tv_cookie (tức thời)
    - File cache: .tv_token_cache (durable qua restart)
    """
    global _auth_token, _tv_cookie
    if token and token != GUEST_TOKEN:
        _auth_token = token
    if cookie:
        _tv_cookie = cookie
    _save_token_cache(token, cookie)
    _logger.info("[AUTH] Credentials saved to .tv_token_cache.")


def _bootstrap_credentials(lg: logging.Logger | None = None) -> tuple[str, str]:
    """
    Đảm bảo credentials hợp lệ trước khi khởi động.
    Gọi lúc startup khi token trống hoặc hết hạn.

    Thứ tự thử:
        1. Refresh token qua HTTP GET (dùng sessionid cookie hiện có)
        2. Refresh token qua headless Chromium (dùng sessionid cookie)
        3. Đăng nhập bằng username/password (HTTP POST - chỉ cho native TV account)

    Trả về (token, tên_phương_thức).
    """
    global _tv_cookie
    log = lg or _logger
    log.info("[AUTH] Bootstrapping credentials...")

    current_cookie = _tv_cookie or TV_COOKIE

    # Bước 1: Refresh via HTTP GET
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, current_cookie)
            return token, "session_refresh"
        log.info("[AUTH] HTTP cookie refresh failed - trying headless Chromium...")

    # Bước 2: Headless Chromium
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "headless_chromium"

    # Bước 3: username/password (HTTP POST) - lấy token + cookie mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Trying username/password login (HTTP POST)...")
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie or current_cookie)
            return token, "http_post_login"

    # Bước 4: Headless fresh login (không cần cookie cũ) - lấy token + cookie hoàn toàn mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Trying headless fresh login (Playwright full login)...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            _save_credentials_to_env(token, new_cookie)
            return token, "headless_fresh_login"

    log.warning("[AUTH] All bootstrap methods failed.")
    return GUEST_TOKEN, "guest"
