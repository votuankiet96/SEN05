"""Small wrapper around the official ctrader-open-api Python SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MissingCTraderSdk(RuntimeError):
    """Raised when the optional cTrader SDK is not installed."""


@dataclass(frozen=True)
class CTraderSdk:
    Client: Any
    Protobuf: Any
    TcpProtocol: Any
    EndPoints: Any
    reactor: Any
    messages: Any
    model_messages: Any


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
            "`pip install ctrader-open-api`. It is not added to requirements.txt "
            "because this change avoids editing old project files without approval."
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


def make_subscribe_spots_req(sdk: CTraderSdk, account_id: int, symbol_ids: list[int]) -> Any:
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
