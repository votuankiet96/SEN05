"""
Quan ly xac thuc TradingView dung chung cho pipeline, ws_live va checker.

Thu tu fallback hien tai:
1. runtime cache trong `data_provider/runtime/cache/tv_token_cache.json`
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
# data_provider/tv/auth.py  -  Xác thực TradingView (module dùng chung)
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
#   Lớp 0 - Token cache (runtime/cache/tv_token_cache.json):
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
#   ws_live.py       - WebSocket live      (bootstrap, get_current_token, renew)
#   pipeline.py - Backfill hàng ngày  (get_valid_tv_connection, refresh_mid_run)
#   checker.py       - Kiểm tra dữ liệu   (get_valid_tv_connection, refresh_mid_run)
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
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

# Bootstrap: thêm project root vào path
import os
import sys
_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import (  # noqa: E402
    TV_AUTH_TOKEN, TV_COOKIE, TV_PASSWORD, TV_USERNAME,
    TV_2FA_SECRET, TV_CAPTCHA_API_KEY, TV_CAPTCHA_SERVICE,
)
from data_provider.common.notifications import tg_alert as _tg_alert  # noqa: E402
from data_provider.paths import TV_TOKEN_CACHE  # noqa: E402

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
TOKEN_PROACTIVE_REFRESH_SEC = int(os.environ.get("TV_TOKEN_PROACTIVE_REFRESH_SEC", "3600"))
AUTH_REFRESH_COOLDOWN_SEC = int(os.environ.get("TV_AUTH_REFRESH_COOLDOWN_SEC", "900"))
AUTH_TRANSIENT_COOLDOWN_SEC = int(os.environ.get("TV_AUTH_TRANSIENT_COOLDOWN_SEC", "300"))

# Cookie lifecycle intervals
COOKIE_CHECK_INTERVAL_SEC        = 2 * 3600        # probe every 2h
COOKIE_RENEWAL_INTERVAL_SEC      = 3 * 24 * 3600   # force-renew every 3 days
COOKIE_PROBE_RETRY_INTERVAL_SEC  = 30 * 60         # retry after 30min on probe fail

# =============================================================================
# GLOBAL AUTH STATE
# =============================================================================

# Token xác thực TradingView (chia sẻ giữa tất cả kết nối)
_auth_token: str = GUEST_TOKEN
_auth_lock  = threading.Lock()
_http_session: requests.Session | None = None
_http_session_lock = threading.Lock()

# Cookie TradingView (runtime, có thể được cập nhật khi refresh)
_tv_cookie: str = TV_COOKIE

# Đường dẫn token cache (lưu runtime credentials tách khỏi .env)
_TOKEN_CACHE = TV_TOKEN_CACHE

# Cookie lifecycle state (updated by _ensure_cookie_fresh)
_last_cookie_check_ts: float    = 0.0
_last_cookie_renewal_ts: float  = 0.0
_cookie_probe_fail_streak: int  = 0
_cookie_renewal_fail_streak: int = 0
_cookie_lock = threading.Lock()
_auth_cooldown_lock = threading.Lock()
_auth_refresh_cooldown_until: float = 0.0
_auth_refresh_cooldown_reason: str = ""


@dataclass(frozen=True)
class _CookieProbeResult:
    status: str
    token: str = GUEST_TOKEN
    status_code: int | None = None
    retry_after_sec: float | None = None
    reason: str = ""


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


def ensure_cookie_fresh(lg: logging.Logger | None = None) -> None:
    """Chủ động probe và gia hạn cookie TradingView nếu cần.

    Gọi trước mỗi batch để phát hiện cookie hết hạn trước khi token cũng hết.
    Non-blocking: bỏ qua nếu renewal đang chạy ở thread khác.
    """
    _ensure_cookie_fresh_ttl_aware(lg)


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

    from config import HISTORICAL_PROVIDER
    if HISTORICAL_PROVIDER == "websocket":
        from types import SimpleNamespace
        tv = SimpleNamespace(token=token)
    else:
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

class _TradingViewHTTPAdapter(HTTPAdapter):
    """Requests adapter — disables SSL chain verification for TradingView.

    Some Windows environments (missing root CA, corporate proxy, etc.) fail
    'unable to get local issuer certificate'.  TradingView is a known host so
    we skip chain verification while keeping the connection encrypted.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

    def send(self, request, **kwargs):
        kwargs["verify"] = False
        return super().send(request, **kwargs)


