"""cTrader Open API session: SDK loading, protobuf requests, the app+account
auth chain, and paginated historical tick fetch. One physical TCP connection
per call — tick_program never keeps a realtime session open.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.notify import write_system_event
from src.pipeline import DecodedHistoricalTick, decode_delta_ticks

#: cTrader requires a heartbeat "every 10 seconds" to keep a connection alive
#: (help.ctrader.com/open-api/connection/). The stock ctrader_open_api SDK
#: (pypi ctrader-open-api, currently 0.9.2 -- confirmed latest, no newer
#: release fixes this) only self-heartbeats once a connection has been idle
#: for *more than 20 seconds* (TcpProtocol._sendStrings, hardcoded), which is
#: already out of spec on its own. This margin (well under 10s) is what
#: _HeartbeatCompliantProtocol enforces instead -- see its subclass below.
_HEARTBEAT_IDLE_SECONDS = 8


@dataclass(frozen=True)
class CTraderSdk:
    Client: Any
    TcpProtocol: Any
    reactor: Any
    messages: Any
    model_messages: Any
    Protobuf: Any
    deferToThread: Any


class MissingCTraderSdk(RuntimeError):
    """Raised when the optional cTrader SDK is not installed."""


def _make_heartbeat_compliant_protocol(base_protocol_cls: Any) -> Any:
    """Subclass the SDK's TcpProtocol to heartbeat well within cTrader's
    documented 10-second requirement, instead of the SDK's own >20s default.

    This project's long-standing ~40% first-attempt "Connection lost"
    failure rate (Twisted dropping the connection ~35-45s into
    CTRADER_ACCOUNT_AUTH_SENT, never root-caused) lines up exactly with a
    connection that goes quiet for longer than the server tolerates before
    the stock SDK's own idle-heartbeat check fires. `_sendStrings` has no
    extension point for the idle threshold, so the whole method is
    overridden here -- the only change from the base implementation is the
    constant on the idle-check line.
    """

    class _HeartbeatCompliantProtocol(base_protocol_cls):
        def _sendStrings(self):
            size = len(self._send_queue)
            if not size:
                idle = self._lastSendMessageTime is None or (
                    datetime.datetime.now() - self._lastSendMessageTime
                ).total_seconds() > _HEARTBEAT_IDLE_SECONDS
                if idle:
                    self.heartbeat()
                return
            for _ in range(min(size, self.factory.numberOfMessagesToSendPerSecond)):
                is_canceled, data = self._send_queue.popleft()
                if is_canceled is not None and is_canceled():
                    continue
                self.sendString(data)
            self._lastSendMessageTime = datetime.datetime.now()

    return _HeartbeatCompliantProtocol


def load_ctrader_sdk() -> CTraderSdk:
    try:
        from ctrader_open_api import Client, Protobuf, TcpProtocol
        from ctrader_open_api.messages import OpenApiMessages_pb2 as messages
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model_messages
        from twisted.internet import reactor
        from twisted.internet.threads import deferToThread
    except ImportError as exc:
        raise MissingCTraderSdk("Install the official cTrader SDK: pip install ctrader-open-api twisted") from exc
    return CTraderSdk(Client=Client, TcpProtocol=_make_heartbeat_compliant_protocol(TcpProtocol), reactor=reactor,
                       messages=messages, model_messages=model_messages, Protobuf=Protobuf, deferToThread=deferToThread)


def new_client(settings: Any, sdk: CTraderSdk) -> Any:
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


def extract_payload(sdk: CTraderSdk, message: Any) -> Any:
    return sdk.Protobuf.extract(message)


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------


def make_application_auth_req(sdk: CTraderSdk, client_id: str, client_secret: str) -> Any:
    req = sdk.messages.ProtoOAApplicationAuthReq()
    req.clientId, req.clientSecret = client_id, client_secret
    return req


def make_account_auth_req(sdk: CTraderSdk, account_id: int, access_token: str) -> Any:
    req = sdk.messages.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId, req.accessToken = int(account_id), access_token
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


def make_symbol_by_id_req(sdk: CTraderSdk, account_id: int, ctrader_symbol_ids: list[int]) -> Any:
    req = sdk.messages.ProtoOASymbolByIdReq()
    req.ctidTraderAccountId = int(account_id)
    req.symbolId.extend(int(i) for i in ctrader_symbol_ids)
    return req


def make_get_tick_data_req(sdk: CTraderSdk, account_id: int, symbol_id: int, quote_type: str,
                            from_timestamp_ms: int, to_timestamp_ms: int) -> Any:
    req = sdk.messages.ProtoOAGetTickDataReq()
    req.ctidTraderAccountId = int(account_id)
    req.symbolId = int(symbol_id)
    req.fromTimestamp = int(from_timestamp_ms)
    req.toTimestamp = int(to_timestamp_ms)
    req.type = sdk.model_messages.ProtoOAQuoteType.Value(quote_type.upper())
    return req


def schedule_from_proto(proto_symbol: Any) -> dict[str, Any]:
    """Real weekly trading schedule + holiday calendar for one symbol.

    cTrader's ``ProtoOAInterval.startSecond``/``endSecond`` are seconds into
    a week that starts **Sunday 00:00** in the symbol's own
    ``scheduleTimeZone`` (verified against this project's own previously
    observed real close/open timestamps -- not just the field name).
    ``ProtoOAHoliday.holidayDate`` is a day count since the Unix epoch
    (1970-01-01), also verified against a real printed value.
    """
    return {
        "tz": str(proto_symbol.scheduleTimeZone),
        "intervals": [[int(iv.startSecond), int(iv.endSecond)] for iv in proto_symbol.schedule],
        "holidays": [
            {
                "name": str(h.name),
                "date_days": int(h.holidayDate),
                "recurring": bool(h.isRecurring),
                "start": int(h.startSecond),
                "end": int(h.endSecond),
            }
            for h in proto_symbol.holiday
        ],
    }


def remote_symbol_from_proto(proto_symbol: Any) -> dict[str, Any]:
    return {
        "ctrader_symbol_id": int(getattr(proto_symbol, "symbolId")),
        "symbol_name": str(getattr(proto_symbol, "symbolName")),
        "digits": int(getattr(proto_symbol, "digits")) if hasattr(proto_symbol, "digits") else None,
        "description": str(getattr(proto_symbol, "description", "")) or None,
        "enabled": bool(getattr(proto_symbol, "enabled")) if hasattr(proto_symbol, "enabled") else None,
        "pip_position": int(getattr(proto_symbol, "pipPosition")) if hasattr(proto_symbol, "pipPosition") else None,
    }


# ---------------------------------------------------------------------------
# Auth chain (over an open TCP connection)
# ---------------------------------------------------------------------------


def validate_configured_account(settings: Any, accounts: list[dict[str, object]]) -> dict[str, object]:
    """Validate that the configured account belongs to the token and expected environment."""
    selected = next(
        (a for a in accounts if int(a.get("ctidTraderAccountId") or 0) == int(settings.account_id or 0)), None,
    )
    if selected is None:
        raise RuntimeError(f"configured cTrader account {settings.account_id} is not granted to the current access token")
    expected_live = settings.env == "live"
    if bool(selected.get("isLive")) != expected_live:
        actual = "live" if selected.get("isLive") else "demo"
        raise RuntimeError(f"configured cTrader account {settings.account_id} is {actual}, expected {settings.env}")
    configured_login = str(settings.trader_login or "").strip()
    account_login = str(selected.get("traderLogin") or "").strip()
    if configured_login and configured_login != account_login:
        raise RuntimeError(f"configured trader login {configured_login} does not match account login {account_login}")
    return selected


def send_auth_chain(
    settings: Any, sdk: CTraderSdk, client: Any,
    on_authed: Callable[[], None], on_error: Callable[[Exception], None], *, context: str = "",
) -> None:
    """App auth -> account-list validation -> account auth -> on_authed()."""
    label = context or f"{settings.env} {settings.endpoint_label}"

    def _error_detail(response: Any) -> str | None:
        try:
            payload = extract_payload(sdk, response)
        except Exception:
            return None
        if payload.__class__.__name__ != "ProtoOAErrorRes":
            return None
        return f"{getattr(payload, 'errorCode', '')}: {getattr(payload, 'description', '')}".strip(": ") or "unknown cTrader error"

    def on_account_auth(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event("ctrader_client", "CTRADER_ACCOUNT_AUTH_FAILED", detail, level="ERROR", session=label)
            on_error(Exception(f"cTrader account auth rejected ({detail})"))
            return
        write_system_event("ctrader_client", "CTRADER_ACCOUNT_AUTH_OK", session=label)
        on_authed()

    def send_account_auth() -> None:
        req = make_account_auth_req(sdk, int(settings.account_id), settings.access_token)
        write_system_event("ctrader_client", "CTRADER_ACCOUNT_AUTH_SENT", session=label)
        client.send(req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_account_auth, on_error)

    def on_account_list(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event("ctrader_client", "CTRADER_ACCOUNT_LIST_FAILED", detail, level="ERROR", session=label)
            on_error(Exception(f"cTrader account list rejected ({detail})"))
            return
        try:
            payload = extract_payload(sdk, response)
            accounts = [
                {"ctidTraderAccountId": int(a.ctidTraderAccountId), "isLive": bool(getattr(a, "isLive", False)),
                 "traderLogin": str(getattr(a, "traderLogin", ""))}
                for a in getattr(payload, "ctidTraderAccount", [])
            ]
            validate_configured_account(settings, accounts)
        except Exception as exc:
            write_system_event("ctrader_client", "CTRADER_ACCOUNT_VALIDATION_FAILED", str(exc), level="ERROR", session=label)
            on_error(exc)
            return
        write_system_event("ctrader_client", "CTRADER_ACCOUNT_VALIDATION_OK", session=label)
        send_account_auth()

    def on_app_auth(response: Any) -> None:
        detail = _error_detail(response)
        if detail is not None:
            write_system_event("ctrader_client", "CTRADER_APP_AUTH_FAILED", detail, level="ERROR", session=label)
            on_error(Exception(f"cTrader application auth rejected ({detail})"))
            return
        write_system_event("ctrader_client", "CTRADER_APP_AUTH_OK", session=label)
        req = make_get_account_list_req(sdk, settings.access_token)
        write_system_event("ctrader_client", "CTRADER_ACCOUNT_LIST_SENT", session=label)
        client.send(req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_account_list, on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    write_system_event("ctrader_client", "CTRADER_APP_AUTH_SENT", session=label)
    client.send(app_req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_app_auth, on_error)


# ---------------------------------------------------------------------------
# Historical tick page fetch (used by backfill.py's per-window loop)
# ---------------------------------------------------------------------------


def next_history_page_to_timestamp(
    *, symbol: str, quote_type: str, from_timestamp_ms: int, current_to_timestamp_ms: int,
    page_ticks: list[DecodedHistoricalTick], unique_page_tick_count: int, has_more: bool,
) -> int | None:
    if not has_more:
        return None
    if not page_ticks:
        raise RuntimeError(f"cTrader returned hasMore=true with an empty {quote_type} page for {symbol}")
    oldest_ms = min(t.timestamp_ms for t in page_ticks)
    if oldest_ms <= from_timestamp_ms:
        raise RuntimeError(f"cTrader {quote_type} pagination is capped at the lower boundary for {symbol}; refusing partial history")
    if oldest_ms >= current_to_timestamp_ms or unique_page_tick_count <= 0:
        raise RuntimeError(f"cTrader {quote_type} pagination made no backward progress for {symbol}; refusing partial history")
    return oldest_ms


def fetch_history_side(
    settings: Any, sdk: CTraderSdk, client: Any, *, account_id: int, symbol_id: int, symbol_label: str,
    quote_type: str, from_timestamp_ms: int, to_timestamp_ms: int, ingest_run_id: str,
    on_complete: Callable[[list[DecodedHistoricalTick]], None], on_error: Callable[[Exception], None],
    should_abort: Callable[[], bool],
) -> None:
    """Fetch one BID or ASK side for a window, following cTrader's hasMore pagination."""
    decoded_ticks: list[DecodedHistoricalTick] = []
    seen_tick_counts: dict[tuple[int, int, str], int] = {}

    def _fetch(current_to_ms: int) -> None:
        if should_abort():
            return
        req = make_get_tick_data_req(sdk, account_id, symbol_id, quote_type, from_timestamp_ms, current_to_ms)
        write_system_event(
            "ctrader_client", "HISTORY_REQUEST_SENT",
            run=ingest_run_id[:8], symbol=symbol_label, side=quote_type,
            timeout_seconds=int(settings.response_timeout_seconds),
        )

        def on_ticks(message: Any) -> None:
            try:
                payload = extract_payload(sdk, message)
                page_ticks = decode_delta_ticks(getattr(payload, "tickData", []), quote_type)
                unique: list[DecodedHistoricalTick] = []
                counts: dict[tuple[int, int, str], int] = {}
                for t in page_ticks:
                    key = (int(t.timestamp_ms), int(t.raw_price), t.quote_type)
                    counts[key] = counts.get(key, 0) + 1
                    if counts[key] <= seen_tick_counts.get(key, 0):
                        continue
                    unique.append(t)
                for key, count in counts.items():
                    seen_tick_counts[key] = max(seen_tick_counts.get(key, 0), count)
                decoded_ticks.extend(unique)
                has_more = bool(getattr(payload, "hasMore", False))
                write_system_event(
                    "ctrader_client", "HISTORY_REQUEST_RESPONSE",
                    run=ingest_run_id[:8], symbol=symbol_label, side=quote_type,
                    ticks=len(page_ticks), unique=len(unique), more=has_more,
                )
                if should_abort():
                    return
                next_to_ms = next_history_page_to_timestamp(
                    symbol=symbol_label, quote_type=quote_type, from_timestamp_ms=from_timestamp_ms,
                    current_to_timestamp_ms=current_to_ms, page_ticks=page_ticks,
                    unique_page_tick_count=len(unique), has_more=has_more,
                )
                if next_to_ms is not None:
                    _fetch(next_to_ms)
                    return
                on_complete(decoded_ticks)
            except Exception as exc:
                on_error(exc)

        client.send(req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_ticks, on_error)

    _fetch(to_timestamp_ms)


