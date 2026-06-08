"""Local token cache for cTrader OAuth secrets.

The cache lives under data_provider/runtime/cache, which is already ignored by
git in this repository. Environment variables still win over cached values.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_provider.paths import CACHE_DIR, ensure_runtime_dirs

DEFAULT_TOKEN_CACHE = CACHE_DIR / "ctrader_ftmo_oauth.json"
UTC = timezone.utc

TOKEN_KEYS = {
    "accessToken",
    "refreshToken",
    "tokenType",
    "expiresIn",
    "errorCode",
    "description",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_token_cache(path: Path | str = DEFAULT_TOKEN_CACHE) -> dict[str, Any]:
    token_path = Path(path)
    if not token_path.exists():
        return {}
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid cTrader token cache JSON: {token_path}") from exc


def _expiry_from_payload(payload: dict[str, Any]) -> str | None:
    expires_in = payload.get("expiresIn")
    if not expires_in:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat()


def merge_token_payload(
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    account_id: int | None = None,
    trader_login: str | None = None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged["updated_at_utc"] = utc_now_iso()
    merged.setdefault("created_at_utc", merged["updated_at_utc"])

    for key in TOKEN_KEYS:
        if key in payload:
            merged[key] = payload[key]

    expires_at = _expiry_from_payload(payload)
    if expires_at:
        merged["expires_at_utc"] = expires_at

    optional_values = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "ctidTraderAccountId": account_id,
        "traderLogin": trader_login,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def save_token_cache(
    payload: dict[str, Any],
    path: Path | str = DEFAULT_TOKEN_CACHE,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    account_id: int | None = None,
    trader_login: str | None = None,
) -> Path:
    ensure_runtime_dirs()
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_token_cache(token_path)
    merged = merge_token_payload(
        payload,
        existing=existing,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        account_id=account_id,
        trader_login=trader_login,
    )
    token_path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token_path


def update_cached_account(
    account_id: int,
    trader_login: str | None = None,
    path: Path | str = DEFAULT_TOKEN_CACHE,
) -> Path:
    existing = load_token_cache(path)
    return save_token_cache(
        {},
        path=path,
        account_id=account_id,
        trader_login=trader_login,
        client_id=existing.get("client_id"),
        client_secret=existing.get("client_secret"),
        redirect_uri=existing.get("redirect_uri"),
        scope=existing.get("scope"),
    )


def token_status(path: Path | str = DEFAULT_TOKEN_CACHE) -> dict[str, Any]:
    token_path = Path(path)
    cache = load_token_cache(token_path)
    expires_at = cache.get("expires_at_utc")
    is_expired = None
    if expires_at:
        is_expired = datetime.fromisoformat(expires_at).astimezone(UTC) <= datetime.now(UTC)
    return {
        "path": str(token_path),
        "exists": token_path.exists(),
        "client_id_set": bool(cache.get("client_id")),
        "client_secret_set": bool(cache.get("client_secret")),
        "access_token_set": bool(cache.get("accessToken")),
        "refresh_token_set": bool(cache.get("refreshToken")),
        "account_id": cache.get("ctidTraderAccountId"),
        "trader_login": cache.get("traderLogin"),
        "redirect_uri": cache.get("redirect_uri"),
        "scope": cache.get("scope"),
        "expires_at_utc": expires_at,
        "is_expired": is_expired,
    }
