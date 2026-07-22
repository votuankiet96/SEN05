"""JWT decoding and token-usability checks for TradingView auth.

Pure functions: they take a token string and return a verdict, they never
read or write the shared auth state in `core_engine.shared.tradingview.auth.core`.
`GUEST_TOKEN` and the token-timing constants live here (not in `core`)
because every function in this module needs them as default argument
values evaluated at import time, and `core` imports from here - not the
other way around - so this module cannot depend on anything in `core`.
"""

from __future__ import annotations

import json
import logging
import time

from core_engine.settings import env_int

# Chuỗi đặc biệt báo hiệu trạng thái chưa xác thực hoặc guest.
GUEST_TOKEN = "unauthorized_user_token"

STARTUP_MIN_TOKEN_TTL_SEC = 10 * 60

TOKEN_PROACTIVE_REFRESH_SEC = env_int(
    "TV_TOKEN_PROACTIVE_REFRESH_SEC", 15 * 60, minimum=0
)

TOKEN_PROACTIVE_RETRY_SEC = env_int(
    "TV_TOKEN_PROACTIVE_RETRY_SEC", 10 * 60, minimum=0
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
