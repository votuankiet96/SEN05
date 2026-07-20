"""
Quản lý xác thực TradingView dùng chung cho historical pipeline và ws_live.

Module này giữ token/cookie TradingView ở trạng thái có thể dùng được cho các
job lấy dữ liệu. Khi token sắp hết hạn hoặc TradingView báo lỗi xác thực, module
sẽ lần lượt thử các nguồn xác thực nhanh trước, rồi mới dùng đến các cách nặng
hơn như Playwright/browser profile.

Thứ tự fallback tổng quát:
1. runtime cache trong `runtime/cache/tv_token_cache.json`
2. token/cookie cấu hình sẵn trong `.env`
3. refresh token qua cookie session bằng HTTP GET
4. refresh từ persistent browser profile
5. đăng nhập username/password qua HTTP POST
6. headless browser refresh với cookie hiện có
7. headless browser đăng nhập mới bằng credentials, nếu được bật
8. guest token như phương án cuối cùng

Mục tiêu của module này không chỉ là "đăng nhập được", mà là:
- tránh khởi động batch bằng token sắp hết hạn
- có thể refresh giữa chừng khi TradingView báo lỗi xác thực
- giữ trạng thái auth nhất quán cho các process dùng chung
- cảnh báo rõ ràng khi hệ thống rơi xuống guest mode
- tự động cập nhật cookie khi cookie hết hạn hoặc trở nên không đáng tin cậy
"""

# =============================================================================
# core_engine/tradingview/auth.py - Xác thực TradingView dùng chung
# =============================================================================
#
# FILE NÀY LÀM GÌ?
#   TradingView yêu cầu đăng nhập để xem đầy đủ dữ liệu.
#   File này quản lý toàn bộ quá trình lấy và làm mới "bằng chứng đã đăng nhập"
#   gồm auth token, cookie và browser profile, rồi cung cấp lại cho các script khác.
#
# AUTH TOKEN LÀ GÌ?
#   Sau khi đăng nhập TradingView, server cấp một chuỗi dài (JWT token), ví dụ:
#     eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...
#   Chuỗi này là "thẻ thông hành": khi gửi kèm request, TradingView biết người
#   dùng là ai và cho phép tải dữ liệu đầy đủ hơn so với Guest.
#
# HỆ THỐNG DỰ PHÒNG NHIỀU LỚP (thử lần lượt từ nhanh đến chậm):
#
#   Lớp 0 - Token cache (runtime/cache/tv_token_cache.json):
#     Lần trước đã đăng nhập thành công nên token/cookie được lưu vào cache cục bộ.
#     Lần này dùng lại nếu token còn đủ hạn, không cần request mạng.
#
#   Lớp 1 - Token tĩnh từ .env (TV_AUTH_TOKEN):
#     Người dùng có thể copy token từ trình duyệt và lưu vào file .env.
#     Không cần mạng để đọc, nhưng token có thể hết hạn sau một thời gian.
#
#   Lớp 1.5 - Làm mới qua cookie (HTTP GET):
#     Gửi HTTP request đến TradingView kèm cookie sessionid.
#     Nếu TradingView nhúng token mới trong HTML trả về, module sẽ extract token đó.
#     Cách này nhanh và hiệu quả khi cookie còn hiệu lực.
#
#   Lớp 2 - Persistent browser profile:
#     Mở profile Playwright đã lưu trước đó, tải TradingView và lấy token/cookie mới.
#     Hữu ích cho tài khoản dùng Google/social login hoặc phiên trình duyệt đã được setup.
#
#   Lớp 2.5 - Đăng nhập username/password (HTTP POST):
#     Gửi POST form mô phỏng đăng nhập trên trình duyệt.
#     Chỉ phù hợp với tài khoản TradingView gốc, không phải Google/SSO login.
#
#   Lớp 3 - Headless Chromium với cookie hiện có:
#     Chạy Chromium ẩn để tải TradingView bằng session cookie hiện có.
#     Cách này thực thi JavaScript đầy đủ nên đáng tin hơn HTTP GET khi trang render bằng JS.
#
#   Lớp 4 - Headless Chromium đăng nhập mới:
#     Mở trang /signin/, điền username/password, xử lý CAPTCHA/2FA nếu có cấu hình.
#     Chỉ chạy khi TV_AUTH_HEADLESS_FRESH_LOGIN được bật.
#
#   Lớp cuối - Guest token:
#     Dùng tài khoản khách. Dữ liệu có thể bị giới hạn, ít lịch sử hơn hoặc thiếu dữ liệu premium.
#     Đây là phương án cuối cùng, không nên chạy lâu ở chế độ guest.
#
# CÁC SCRIPT DÙNG FILE NÀY:
#   live_fetching.py - WebSocket live      (bootstrap, get_current_token, renew)
#   historical_pulling.py - Backfill hằng ngày  (get_valid_tv_connection, refresh_mid_run)
#
# CẤU HÌNH CẦN THIẾT TRONG FILE .env:
#   TV_AUTH_TOKEN=eyJhbGci...   -> copy từ trình duyệt khi cần token tĩnh
#   TV_COOKIE=sessionid=abc...  -> copy toàn bộ cookie header từ trình duyệt
#   TV_USERNAME=your@email.com  -> chỉ cần nếu dùng username/password
#   TV_PASSWORD=your_password   -> chỉ cần nếu dùng username/password
# =============================================================================

import json
import logging

# Bootstrap/imports dùng chung cho module xác thực
import os
import random
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core_engine.logkit.factory import setup_logger  # noqa: E402
from core_engine.logkit.formatters import operation_line  # noqa: E402
from core_engine.reporting.discord import notify_auth_event, sanitize_ssl_keylogfile, tg_alert as _tg_alert  # noqa: E402
from core_engine.settings import (  # noqa: E402
    AUTH_LOG,
    CACHE_DIR,
    HISTORICAL_PROVIDER,
    TV_2FA_SECRET,
    TV_AUTH_TOKEN,
    TV_CAPTCHA_API_KEY,
    TV_CAPTCHA_SERVICE,
    TV_COOKIE,
    TV_PASSWORD,
    TV_USERNAME,
    env_bool as env_flag,
    env_int,
)
from core_engine.coordination.locks import _local_pid_alive as is_pid_alive  # noqa: E402