# ---------------------------------------------------------------------------
# One-shot diagnostic calls (no ingest, no batcher)
# ---------------------------------------------------------------------------


def _failure_summary(failure: Any) -> str:
    value = getattr(failure, "value", None)
    if value is not None:
        return f"{value.__class__.__name__}: {value}"
    return f"{failure.__class__.__name__}: {failure}" if isinstance(failure, BaseException) else str(failure)


def fetch_account_list(settings: Any, timeout_seconds: int = 45) -> list[dict[str, object]]:
    """Authenticate the app and list accounts granted by the access token."""
    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: list[dict[str, object]] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        if errors or result:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_app_auth(_response: Any) -> None:
        req = make_get_account_list_req(sdk, settings.access_token)

        def on_accounts(message: Any) -> None:
            payload = extract_payload(sdk, message)
            for a in getattr(payload, "ctidTraderAccount", []):
                result.append({"ctidTraderAccountId": int(a.ctidTraderAccountId), "isLive": bool(getattr(a, "isLive", False)),
                               "brokerName": str(getattr(a, "brokerName", "")), "traderLogin": str(getattr(a, "traderLogin", ""))})
            stop_reactor(sdk, client)

        client.send(req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_accounts, on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.setConnectedCallback(
        lambda _c: client.send(app_req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_app_auth, on_error)
    )
    client.setDisconnectedCallback(lambda _c, reason: None if result else on_error(reason))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("account-list timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def fetch_remote_symbols_with_schedules(
    settings: Any, *, schedule_ids: Callable[[list[Any]], list[int]], timeout_seconds: int = 60,
) -> tuple[list[Any], dict[int, dict[str, Any]]]:
    """One connection, one auth chain: fetch the light symbol list, then --
    still on the same connection -- fetch schedule/holiday data for whichever
    symbol ids `schedule_ids(remotes)` picks (e.g. the ones that matched a
    tracked target).

    Twisted's global reactor can only run once per process
    (ReactorNotRestartable): calling two separate one-shot cTrader calls
    back to back in the same process crashes on the second `reactor.run()`.
    This was tried as two separate calls (fetch_remote_symbols() then a
    standalone fetch_remote_schedules()) during initial development on
    2026-08-25 and reproducibly raised ReactorNotRestartable on the second
    call -- do not "simplify" this back into that shape.
    """
    from src.configuration import RemoteSymbol

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    remotes_result: list[Any] = []
    schedules_result: dict[int, dict[str, Any]] = {}
    errors: list[Exception] = []
    done: list[bool] = []

    def on_error(failure: Any) -> None:
        if errors or done:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_authed() -> None:
        req = make_symbols_list_req(sdk, int(settings.account_id))

        def on_symbols(message: Any) -> None:
            payload = extract_payload(sdk, message)
            for proto_symbol in getattr(payload, "symbol", None) or getattr(payload, "symbols", []):
                remotes_result.append(RemoteSymbol(**remote_symbol_from_proto(proto_symbol)))
            wanted_ids = list(schedule_ids(remotes_result))
            if not wanted_ids:
                done.append(True)
                stop_reactor(sdk, client)
                return
            sched_req = make_symbol_by_id_req(sdk, int(settings.account_id), wanted_ids)

            def on_schedules(message2: Any) -> None:
                payload2 = extract_payload(sdk, message2)
                for proto_symbol in payload2.symbol:
                    schedules_result[int(proto_symbol.symbolId)] = schedule_from_proto(proto_symbol)
                done.append(True)
                stop_reactor(sdk, client)

            client.send(sched_req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_schedules, on_error)

        client.send(req, responseTimeoutInSeconds=settings.response_timeout_seconds).addCallbacks(on_symbols, on_error)

    client.setConnectedCallback(lambda _c: send_auth_chain(settings, sdk, client, on_authed, on_error))
    client.setDisconnectedCallback(lambda _c, reason: None if done else on_error(reason))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("symbol+schedule sync timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return remotes_result, schedules_result


def verify_account_auth(settings: Any, timeout_seconds: int = 45) -> dict[str, object]:
    """Verify cTrader application + account auth without touching SQL Server."""
    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: dict[str, object] = {}
    errors: list[Exception] = []
    label = f"auth-check | env={settings.env} | endpoint={settings.endpoint_label}"

    def on_error(failure: Any) -> None:
        if errors or result:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_authed() -> None:
        result.update({"account_auth_ok": True, "account_id": int(settings.account_id or 0),
                       "trader_login": settings.trader_login, "env": settings.env, "endpoint": settings.endpoint_label})
        stop_reactor(sdk, client)

    client.setConnectedCallback(lambda _c: send_auth_chain(settings, sdk, client, on_authed, on_error, context=label))
    client.setDisconnectedCallback(lambda _c, reason: None if result else on_error(reason))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("account auth check timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result