def _get_http_session() -> requests.Session:
    """Return the shared HTTP session used for TradingView auth refresh."""
    global _http_session
    if _http_session is None:
        with _http_session_lock:
            if _http_session is None:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                sess = requests.Session()
                sess.mount("https://", _TradingViewHTTPAdapter())
                _http_session = sess
    return _http_session

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
            resp = _get_http_session().request(method, url, **kwargs)

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


def _retry_after_seconds(headers, default_sec: float) -> float:
    raw = ""
    try:
        raw = headers.get("Retry-After", "") if headers else ""
    except Exception:
        raw = ""
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return max(1.0, float(default_sec))


def _set_auth_cooldown(seconds: float, reason: str, lg: logging.Logger | None = None) -> None:
    global _auth_refresh_cooldown_until, _auth_refresh_cooldown_reason
    if seconds <= 0:
        return
    log = lg or _logger
    until = time.time() + float(seconds)
    with _auth_cooldown_lock:
        if until <= _auth_refresh_cooldown_until + 5:
            return
        _auth_refresh_cooldown_until = until
        _auth_refresh_cooldown_reason = reason
    log.warning("[AUTH] Auth refresh cooldown active for %.0fs (%s).", seconds, reason)


def _auth_cooldown_remaining() -> tuple[float, str]:
    with _auth_cooldown_lock:
        return max(0.0, _auth_refresh_cooldown_until - time.time()), _auth_refresh_cooldown_reason


def _auth_cooldown_blocks_refresh(lg: logging.Logger | None = None, *, context: str = "auth") -> bool:
    remaining, reason = _auth_cooldown_remaining()
    if remaining <= 0:
        return False
    log = lg or _logger
    log.warning(
        "[AUTH] Skipping %s refresh for %.0fs due to cooldown (%s).",
        context,
        remaining,
        reason or "rate/transient protection",
    )
    return True


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

    Lớp 0  : File cache runtime/cache/tv_token_cache.json (không cần mạng - nhanh nhất)
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
            log.info("[AUTH] Using cached token from runtime token cache.")
            return cached_token, "cached_token"

    # LỚP 1: Static token từ .env
    if TV_AUTH_TOKEN and TV_AUTH_TOKEN != GUEST_TOKEN:
        if _is_token_reusable_for_startup(TV_AUTH_TOKEN, "static_token", log):
            log.info("[AUTH] Using static TV_AUTH_TOKEN from .env.")
            return TV_AUTH_TOKEN, "static_token"

    if _auth_cooldown_blocks_refresh(log, context="resolve"):
        return GUEST_TOKEN, "cooldown"

    # LỚP 1.5: Refresh qua session cookie
    current_cookie = _tv_cookie or TV_COOKIE
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie, log)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, current_cookie):
                return token, "session_refresh"

    # LỚP 2: username/password - lấy token + cookie mới từ response HTTP POST
    if TV_USERNAME and TV_PASSWORD:
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "username/password"

    # LỚP 2.5: Headless Chromium với cookie cũ
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "headless_chromium"

    # LỚP 2.6: Headless Chromium đăng nhập từ đầu (không cần cookie cũ)
    # Dùng khi cookie đã hết hạn hoàn toàn - lấy token + cookie hoàn toàn mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Cookie expired - trying headless fresh login...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie):
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

        log.warning("[AUTH] Token refresh required - bootstrapping credentials.")
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
        part += "=" * (-len(part) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(part).decode())
        return float(payload["exp"]) - time.time()
    except Exception:
        return -1.0


def _is_refreshed_token_usable(
    token: str,
    source: str,
    log: logging.Logger,
    *,
    min_remaining_sec: int = 60,
) -> bool:
    if not token or token == GUEST_TOKEN:
        return False
    remaining = _jwt_expires_in(token)
    if remaining == -1.0:
        log.warning("[AUTH] Rejected %s token because JWT expiry could not be decoded.", source)
        return False
    if remaining <= min_remaining_sec:
        log.warning(
            "[AUTH] Rejected %s token because it expires too soon (%.0fs remaining).",
            source,
            remaining,
        )
        return False
    return True


