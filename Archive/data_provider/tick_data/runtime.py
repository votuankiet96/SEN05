"""Runtime settings, SDK wrapper and shared cTrader client helpers."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_provider.paths import LOG_DIR, SPOOL_DIR, ensure_runtime_dirs

from .auth import refresh_access_token
from .symbols import TargetSymbol
from .token_store import (
    DEFAULT_REFRESH_SAFETY_SECONDS,
    DEFAULT_TOKEN_CACHE,
    load_token_cache,
    parse_utc_datetime,
    save_token_cache,
    seconds_until_expiry,
    token_refresh_recommended,
)

logger = logging.getLogger(__name__)

DEMO_PROTOBUF_HOST = "demo.ctraderapi.com"
LIVE_PROTOBUF_HOST = "live.ctraderapi.com"
PROTOBUF_PORT = 5035


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
    queue_maxsize: int
    response_timeout_seconds: float
    reconnect_min_seconds: float
    reconnect_max_seconds: float
    stale_seconds_btc: int
    stale_seconds_market: int
    heartbeat_log_seconds: int
    discord_report_seconds: int
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


def _parse_account_id(value: str) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("CTRADER_ACCOUNT_ID must be the numeric ctidTraderAccountId") from exc


def _target_symbols_from_config(raw_symbols: list[dict[str, object]]) -> tuple[TargetSymbol, ...]:
    return tuple(
        TargetSymbol(
            symbol_id=int(item["symbol_id"]),
            local_symbol=str(item["symbol"]).upper(),
            asset_type=str(item["asset_type"]),
            table=str(item["table"]),
        )
        for item in raw_symbols
    )


def load_settings() -> TickRuntimeSettings:
    """Load runtime settings from config.py, environment and token cache."""
    import config

    ensure_runtime_dirs()
    token_cache = load_token_cache(DEFAULT_TOKEN_CACHE)
    env = config.CTRADER_FTMO_ENV
    if env not in {"demo", "live"}:
        raise ValueError("CTRADER_FTMO_ENV must be 'demo' or 'live'")

    host = DEMO_PROTOBUF_HOST if env == "demo" else LIVE_PROTOBUF_HOST
    account_id = config.CTRADER_ACCOUNT_ID or str(token_cache.get("ctidTraderAccountId", ""))
    redirect_uri = (
        os.environ.get("CTRADER_REDIRECT_URI")
        or str(token_cache.get("redirect_uri") or config.CTRADER_REDIRECT_URI)
    )
    oauth_scope = (
        os.environ.get("CTRADER_OAUTH_SCOPE")
        or str(token_cache.get("scope") or config.CTRADER_OAUTH_SCOPE)
    )
    return TickRuntimeSettings(
        env=env,
        host=host,
        port=PROTOBUF_PORT,
        schema=config.CTRADER_FTMO_TICK_SCHEMA,
        client_id=config.CTRADER_CLIENT_ID or str(token_cache.get("client_id", "")),
        client_secret=config.CTRADER_CLIENT_SECRET or str(token_cache.get("client_secret", "")),
        access_token=config.CTRADER_ACCESS_TOKEN or str(token_cache.get("accessToken", "")),
        refresh_token=config.CTRADER_REFRESH_TOKEN or str(token_cache.get("refreshToken", "")),
        access_token_expires_at_utc=parse_utc_datetime(token_cache.get("expires_at_utc")),
        token_refresh_safety_seconds=int(
            os.environ.get(
                "CTRADER_FTMO_TOKEN_REFRESH_SAFETY_SECONDS",
                str(DEFAULT_REFRESH_SAFETY_SECONDS),
            )
        ),
        account_id=_parse_account_id(account_id),
        trader_login=config.CTRADER_TRADER_LOGIN or str(token_cache.get("traderLogin", "")),
        redirect_uri=redirect_uri,
        oauth_scope=oauth_scope,
        symbols=_target_symbols_from_config(config.CTRADER_FTMO_TICK_SYMBOLS),
        batch_size=config.CTRADER_FTMO_TICK_BATCH_SIZE,
        flush_seconds=config.CTRADER_FTMO_TICK_FLUSH_SECONDS,
        queue_maxsize=config.CTRADER_FTMO_TICK_QUEUE_MAXSIZE,
        response_timeout_seconds=config.CTRADER_FTMO_TICK_RESPONSE_TIMEOUT_SECONDS,
        reconnect_min_seconds=config.CTRADER_FTMO_TICK_RECONNECT_MIN_SECONDS,
        reconnect_max_seconds=config.CTRADER_FTMO_TICK_RECONNECT_MAX_SECONDS,
        stale_seconds_btc=config.CTRADER_FTMO_TICK_STALE_SECONDS_BTC,
        stale_seconds_market=config.CTRADER_FTMO_TICK_STALE_SECONDS_MARKET,
        heartbeat_log_seconds=config.CTRADER_FTMO_TICK_HEARTBEAT_LOG_SECONDS,
        discord_report_seconds=config.CTRADER_FTMO_TICK_DISCORD_REPORT_SECONDS,
        spool_path=SPOOL_DIR / "ctrader_ftmo_tick_spool.db",
        log_path=LOG_DIR / "ctrader_ftmo_tick.log",
    )


def load_ctrader_sdk() -> CTraderSdk:
    """Load official SDK modules lazily so offline tests do not require it."""
    try:
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages import OpenApiMessages_pb2 as messages
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_messages
        from twisted.internet import reactor
    except ImportError as exc:
        raise MissingCTraderSdk(
            "Install the official cTrader SDK in the active venv with "
            "`pip install ctrader-open-api` or `pip install -r requirements.txt`."
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


def make_application_auth_req(sdk: CTraderSdk, client_id: str, client_secret: str) -> Any:
    """Create the app-level auth request required before account operations."""
    req = sdk.messages.ProtoOAApplicationAuthReq()
    req.clientId = client_id
    req.clientSecret = client_secret
    return req


def make_account_auth_req(sdk: CTraderSdk, account_id: int, access_token: str) -> Any:
    """Create account-level auth for one granted cTrader account."""
    req = sdk.messages.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = int(account_id)
    req.accessToken = access_token
    return req


def make_get_account_list_req(sdk: CTraderSdk, access_token: str) -> Any:
    """Create the request that lists accounts granted to an access token."""
    req = sdk.messages.ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = access_token
    return req


def make_symbols_list_req(sdk: CTraderSdk, account_id: int) -> Any:
    """Create the request that returns account-scoped cTrader symbols."""
    req = sdk.messages.ProtoOASymbolsListReq()
    req.ctidTraderAccountId = int(account_id)
    try:
        req.includeArchivedSymbols = False
    except Exception:
        pass
    return req


def make_subscribe_spots_req(sdk: CTraderSdk, account_id: int, symbol_ids: list[int]) -> Any:
    """Create a live spot subscription request with source timestamps enabled."""
    req = sdk.messages.ProtoOASubscribeSpotsReq()
    req.ctidTraderAccountId = int(account_id)
    req.symbolId.extend([int(symbol_id) for symbol_id in symbol_ids])
    req.subscribeToSpotTimestamp = True
    return req


def make_get_tick_data_req(
    sdk: CTraderSdk,
    account_id: int,
    symbol_id: int,
    quote_type: str,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
) -> Any:
    """Create a historical BID/ASK tick request for one symbol and time window."""
    req = sdk.messages.ProtoOAGetTickDataReq()
    req.ctidTraderAccountId = int(account_id)
    req.symbolId = int(symbol_id)
    req.fromTimestamp = int(from_timestamp_ms)
    req.toTimestamp = int(to_timestamp_ms)
    req.type = sdk.model_messages.ProtoOAQuoteType.Value(quote_type.upper())
    return req


def remote_symbol_from_proto(proto_symbol: Any) -> dict[str, Any]:
    """Extract only stable fields we use from a ProtoOALightSymbol/Symbol."""
    return {
        "ctrader_symbol_id": int(getattr(proto_symbol, "symbolId")),
        "symbol_name": str(getattr(proto_symbol, "symbolName")),
        "digits": int(getattr(proto_symbol, "digits"))
        if hasattr(proto_symbol, "digits") and getattr(proto_symbol, "digits") is not None
        else None,
        "description": str(getattr(proto_symbol, "description", "")) or None,
        "enabled": bool(getattr(proto_symbol, "enabled"))
        if hasattr(proto_symbol, "enabled")
        else None,
        "base_asset_id": int(getattr(proto_symbol, "baseAssetId"))
        if hasattr(proto_symbol, "baseAssetId")
        else None,
        "quote_asset_id": int(getattr(proto_symbol, "quoteAssetId"))
        if hasattr(proto_symbol, "quoteAssetId")
        else None,
        "pip_position": int(getattr(proto_symbol, "pipPosition"))
        if hasattr(proto_symbol, "pipPosition") and getattr(proto_symbol, "pipPosition") is not None
        else None,
    }


def _expires_at_from_payload(payload: dict[str, Any]) -> datetime | None:
    expires_in = payload.get("expiresIn")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def _settings_with_token_payload(
    settings: TickRuntimeSettings,
    payload: dict[str, Any],
) -> TickRuntimeSettings:
    access_token = str(payload.get("accessToken") or settings.access_token)
    refresh_token = str(payload.get("refreshToken") or settings.refresh_token)
    expires_at = _expires_at_from_payload(payload) or settings.access_token_expires_at_utc
    return replace(
        settings,
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_at_utc=expires_at,
    )


def ensure_fresh_access_token(
    settings: TickRuntimeSettings,
    reason: str,
    lg: logging.Logger = logger,
) -> TickRuntimeSettings:
    """Refresh the cTrader access token when the local cache says it is due."""
    if not settings.should_refresh_access_token:
        return settings

    missing = [
        name
        for name, value in {
            "CTRADER_CLIENT_ID": settings.client_id,
            "CTRADER_CLIENT_SECRET": settings.client_secret,
            "CTRADER_REFRESH_TOKEN": settings.refresh_token,
        }.items()
        if not value
    ]
    if missing:
        if settings.access_token:
            lg.warning(
                "cTrader token refresh recommended before %s but missing %s; "
                "continuing with current access token",
                reason,
                ",".join(missing),
            )
        return settings

    try:
        payload = refresh_access_token(
            settings.client_id,
            settings.client_secret,
            settings.refresh_token,
        )
    except Exception:
        remaining = settings.access_token_seconds_remaining
        if settings.access_token and (remaining is None or remaining > 0):
            lg.exception(
                "cTrader token refresh failed before %s; continuing with current token",
                reason,
            )
            return settings
        raise

    save_token_cache(
        payload,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri,
        scope=settings.oauth_scope,
        account_id=settings.account_id,
        trader_login=settings.trader_login,
    )
    refreshed = _settings_with_token_payload(settings, payload)
    remaining = refreshed.access_token_seconds_remaining
    lg.info(
        "refreshed cTrader access token before %s; ttl=%s seconds",
        reason,
        "unknown" if remaining is None else remaining,
    )
    return refreshed


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


def send_auth_chain(
    settings: TickRuntimeSettings,
    sdk: CTraderSdk,
    client: Any,
    on_authed: Callable[[], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Send application auth and then account auth before cTrader operations."""
    def on_app_auth(_response: Any) -> None:
        account_req = make_account_auth_req(sdk, int(settings.account_id), settings.access_token)
        client.send(
            account_req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(lambda _r: on_authed(), on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.send(
        app_req,
        responseTimeoutInSeconds=settings.response_timeout_seconds,
    ).addCallbacks(on_app_auth, on_error)