class _AuthLogFilter(logging.Filter):
    """Chuẩn hóa cách ghi auth.log mà không thay đổi hành vi xác thực TradingView."""

    _REPLACEMENTS = (
        ("auth_token", "login token"),
        ("tv.token", "runtime login token"),
        ("cookie", "browser session"),
        ("headless Chromium", "automatic browser refresh"),
        ("headless fresh login", "automatic browser login"),
        ("guest token", "limited TradingView access"),
        ("guest", "limited access"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.msg)
        if msg.startswith("[AUTH]"):
            text = msg[len("[AUTH]") :].strip()
            for src, dst in self._REPLACEMENTS:
                text = text.replace(src, dst)
            record.msg = operation_line("AUTH", text)
        return True


# Module-level logger, dùng khi caller không truyền logger vào.
_logger = setup_logger("tv_auth", str(AUTH_LOG), rotating=True, console=False, utc=True, pipe_format=True)
_logger.addFilter(_AuthLogFilter())

sanitize_ssl_keylogfile()
import requests

try:
    import truststore  # type: ignore

    truststore.inject_into_ssl()
    _SYSTEM_TRUSTSTORE_ACTIVE = True
except Exception:
    _SYSTEM_TRUSTSTORE_ACTIVE = False

# =============================================================================
# HẰNG SỐ
# =============================================================================

# Chuỗi đặc biệt báo hiệu trạng thái chưa xác thực hoặc guest.
GUEST_TOKEN = "unauthorized_user_token"


def safe_auth_source_label(source: str | None) -> str:
    """Trả về nhãn nguồn auth an toàn, không chứa token/cookie bí mật."""
    text = str(source or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower()
    if any(marker in lowered for marker in ("sessionid=", "sessionid_sign=", "device_t=", "g_state=", "cookie:")):
        return "interactive_browser_session"
    if len(text) > 80 and (";" in text or "." in text):
        return "credential_material_redacted"
    return text

# Từ khóa trong message lỗi cho biết token đã hết hạn hoặc không hợp lệ.
TOKEN_EXPIRY_KEYWORDS = ("unauthorized", "auth_error", "not_authorized")

# Tham số retry HTTP.
HTTP_MAX_RETRIES = 4
HTTP_BASE_DELAY_SEC = 2.0
HTTP_MAX_DELAY_SEC = 120.0
STARTUP_MIN_TOKEN_TTL_SEC = 10 * 60
TOKEN_PROACTIVE_REFRESH_SEC = env_int(
    "TV_TOKEN_PROACTIVE_REFRESH_SEC", 15 * 60, minimum=0
)
TOKEN_PROACTIVE_RETRY_SEC = env_int(
    "TV_TOKEN_PROACTIVE_RETRY_SEC", 10 * 60, minimum=0
)
AUTH_REFRESH_COOLDOWN_SEC = env_int("TV_AUTH_REFRESH_COOLDOWN_SEC", 900, minimum=0)
AUTH_TRANSIENT_COOLDOWN_SEC = env_int(
    "TV_AUTH_TRANSIENT_COOLDOWN_SEC", 300, minimum=0
)
AUTH_CONNECTIVITY_PREFLIGHT = env_flag("TV_AUTH_CONNECTIVITY_PREFLIGHT", True)
AUTH_CONNECTIVITY_CONNECT_TIMEOUT_SEC = env_int(
    "TV_AUTH_CONNECTIVITY_CONNECT_TIMEOUT_SEC", 2, minimum=1
)
AUTH_CONNECTIVITY_READ_TIMEOUT_SEC = env_int(
    "TV_AUTH_CONNECTIVITY_READ_TIMEOUT_SEC", 4, minimum=1
)
AUTH_REFRESH_LOCK_STALE_SEC = env_int(
    "TV_AUTH_REFRESH_LOCK_STALE_SEC", 20 * 60, minimum=60
)
AUTH_HEADLESS_FRESH_LOGIN_ENABLED = env_flag("TV_AUTH_HEADLESS_FRESH_LOGIN", False)

# Chu kỳ kiểm tra và gia hạn cookie.
COOKIE_CHECK_INTERVAL_SEC = 2 * 3600  # probe every 2h
COOKIE_RENEWAL_INTERVAL_SEC = 3 * 24 * 3600  # force-renew every 3 days
COOKIE_PROBE_RETRY_INTERVAL_SEC = 30 * 60  # retry after 30min on probe fail

# =============================================================================
# TRẠNG THÁI AUTH TOÀN CỤC
# =============================================================================

# Token xác thực TradingView, chia sẻ giữa các kết nối trong process.
_auth_token: str = GUEST_TOKEN
_auth_lock = threading.Lock()
_http_session: requests.Session | None = None
_http_session_lock = threading.Lock()

# Cookie TradingView ở runtime, có thể được cập nhật khi refresh.
_tv_cookie: str = TV_COOKIE

# Đường dẫn token cache, lưu runtime credentials tách khỏi .env.
_TOKEN_CACHE = CACHE_DIR / "tv_token_cache.json"
_AUTH_REFRESH_LOCK = _TOKEN_CACHE.parent / "tv_auth_refresh.lock"
_PLAYWRIGHT_BROWSER_CACHE = _TOKEN_CACHE.parent / "playwright-browsers"
_BROWSER_PROFILE_DIR = (
    Path(os.environ["TV_BROWSER_PROFILE_DIR"]).expanduser()
    if os.environ.get("TV_BROWSER_PROFILE_DIR", "").strip()
    else _TOKEN_CACHE.parent / "tradingview-browser-profile"
)
_PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Trạng thái vòng đời cookie, được cập nhật bởi _ensure_cookie_fresh.
_last_cookie_check_ts: float = 0.0
_last_cookie_renewal_ts: float = 0.0
_cookie_probe_fail_streak: int = 0
_cookie_renewal_fail_streak: int = 0
_cookie_lock = threading.Lock()
_auth_cooldown_lock = threading.Lock()
_auth_refresh_cooldown_until: float = 0.0
_auth_refresh_cooldown_reason: str = ""
_last_proactive_refresh_attempt_ts: float = 0.0


@dataclass(frozen=True)
class _CookieProbeResult:
    status: str
    token: str = GUEST_TOKEN
    status_code: int | None = None
    retry_after_sec: float | None = None
    reason: str = ""


# =============================================================================
# PUBLIC API - dùng bởi live_fetching.py
# =============================================================================


def get_current_token() -> str:
    """Trả về token hiện tại theo cách an toàn giữa các thread."""
    with _auth_lock:
        return _auth_token

def get_current_cookie() -> str:
    """Trả về TradingView cookie header đang dùng, nếu có."""
    return _tv_cookie or ""


def set_current_cookie(cookie: str) -> None:
    """Cập nhật TradingView cookie header trong bộ nhớ."""
    global _tv_cookie
    _tv_cookie = cookie or ""

def get_auth_mode() -> str:
    """
    Trả về chế độ xác thực hiện tại: "guest" hoặc "authenticated".
    Dùng để phát hiện guest mode trước khi chạy historical pipeline/live.
    """
    with _auth_lock:
        return "guest" if _auth_token == GUEST_TOKEN else "authenticated"



def is_guest_mode() -> bool:
    """Trả về True khi token hiện tại là guest hoặc chưa xác thực."""
    with _auth_lock:
        return _auth_token == GUEST_TOKEN


def set_current_token(token: str) -> None:
    """Ghi token mới vào global state theo cách an toàn giữa các thread."""
    global _auth_token
    with _auth_lock:
        _auth_token = token



def token_expires_in(token: str) -> float:
    """Trả về số giây còn lại trước khi token hết hạn; số âm nếu không xác định được."""
    return _jwt_expires_in(token)


def load_token_cache() -> dict:
    """Đọc cache xác thực TradingView đã lưu trên đĩa."""
    return _load_token_cache()


def resolve_auth_token(lg: logging.Logger | None = None) -> tuple[str, str]:
    """Lấy token xác thực thông qua chuỗi fallback đã cấu hình."""
    return _resolve_auth_token(lg)


def has_2fa_secret() -> bool:
    return bool(TV_2FA_SECRET)


def check_and_refresh(lg: logging.Logger | None = None) -> None:
    """
    Chủ động làm mới token nếu token sắp hết hạn.
    Gọi trước mỗi batch WebSocket để không bắt đầu batch bằng token quá yếu hoặc đã hết hạn.
    """
    _check_and_maybe_refresh_token(lg)


def renew(lg: logging.Logger | None = None) -> None:
    """Gia hạn token khi TradingView báo lỗi xác thực giữa chừng."""
    _renew_auth_token_coordinated(lg)


def bootstrap(lg: logging.Logger | None = None) -> tuple[str, str]:
    """
    Đảm bảo credentials hợp lệ trước khi khởi động.
    Trả về (token, tên_phương_thức).
    """
    return _bootstrap_credentials_coordinated(lg)


def ensure_cookie_fresh(lg: logging.Logger | None = None) -> None:
    """Chủ động probe và gia hạn cookie TradingView nếu cần.

    Gọi trước mỗi batch để phát hiện cookie hết hạn trước khi token cũng hết.
    Non-blocking: bỏ qua nếu renewal đang chạy ở thread khác.
    """
    _ensure_cookie_fresh_ttl_aware(lg)


def browser_profile_status() -> dict:
    """Trả về trạng thái browser profile/cache TradingView, không chứa dữ liệu bí mật."""
    cache = _load_token_cache()
    cache_token = str(cache.get("TV_AUTH_TOKEN") or "")
    cache_cookie = str(cache.get("TV_COOKIE") or "")
    env_token = TV_AUTH_TOKEN or ""
    env_cookie = TV_COOKIE or ""
    token_source = "runtime_cache" if cache_token else ("config/dp_provider.env" if env_token else "")
    token = cache_token or env_token
    profile_exists = _BROWSER_PROFILE_DIR.exists()
    profile_files = 0
    try:
        if profile_exists:
            profile_files = sum(1 for _ in _BROWSER_PROFILE_DIR.rglob("*"))
    except Exception:
        profile_files = -1
    return {
        "token": _token_status_summary(token, token_source),
        "token_source": token_source,
        "token_present": bool(token),
        "cookie_present": bool(cache_cookie or env_cookie),
        "username_present": bool(TV_USERNAME),
        "password_present": bool(TV_PASSWORD),
        "cache_present": bool(cache),
        "cache_has_token": bool(cache_token),
        "cache_has_cookie": bool(cache_cookie),
        "profile_dir": str(_BROWSER_PROFILE_DIR),
        "profile_present": profile_exists,
        "profile_file_count": profile_files,
        "browser_cache_dir": str(_PLAYWRIGHT_BROWSER_CACHE),
        "headless_fresh_login_enabled": AUTH_HEADLESS_FRESH_LOGIN_ENABLED,
    }


def refresh_from_browser_profile(lg: logging.Logger | None = None) -> tuple[str, str]:
    """Làm mới token/cookie TradingView từ persistent browser profile."""
    return _refresh_from_browser_profile(lg=lg)


def interactive_browser_login(
    *,
    timeout_sec: int = 600,
    lg: logging.Logger | None = None,
) -> tuple[str, str]:
    """Mở browser profile hiển thị, chờ người vận hành đăng nhập, rồi cache credentials."""
    return _interactive_browser_login(timeout_sec=timeout_sec, lg=lg)


@contextmanager
def auth_refresh_process_lock(lg: logging.Logger | None = None):
    """Guard public để các CLI auth job dùng chung khóa refresh."""
    with _auth_refresh_process_lock(lg) as acquired:
        yield acquired


def auth_refresh_lock_status() -> dict:
    """Trả về trạng thái khóa auth-refresh để chẩn đoán, không chứa dữ liệu bí mật."""
    status: dict[str, object] = {
        "present": _AUTH_REFRESH_LOCK.exists(),
        "path": str(_AUTH_REFRESH_LOCK),
        "pid": None,
        "owner_alive": None,
        "age_seconds": None,
        "stale_seconds": AUTH_REFRESH_LOCK_STALE_SEC,
    }
    if not status["present"]:
        return status
    try:
        stat = _AUTH_REFRESH_LOCK.stat()
        status["age_seconds"] = max(0.0, time.time() - stat.st_mtime)
    except Exception:
        status["age_seconds"] = None
    try:
        raw = _AUTH_REFRESH_LOCK.read_text(encoding="utf-8", errors="replace").strip()
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError, AttributeError):
        pid = 0
    if pid > 0:
        status["pid"] = pid
        status["owner_alive"] = is_pid_alive(pid)
    return status


def cleanup_abandoned_auth_refresh_lock(
    lg: logging.Logger | None = None,
    *,
    owner_pid: int | None = None,
) -> bool:
    """Xóa auth-refresh lock bị bỏ lại bởi process đã chết hoặc đã stale."""
    log = lg or _logger
    status = auth_refresh_lock_status()
    if not status.get("present"):
        return False

    pid = int(status.get("pid") or 0)
    owner_alive = status.get("owner_alive")
    age = float(status.get("age_seconds") or 0.0)
    reason = ""

    if owner_pid is not None:
        if pid != int(owner_pid):
            return False
        if owner_alive is not False:
            return False
        reason = f"dead owner pid={pid}"
    elif pid > 0 and owner_alive is False:
        reason = f"dead owner pid={pid}"
    elif age >= AUTH_REFRESH_LOCK_STALE_SEC:
        reason = f"stale age={age:.0f}s"
    else:
        return False

    try:
        _AUTH_REFRESH_LOCK.unlink()
        log.warning("[AUTH] Removed abandoned auth refresh lock (%s).", reason)
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        log.debug("[AUTH] Could not remove abandoned auth refresh lock: %s", exc)
        return False


def diagnose_connectivity(
    *,
    include_browser: bool = True,
    network_timeout_sec: int = 6,
    browser_timeout_ms: int = 10_000,
    lg: logging.Logger | None = None,
) -> dict:
    """Trả về thông tin chẩn đoán auth/network/browser TradingView, không chứa dữ liệu bí mật."""
    log = lg or _logger
    started = time.time()
    cleanup_abandoned_auth_refresh_lock(log)
    host = "www.tradingview.com"
    urls = {
        "https_root": "https://www.tradingview.com/",
        "https_signin": "https://www.tradingview.com/accounts/signin/",
    }
    checks: dict[str, dict] = {}

    def add(name: str, ok: bool, detail: str, **extra) -> None:
        checks[name] = {"ok": bool(ok), "detail": str(detail), **extra}

    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({str(item[4][0]) for item in infos})
        add("dns", bool(ips), f"{host} -> {', '.join(ips[:4])}", ips=ips[:8])
    except Exception as exc:
        add("dns", False, f"{type(exc).__name__}: {exc}")

    tcp_timeout = max(0.75, float(network_timeout_sec))
    http_connect_timeout = max(0.75, tcp_timeout)
    http_read_timeout = max(1.0, float(network_timeout_sec))

    try:
        with socket.create_connection((host, 443), timeout=tcp_timeout):
            pass
        add("tcp_443", True, "TCP 443 connection opened")
    except Exception as exc:
        add("tcp_443", False, f"{type(exc).__name__}: {exc}")

    headers = {
        "User-Agent": _PLAYWRIGHT_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    def _probe_http(url: str):
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return _get_http_session().get(
                    url,
                    headers=headers,
                    timeout=(http_connect_timeout, http_read_timeout),
                    allow_redirects=True,
                )
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(0.5)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("HTTP probe failed without an exception")

    for name, url in urls.items():
        if checks.get("tcp_443", {}).get("ok") is False:
            add(name, False, "Skipped because TCP 443 check failed.")
            continue
        if name == "https_signin" and checks.get("https_root", {}).get("ok") is False:
            add(name, False, "Skipped because TradingView root HTTPS check failed.")
            continue
        try:
            resp = _probe_http(url)
            add(
                name,
                resp.status_code < 500,
                f"HTTP {resp.status_code} -> {resp.url}",
                status_code=resp.status_code,
                final_url=resp.url,
            )
        except Exception as exc:
            add(name, False, f"{type(exc).__name__}: {exc}")

    if include_browser and checks.get("https_root", {}).get("ok") is False:
        add("playwright_https_root", False, "Skipped because TradingView root HTTPS check failed.")
        add("playwright_https_signin", False, "Skipped because TradingView root HTTPS check failed.")
    elif include_browser:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            add("playwright_import", False, f"Playwright unavailable: {exc}")
        else:
            add("playwright_import", True, "Playwright package import OK")
            for name, url in urls.items():
                step_name = f"playwright_{name}"
                profile = Path(tempfile.mkdtemp(prefix="sen05_tv_diag_"))
                try:
                    with _playwright_runtime_env():
                        with sync_playwright() as pw:
                            ctx = pw.chromium.launch_persistent_context(
                                str(profile),
                                headless=True,
                                args=["--no-sandbox"],
                                user_agent=_PLAYWRIGHT_USER_AGENT,
                                locale="en-US",
                            )
                            page = ctx.pages[0] if ctx.pages else ctx.new_page()
                            try:
                                page.goto(
                                    url,
                                    wait_until="domcontentloaded",
                                    timeout=max(5_000, int(browser_timeout_ms)),
                                )
                                add(step_name, True, f"Loaded {page.url}", final_url=page.url)
                            except Exception as exc:
                                add(step_name, False, f"{type(exc).__name__}: {exc}")
                            finally:
                                ctx.close()
                except Exception as exc:
                    add(step_name, False, f"{type(exc).__name__}: {exc}")
                finally:
                    shutil.rmtree(profile, ignore_errors=True)

    auth_status = browser_profile_status()
    token_state = str((auth_status.get("token") or {}).get("state") or "missing")
    network_ok = bool(
        checks.get("dns", {}).get("ok")
        and checks.get("tcp_443", {}).get("ok")
        and checks.get("https_root", {}).get("ok")
    )
    browser_root = checks.get("playwright_https_root")
    browser_ok = bool(browser_root.get("ok")) if isinstance(browser_root, dict) else True

    token_usable = token_state in {"valid", "expiring_soon"}
    if not network_ok and token_usable:
        status = "warn"
        message = (
            "TradingView token cache is usable, but the network check failed. "
            "Data jobs may retry/fail until TradingView connectivity recovers."
        )
    elif not network_ok:
        status = "blocker"
        message = "TradingView network check failed before auth refresh could run."
    elif token_usable and browser_ok:
        status = "ok" if token_state == "valid" else "warn"
        message = "TradingView auth is usable."
    elif token_usable:
        status = "warn"
        message = "TradingView token is usable, but browser-assisted refresh is blocked."
    elif not browser_ok:
        status = "blocker"
        message = "TradingView token is not usable and browser-assisted login cannot reach TradingView."
    else:
        status = "blocker"
        message = "TradingView token is not usable; refresh credentials before OHLCV jobs."

    lock_status = auth_refresh_lock_status()
    result = {
        "status": status,
        "message": message,
        "auth": auth_status,
        "checks": checks,
        "duration_seconds": round(time.time() - started, 3),
        "browser_profile_dir": str(_BROWSER_PROFILE_DIR),
        "browser_cache_dir": str(_PLAYWRIGHT_BROWSER_CACHE),
        "auth_lock_present": bool(lock_status.get("present")),
        "auth_lock": lock_status,
    }
    log.info("[AUTH] Diagnose completed: %s - %s", status, message)
    return result


# =============================================================================
# PUBLIC API - dùng bởi historical_pulling / live_fetching
# =============================================================================


def refresh_mid_run(tv, lg: logging.Logger | None = None) -> bool:
    """
    Gọi khi phát hiện nhiều failure liên tiếp trong pipeline/gap_fill.
    Hàm sẽ thử lấy token mới và cập nhật tv.token cho connection đang chạy.
    Trả về True nếu lấy được token hợp lệ, không phải guest.
    """
    log = lg or _logger
    log.warning("[AUTH] Mid-run token refresh triggered...")

    # Reset để buộc resolve lại từ đầu, tránh dùng tiếp token cũ trong bộ nhớ.
    global _auth_token
    with _auth_lock:
        _auth_token = GUEST_TOKEN

    new_token, source = _bootstrap_credentials_coordinated(log)
    if new_token == GUEST_TOKEN:
        new_token, source = _resolve_auth_token(log)

    _update_global_token(new_token)

    if new_token != GUEST_TOKEN:
        tv.token = new_token
        safe_source = safe_auth_source_label(source)
        log.info("[AUTH] Mid-run refresh OK (source: %s) - updated tv.token.", safe_source)
        notify_auth_event(
            severity="INFO",
            title="TradingView session refreshed during the run",
            summary="The system refreshed TradingView access while a job was still running.",
            current_state={"source": safe_source, "access": "confirmed session"},
            data_result="The current job can continue using renewed TradingView access.",
            health_risk="Low. Automatic login recovery worked.",
            recommended_action="No action needed unless this repeats very frequently.",
            trace={"cache": "runtime/cache/tv_token_cache.json"},
            result="refreshing",
        )
        return True
    else:
        log.error("[AUTH] Mid-run refresh FAILED - continuing with guest token.")
        notify_auth_event(
            severity="ERROR",
            title="TradingView session refresh failed during the run",
            summary="The system tried to renew TradingView access but fell back to limited access.",
            current_state={"access": "guest / limited"},
            data_result="The running job may receive fewer candles or miss premium symbols.",
            health_risk="High if this happens during market hours or historical backfill.",
            reason="No valid TradingView token could be obtained.",
            recommended_action="Check TradingView credentials and browser login, then rerun the affected job if data looks incomplete.",
            trace={"cache": "runtime/cache/tv_token_cache.json"},
            result="failed",
        )
        return False


# =============================================================================
# HÀM HỖ TRỢ NỘI BỘ
# =============================================================================


def _get_http_session() -> requests.Session:
    """Trả về HTTP session dùng chung cho các bước refresh auth TradingView.

    Mặc định an toàn: TLS verification đi qua OS trust store nhờ `truststore`
    được inject lúc import. Cách này nhận các root CA của proxy/AV inspection
    trong Windows store, kể cả khi certifi không có các CA đó.
    """
    global _http_session
    if _http_session is None:
        with _http_session_lock:
            if _http_session is None:
                _http_session = requests.Session()
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
    HTTP 4xx khác (400/401/403/404) -> không retry vì đây là lỗi từ client.
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
                        wait = min(base_delay * (2**attempt), max_delay)
                    wait += random.uniform(0.5, 2.0)
                    _logger.warning(
                        "[HTTP] 429 Too Many Requests - retry %d/%d in %.1fs.",
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    last_exc = requests.HTTPError(
                        f"429 Too Many Requests (attempt {attempt + 1})", response=resp
                    )
                    continue
                raise requests.HTTPError(
                    f"429 Too Many Requests - retry limit reached after {max_retries} attempts ({url})",
                    response=resp,
                )

            if resp.status_code >= 500:
                if attempt < max_retries:
                    wait = min(base_delay * (2**attempt), max_delay) + random.uniform(0.5, 2.0)
                    _logger.warning(
                        "[HTTP] %d Server Error - retry %d/%d in %.1fs.",
                        resp.status_code,
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    last_exc = requests.HTTPError(
                        f"{resp.status_code} Server Error (attempt {attempt + 1})", response=resp
                    )
                    continue
                raise requests.HTTPError(
                    f"{resp.status_code} Server Error - retry limit reached after {max_retries} attempts ({url})",
                    response=resp,
                )

            raise requests.HTTPError(
                f"HTTP {resp.status_code} Client Error - not retrying ({url})", response=resp
            )

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = min(base_delay * (2**attempt), max_delay) + random.uniform(0.5, 2.0)
                _logger.warning(
                    "[HTTP] Network error %s - retry %d/%d in %.1fs.",
                    type(exc).__name__,
                    attempt + 1,
                    max_retries,
                    wait,
                )
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


def _auth_cooldown_blocks_refresh(
    lg: logging.Logger | None = None, *, context: str = "auth"
) -> bool:
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


def tradingview_http_connectivity_preflight(
    lg: logging.Logger | None = None,
) -> tuple[bool, str]:
    """Probe HTTPS TradingView thật nhanh trước các nhánh refresh đắt hơn.

    Token cache còn hợp lệ vẫn được chấp nhận mà không cần check này.
    Mục đích là tránh mất nhiều phút vào HTTP retry hoặc Chromium timeout
    khi TradingView tạm thời không truy cập được.
    """
    log = lg or _logger
    if not AUTH_CONNECTIVITY_PREFLIGHT:
        return True, "disabled"

    headers = {
        "User-Agent": _PLAYWRIGHT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = _get_http_session().get(
            "https://www.tradingview.com/",
            headers=headers,
            timeout=(
                float(AUTH_CONNECTIVITY_CONNECT_TIMEOUT_SEC),
                float(AUTH_CONNECTIVITY_READ_TIMEOUT_SEC),
            ),
            allow_redirects=True,
        )
        if resp.status_code < 500:
            return True, f"HTTP {resp.status_code} -> {resp.url}"
        detail = f"HTTP {resp.status_code} -> {resp.url}"
    except Exception as exc:  # noqa: BLE001 - report any network stack failure.
        detail = f"{type(exc).__name__}: {exc}"

    _set_auth_cooldown(
        AUTH_TRANSIENT_COOLDOWN_SEC,
        "TradingView connectivity preflight failure",
        log,
    )
    log.warning("[AUTH] TradingView connectivity preflight failed: %s", detail)
    return False, detail[:300]


def _try_acquire_auth_refresh_lock(lg: logging.Logger | None = None) -> bool:
    """Tạo file lock nhỏ để mỗi lần chỉ một OS process renew auth TradingView."""
    log = lg or _logger
    _AUTH_REFRESH_LOCK.parent.mkdir(parents=True, exist_ok=True)

    def _create() -> bool:
        fd = os.open(str(_AUTH_REFRESH_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        payload = {
            "pid": os.getpid(),
            "thread": threading.get_ident(),
            "created_at": time.time(),
        }
        try:
            os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    try:
        return _create()
    except FileExistsError:
        if cleanup_abandoned_auth_refresh_lock(log):
            try:
                return _create()
            except FileExistsError:
                return False

        status = auth_refresh_lock_status()
        age = float(status.get("age_seconds") or 0.0)
        if age < AUTH_REFRESH_LOCK_STALE_SEC:
            return False
        try:
            _AUTH_REFRESH_LOCK.unlink()
            log.warning("[AUTH] Removed stale auth refresh lock (age=%.0fs).", age)
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.debug("[AUTH] Could not remove stale auth refresh lock: %s", exc)
            return False
        try:
            return _create()
        except FileExistsError:
            return False


def _release_auth_refresh_lock() -> None:
    try:
        _AUTH_REFRESH_LOCK.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        _logger.debug("[AUTH] Could not release auth refresh lock: %s", exc)


@contextmanager
def _auth_refresh_process_lock(lg: logging.Logger | None = None):
    acquired = _try_acquire_auth_refresh_lock(lg)
    try:
        yield acquired
    finally:
        if acquired:
            _release_auth_refresh_lock()


def _load_reusable_cached_token(
    log: logging.Logger,
    *,
    min_remaining_sec: int = 60,
) -> tuple[str, str]:
    """Trả về token trong runtime cache nếu còn dùng được, không lộ giá trị bí mật."""
    global _tv_cookie
    cache = _load_token_cache()
    cached_token = str(cache.get("TV_AUTH_TOKEN") or "")
    cached_cookie = str(cache.get("TV_COOKIE") or "")
    if cached_cookie:
        _tv_cookie = cached_cookie
    if cached_token and cached_token != GUEST_TOKEN:
        if _is_refreshed_token_usable(
            cached_token,
            "runtime cache",
            log,
            min_remaining_sec=min_remaining_sec,
        ):
            return cached_token, "cached_token"
    return "", ""


def _fetch_auth_token_from_credentials(username: str, password: str) -> tuple[str, str]:
    """
    Đăng nhập TradingView bằng username/password để lấy auth token và cookie mới.
    Mô phỏng hành vi trình duyệt Chrome bằng HTTP POST form.

    Cả token và cookie đều được extract rồi trả về để caller lưu vào cache.

    Trả về (token, cookie_str) hoặc (GUEST_TOKEN, "") nếu thất bại.
    """
    try:
        r = _http_request_with_retry(
            "POST",
            "https://www.tradingview.com/accounts/signin/",
            data={"username": username, "password": password, "remember": "on"},
            headers={
                "Referer": "https://www.tradingview.com",
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


def _resolve_auth_token(
    lg: logging.Logger | None = None,
    *,
    use_process_lock: bool = True,
) -> tuple[str, str]:
    """
    Hàm nội bộ: thử lần lượt từng lớp xác thực, trả về (token, tên_phương_thức).

    Đây là "dispatcher": không tự xác thực mà gọi các hàm chuyên trách.
    Thứ tự thử ưu tiên nguồn nhanh và ít tốn tài nguyên trước.

    Lớp 0  : runtime cache runtime/cache/tv_token_cache.json
    Lớp 1  : token tĩnh từ .env
    Lớp 1.5: làm mới qua HTTP GET + cookie
    Lớp 2  : làm mới từ persistent browser profile
    Lớp 2.5: đăng nhập username/password qua HTTP POST
    Lớp 3  : headless Chromium với cookie hiện có
    Lớp 4  : headless Chromium đăng nhập mới, nếu được bật
    Lớp cuối: guest token với dữ liệu có thể bị giới hạn
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

    if use_process_lock:
        with _auth_refresh_process_lock(log) as acquired:
            if not acquired:
                cached_token, source = _load_reusable_cached_token(log, min_remaining_sec=60)
                if cached_token:
                    log.info(
                        "[AUTH] Another process is refreshing credentials; using %s.",
                        source,
                    )
                    return cached_token, source
                log.warning(
                    "[AUTH] Another process is refreshing credentials; no usable token is available yet."
                )
                return GUEST_TOKEN, "auth_refresh_in_progress"

            cached_token, source = _load_reusable_cached_token(
                log, min_remaining_sec=STARTUP_MIN_TOKEN_TTL_SEC
            )
            if cached_token:
                log.info("[AUTH] Using cached token refreshed by another process.")
                return cached_token, source
            return _resolve_auth_token_refresh_paths_guarded(log)

    return _resolve_auth_token_refresh_paths_guarded(log)


def _resolve_auth_token_refresh_paths(log: logging.Logger) -> tuple[str, str]:
    """Chạy các nhánh refresh qua mạng/browser; caller đang giữ process-level auth lock."""
    global _tv_cookie

    # LỚP 1.5: Làm mới qua session cookie.
    current_cookie = _tv_cookie or TV_COOKIE
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie, log)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, current_cookie):
                return token, "session_refresh"

    # LỚP 2: Làm mới từ persistent browser profile.
    token, new_cookie = _refresh_from_browser_profile(log)
    if token != GUEST_TOKEN:
        if _save_credentials_to_env(token, new_cookie or current_cookie):
            return token, "browser_profile"

    if TV_USERNAME and TV_PASSWORD:
        # LỚP 2.5: username/password - lấy token + cookie mới từ response HTTP POST
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "username/password"

    # LỚP 3: Headless Chromium với cookie hiện có
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "headless_chromium"

    # LỚP 4: Headless Chromium đăng nhập từ đầu, không cần cookie cũ.
    # Dùng khi cookie đã hết hạn hoàn toàn và cấu hình cho phép full login tự động.
    if TV_USERNAME and TV_PASSWORD and AUTH_HEADLESS_FRESH_LOGIN_ENABLED:
        log.info("[AUTH] Cookie expired - trying headless fresh login...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie):
                return token, "headless_fresh_login"
    elif TV_USERNAME and TV_PASSWORD:
        log.info(
            "[AUTH] Skipping headless fresh login because TV_AUTH_HEADLESS_FRESH_LOGIN is disabled. "
            "For Google/SSO accounts, refresh TV_AUTH_TOKEN and TV_COOKIE from an interactive browser."
        )

    # LỚP CUỐI: Guest
    log.warning("[AUTH] Falling back to guest token - Premium data may be unavailable.")
    notify_auth_event(
        severity="WARNING",
        title="TradingView login could not be verified",
        summary="TradingView access could not be confirmed, so the system will use limited access for now.",
        current_state={"access": "guest / limited"},
        data_result="Live and historical data may still work, but coverage can be reduced.",
        health_risk="Medium. Premium symbols or deeper historical ranges may be unavailable.",
        reason="All configured refresh paths failed or were unavailable.",
        recommended_action="Refresh TradingView login if candles stop arriving or historical pulls become shallow.",
        trace={"cache": "runtime/cache/tv_token_cache.json"},
        result="warning",
    )
    return GUEST_TOKEN, "guest"


def _resolve_auth_token_refresh_paths_guarded(log: logging.Logger) -> tuple[str, str]:
    network_ok, network_detail = tradingview_http_connectivity_preflight(log)
    if not network_ok:
        log.warning(
            "[AUTH] Skipping TradingView auth refresh paths because connectivity is unavailable: %s",
            network_detail,
        )
        return GUEST_TOKEN, "network_unavailable"
    return _resolve_auth_token_refresh_paths(log)


def _renew_auth_token(lg: logging.Logger | None = None) -> None:
    """
    Gia hạn token khi TradingView báo lỗi xác thực giữa chừng.
    Dùng lock để đảm bảo chỉ một thread thực hiện gia hạn tại một thời điểm.
    """
    global _auth_token
    log = lg or _logger
    with _auth_lock:
        if _auth_token != GUEST_TOKEN:
            return  # Thread khác đã gia hạn xong.

        log.warning("[AUTH] Token refresh required - bootstrapping credentials.")
        notify_auth_event(
            severity="INFO",
            title="TradingView session is expiring",
            summary="The current TradingView session is near expiration. The system is refreshing it automatically.",
            current_state={"access": "refreshing"},
            data_result="No data loss is expected while refresh is in progress.",
            health_risk="Low unless refresh fails in the next message.",
            recommended_action="No action needed now.",
            trace={"cache": "runtime/cache/tv_token_cache.json"},
            result="refreshing",
        )

        new_token, source = _bootstrap_credentials(log)
        if new_token == GUEST_TOKEN:
            new_token, source = _resolve_auth_token(log)

        _auth_token = new_token

        if new_token != GUEST_TOKEN:
            safe_source = safe_auth_source_label(source)
            log.info("[AUTH] Token renewed successfully (source: %s).", safe_source)
            notify_auth_event(
                severity="INFO",
                title="TradingView session refreshed during the run",
                summary="TradingView access was renewed successfully.",
                current_state={"source": safe_source, "access": "confirmed session"},
                data_result="Live and historical jobs can continue with renewed access.",
                health_risk="Low. Automatic refresh worked.",
                recommended_action="No action needed.",
                trace={"cache": "runtime/cache/tv_token_cache.json"},
                result="refreshing",
            )
        else:
            log.error("[AUTH] Token renewal failed - all groups will use guest access.")
            notify_auth_event(
                severity="ERROR",
                title="TradingView session refresh failed",
                summary="TradingView token renewal failed and the system is using limited access.",
                current_state={"access": "guest / limited"},
                data_result="Live or historical jobs may receive incomplete data.",
                health_risk="High if the market is open or backfill is running.",
                reason="Automatic token renewal could not obtain a valid token.",
                recommended_action="Check TradingView credentials, browser profile, and network access, then rerun affected jobs.",
                trace={"cache": "runtime/cache/tv_token_cache.json"},
                result="failed",
            )


def _renew_auth_token_coordinated(
    lg: logging.Logger | None = None,
    *,
    force: bool = False,
    fallback_token: str | None = None,
) -> None:
    """Renew auth TradingView có phối hợp, tránh nhiều OS process refresh cùng lúc."""
    global _auth_token
    log = lg or _logger
    with _auth_lock:
        current_token = fallback_token or (_auth_token if _auth_token != GUEST_TOKEN else "")
        if not force and _auth_token != GUEST_TOKEN:
            return

    if force and current_token:
        log.warning("[AUTH] Proactive token refresh requested - bootstrapping credentials.")
    else:
        log.warning("[AUTH] Token refresh required - bootstrapping credentials.")
        _tg_alert(
            "INFO",
            "TradingView session is expiring; refreshing automatically",
        )

    with _auth_refresh_process_lock(log) as acquired:
        if not acquired:
            cached_token, source = _load_reusable_cached_token(log, min_remaining_sec=60)
            if cached_token:
                with _auth_lock:
                    _auth_token = cached_token
                log.info(
                    "[AUTH] Another process is refreshing credentials; using %s from runtime cache.",
                    source,
                )
                return
            if _is_refreshed_token_usable(
                current_token,
                "existing token",
                log,
                min_remaining_sec=60,
            ):
                with _auth_lock:
                    _auth_token = current_token
                log.warning(
                    "[AUTH] Another process is refreshing credentials; continuing with existing "
                    "token (ttl=%.0fs).",
                    _jwt_expires_in(current_token),
                )
                return
            log.warning(
                "[AUTH] Another process is refreshing credentials, but no usable fallback token is available."
            )
            return

        new_token, source = _bootstrap_credentials(log)
        if new_token == GUEST_TOKEN:
            new_token, source = _resolve_auth_token(log, use_process_lock=False)

    if new_token != GUEST_TOKEN:
        with _auth_lock:
            _auth_token = new_token
        if force:
            log.info("[AUTH] Proactive token refresh completed (source: %s).", safe_auth_source_label(source))
        else:
            safe_source = safe_auth_source_label(source)
            log.info("[AUTH] Token renewed successfully (source: %s).", safe_source)
            _tg_alert(
                "INFO",
                "TradingView session refreshed during the run\n"
                f"Source: {safe_source}\n"
                "Meaning: live and historical jobs can continue with renewed access.",
            )
        return

    if _is_refreshed_token_usable(
        current_token,
        "existing token",
        log,
        min_remaining_sec=60,
    ):
        with _auth_lock:
            _auth_token = current_token
        log.warning(
            "[AUTH] Token refresh failed; continuing with existing token (ttl=%.0fs).",
            _jwt_expires_in(current_token),
        )
        return

    with _auth_lock:
        _auth_token = GUEST_TOKEN
    log.error("[AUTH] Token renewal failed - all groups will use guest access.")
    _tg_alert(
        "ERROR",
        "TradingView session refresh failed\n"
        "Meaning: the system is using limited TradingView access.\n"
        "Suggested action: check TradingView credentials and browser login.",
    )


def _jwt_expires_in(token: str) -> float:
    """
    Giải mã JWT để lấy trường 'exp', rồi trả về số giây còn lại.
    Trả về -1.0 nếu không thể giải mã, ví dụ token không phải JWT.
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


def _token_needs_proactive_refresh(
    token: str, *, threshold_sec: int = TOKEN_PROACTIVE_REFRESH_SEC
) -> bool:
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
    Từ chối token đã hết hạn hoặc gần hết hạn lúc startup.

    Việc này tránh cho historical/live job báo kết nối TradingView đã sẵn sàng
    trong khi cached/static token sẽ lỗi ngay ở lần pull đầu tiên.
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
    Chủ động làm mới token nếu sắp hết hạn.
    Gọi trước mỗi batch WebSocket để tránh bắt đầu batch bằng token gần hết hạn.
    """
    global _last_proactive_refresh_attempt_ts
    log = lg or _logger
    need_refresh = False
    fallback_token = ""
    with _auth_lock:
        remaining = _jwt_expires_in(_auth_token)
        if remaining != -1.0 and remaining < TOKEN_PROACTIVE_REFRESH_SEC:
            now = time.time()
            if (
                TOKEN_PROACTIVE_RETRY_SEC > 0
                and now - _last_proactive_refresh_attempt_ts < TOKEN_PROACTIVE_RETRY_SEC
                and remaining > STARTUP_MIN_TOKEN_TTL_SEC
            ):
                log.debug(
                    "[AUTH] Token ttl=%.0fs; proactive refresh retry suppressed.",
                    remaining,
                )
                return
            log.info("[AUTH] Token expiring in %.0fs - proactive refresh.", remaining)
            fallback_token = _auth_token
            _last_proactive_refresh_attempt_ts = now
            need_refresh = True
    if need_refresh:
        _renew_auth_token_coordinated(log, force=True, fallback_token=fallback_token)


def _probe_cookie_session(cookie_str: str, lg: logging.Logger | None = None) -> _CookieProbeResult:
    log = lg or _logger
    if not cookie_str:
        return _CookieProbeResult("missing", reason="cookie is empty")
    try:
        resp = _http_request_with_retry(
            "GET",
            "https://www.tradingview.com/",
            headers={
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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
            return _CookieProbeResult(
                "expired", status_code=resp.status_code, reason="sign-in redirect"
            )
        m = re.search(r'"auth_token"\s*:\s*"(eyJ[A-Za-z0-9._-]+)"', resp.text)
        if m:
            token = m.group(1)
            log.info("[AUTH] Token refreshed via session cookie (HTTP GET).")
            return _CookieProbeResult("ok", token=token, status_code=resp.status_code)
        log.info(
            "[AUTH] Cookie probe inconclusive, not expired: HTTP %d but auth_token was not "
            "visible in HTML. TradingView may render this page with browser-side JavaScript.",
            resp.status_code,
        )
        return _CookieProbeResult(
            "inconclusive", status_code=resp.status_code, reason="token not in HTML"
        )
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
    Lớp 1.5 - Làm mới auth_token bằng HTTP GET đến homepage TradingView.
    Khi sessionid cookie còn hiệu lực, TradingView có thể nhúng auth_token vào HTML.
    Trả về token mới, hoặc GUEST_TOKEN nếu thất bại.

    Nhận lg để log vào logger của caller khi có truyền vào.
    KHÔNG log giá trị cookie/token, chỉ log lý do thất bại.
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
        _set_auth_cooldown(
            AUTH_TRANSIENT_COOLDOWN_SEC, "TradingView transient auth probe failure", log
        )
    return GUEST_TOKEN


def _token_status_summary(token: str, source: str = "") -> dict:
    if not token:
        return {"present": False, "source": source, "state": "missing", "seconds_remaining": None}
    if token == GUEST_TOKEN:
        return {"present": True, "source": source, "state": "guest", "seconds_remaining": None}
    remaining = _jwt_expires_in(token)
    if remaining == -1.0:
        return {"present": True, "source": source, "state": "unknown", "seconds_remaining": None}
    if remaining <= 0:
        return {
            "present": True,
            "source": source,
            "state": "expired",
            "seconds_remaining": int(remaining),
        }
    state = "expiring_soon" if remaining < TOKEN_PROACTIVE_REFRESH_SEC else "valid"
    return {
        "present": True,
        "source": source,
        "state": state,
        "seconds_remaining": int(remaining),
    }


@contextmanager
def _playwright_runtime_env():
    """Dùng browser cache Playwright do app quản lý cho mọi lần chạy auth browser."""
    _PLAYWRIGHT_BROWSER_CACHE.mkdir(parents=True, exist_ok=True)
    old_browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    old_node_options = os.environ.get("NODE_OPTIONS")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_PLAYWRIGHT_BROWSER_CACHE)
    if not old_node_options:
        os.environ["NODE_OPTIONS"] = "--use-system-ca"
    elif "--use-system-ca" not in old_node_options:
        os.environ["NODE_OPTIONS"] = f"{old_node_options} --use-system-ca"
    try:
        yield
    finally:
        if old_browsers is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = old_browsers
        if old_node_options is None:
            os.environ.pop("NODE_OPTIONS", None)
        else:
            os.environ["NODE_OPTIONS"] = old_node_options


def _activate_playwright_runtime_env() -> None:
    _PLAYWRIGHT_BROWSER_CACHE.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_PLAYWRIGHT_BROWSER_CACHE)
    node_options = os.environ.get("NODE_OPTIONS", "")
    if "--use-system-ca" not in node_options:
        os.environ["NODE_OPTIONS"] = (
            "--use-system-ca" if not node_options else f"{node_options} --use-system-ca"
        )


def _parse_cookie_list(raw: str) -> list[dict]:
    result = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        result.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".tradingview.com",
                "path": "/",
                "secure": True,
            }
        )
    return result


def _cookie_header_from_list(cookies: list[dict]) -> str:
    parts: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        domain = str(cookie.get("domain") or "")
        if not name or not value:
            continue
        if domain and "tradingview.com" not in domain:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def _extract_token_from_page(page, cookies: list[dict] | None = None) -> str:
    token = ""
    try:
        token = (
            page.evaluate(
                """() => {
                const tokenFromText = (text) => {
                    if (!text) return "";
                    const patterns = [
                        /"auth_token"\\s*:\\s*"(eyJ[^"]+)"/,
                        /(?:^|;\\s*)auth_token=(eyJ[^;]+)/
                    ];
                    for (const pattern of patterns) {
                        const m = String(text).match(pattern);
                        if (m) return decodeURIComponent(m[1]);
                    }
                    return "";
                };
                try { const t = tokenFromText(document.documentElement.innerHTML); if (t) return t; } catch(e) {}
                try { const t = tokenFromText(document.cookie); if (t) return t; } catch(e) {}
                try { if (window.__tv_initData && window.__tv_initData.auth_token) return window.__tv_initData.auth_token; } catch(e) {}
                try { if (window.initData && window.initData.auth_token) return window.initData.auth_token; } catch(e) {}
                try { const t = localStorage.getItem("auth_token") || sessionStorage.getItem("auth_token"); if (t) return t; } catch(e) {}
                return "";
            }"""
            )
            or ""
        )
    except Exception:
        token = ""
    if token:
        return token
    for cookie in cookies or []:
        if str(cookie.get("name") or "") == "auth_token":
            value = str(cookie.get("value") or "")
            if value.startswith("eyJ"):
                return value
    return ""


def _new_tv_context(browser_or_pw, *, headless: bool, persistent: bool, cookie_str: str = ""):
    kwargs = {
        "user_agent": _PLAYWRIGHT_USER_AGENT,
        "locale": "en-US",
    }
    if persistent:
        _BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return browser_or_pw.chromium.launch_persistent_context(
            str(_BROWSER_PROFILE_DIR),
            headless=headless,
            args=["--no-sandbox"],
            **kwargs,
        )
    browser = browser_or_pw.chromium.launch(headless=headless, args=["--no-sandbox"])
    ctx = browser.new_context(**kwargs)
    if cookie_str:
        ctx.add_cookies(_parse_cookie_list(cookie_str))
    return ctx


def _refresh_from_browser_profile(lg: logging.Logger | None = None) -> tuple[str, str]:
    log = lg or _logger
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log.warning("[AUTH] Playwright is not installed - browser-profile refresh skipped.")
        return GUEST_TOKEN, ""

    if not _BROWSER_PROFILE_DIR.exists():
        log.info("[AUTH] Browser profile does not exist yet; run interactive TV auth setup first.")
        return GUEST_TOKEN, ""

    try:
        with _playwright_runtime_env():
            with sync_playwright() as pw:
                ctx = _new_tv_context(pw, headless=True, persistent=True)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                _apply_playwright_stealth(page)
                page.goto("https://www.tradingview.com/", wait_until="networkidle", timeout=45_000)
                cookies = ctx.cookies()
                token = _extract_token_from_page(page, cookies)
                cookie_out = _cookie_header_from_list(cookies)
                ctx.close()
        if token and _save_credentials_to_env(token, cookie_out):
            log.info("[AUTH] Token refreshed from persistent browser profile.")
            return token, cookie_out
        if cookie_out:
            _save_token_cache("", cookie_out)
        log.warning("[AUTH] Browser profile loaded but no usable TradingView token was found.")
        return GUEST_TOKEN, cookie_out
    except Exception as exc:
        log.warning("[AUTH] Browser-profile refresh failed: %s", exc)
        return GUEST_TOKEN, ""


def _interactive_browser_login(
    *,
    timeout_sec: int = 600,
    lg: logging.Logger | None = None,
) -> tuple[str, str]:
    log = lg or _logger
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log.warning("[AUTH] Playwright is not installed - cannot open interactive auth browser.")
        return GUEST_TOKEN, ""

    deadline = time.time() + max(60, int(timeout_sec))
    try:
        with _playwright_runtime_env():
            with sync_playwright() as pw:
                ctx = _new_tv_context(pw, headless=False, persistent=True)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                _apply_playwright_stealth(page)
                page.goto(
                    "https://www.tradingview.com/accounts/signin/",
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                log.info(
                    "[AUTH] Interactive TradingView browser opened. Complete login; waiting up to %ss.",
                    int(timeout_sec),
                )
                while time.time() < deadline:
                    try:
                        page.wait_for_timeout(2_000)
                        cookies = ctx.cookies()
                        token = _extract_token_from_page(page, cookies)
                        cookie_out = _cookie_header_from_list(cookies)
                        if token and _is_refreshed_token_usable(
                            token, "interactive browser", log, min_remaining_sec=60
                        ):
                            _save_credentials_to_env(token, cookie_out)
                            ctx.close()
                            log.info("[AUTH] Interactive browser login captured token and cookie.")
                            return token, "interactive_browser_session"
                    except Exception as exc:
                        log.debug("[AUTH] Still waiting for interactive login: %s", exc)
                try:
                    cookies = ctx.cookies()
                    cookie_out = _cookie_header_from_list(cookies)
                    if cookie_out:
                        _save_token_cache("", cookie_out)
                finally:
                    ctx.close()
        log.warning("[AUTH] Interactive browser login timed out before a usable token was found.")
        return GUEST_TOKEN, ""
    except Exception as exc:
        log.warning("[AUTH] Interactive browser login failed: %s", exc)
        return GUEST_TOKEN, ""


def _headless_refresh(cookie_str: str) -> tuple[str, str]:
    """
    Lớp 3 - Dùng headless Chromium (Playwright) để load TradingView và extract credentials.
    Đáng tin cậy hơn HTTP GET vì Playwright thực thi JavaScript đầy đủ.
    Hỗ trợ các phiên đã đăng nhập bằng Google/social login khi cookie vẫn còn hiệu lực.

    Trả về (token, cookie_str_mới) hoặc (GUEST_TOKEN, "") nếu thất bại.

    Cài đặt, chỉ cần làm một lần:
        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        _logger.warning("[AUTH] Playwright is not installed - skipping headless refresh.")
        _logger.warning(
            "[AUTH] Install with: pip install playwright && playwright install chromium"
        )
        return GUEST_TOKEN, ""
    _activate_playwright_runtime_env()


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

            token: str = (
                page.evaluate(
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
                )
                or ""
            )

            all_cookies = ctx.cookies()
            if not token:
                token = _extract_token_from_page(page, all_cookies)
            cookie_out = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)
            browser.close()

            if token:
                _logger.info("[AUTH] Token refreshed via headless Chromium.")
                return token, cookie_out
            _logger.warning(
                "[AUTH] Headless load finished but auth_token was not found - session may be expired."
            )
            return GUEST_TOKEN, cookie_out

    except Exception as exc:
        _logger.warning("[AUTH] Headless refresh failed: %s", exc)
        return GUEST_TOKEN, ""


def _headless_login_fresh(username: str, password: str) -> tuple[str, str]:
    """
    Lớp 4 - Đăng nhập TradingView từ đầu bằng Playwright, không cần cookie cũ.

    Khác với _headless_refresh(), hàm này không dùng cookie có sẵn:
    - Điều hướng đến trang /signin/, điền username/password và submit form.
    - Phù hợp khi cookie đã hết hạn hoàn toàn.
    - Nếu thành công, hệ thống có token + cookie mới mà không cần copy thủ công từ trình duyệt.

    Giới hạn: chỉ hoạt động với tài khoản TradingView gốc (email/password).
    Không hỗ trợ Google login/SSO; các trường hợp đó cần browser profile hoặc cookie còn hiệu lực.

    Trả về (token, cookie_str) hoặc (GUEST_TOKEN, "") nếu thất bại.

    Yêu cầu: pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        _logger.warning("[AUTH] Playwright is not installed - skipping headless fresh login.")
        _logger.warning(
            "[AUTH] Install with: pip install playwright && playwright install chromium"
        )
        return GUEST_TOKEN, ""
    _activate_playwright_runtime_env()

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

            # Mở trang đăng nhập TradingView.
            page.goto(
                "https://www.tradingview.com/accounts/signin/", wait_until="domcontentloaded", timeout=45_000
            )

            # Điền email, thử các selector phổ biến theo thứ tự.
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

            # Điền password.
            page.fill('input[type="password"]', password)

            # Gửi form bằng nút submit hoặc phím Enter.
            try:
                page.click('button[type="submit"]')
            except Exception:
                page.keyboard.press("Enter")

            # Chờ ngắn để CAPTCHA hoặc 2FA có thể hiện ra.
            try:
                page.wait_for_timeout(2_000)
            except Exception:
                pass

            # --- CAPTCHA auto-solve, chỉ khi TV_CAPTCHA_API_KEY được cấu hình ---
            try:
                sitekey: str = (
                    page.evaluate(
                        """() => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : null;
                    }"""
                    )
                    or ""
                )
                if sitekey:
                    captcha_token = _solve_captcha_hcaptcha(
                        sitekey, "https://www.tradingview.com/accounts/signin/"
                    )
                    if captcha_token:
                        _logger.info(
                            "[AUTH] hCaptcha detected - injecting solution from %s.",
                            TV_CAPTCHA_SERVICE or "capsolver",
                        )
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
                        _logger.warning(
                            "[AUTH] hCaptcha detected but solving failed - login may not complete."
                        )
            except Exception:
                pass  # Không thấy CAPTCHA trong lần thử này.

            # --- 2FA auto-fill, chỉ khi TV_2FA_SECRET được cấu hình ---
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
                pass  # Không thấy 2FA trong lần thử này.

            # Chờ chuyển về trang chủ sau khi đăng nhập thành công.
            try:
                page.wait_for_url("**/tradingview.com/**", timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass  # Tiếp tục extract dù timeout; token có thể đã có.

            # Extract auth token từ HTML hoặc JS globals.
            token: str = (
                page.evaluate(
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
                )
                or ""
            )

            all_cookies = ctx.cookies()
            if not token:
                token = _extract_token_from_page(page, all_cookies)
            cookie_out = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)
            browser.close()

            if token:
                _logger.info(
                    "[AUTH] Headless fresh login succeeded - new token and cookie received."
                )
                return token, cookie_out

            _logger.warning("[AUTH] Headless fresh login finished but auth_token was not found.")
            return GUEST_TOKEN, cookie_out

    except Exception as exc:
        _logger.warning("[AUTH] Headless fresh login failed: %s", exc)
        return GUEST_TOKEN, ""


# =============================================================================
# HÀM HỖ TRỢ VÒNG ĐỜI COOKIE
# =============================================================================


def _apply_playwright_stealth(page) -> None:
    """Áp dụng playwright-stealth nếu có cài, giúp giảm dấu hiệu bot fingerprinting."""
    try:
        from playwright_stealth import stealth_sync  # type: ignore

        stealth_sync(page)
    except ImportError:
        pass


def _get_totp_code() -> str | None:
    """Tạo mã TOTP từ TV_2FA_SECRET; trả về None nếu thiếu secret hoặc pyotp."""
    if not TV_2FA_SECRET:
        return None
    try:
        import pyotp  # type: ignore

        return pyotp.TOTP(TV_2FA_SECRET).now()
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
            _logger.warning("[AUTH] CapSolver: task creation failed - %s", r.text[:200])
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
                "key": TV_CAPTCHA_API_KEY,
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
                params={"key": TV_CAPTCHA_API_KEY, "action": "get", "id": task_id, "json": 1},
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
    """Chọn CapSolver hoặc 2Captcha theo TV_CAPTCHA_SERVICE; trả về None nếu đang tắt."""
    if not TV_CAPTCHA_API_KEY:
        return None
    service = (TV_CAPTCHA_SERVICE or "capsolver").lower()
    if service == "2captcha":
        return _solve_via_2captcha(sitekey, page_url)
    return _solve_via_capsolver(sitekey, page_url)


def _save_cookie_renewal_ts(ts: float) -> None:
    """Lưu timestamp lần gia hạn cookie gần nhất vào file token cache."""
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
    """Quản lý vòng đời cookie/token theo TTL cho public API của ws_live."""
    global \
        _last_cookie_check_ts, \
        _last_cookie_renewal_ts, \
        _cookie_probe_fail_streak, \
        _cookie_renewal_fail_streak, \
        _tv_cookie, \
        _auth_token

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
        COOKIE_PROBE_RETRY_INTERVAL_SEC
        if _cookie_probe_fail_streak > 0
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
        _set_auth_cooldown(
            AUTH_TRANSIENT_COOLDOWN_SEC, "TradingView transient cookie probe failure", log
        )
        return

    has_renewal_ts = _last_cookie_renewal_ts > 0
    age_since_renewal = now - _last_cookie_renewal_ts if has_renewal_ts else 0.0
    force_by_age = has_renewal_ts and age_since_renewal >= COOKIE_RENEWAL_INTERVAL_SEC
    need_renewal = False

    if probe.status == "expired":
        _cookie_probe_fail_streak += 1
        need_renewal = True
        log.warning(
            "[AUTH] Cookie probe confirmed expired session (streak=%d).", _cookie_probe_fail_streak
        )
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
                "[AUTH] Cookie probe inconclusive; token still healthy for %.0fs, so the "
                "saved session remains usable and browser renewal is not needed now.",
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
        notify_auth_event(
            severity="INFO",
            title="Refreshing TradingView browser session",
            summary="The system is refreshing the saved TradingView browser session before it becomes unreliable.",
            current_state={
                "cookie_probe": probe.status,
                "token_ttl_seconds": f"{token_remaining:.0f}",
                "session_age_hours": f"{age_since_renewal / 3600:.1f}",
            },
            data_result="No data result yet. This message means the refresh has started.",
            health_risk="Low unless the next message says the refresh failed.",
            reason="The current browser session looks old, uncertain, or was forced by age.",
            recommended_action="No action needed now.",
            trace={"cache": "runtime/cache/tv_token_cache.json"},
            result="refreshing",
        )

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
            notify_auth_event(
                severity="INFO",
                title="TradingView browser session refreshed successfully",
                summary="The saved TradingView browser session was refreshed.",
                current_state={"token": "updated" if token_updated else "unchanged", "browser_session": "updated"},
                data_result="Future automatic login refresh should be smoother.",
                health_risk="Low. Browser-based recovery is ready for later runs.",
                recommended_action="No action needed.",
                trace={"cache": "runtime/cache/tv_token_cache.json"},
                result="refreshing",
            )
        else:
            _cookie_renewal_fail_streak += 1
            log.error("[AUTH] Cookie renewal FAILED (attempt %d).", _cookie_renewal_fail_streak)
            notify_auth_event(
                severity="ERROR",
                title="TradingView browser session refresh failed",
                summary="The system could not refresh the saved TradingView browser session.",
                current_state={"attempt": _cookie_renewal_fail_streak, "access": "will retry later"},
                data_result="Current jobs may continue with existing token if it is still valid.",
                health_risk="Medium. If the token later expires, the system may fall back to limited access.",
                reason="Headless browser refresh did not return a usable cookie/session.",
                recommended_action="Check Playwright/Chromium installation, TradingView login state, and network access.",
                trace={"cache": "runtime/cache/tv_token_cache.json"},
                result="failed",
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
    tmp = _TOKEN_CACHE.with_name(f"{_TOKEN_CACHE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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
    if (
        token
        and token != GUEST_TOKEN
        and _is_refreshed_token_usable(token, "cache", _logger, min_remaining_sec=0)
    ):
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
    - In-memory: _auth_token và _tv_cookie, có hiệu lực ngay trong process.
    - File cache: runtime/cache/tv_token_cache.json, dùng lại được sau restart.
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


def _bootstrap_credentials_coordinated(lg: logging.Logger | None = None) -> tuple[str, str]:
    """Bootstrap credentials TradingView dưới process-level auth lock dùng chung."""
    log = lg or _logger
    with _auth_refresh_process_lock(log) as acquired:
        if not acquired:
            cached_token, source = _load_reusable_cached_token(log, min_remaining_sec=60)
            if cached_token:
                log.info(
                    "[AUTH] Another process refreshed credentials; using %s from runtime cache.",
                    source,
                )
                return cached_token, source
            log.warning(
                "[AUTH] Another process is refreshing credentials; bootstrap will not start a second refresh."
            )
            return GUEST_TOKEN, "auth_refresh_in_progress"

        cached_token, source = _load_reusable_cached_token(
            log, min_remaining_sec=STARTUP_MIN_TOKEN_TTL_SEC
        )
        if cached_token:
            log.info("[AUTH] Using cached token before bootstrap refresh.")
            return cached_token, source
        return _bootstrap_credentials(log)


def _bootstrap_credentials(lg: logging.Logger | None = None) -> tuple[str, str]:
    """
    Đảm bảo credentials hợp lệ trước khi khởi động.
    Gọi lúc startup khi token trống, hết hạn hoặc sắp hết hạn.

    Thứ tự thử trong hàm này:
        1. Làm mới token qua HTTP GET, dùng sessionid cookie hiện có.
        2. Làm mới token/cookie từ persistent browser profile.
        3. Làm mới qua headless Chromium với cookie hiện có.
        4. Đăng nhập username/password qua HTTP POST.
        5. Headless fresh login nếu TV_AUTH_HEADLESS_FRESH_LOGIN được bật.

    Trả về (token, tên_phương_thức).
    """
    global _tv_cookie
    log = lg or _logger
    log.info("[AUTH] Bootstrapping credentials...")

    if _auth_cooldown_blocks_refresh(log, context="bootstrap"):
        return GUEST_TOKEN, "cooldown"

    network_ok, network_detail = tradingview_http_connectivity_preflight(log)
    if not network_ok:
        log.warning(
            "[AUTH] Skipping credential bootstrap because TradingView connectivity is unavailable: %s",
            network_detail,
        )
        return GUEST_TOKEN, "network_unavailable"

    current_cookie = _tv_cookie or TV_COOKIE

    # Bước 1: Làm mới qua HTTP GET.
    if current_cookie:
        token = _refresh_token_via_cookie(current_cookie, log)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, current_cookie):
                return token, "session_refresh"
        log.info("[AUTH] HTTP cookie refresh failed - trying headless Chromium...")

    # Bước 2: Làm mới từ persistent browser profile.
    token, new_cookie = _refresh_from_browser_profile(log)
    if token != GUEST_TOKEN:
        if _save_credentials_to_env(token, new_cookie or current_cookie):
            return token, "browser_profile"

    # Bước 3: Headless Chromium với cookie hiện có.
    if current_cookie:
        token, new_cookie = _headless_refresh(current_cookie)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "headless_chromium"

    # Bước 4: username/password (HTTP POST) - lấy token + cookie mới.
    if TV_USERNAME and TV_PASSWORD:
        log.info("[AUTH] Trying username/password login (HTTP POST)...")
        token, new_cookie = _fetch_auth_token_from_credentials(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie or current_cookie):
                return token, "http_post_login"

    # Bước 5: Headless fresh login, không cần cookie cũ.
    if TV_USERNAME and TV_PASSWORD and AUTH_HEADLESS_FRESH_LOGIN_ENABLED:
        log.info("[AUTH] Trying headless fresh login (Playwright full login)...")
        token, new_cookie = _headless_login_fresh(TV_USERNAME, TV_PASSWORD)
        if token != GUEST_TOKEN:
            if _save_credentials_to_env(token, new_cookie):
                return token, "headless_fresh_login"
    elif TV_USERNAME and TV_PASSWORD:
        log.info(
            "[AUTH] Skipping headless fresh login because TV_AUTH_HEADLESS_FRESH_LOGIN is disabled. "
            "Refresh token/cookie manually for Google/SSO accounts."
        )

    log.warning("[AUTH] All bootstrap methods failed.")
    return GUEST_TOKEN, "guest"