def _token_needs_proactive_refresh(token: str, *, threshold_sec: int = TOKEN_PROACTIVE_REFRESH_SEC) -> bool:
    if not token or token == GUEST_TOKEN:
        return True
    remaining = _jwt_expires_in(token)
    return remaining == -1.0 or remaining < threshold_sec


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
        if remaining != -1.0 and remaining < TOKEN_PROACTIVE_REFRESH_SEC:
            log.info("[AUTH] Token expiring in %.0fs - proactive refresh.", remaining)
            _auth_token = GUEST_TOKEN
            need_refresh = True
    if need_refresh:
        _renew_auth_token(log)


def _probe_cookie_session(cookie_str: str, lg: logging.Logger | None = None) -> _CookieProbeResult:
    log = lg or _logger
    if not cookie_str:
        return _CookieProbeResult("missing", reason="cookie is empty")
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
        if resp.status_code == 429:
            return _CookieProbeResult(
                "rate_limited",
                status_code=429,
                retry_after_sec=_retry_after_seconds(resp.headers, AUTH_REFRESH_COOLDOWN_SEC),
                reason="HTTP 429",
            )
        if resp.status_code >= 500:
            return _CookieProbeResult("transient", status_code=resp.status_code, reason="HTTP 5xx")
        if resp.status_code in (401, 403) or "sign-in" in resp.url:
            log.warning(
                "[AUTH] Cookie probe: session expired - redirected to sign-in (status=%d).",
                resp.status_code,
            )
            return _CookieProbeResult("expired", status_code=resp.status_code, reason="sign-in redirect")
        m = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', resp.text)
        if m:
            token = m.group(1)
            log.info("[AUTH] Token refreshed via session cookie (HTTP GET).")
            return _CookieProbeResult("ok", token=token, status_code=resp.status_code)
        log.info(
            "[AUTH] Cookie probe inconclusive: HTTP %d but auth_token was not found in HTML "
            "(TradingView may use CSR rendering).",
            resp.status_code,
        )
        return _CookieProbeResult("inconclusive", status_code=resp.status_code, reason="token not in HTML")
    except requests.HTTPError as exc:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        headers = getattr(resp, "headers", {}) if resp is not None else {}
        if status == 429:
            return _CookieProbeResult(
                "rate_limited",
                status_code=status,
                retry_after_sec=_retry_after_seconds(headers, AUTH_REFRESH_COOLDOWN_SEC),
                reason="HTTP 429",
            )
        if status in (401, 403):
            log.warning("[AUTH] Cookie probe: session expired (status=%s).", status)
            return _CookieProbeResult("expired", status_code=status, reason="auth rejected")
        if status and status >= 500:
            return _CookieProbeResult("transient", status_code=status, reason="HTTP 5xx")
        log.warning("[AUTH] Cookie probe: HTTP request failed - %s", exc)
        return _CookieProbeResult("transient", status_code=status, reason=str(exc))
    except (requests.ConnectionError, requests.Timeout) as exc:
        log.warning("[AUTH] Cookie probe: network request failed - %s", exc)
        return _CookieProbeResult("transient", reason=type(exc).__name__)
    except Exception as exc:
        log.warning("[AUTH] Cookie probe: HTTP request failed - %s", exc)
        return _CookieProbeResult("transient", reason=str(exc))


