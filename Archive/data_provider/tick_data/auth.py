"""OAuth helpers for the cTrader/FTMO tick-data project.

Only token acquisition and refresh live here. The functions intentionally return
raw cTrader payloads to the caller so CLI/service code can decide whether to
print, cache, or use the token in memory. Do not log payload values from this
module in production because they can contain access and refresh tokens.
"""

from __future__ import annotations

import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORIZATION_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class CTraderAuthError(RuntimeError):
    """Raised when cTrader OAuth token exchange fails."""


class OAuthCallbackError(RuntimeError):
    """Raised when the local OAuth callback returns an error."""


def _bind_host(hostname: str | None) -> str:
    if hostname in {"localhost", "127.0.0.1", None, ""}:
        return "127.0.0.1"
    raise ValueError("CTRADER_REDIRECT_URI must use localhost or 127.0.0.1 for oauth-login")


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    scope: str = "accounts",
    product: str = "web",
) -> str:
    """Build the cTrader granting-access URL for an operator browser login."""
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "product": product,
        }
    )
    return f"{AUTHORIZATION_URL}?{params}"


def _raise_for_token_error(payload: dict[str, Any]) -> None:
    error = payload.get("errorCode") or payload.get("error") or payload.get("message")
    if error:
        raise CTraderAuthError(f"cTrader OAuth failed: {error}")


def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout: int = 20,
) -> dict[str, Any]:
    """Exchange the OAuth callback code for access/refresh token material."""
    response = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    _raise_for_token_error(payload)
    return payload


def run_local_oauth_login(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    scope: str,
    timeout_seconds: int = 120,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Run a localhost callback OAuth flow and exchange the returned code."""
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http":
        raise ValueError("oauth-login local callback requires an http:// localhost redirect URI")
    if not parsed.port:
        raise ValueError("CTRADER_REDIRECT_URI must include a localhost port, e.g. http://localhost:8765/callback")

    bind_host = _bind_host(parsed.hostname)
    callback_path = parsed.path or "/"
    result_queue: queue.Queue[dict[str, str]] = queue.Queue(maxsize=1)

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - inherited API name
            request = urlparse(self.path)
            if request.path != callback_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Unknown callback path.")
                return

            query = parse_qs(request.query)
            if "error" in query:
                payload = {"error": query["error"][0], "description": query.get("description", [""])[0]}
            elif "code" in query:
                payload = {"code": query["code"][0]}
            else:
                payload = {"error": "missing_code", "description": "No code query parameter was returned."}

            try:
                result_queue.put_nowait(payload)
            except queue.Full:
                pass

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h3>cTrader OAuth received.</h3>"
                b"<p>You can close this browser tab and return to SEN05.</p>"
                b"</body></html>"
            )

    server = ThreadingHTTPServer((bind_host, parsed.port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    auth_url = build_authorization_url(client_id, redirect_uri, scope)
    print(f"Open this cTrader OAuth URL if your browser does not open automatically:\n{auth_url}")
    if open_browser:
        webbrowser.open(auth_url)

    try:
        payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(f"Timed out waiting for cTrader OAuth callback after {timeout_seconds}s") from exc
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    if "error" in payload:
        raise OAuthCallbackError(f"cTrader OAuth callback failed: {payload['error']} {payload.get('description', '')}")

    return exchange_code_for_token(
        client_id=client_id,
        client_secret=client_secret,
        code=payload["code"],
        redirect_uri=redirect_uri,
    )


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    timeout: int = 20,
) -> dict[str, Any]:
    """Refresh an access token using the cached or environment refresh token."""
    response = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    _raise_for_token_error(payload)
    return payload
