"""Runtime settings and cTrader SDK helpers for historical tick jobs.

Merged from:
  tick_program/engine/tick_data/runtime.py       - TickRuntimeSettings + SDK helpers
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tick_engine.data_storage.symbols import TargetSymbol
from tick_engine.utils_support.auth import refresh_access_token
from tick_engine.utils_support.token_store import (
    DEFAULT_REFRESH_SAFETY_SECONDS,
    load_token_cache,
    parse_utc_datetime,
    save_token_cache,
    seconds_until_expiry,
    token_refresh_recommended,
)
from tick_engine.reporting.system_log import write_system_event

logger = logging.getLogger(__name__)

DEMO_PROTOBUF_HOST = "demo.ctraderapi.com"
LIVE_PROTOBUF_HOST = "live.ctraderapi.com"
PROTOBUF_PORT = 5035

# ---------------------------------------------------------------------------
# TickRuntimeSettings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickRuntimeSettings:
    env: str
    host: str
    port: int
    schema: str
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    access_token_expires_at_utc: datetime | None
    token_refresh_safety_seconds: int
    account_id: int | None
    trader_login: str
    redirect_uri: str
    oauth_scope: str
    symbols: tuple[TargetSymbol, ...]
    batch_size: int
    flush_seconds: float
    response_timeout_seconds: float
    max_quote_side_age_seconds: int
    spool_path: Path
    log_path: Path

    @property
    def missing_api_fields(self) -> tuple[str, ...]:
        required = {
            "CTRADER_CLIENT_ID": self.client_id,
            "CTRADER_CLIENT_SECRET": self.client_secret,
            "CTRADER_ACCESS_TOKEN": self.access_token,
            "CTRADER_ACCOUNT_ID": self.account_id,
        }
        return tuple(name for name, value in required.items() if not value)

    @property
    def endpoint_label(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def access_token_seconds_remaining(self) -> int | None:
        return seconds_until_expiry(self.access_token_expires_at_utc)

    @property
    def should_refresh_access_token(self) -> bool:
        if not self.access_token and self.refresh_token:
            return True
        return token_refresh_recommended(
            self.access_token_expires_at_utc,
            safety_seconds=self.token_refresh_safety_seconds,
        )


# ---------------------------------------------------------------------------
# CTrader SDK
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CTraderSdk:
    Client: Any
    Protobuf: Any
    TcpProtocol: Any
    EndPoints: Any
    reactor: Any
    messages: Any
    model_messages: Any


class MissingCTraderSdk(RuntimeError):
    """Raised when the optional cTrader SDK is not installed."""


def load_ctrader_sdk() -> CTraderSdk:
    try:
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages import OpenApiMessages_pb2 as messages
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_messages
        from twisted.internet import reactor
    except ImportError as exc:
        raise MissingCTraderSdk(
            "Install the official cTrader SDK: pip install ctrader-open-api twisted"
        ) from exc
    return CTraderSdk(
        Client=Client,
        Protobuf=Protobuf,
        TcpProtocol=TcpProtocol,
        EndPoints=EndPoints,
        reactor=reactor,
        messages=messages,
        model_messages=model_messages,
    )


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------


def _parse_account_id(value: str) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("CTRADER_ACCOUNT_ID must be the numeric ctidTraderAccountId") from exc


def _target_symbols_from_config(raw_symbols: list[dict]) -> tuple[TargetSymbol, ...]:
    return tuple(
        TargetSymbol(
            symbol_id=int(item["symbol_id"]),
            local_symbol=str(item.get("symbol") or item.get("tv_symbol", "")).upper(),
            asset_type=str(item["asset_type"]),
            table=str(item.get("table", f"tick.{item.get('symbol') or item.get('tv_symbol', '')}")),
        )
        for item in raw_symbols
    )


def load_settings() -> TickRuntimeSettings:
    """Load runtime settings from tick_engine.settings, env, and token cache."""
    import tick_engine.settings as cfg

    cfg.ensure_runtime_dirs()
    token_cache = load_token_cache()
    env = cfg.CTRADER_ENV
    if env not in {"demo", "live"}:
        raise ValueError("CTRADER_ENV must be 'demo' or 'live'")

    host = DEMO_PROTOBUF_HOST if env == "demo" else LIVE_PROTOBUF_HOST
    account_id_raw = cfg.CTRADER_ACCOUNT_ID or str(token_cache.get("ctidTraderAccountId", ""))
    redirect_uri = os.environ.get("CTRADER_REDIRECT_URI") or str(
        token_cache.get("redirect_uri") or cfg.CTRADER_REDIRECT_URI
    )
    oauth_scope = os.environ.get("CTRADER_OAUTH_SCOPE") or str(
        token_cache.get("scope") or cfg.CTRADER_OAUTH_SCOPE
    )
    return TickRuntimeSettings(
        env=env,
        host=host,
        port=PROTOBUF_PORT,
        schema=cfg.TICK_SCHEMA,
        client_id=cfg.CTRADER_CLIENT_ID or str(token_cache.get("client_id", "")),
        client_secret=cfg.CTRADER_CLIENT_SECRET or str(token_cache.get("client_secret", "")),
        access_token=cfg.CTRADER_ACCESS_TOKEN or str(token_cache.get("accessToken", "")),
        refresh_token=cfg.CTRADER_REFRESH_TOKEN or str(token_cache.get("refreshToken", "")),
        access_token_expires_at_utc=parse_utc_datetime(token_cache.get("expires_at_utc")),
        token_refresh_safety_seconds=int(
            os.environ.get("CTRADER_FTMO_TOKEN_REFRESH_SAFETY_SECONDS",
                           str(DEFAULT_REFRESH_SAFETY_SECONDS))
        ),
        account_id=_parse_account_id(account_id_raw),
        trader_login=cfg.CTRADER_TRADER_LOGIN or str(token_cache.get("traderLogin", "")),
        redirect_uri=redirect_uri,
        oauth_scope=oauth_scope,
        symbols=_target_symbols_from_config(cfg.SYMBOLS),
        batch_size=cfg.TICK_BATCH_SIZE,
        flush_seconds=cfg.TICK_FLUSH_SECONDS,
        response_timeout_seconds=cfg.TICK_RESPONSE_TIMEOUT_SECONDS,
        max_quote_side_age_seconds=max(0, cfg.TICK_MAX_QUOTE_SIDE_AGE_SECONDS),
        spool_path=cfg.WS_OVERFLOW_SPOOL,
        log_path=cfg.MANUAL_LOG,
    )


# ---------------------------------------------------------------------------
# cTrader request helpers
# ---------------------------------------------------------------------------


def make_application_auth_req(sdk: CTraderSdk, client_id: str, client_secret: str) -> Any:
    req = sdk.messages.ProtoOAApplicationAuthReq()
    req.clientId = client_id
    req.clientSecret = client_secret
    return req


def make_account_auth_req(sdk: CTraderSdk, account_id: int, access_token: str) -> Any:
    req = sdk.messages.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = int(account_id)
    req.accessToken = access_token
    return req


def make_get_account_list_req(sdk: CTraderSdk, access_token: str) -> Any:
    req = sdk.messages.ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = access_token
    return req


def make_symbols_list_req(sdk: CTraderSdk, account_id: int) -> Any:
    req = sdk.messages.ProtoOASymbolsListReq()
    req.ctidTraderAccountId = int(account_id)
    try:
        req.includeArchivedSymbols = False
    except Exception:
        pass
    return req


def make_get_tick_data_req(
    sdk: CTraderSdk,
    account_id: int,
    symbol_id: int,
    quote_type: str,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
) -> Any:
    req = sdk.messages.ProtoOAGetTickDataReq()
    req.ctidTraderAccountId = int(account_id)
    req.symbolId = int(symbol_id)
    req.fromTimestamp = int(from_timestamp_ms)
    req.toTimestamp = int(to_timestamp_ms)
    req.type = sdk.model_messages.ProtoOAQuoteType.Value(quote_type.upper())
    return req


def remote_symbol_from_proto(proto_symbol: Any) -> dict[str, Any]:
    return {
        "ctrader_symbol_id": int(getattr(proto_symbol, "symbolId")),
        "symbol_name": str(getattr(proto_symbol, "symbolName")),
        "digits": int(getattr(proto_symbol, "digits"))
        if hasattr(proto_symbol, "digits") and getattr(proto_symbol, "digits") is not None
        else None,
        "description": str(getattr(proto_symbol, "description", "")) or None,
        "enabled": bool(getattr(proto_symbol, "enabled"))
        if hasattr(proto_symbol, "enabled") else None,
        "base_asset_id": int(getattr(proto_symbol, "baseAssetId"))
        if hasattr(proto_symbol, "baseAssetId") else None,
        "quote_asset_id": int(getattr(proto_symbol, "quoteAssetId"))
        if hasattr(proto_symbol, "quoteAssetId") else None,
        "pip_position": int(getattr(proto_symbol, "pipPosition"))
        if hasattr(proto_symbol, "pipPosition") and getattr(proto_symbol, "pipPosition") is not None
        else None,
    }


def extract_payload(sdk: CTraderSdk, message: Any) -> Any:
    return sdk.Protobuf.extract(message)


def new_client(settings: TickRuntimeSettings, sdk: CTraderSdk) -> Any:
    return sdk.Client(settings.host, settings.port, sdk.TcpProtocol)


def stop_reactor(sdk: CTraderSdk, client: Any | None = None) -> None:
    try:
        if client is not None:
            client.stopService()
    finally:
        try:
            sdk.reactor.stop()
        except Exception:
            pass


def validate_configured_account(
    settings: TickRuntimeSettings,
    accounts: list[dict[str, object]],
) -> dict[str, object]:
    """Validate that the configured account belongs to the token and expected environment."""
    selected = next(
        (
            account
            for account in accounts
            if int(account.get("ctidTraderAccountId") or 0) == int(settings.account_id or 0)
        ),
        None,
    )
    if selected is None:
        raise RuntimeError(
            f"configured cTrader account {settings.account_id} is not granted to the current access token"
        )
    expected_live = settings.env == "live"
    if bool(selected.get("isLive")) != expected_live:
        actual = "live" if bool(selected.get("isLive")) else "demo"
        raise RuntimeError(
            f"configured cTrader account {settings.account_id} is {actual}, expected {settings.env}"
        )
    configured_login = str(settings.trader_login or "").strip()
    account_login = str(selected.get("traderLogin") or "").strip()
    if configured_login and configured_login != account_login:
        raise RuntimeError(
            f"configured trader login {configured_login} does not match account login {account_login}"
        )
    return selected


def send_auth_chain(
    settings: TickRuntimeSettings,
    sdk: CTraderSdk,
    client: Any,
    on_authed: Callable[[], None],
    on_error: Callable[[Exception], None],
    *,
    context: str = "",
) -> None:
    label = context or f"{settings.env} {settings.endpoint_label}"

    def _error_detail(response: Any) -> str | None:
        try:
            payload = extract_payload(sdk, response)
        except Exception:
            return None
        if payload.__class__.__name__ != "ProtoOAErrorRes":
            return None
        code = getattr(payload, "errorCode", "") or ""
        description = getattr(payload, "description", "") or ""
        return f"{code}: {description}".strip(": ").strip() or "unknown cTrader error"

    def on_account_auth(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event("CTrader Auth", "account fail", f"{label} | error={detail}", level="ERROR")
            on_error(Exception(f"cTrader account auth rejected ({detail})"))
            return
        write_system_event("CTrader Auth", "account ok", label)
        on_authed()

    def send_account_auth() -> None:
        account_req = make_account_auth_req(sdk, int(settings.account_id), settings.access_token)
        write_system_event("CTrader Auth", "account sent", label)
        client.send(
            account_req, responseTimeoutInSeconds=settings.response_timeout_seconds
        ).addCallbacks(on_account_auth, on_error)

    def on_account_list(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event(
                "CTrader Auth", "account list fail", f"{label} | error={detail}", level="ERROR"
            )
            on_error(Exception(f"cTrader account list rejected ({detail})"))
            return
        try:
            payload = extract_payload(sdk, response)
            accounts = [
                {
                    "ctidTraderAccountId": int(getattr(account, "ctidTraderAccountId")),
                    "isLive": bool(getattr(account, "isLive", False)),
                    "brokerName": str(getattr(account, "brokerName", "")),
                    "traderLogin": str(getattr(account, "traderLogin", "")),
                }
                for account in getattr(payload, "ctidTraderAccount", [])
            ]
            validate_configured_account(settings, accounts)
        except Exception as exc:
            write_system_event(
                "CTrader Auth", "account validation fail", f"{label} | error={exc}", level="ERROR"
            )
            on_error(exc)
            return
        write_system_event("CTrader Auth", "account validation ok", label)
        send_account_auth()

    def on_app_auth(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event("CTrader Auth", "application fail", f"{label} | error={detail}", level="ERROR")
            on_error(Exception(f"cTrader application auth rejected ({detail})"))
            return
        write_system_event("CTrader Auth", "application ok", label)
        account_list_req = make_get_account_list_req(sdk, settings.access_token)
        write_system_event("CTrader Auth", "account validation sent", label)
        client.send(
            account_list_req, responseTimeoutInSeconds=settings.response_timeout_seconds
        ).addCallbacks(on_account_list, on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    write_system_event("CTrader Auth", "application sent", label)
    client.send(
        app_req, responseTimeoutInSeconds=settings.response_timeout_seconds
    ).addCallbacks(on_app_auth, on_error)


# ---------------------------------------------------------------------------
# Token refresh helpers
# ---------------------------------------------------------------------------


def _expires_at_from_payload(payload: dict[str, Any]) -> datetime | None:
    expires_in = payload.get("expiresIn")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def _settings_with_token_payload(
    settings: TickRuntimeSettings, payload: dict[str, Any]
) -> TickRuntimeSettings:
    access_token = str(payload.get("accessToken") or settings.access_token)
    refresh_token = str(payload.get("refreshToken") or settings.refresh_token)
    expires_at = _expires_at_from_payload(payload) or settings.access_token_expires_at_utc
    return replace(settings, access_token=access_token, refresh_token=refresh_token,
                   access_token_expires_at_utc=expires_at)


def reload_access_token_from_cache(
    settings: TickRuntimeSettings,
    reason: str = "",
    lg: logging.Logger = logger,
) -> TickRuntimeSettings:
    """Reload the rotated OAuth token from disk cache into settings."""
    import tick_engine.settings as cfg

    if cfg.CTRADER_ACCESS_TOKEN:
        return settings
    try:
        cache = load_token_cache()
    except Exception:
        lg.exception("could not reload cTrader token cache%s", f" before {reason}" if reason else "")
        return settings
    cached_access = str(cache.get("accessToken") or "")
    if not cached_access:
        return settings
    cached_refresh = str(cache.get("refreshToken") or settings.refresh_token)
    if cached_access == settings.access_token and cached_refresh == settings.refresh_token:
        return settings
    if reason:
        lg.info("reloaded rotated cTrader access token from cache before %s", reason)
    return replace(
        settings,
        access_token=cached_access,
        refresh_token=cached_refresh,
        access_token_expires_at_utc=parse_utc_datetime(cache.get("expires_at_utc"))
        or settings.access_token_expires_at_utc,
    )


def ensure_fresh_access_token(
    settings: TickRuntimeSettings,
    reason: str,
    lg: logging.Logger = logger,
) -> TickRuntimeSettings:
    """Refresh the cTrader access token when the cache says it is due."""
    settings = reload_access_token_from_cache(settings, reason, lg)
    if not settings.should_refresh_access_token:
        return settings

    missing = [
        name for name, value in {
            "CTRADER_CLIENT_ID": settings.client_id,
            "CTRADER_CLIENT_SECRET": settings.client_secret,
            "CTRADER_REFRESH_TOKEN": settings.refresh_token,
        }.items() if not value
    ]
    if missing:
        if settings.access_token:
            lg.warning(
                "cTrader token refresh recommended before %s but missing %s; continuing",
                reason, ",".join(missing),
            )
        return settings

    try:
        payload = refresh_access_token(
            settings.client_id, settings.client_secret, settings.refresh_token
        )
    except Exception:
        remaining = settings.access_token_seconds_remaining
        if settings.access_token and (remaining is None or remaining > 0):
            lg.exception(
                "cTrader token refresh failed before %s; continuing with current token", reason
            )
            return settings
        raise

    save_token_cache(
        payload, client_id=settings.client_id, client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri, scope=settings.oauth_scope,
        account_id=settings.account_id, trader_login=settings.trader_login,
    )
    refreshed = _settings_with_token_payload(settings, payload)
    remaining = refreshed.access_token_seconds_remaining
    lg.info("refreshed cTrader access token before %s; ttl=%s seconds",
            reason, "unknown" if remaining is None else remaining)
    return refreshed