def _refresh_token_via_cookie(cookie_str: str, lg: logging.Logger | None = None) -> str:
    """
    Lớp 1.5 - Làm mới auth_token bằng HTTP GET homepage TradingView.
    TradingView nhúng auth_token vào HTML khi user đã đăng nhập với sessionid cookie.
    Trả về token mới, hoặc GUEST_TOKEN nếu thất bại.

    Nhận lg để log vào ws_live.log (thay vì _logger nội bộ bị mất).
    KHÔNG log cookie/token value — chỉ log reason thất bại.
    """
    log = lg or _logger
    result = _probe_cookie_session(cookie_str, log)
    if result.status == "ok" and _is_refreshed_token_usable(result.token, "session cookie", log):
        return result.token
    if result.status == "rate_limited":
        _set_auth_cooldown(
            result.retry_after_sec or AUTH_REFRESH_COOLDOWN_SEC,
            "TradingView HTTP 429 during cookie probe",
            log,
        )
    elif result.status == "transient":
        _set_auth_cooldown(AUTH_TRANSIENT_COOLDOWN_SEC, "TradingView transient auth probe failure", log)
    return GUEST_TOKEN
    """
            log.warning("[AUTH] Cookie probe: session expired — redirected to sign-in (status=%d).",
                        resp.status_code)
        else:
            log.warning(
                "[AUTH] Cookie probe: HTTP %d — auth_token not found in HTML "
                "(TradingView may use CSR rendering; headless renewal required).",
                resp.status_code,
            )
    except Exception as exc:
        log.warning("[AUTH] Cookie probe: HTTP request failed — %s", exc)
    return GUEST_TOKEN
    """


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
            _apply_playwright_stealth(page)
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
            _apply_playwright_stealth(page)

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

            # Chờ 2 giây để CAPTCHA hoặc 2FA có thể hiện ra
            try:
                page.wait_for_timeout(2_000)
            except Exception:
                pass

            # --- CAPTCHA auto-solve (chỉ khi TV_CAPTCHA_API_KEY được cấu hình) ---
            try:
                sitekey: str = page.evaluate(
                    """() => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : null;
                    }"""
                ) or ""
                if sitekey:
                    captcha_token = _solve_captcha_hcaptcha(sitekey, "https://www.tradingview.com/signin/")
                    if captcha_token:
                        _logger.info("[AUTH] hCaptcha detected — injecting solution from %s.",
                                     TV_CAPTCHA_SERVICE or "capsolver")
                        page.evaluate(
                            """(token) => {
                                const ta = document.querySelector('textarea[name="h-captcha-response"]');
                                if (ta) ta.value = token;
                                const ta2 = document.querySelector('textarea[name="g-recaptcha-response"]');
                                if (ta2) ta2.value = token;
                                const form = document.querySelector('form');
                                if (form) form.submit();
                            }""",
                            captcha_token,
                        )
                        try:
                            page.wait_for_load_state("networkidle", timeout=20_000)
                        except Exception:
                            pass
                    elif TV_CAPTCHA_API_KEY:
                        _logger.warning("[AUTH] hCaptcha detected but solving failed — login may not complete.")
            except Exception:
                pass  # No CAPTCHA on this attempt

            # --- 2FA auto-fill (chỉ khi TV_2FA_SECRET được cấu hình) ---
            try:
                totp_code = _get_totp_code()
                if totp_code:
                    for sel_2fa in [
                        'input[name="code"]',
                        'input[inputmode="numeric"]',
                        'input[type="text"][autocomplete="one-time-code"]',
                    ]:
                        try:
                            page.wait_for_selector(sel_2fa, timeout=5_000)
                            page.fill(sel_2fa, totp_code)
                            try:
                                page.click('button[type="submit"]')
                            except Exception:
                                page.keyboard.press("Enter")
                            try:
                                page.wait_for_load_state("networkidle", timeout=20_000)
                            except Exception:
                                pass
                            _logger.info("[AUTH] 2FA code auto-filled.")
                            break
                        except Exception:
                            continue
            except Exception:
                pass  # No 2FA on this attempt

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


# =============================================================================
# COOKIE LIFECYCLE HELPERS
# =============================================================================

def _apply_playwright_stealth(page) -> None:
    """Apply playwright-stealth if installed — reduces bot-detection fingerprinting."""
    try:
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
    except ImportError:
        pass


def _get_totp_code() -> str | None:
    """Generate TOTP code from TV_2FA_SECRET. Returns None if secret/pyotp not available."""
    if not TV_2FA_SECRET:
        return None
    try:
        import pyotp  # type: ignore
        return pyotp.TOTP(TV_2FA_SECRET).now()
    except ImportError:
        _logger.warning("[AUTH] pyotp not installed — cannot auto-fill 2FA. Install: pip install pyotp")
        return None
    except Exception as exc:
        _logger.warning("[AUTH] TOTP generation failed: %s", exc)
        return None


