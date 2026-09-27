"""cTrader OAuth: token exchange/refresh, local browser callback login,
and the on-disk token cache. Fail-closed — callers decide what to do when
a token is missing or expired; this module never invents a token.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

UTC = timezone.utc
AUTHORIZATION_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
TOKEN_KEYS = {"accessToken", "refreshToken", "tokenType", "expiresIn", "errorCode", "description"}


class CTraderAuthError(RuntimeError):
    """Raised when cTrader OAuth token exchange fails."""


class OAuthCallbackError(RuntimeError):
    """Raised when the local OAuth callback returns an error."""


# ---------------------------------------------------------------------------
# OAuth HTTP flows
# ---------------------------------------------------------------------------


def build_authorization_url(client_id: str, redirect_uri: str, scope: str = "accounts") -> str:
    params = urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "product": "web"})
    return f"{AUTHORIZATION_URL}?{params}"


def redact_authorization_url(auth_url: str) -> str:
    parsed = urlparse(auth_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "client_id" in query:
        query["client_id"] = ["<redacted>"]
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in query.items()})))


def _token_request(params: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        response = requests.get(TOKEN_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise CTraderAuthError(f"cTrader OAuth request failed: timeout after {timeout}s") from None
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CTraderAuthError(f"cTrader OAuth request failed: HTTP {status}") from None
    except requests.exceptions.RequestException as exc:
        raise CTraderAuthError(f"cTrader OAuth request failed: {exc.__class__.__name__}") from None
    try:
        payload = response.json()
    except ValueError as exc:
        raise CTraderAuthError("cTrader OAuth request failed: invalid JSON response") from exc
    error = payload.get("errorCode") or payload.get("error") or payload.get("message")
    if error:
        raise CTraderAuthError(f"cTrader OAuth failed: {error}")
    return payload


def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str, timeout: int = 20) -> dict[str, Any]:
    return _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
         "client_id": client_id, "client_secret": client_secret},
        timeout=timeout,
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str, timeout: int = 20) -> dict[str, Any]:
    return _token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token,
         "client_id": client_id, "client_secret": client_secret},
        timeout=timeout,
    )


def run_local_oauth_login(
    client_id: str, client_secret: str, redirect_uri: str, scope: str,
    timeout_seconds: int = 120, open_browser: bool = True,
) -> dict[str, Any]:
    """Run a localhost callback OAuth flow and exchange the returned code."""
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or not parsed.port:
        raise ValueError("oauth-login requires an http://localhost:<port>/... redirect URI")
    bind_host = "127.0.0.1" if parsed.hostname in {"localhost", "127.0.0.1", None, ""} else None
    if bind_host is None:
        raise ValueError("redirect URI must use localhost or 127.0.0.1 for oauth-login")
    callback_path = parsed.path or "/"
    result_queue: queue.Queue[dict[str, str]] = queue.Queue(maxsize=1)

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _fmt: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            request = urlparse(self.path)
            if request.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(request.query)
            payload = (
                {"error": query["error"][0], "description": query.get("description", [""])[0]}
                if "error" in query
                else {"code": query["code"][0]} if "code" in query
                else {"error": "missing_code", "description": "No code query parameter."}
            )
            try:
                result_queue.put_nowait(payload)
            except queue.Full:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h3>cTrader OAuth received.</h3><p>You can close this tab.</p></body></html>")

    server = ThreadingHTTPServer((bind_host, parsed.port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    auth_url = build_authorization_url(client_id, redirect_uri, scope)
    print(f"Open this URL if your browser does not open automatically:\n{redact_authorization_url(auth_url)}")
    if open_browser:
        webbrowser.open(auth_url)
    try:
        payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(f"Timed out waiting for cTrader OAuth callback after {timeout_seconds}s") from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if "error" in payload:
        raise OAuthCallbackError(f"cTrader OAuth callback failed: {payload['error']} {payload.get('description', '')}")
    return exchange_code_for_token(client_id, client_secret, payload["code"], redirect_uri)


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


def parse_utc_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def seconds_until_expiry(expires_at: Any) -> int | None:
    expiry = parse_utc_datetime(expires_at)
    return None if expiry is None else int((expiry - datetime.now(UTC)).total_seconds())


def load_token_cache(path: Path | str | None = None) -> dict[str, Any]:
    from src.configuration import TOKEN_CACHE

    token_path = Path(path) if path else TOKEN_CACHE
    if not token_path.exists():
        return {}
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid cTrader token cache JSON: {token_path}") from exc


def save_token_cache(
    payload: dict[str, Any], *, client_id: str | None = None, client_secret: str | None = None,
    redirect_uri: str | None = None, scope: str | None = None,
    account_id: int | None = None, trader_login: str | None = None,
) -> Path:
    from src.configuration import TOKEN_CACHE, ensure_runtime_dirs
    from src.spool import JobLockConflict, exclusive_job_lock

    ensure_runtime_dirs()
    token_path = TOKEN_CACHE
    deadline = time.monotonic() + 10.0
    while True:
        try:
            with exclusive_job_lock("token-cache", label="token cache writer"):
                existing = load_token_cache(token_path)
                merged = dict(existing)
                merged["updated_at_utc"] = datetime.now(UTC).isoformat()
                merged.setdefault("created_at_utc", merged["updated_at_utc"])
                for key in TOKEN_KEYS:
                    if key in payload:
                        merged[key] = payload[key]
                if payload.get("expiresIn"):
                    merged["expires_at_utc"] = (datetime.now(UTC) + timedelta(seconds=int(payload["expiresIn"]))).isoformat()
                for key, value in {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
                                    "scope": scope, "ctidTraderAccountId": account_id, "traderLogin": trader_login}.items():
                    if value not in (None, ""):
                        merged[key] = value
                tmp_path = token_path.with_name(f".{token_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                try:
                    with tmp_path.open("w", encoding="utf-8") as handle:
                        json.dump(merged, handle, indent=2, sort_keys=True)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_path, token_path)
                finally:
                    tmp_path.unlink(missing_ok=True)
                break
        except JobLockConflict:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for the cTrader token cache writer lock")
            time.sleep(0.1)
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token_path


def update_cached_account(account_id: int, trader_login: str | None = None) -> Path:
    return save_token_cache({}, account_id=account_id, trader_login=trader_login)


def token_status() -> dict[str, Any]:
    from src.configuration import TOKEN_CACHE

    cache = load_token_cache(TOKEN_CACHE)
    expires_at = cache.get("expires_at_utc")
    remaining = seconds_until_expiry(expires_at)
    return {
        "path": str(TOKEN_CACHE),
        "exists": TOKEN_CACHE.exists(),
        "client_id_set": bool(cache.get("client_id")),
        "client_secret_set": bool(cache.get("client_secret")),
        "access_token_set": bool(cache.get("accessToken")),
        "refresh_token_set": bool(cache.get("refreshToken")),
        "account_id": cache.get("ctidTraderAccountId"),
        "trader_login": cache.get("traderLogin"),
        "redirect_uri": cache.get("redirect_uri"),
        "scope": cache.get("scope"),
        "expires_at_utc": expires_at,
        "seconds_until_expiry": remaining,
        "is_expired": (remaining is not None and remaining <= 0) if expires_at else None,
        "refresh_recommended": remaining is not None and remaining <= 24 * 60 * 60,
    }


# ---------------------------------------------------------------------------
# Settings-level refresh helpers
# ---------------------------------------------------------------------------


def reload_access_token_from_cache(settings: Any, reason: str = "") -> Any:
    """Reload a rotated OAuth token from disk cache into a Settings snapshot."""
    from dataclasses import replace

    try:
        cache = load_token_cache()
    except Exception:
        return settings
    cached_access = str(cache.get("accessToken") or "")
    if not cached_access or cached_access == settings.access_token:
        return settings
    return replace(
        settings,
        access_token=cached_access,
        refresh_token=str(cache.get("refreshToken") or settings.refresh_token),
        access_token_expires_at_utc=parse_utc_datetime(cache.get("expires_at_utc")) or settings.access_token_expires_at_utc,
    )


def ensure_fresh_access_token(settings: Any, reason: str) -> Any:
    """Refresh the cTrader access token in-place when the cache says it is due."""
    from dataclasses import replace

    settings = reload_access_token_from_cache(settings, reason)
    if not settings.should_refresh_access_token:
        return settings
    if not (settings.client_id and settings.client_secret and settings.refresh_token):
        return settings  # can't refresh; caller will surface a clearer error if the token is actually dead

    try:
        payload = refresh_access_token(settings.client_id, settings.client_secret, settings.refresh_token)
    except Exception:
        remaining = settings.access_token_seconds_remaining
        if settings.access_token and (remaining is None or remaining > 0):
            return settings
        raise

    save_token_cache(
        payload, client_id=settings.client_id, client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri, scope=settings.oauth_scope,
        account_id=settings.account_id, trader_login=settings.trader_login,
    )
    expires_at = None
    if payload.get("expiresIn"):
        expires_at = datetime.now(UTC) + timedelta(seconds=int(payload["expiresIn"]))
    return replace(
        settings,
        access_token=str(payload.get("accessToken") or settings.access_token),
        refresh_token=str(payload.get("refreshToken") or settings.refresh_token),
        access_token_expires_at_utc=expires_at or settings.access_token_expires_at_utc,
    )
