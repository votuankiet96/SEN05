"""TradingView authentication - public facade.

Historically this was a single ~2,500-line `auth.py`. It is now a small
package:

- `core.py` - the state machine (module-level token/cookie/session state,
  the resolve/renew/bootstrap fallback chain, HTTP request retry, cookie
  session probing, browser automation, cooldown/process-lock handling).
  Everything here is tightly coupled through shared mutable state (the
  same `_auth_token`/`_tv_cookie`/cooldown variables are read and written
  across nearly every function), so it stays together rather than being
  split further in this pass.
- `jwt_utils.py` - pure JWT decoding and token-usability checks; no shared
  state, `core` imports from here (not the other way around).
- `captcha.py` - hCaptcha solving for the headless-login fallback; also no
  shared state.

This module re-exports exactly the public names the previous single-file
`auth.py` exposed, so existing call sites
(`from core_engine.shared.tradingview import auth as tv_auth`, then
`tv_auth.get_current_token()` etc.) are unaffected by the split.
"""

from core_engine.shared.tradingview.auth.core import (
    AUTH_CONNECTIVITY_CONNECT_TIMEOUT_SEC,
    AUTH_CONNECTIVITY_PREFLIGHT,
    AUTH_CONNECTIVITY_READ_TIMEOUT_SEC,
    AUTH_HEADLESS_FRESH_LOGIN_ENABLED,
    AUTH_REFRESH_COOLDOWN_SEC,
    AUTH_REFRESH_LOCK_STALE_SEC,
    AUTH_TRANSIENT_COOLDOWN_SEC,
    COOKIE_CHECK_INTERVAL_SEC,
    COOKIE_PROBE_RETRY_INTERVAL_SEC,
    COOKIE_RENEWAL_INTERVAL_SEC,
    GUEST_TOKEN,
    HTTP_BASE_DELAY_SEC,
    HTTP_MAX_DELAY_SEC,
    HTTP_MAX_RETRIES,
    STARTUP_MIN_TOKEN_TTL_SEC,
    TOKEN_EXPIRY_KEYWORDS,
    TOKEN_PROACTIVE_REFRESH_SEC,
    TOKEN_PROACTIVE_RETRY_SEC,
    auth_refresh_lock_status,
    bootstrap,
    browser_profile_status,
    check_and_refresh,
    cleanup_abandoned_auth_refresh_lock,
    diagnose_connectivity,
    ensure_cookie_fresh,
    get_current_cookie,
    get_current_token,
    has_2fa_secret,
    interactive_browser_login,
    is_guest_mode,
    load_token_cache,
    refresh_mid_run,
    renew,
    resolve_auth_token,
    safe_auth_source_label,
    set_current_cookie,
    set_current_token,
    token_expires_in,
    tradingview_http_connectivity_preflight,
)

__all__ = [
    "AUTH_CONNECTIVITY_CONNECT_TIMEOUT_SEC",
    "AUTH_CONNECTIVITY_PREFLIGHT",
    "AUTH_CONNECTIVITY_READ_TIMEOUT_SEC",
    "AUTH_HEADLESS_FRESH_LOGIN_ENABLED",
    "AUTH_REFRESH_COOLDOWN_SEC",
    "AUTH_REFRESH_LOCK_STALE_SEC",
    "AUTH_TRANSIENT_COOLDOWN_SEC",
    "COOKIE_CHECK_INTERVAL_SEC",
    "COOKIE_PROBE_RETRY_INTERVAL_SEC",
    "COOKIE_RENEWAL_INTERVAL_SEC",
    "GUEST_TOKEN",
    "HTTP_BASE_DELAY_SEC",
    "HTTP_MAX_DELAY_SEC",
    "HTTP_MAX_RETRIES",
    "STARTUP_MIN_TOKEN_TTL_SEC",
    "TOKEN_EXPIRY_KEYWORDS",
    "TOKEN_PROACTIVE_REFRESH_SEC",
    "TOKEN_PROACTIVE_RETRY_SEC",
    "auth_refresh_lock_status",
    "bootstrap",
    "browser_profile_status",
    "check_and_refresh",
    "cleanup_abandoned_auth_refresh_lock",
    "diagnose_connectivity",
    "ensure_cookie_fresh",
    "get_current_cookie",
    "get_current_token",
    "has_2fa_secret",
    "interactive_browser_login",
    "is_guest_mode",
    "load_token_cache",
    "refresh_mid_run",
    "renew",
    "resolve_auth_token",
    "safe_auth_source_label",
    "set_current_cookie",
    "set_current_token",
    "token_expires_in",
    "tradingview_http_connectivity_preflight",
]