def _solve_via_capsolver(sitekey: str, page_url: str) -> str | None:
    """Submit hCaptcha to CapSolver API and poll for solution token."""
    try:
        payload = {
            "clientKey": TV_CAPTCHA_API_KEY,
            "task": {
                "type": "HCaptchaTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }
        r = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=15)
        task_id = r.json().get("taskId")
        if not task_id:
            _logger.warning("[AUTH] CapSolver: task creation failed — %s", r.text[:200])
            return None
        for _ in range(60):
            time.sleep(1)
            r2 = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": TV_CAPTCHA_API_KEY, "taskId": task_id},
                timeout=15,
            )
            data = r2.json()
            status = data.get("status")
            if status == "ready":
                return data.get("solution", {}).get("gRecaptchaResponse")
            if status == "failed":
                _logger.warning("[AUTH] CapSolver: task failed — %s", data)
                return None
        _logger.warning("[AUTH] CapSolver: timed out waiting for solution.")
        return None
    except Exception as exc:
        _logger.warning("[AUTH] CapSolver error: %s", exc)
        return None


def _solve_via_2captcha(sitekey: str, page_url: str) -> str | None:
    """Submit hCaptcha to 2Captcha API and poll for solution token."""
    try:
        r = requests.post(
            "http://2captcha.com/in.php",
            data={"key": TV_CAPTCHA_API_KEY, "method": "hcaptcha",
                  "sitekey": sitekey, "pageurl": page_url, "json": 1},
            timeout=15,
        )
        data = r.json()
        if data.get("status") != 1:
            _logger.warning("[AUTH] 2Captcha: submit failed — %s", data)
            return None
        task_id = data["request"]
        for _ in range(30):
            time.sleep(2)
            r2 = requests.get(
                "http://2captcha.com/res.php",
                params={"key": TV_CAPTCHA_API_KEY, "action": "get",
                        "id": task_id, "json": 1},
                timeout=15,
            )
            data2 = r2.json()
            if data2.get("status") == 1:
                return data2["request"]
            if data2.get("request") != "CAPCHA_NOT_READY":
                _logger.warning("[AUTH] 2Captcha: error — %s", data2)
                return None
        _logger.warning("[AUTH] 2Captcha: timed out waiting for solution.")
        return None
    except Exception as exc:
        _logger.warning("[AUTH] 2Captcha error: %s", exc)
        return None


def _solve_captcha_hcaptcha(sitekey: str, page_url: str) -> str | None:
    """Dispatch to CapSolver or 2Captcha based on TV_CAPTCHA_SERVICE. Returns None if disabled."""
    if not TV_CAPTCHA_API_KEY:
        return None
    service = (TV_CAPTCHA_SERVICE or "capsolver").lower()
    if service == "2captcha":
        return _solve_via_2captcha(sitekey, page_url)
    return _solve_via_capsolver(sitekey, page_url)


def _save_cookie_renewal_ts(ts: float) -> None:
    """Persist last cookie renewal timestamp into the token cache file."""
    try:
        data: dict = {}
        if _TOKEN_CACHE.exists():
            data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
        data["last_cookie_renewal_ts"] = ts
        _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _write_token_cache_atomic(data)
    except Exception as exc:
        _logger.warning("[AUTH] Could not persist cookie renewal timestamp: %s", exc)


