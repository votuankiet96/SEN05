"""Runtime settings for the cTrader FTMO tick provider."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from data_provider.paths import LOG_DIR, SPOOL_DIR, ensure_runtime_dirs

from .models import TargetSymbol
from .token_store import DEFAULT_TOKEN_CACHE, load_token_cache

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
    account_id: int | None
    trader_login: str
    redirect_uri: str
    oauth_scope: str
    symbols: tuple[TargetSymbol, ...]
    batch_size: int
    flush_seconds: float
    queue_maxsize: int
    reconnect_min_seconds: float
    reconnect_max_seconds: float
    stale_seconds_btc: int
    stale_seconds_market: int
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
    """Load runtime settings from config.py and environment-backed config values."""
    import config

    ensure_runtime_dirs()
    token_cache = load_token_cache(DEFAULT_TOKEN_CACHE)
    env = config.CTRADER_FTMO_ENV
    if env not in {"demo", "live"}:
        raise ValueError("CTRADER_FTMO_ENV must be 'demo' or 'live'")

    host = DEMO_PROTOBUF_HOST if env == "demo" else LIVE_PROTOBUF_HOST
    client_id = config.CTRADER_CLIENT_ID or str(token_cache.get("client_id", ""))
    client_secret = config.CTRADER_CLIENT_SECRET or str(token_cache.get("client_secret", ""))
    access_token = config.CTRADER_ACCESS_TOKEN or str(token_cache.get("accessToken", ""))
    refresh_token = config.CTRADER_REFRESH_TOKEN or str(token_cache.get("refreshToken", ""))
    account_id = config.CTRADER_ACCOUNT_ID or str(token_cache.get("ctidTraderAccountId", ""))
    trader_login = config.CTRADER_TRADER_LOGIN or str(token_cache.get("traderLogin", ""))
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
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=_parse_account_id(account_id),
        trader_login=trader_login,
        redirect_uri=redirect_uri,
        oauth_scope=oauth_scope,
        symbols=_target_symbols_from_config(config.CTRADER_FTMO_TICK_SYMBOLS),
        batch_size=config.CTRADER_FTMO_TICK_BATCH_SIZE,
        flush_seconds=config.CTRADER_FTMO_TICK_FLUSH_SECONDS,
        queue_maxsize=config.CTRADER_FTMO_TICK_QUEUE_MAXSIZE,
        reconnect_min_seconds=config.CTRADER_FTMO_TICK_RECONNECT_MIN_SECONDS,
        reconnect_max_seconds=config.CTRADER_FTMO_TICK_RECONNECT_MAX_SECONDS,
        stale_seconds_btc=config.CTRADER_FTMO_TICK_STALE_SECONDS_BTC,
        stale_seconds_market=config.CTRADER_FTMO_TICK_STALE_SECONDS_MARKET,
        spool_path=SPOOL_DIR / "ctrader_ftmo_tick_spool.db",
        log_path=LOG_DIR / "ctrader_ftmo_tick.log",
    )
