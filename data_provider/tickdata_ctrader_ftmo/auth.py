"""OAuth helpers for cTrader Open API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests

AUTHORIZATION_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class CTraderAuthError(RuntimeError):
    """Raised when cTrader OAuth token exchange fails."""


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    scope: str = "accounts",
    product: str = "web",
) -> str:
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


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    timeout: int = 20,
) -> dict[str, Any]:
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