def _ensure_cookie_fresh_ttl_aware(lg: logging.Logger | None = None) -> None:
    """TTL-aware cookie/token lifecycle used by ws_live public API."""
    global _last_cookie_check_ts, _last_cookie_renewal_ts, \
           _cookie_probe_fail_streak, _cookie_renewal_fail_streak, _tv_cookie, _auth_token

    log = lg or _logger
    now = time.time()

    if _last_cookie_renewal_ts == 0.0:
        try:
            cached_ts = float(_load_token_cache().get("last_cookie_renewal_ts", 0.0))
            if cached_ts > 0:
                _last_cookie_renewal_ts = cached_ts
        except Exception:
            pass

    check_interval = (
        COOKIE_PROBE_RETRY_INTERVAL_SEC if _cookie_probe_fail_streak > 0
        else COOKIE_CHECK_INTERVAL_SEC
    )
    if now - _last_cookie_check_ts < check_interval:
        return
    if _auth_cooldown_blocks_refresh(log, context="cookie lifecycle"):
        return

    _last_cookie_check_ts = now
    current_cookie = _tv_cookie or TV_COOKIE
    if not current_cookie:
        return

    with _auth_lock:
        current_token = _auth_token
    token_remaining = _jwt_expires_in(current_token)
    token_is_guest = current_token == GUEST_TOKEN
    token_near_expiry = (
        token_is_guest
        or token_remaining == -1.0
        or (token_remaining != -1.0 and token_remaining < TOKEN_PROACTIVE_REFRESH_SEC)
    )

    probe = _probe_cookie_session(current_cookie, log)
    if probe.status == "ok":
        _cookie_probe_fail_streak = 0
        if token_near_expiry and _save_credentials_to_env(probe.token, current_cookie):
            log.info("[AUTH] Cookie probe refreshed token because ttl=%.0fs.", token_remaining)
        return

    if probe.status == "rate_limited":
        _set_auth_cooldown(
            probe.retry_after_sec or AUTH_REFRESH_COOLDOWN_SEC,
            "TradingView HTTP 429 during cookie lifecycle",
            log,
        )
        return

    if probe.status == "transient":
        _set_auth_cooldown(AUTH_TRANSIENT_COOLDOWN_SEC, "TradingView transient cookie probe failure", log)
        return

    has_renewal_ts = _last_cookie_renewal_ts > 0
    age_since_renewal = now - _last_cookie_renewal_ts if has_renewal_ts else 0.0
    force_by_age = has_renewal_ts and age_since_renewal >= COOKIE_RENEWAL_INTERVAL_SEC
    need_renewal = False

    if probe.status == "expired":
        _cookie_probe_fail_streak += 1
        need_renewal = True
        log.warning("[AUTH] Cookie probe confirmed expired session (streak=%d).", _cookie_probe_fail_streak)
    elif probe.status == "inconclusive":
        _cookie_probe_fail_streak = 0
        if token_near_expiry:
            need_renewal = True
            log.info(
                "[AUTH] Cookie probe inconclusive and token ttl=%.0fs - headless renewal is allowed.",
                token_remaining,
            )
        elif force_by_age:
            need_renewal = True
            log.info(
                "[AUTH] Cookie probe inconclusive but renewal age is %.1fh - headless renewal is allowed.",
                age_since_renewal / 3600,
            )
        else:
            log.info(
                "[AUTH] Cookie probe inconclusive; token still healthy for %.0fs, skipping headless renewal.",
                token_remaining,
            )
    elif force_by_age:
        need_renewal = True
        log.info("[AUTH] Cookie renewal forced by age %.1fh.", age_since_renewal / 3600)
    else:
        log.debug("[AUTH] Cookie probe skipped (%s).", probe.status)

    if not need_renewal:
        return
    if _auth_cooldown_blocks_refresh(log, context="cookie renewal"):
        return
    if not _cookie_lock.acquire(blocking=False):
        log.debug("[AUTH] Cookie renewal already in progress - skipping.")
        return

    try:
        log.info(
            "[AUTH] Cookie renewal triggered (reason=%s, token_ttl=%.0fs, age=%.1fh).",
            probe.status,
            token_remaining,
            age_since_renewal / 3600,
        )
        _tg_alert("WARNING", "[AUTH] TradingView cookie renewal started - fetching a fresh session.")

        new_token, new_cookie = GUEST_TOKEN, ""
        if current_cookie:
            new_token, new_cookie = _headless_refresh(current_cookie)
        if new_token == GUEST_TOKEN and TV_USERNAME and TV_PASSWORD:
            new_token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)

        token_updated = False
        if new_token != GUEST_TOKEN:
            token_updated = _save_credentials_to_env(new_token, new_cookie or current_cookie)
        elif new_cookie:
            _tv_cookie = new_cookie
            _save_token_cache("", new_cookie)

        if new_cookie:
            _tv_cookie = new_cookie
            _cookie_probe_fail_streak = 0
            _cookie_renewal_fail_streak = 0
            _last_cookie_renewal_ts = time.time()
            _save_cookie_renewal_ts(_last_cookie_renewal_ts)
            log.info(
                "[AUTH] Cookie renewal succeeded (token=%s, cookie=updated).",
                "updated" if token_updated else "unchanged",
            )
            _tg_alert("INFO", "[AUTH] TradingView cookie renewed successfully.")
        else:
            _cookie_renewal_fail_streak += 1
            log.error("[AUTH] Cookie renewal FAILED (attempt %d).", _cookie_renewal_fail_streak)
            _tg_alert(
                "ERROR",
                f"[AUTH] Cookie renewal FAILED (attempt {_cookie_renewal_fail_streak}).\n"
                "System will retry at the next scheduled check.",
            )
    except Exception as exc:
        log.error("[AUTH] Unexpected error during cookie renewal: %s", exc)
    finally:
        _cookie_lock.release()


def _ensure_cookie_fresh(lg: logging.Logger | None = None) -> None:
    """
    Proactive cookie lifecycle management.

    Phase 1 — probe (every 2h, or every 30min after a failed probe):
        HTTP GET tradingview.com with current cookie.
        If OK: reset fail streak, optionally refresh expiring token.
        If fail: increment fail streak.

    Phase 2 — renewal (triggered when probe fails twice OR cookie age >= 3 days):
        headless_refresh (existing cookie) → headless_login_fresh (fresh login).
        Non-blocking: skips if another renewal thread already holds _cookie_lock.
    """
    global _last_cookie_check_ts, _last_cookie_renewal_ts, \
           _cookie_probe_fail_streak, _cookie_renewal_fail_streak, _tv_cookie, _auth_token

    log = lg or _logger
    now = time.time()

    # Recover last_cookie_renewal_ts from cache after a restart
    if _last_cookie_renewal_ts == 0.0:
        try:
            cached_ts = float(_load_token_cache().get("last_cookie_renewal_ts", 0.0))
            if cached_ts > 0:
                _last_cookie_renewal_ts = cached_ts
        except Exception:
            pass

    # --- Phase 1: probe ---
    check_interval = (
        COOKIE_PROBE_RETRY_INTERVAL_SEC if _cookie_probe_fail_streak > 0
        else COOKIE_CHECK_INTERVAL_SEC
    )
    if now - _last_cookie_check_ts < check_interval:
        return

    _last_cookie_check_ts = now
    current_cookie = _tv_cookie or TV_COOKIE
    if not current_cookie:
        return

    log.debug("[AUTH] Cookie probe running.")
    probe_token = _refresh_token_via_cookie(current_cookie, log)
    probe_ok = probe_token != GUEST_TOKEN

    if probe_ok:
        _cookie_probe_fail_streak = 0
        log.debug("[AUTH] Cookie probe OK — session still alive.")
        # Opportunistically refresh a nearly-expired token
        with _auth_lock:
            remaining = _jwt_expires_in(_auth_token)
            if remaining < 600 or _auth_token == GUEST_TOKEN:
                _auth_token = probe_token
                log.info("[AUTH] Cookie probe opportunistically refreshed expiring token.")
    else:
        _cookie_probe_fail_streak += 1
        log.warning("[AUTH] Cookie probe FAILED (streak=%d).", _cookie_probe_fail_streak)

    # --- Phase 2: renewal decision ---
    has_renewal_ts = _last_cookie_renewal_ts > 0
    age_since_renewal = now - _last_cookie_renewal_ts if has_renewal_ts else 0.0
    need_renewal = (
        _cookie_probe_fail_streak >= 2
        or (has_renewal_ts and age_since_renewal >= COOKIE_RENEWAL_INTERVAL_SEC)
    )
    if not need_renewal:
        return

    if not _cookie_lock.acquire(blocking=False):
        log.debug("[AUTH] Cookie renewal already in progress — skipping.")
        return

    try:
        log.info(
            "[AUTH] Cookie renewal triggered (streak=%d, age=%.1fh).",
            _cookie_probe_fail_streak, age_since_renewal / 3600,
        )
        _tg_alert("WARNING", "[AUTH] TradingView cookie renewal started — fetching a fresh session.")

        new_token, new_cookie = GUEST_TOKEN, ""

        # Attempt 1: headless with existing session
        if current_cookie:
            new_token, new_cookie = _headless_refresh(current_cookie)

        # Attempt 2: full fresh login
        if new_token == GUEST_TOKEN and TV_USERNAME and TV_PASSWORD:
            new_token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)

        if new_cookie:
            carry_token = new_token if new_token != GUEST_TOKEN else _auth_token
            _save_credentials_to_env(carry_token, new_cookie)
            _tv_cookie = new_cookie
            _cookie_probe_fail_streak = 0
            _cookie_renewal_fail_streak = 0
            _last_cookie_renewal_ts = time.time()
            _save_cookie_renewal_ts(_last_cookie_renewal_ts)
            log.info("[AUTH] Cookie renewal succeeded.")
            _tg_alert("INFO", "[AUTH] TradingView cookie renewed successfully.")
        else:
            _cookie_renewal_fail_streak += 1
            log.error("[AUTH] Cookie renewal FAILED (attempt %d).", _cookie_renewal_fail_streak)
            _tg_alert(
                "ERROR",
                f"[AUTH] Cookie renewal FAILED (attempt {_cookie_renewal_fail_streak}).\n"
                "System will retry at the next scheduled check.",
            )
    except Exception as exc:
        log.error("[AUTH] Unexpected error during cookie renewal: %s", exc)
    finally:
        _cookie_lock.release()


def _load_token_cache() -> dict:
    """Đọc runtime token/cookie từ file runtime/cache/tv_token_cache.json."""
    try:
        if _TOKEN_CACHE.exists():
            return json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_token_cache_atomic(data: dict) -> None:
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOKEN_CACHE.with_name(
        f"{_TOKEN_CACHE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(_TOKEN_CACHE))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _save_token_cache(token: str, cookie: str) -> None:
    """Lưu runtime token/cookie vào runtime/cache/tv_token_cache.json.

    Reads the existing file first so extra fields (e.g. last_cookie_renewal_ts)
    written by _save_cookie_renewal_ts are not silently discarded.
    """
    data: dict = {}
    try:
        if _TOKEN_CACHE.exists():
            data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    if token and token != GUEST_TOKEN and _is_refreshed_token_usable(token, "cache", _logger, min_remaining_sec=0):
        data["TV_AUTH_TOKEN"] = token
    if cookie:
        data["TV_COOKIE"] = cookie
    try:
        _write_token_cache_atomic(data)
    except Exception as exc:
        _logger.warning("[AUTH] Could not write token cache: %s", exc)


def _save_credentials_to_env(token: str, cookie: str) -> bool:
    """
    Cập nhật runtime credentials:
    - In-memory: _auth_token và _tv_cookie (tức thời)
    - File cache: runtime/cache/tv_token_cache.json (durable qua restart)
    """
    global _auth_token, _tv_cookie
    token_ok = _is_refreshed_token_usable(token, "runtime credential", _logger)
    token_to_save = token if token_ok else ""
    if token_ok:
        _auth_token = token
    if cookie:
        _tv_cookie = cookie
    _save_token_cache(token_to_save, cookie)
    _logger.info(
        "[AUTH] Credentials saved to runtime token cache (token=%s, cookie=%s).",
        "updated" if token_ok else "unchanged",
        "updated" if cookie else "unchanged",
    )
    return token_ok


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

    if _auth_cooldown_blocks_refresh(log, context="bootstrap"):
        return GUEST_TOKEN, "cooldown"

    current_cookie = _tv_cookie or TV_COOKIE

    # Bước 1: Refresh via HTTP GET
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie, log)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, current_cookie):
                return token, "session_refresh"
        log.info("[AUTH] HTTP cookie refresh failed - trying headless Chromium...")

    # Bước 2: Headless Chromium
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "headless_chromium"

    # Bước 3: username/password (HTTP POST) - lấy token + cookie mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Trying username/password login (HTTP POST)...")
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "http_post_login"

    # Bước 4: Headless fresh login (không cần cookie cũ) - lấy token + cookie hoàn toàn mới
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Trying headless fresh login (Playwright full login)...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie):
                return token, "headless_fresh_login"

    log.warning("[AUTH] All bootstrap methods failed.")
    return GUEST_TOKEN, "guest"
