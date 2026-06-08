"""cTrader Open API service runners.

The SDK is callback/Twisted based, so the code here keeps the boundary small:
offline modules remain independently testable, while live API calls are isolated
to this file.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .batcher import TickBatcher
from .history import decode_delta_ticks, iter_tick_windows
from .models import RemoteSymbol, TargetSymbol, TickRecord
from .sdk import (
    CTraderSdk,
    load_ctrader_sdk,
    make_account_auth_req,
    make_application_auth_req,
    make_get_account_list_req,
    make_get_tick_data_req,
    make_subscribe_spots_req,
    make_symbols_list_req,
    remote_symbol_from_proto,
)
from .settings import TickRuntimeSettings
from .spool_sqlite import TickSpool
from .store_sql import TickSqlStore
from .symbol_matcher import build_symbol_matches

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    quote_type: str
    from_timestamp_ms: int
    to_timestamp_ms: int


def _extract_payload(sdk: CTraderSdk, message: Any) -> Any:
    return sdk.Protobuf.extract(message)


def _new_client(settings: TickRuntimeSettings, sdk: CTraderSdk) -> Any:
    return sdk.Client(settings.host, settings.port, sdk.TcpProtocol)


def _stop_reactor(sdk: CTraderSdk, client: Any | None = None) -> None:
    try:
        if client is not None:
            client.stopService()
    finally:
        try:
            sdk.reactor.stop()
        except Exception:
            pass


def _send_auth_chain(
    settings: TickRuntimeSettings,
    sdk: CTraderSdk,
    client: Any,
    on_authed: Callable[[], None],
    on_error: Callable[[Exception], None],
) -> None:
    def on_app_auth(_response: Any) -> None:
        account_req = make_account_auth_req(sdk, int(settings.account_id), settings.access_token)
        client.send(account_req).addCallbacks(lambda _r: on_authed(), on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.send(app_req).addCallbacks(on_app_auth, on_error)


def fetch_remote_symbols(settings: TickRuntimeSettings, timeout_seconds: int = 45) -> list[RemoteSymbol]:
    """Authenticate and fetch the account symbol list from cTrader."""
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = _new_client(settings, sdk)
    result: list[RemoteSymbol] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        errors.append(Exception(str(failure)))
        _stop_reactor(sdk, client)

    def on_authed() -> None:
        req = make_symbols_list_req(sdk, int(settings.account_id))

        def on_symbols(message: Any) -> None:
            payload = _extract_payload(sdk, message)
            symbols = getattr(payload, "symbol", None) or getattr(payload, "symbols", [])
            for proto_symbol in symbols:
                data = remote_symbol_from_proto(proto_symbol)
                result.append(RemoteSymbol(**data, raw=data))
            _stop_reactor(sdk, client)

        client.send(req).addCallbacks(on_symbols, on_error)

    client.setConnectedCallback(
        lambda _client: _send_auth_chain(settings, sdk, client, on_authed, on_error)
    )
    client.setDisconnectedCallback(lambda _client, reason: on_error(Exception(str(reason))))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("symbol sync timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def fetch_account_list(settings: TickRuntimeSettings, timeout_seconds: int = 45) -> list[dict[str, object]]:
    """Authenticate the app and list accounts granted by the access token."""
    required = {
        "CTRADER_CLIENT_ID": settings.client_id,
        "CTRADER_CLIENT_SECRET": settings.client_secret,
        "CTRADER_ACCESS_TOKEN": settings.access_token,
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = _new_client(settings, sdk)
    result: list[dict[str, object]] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        errors.append(Exception(str(failure)))
        _stop_reactor(sdk, client)

    def on_app_auth(_response: Any) -> None:
        req = make_get_account_list_req(sdk, settings.access_token)

        def on_accounts(message: Any) -> None:
            payload = _extract_payload(sdk, message)
            accounts = getattr(payload, "ctidTraderAccount", [])
            for account in accounts:
                result.append(
                    {
                        "ctidTraderAccountId": int(getattr(account, "ctidTraderAccountId")),
                        "isLive": bool(getattr(account, "isLive", False)),
                        "brokerName": str(getattr(account, "brokerName", "")),
                        "traderLogin": str(getattr(account, "traderLogin", "")),
                    }
                )
            _stop_reactor(sdk, client)

        client.send(req).addCallbacks(on_accounts, on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.setConnectedCallback(lambda _client: client.send(app_req).addCallbacks(on_app_auth, on_error))
    client.setDisconnectedCallback(lambda _client, reason: on_error(Exception(str(reason))))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("account-list timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def sync_symbols(settings: TickRuntimeSettings, store: TickSqlStore | None, apply: bool) -> list[str]:
    """Fetch remote symbols, match them, optionally persist to tick.SymbolMap."""
    remotes = fetch_remote_symbols(settings)
    matches = build_symbol_matches(settings.symbols, remotes)
    if apply and store is not None:
        store.upsert_symbol_matches(matches)

    lines = []
    for match in matches:
        remote_name = match.remote.symbol_name if match.remote else "NONE"
        remote_id = match.remote.ctrader_symbol_id if match.remote else "NONE"
        lines.append(
            f"{match.target.local_symbol}: {match.status} "
            f"score={match.score} remote={remote_name} id={remote_id} reason={match.reason}"
        )
    return lines


def run_live_ingest(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    run_label: str = "LIVE",
    note: str | None = None,
    duration_seconds: int | None = None,
) -> None:
    """Run continuous cTrader spot subscription and SQL/spool persistence."""
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if not matched:
        raise RuntimeError("no MATCHED symbols in tick.SymbolMap; run symbol-sync --apply first")

    sdk = load_ctrader_sdk()
    client = _new_client(settings, sdk)
    spool = TickSpool(settings.spool_path)
    batcher = TickBatcher(
        store=store,
        spool=spool,
        batch_size=settings.batch_size,
        flush_seconds=settings.flush_seconds,
    )
    by_remote_id = {remote.ctrader_symbol_id: (target, remote) for target, remote in matched.values()}
    ingest_run_id = store.start_ingest_run(
        run_label,
        note=note or f"cTrader {settings.env} {settings.endpoint_label}",
    )

    def on_error(failure: Any) -> None:
        logger.error("live ingest error: %s", failure)

    def flush_loop() -> None:
        batcher.flush()
        try:
            drained = batcher.drain_spool()
            if drained:
                logger.info("drained %d spooled ticks", drained)
        except Exception as exc:
            logger.warning("spool drain skipped: %s", exc)
        sdk.reactor.callLater(settings.flush_seconds, flush_loop)

    def on_message(_client: Any, message: Any) -> None:
        payload = _extract_payload(sdk, message)
        if payload.__class__.__name__ != "ProtoOASpotEvent":
            return
        symbol_id = int(getattr(payload, "symbolId"))
        if symbol_id not in by_remote_id:
            return
        target, remote = by_remote_id[symbol_id]
        record = TickRecord.from_live_spot(
            target=target,
            remote=remote,
            event=payload,
            received_at_utc=datetime.now(timezone.utc),
            ingest_run_id=ingest_run_id,
        )
        batcher.add(record)
        store.update_ingest_state(
            target,
            remote,
            record.tick_time_utc,
            record.source_timestamp_ms,
            status="LIVE",
        )

    def on_authed() -> None:
        symbol_ids = sorted(by_remote_id)
        req = make_subscribe_spots_req(sdk, int(settings.account_id), symbol_ids)
        client.send(req).addErrback(on_error)
        flush_loop()
        if duration_seconds is not None:
            sdk.reactor.callLater(int(duration_seconds), _stop_reactor, sdk, client)
        logger.info("subscribed to %d cTrader spot symbols", len(symbol_ids))

    def on_connected(_client: Any) -> None:
        _send_auth_chain(settings, sdk, client, on_authed, on_error)

    def on_shutdown() -> None:
        batcher.flush()
        try:
            store.finish_ingest_run(
                ingest_run_id,
                status="STOPPED",
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
            )
        finally:
            _stop_reactor(sdk, client)

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(lambda _client, reason: logger.warning("cTrader disconnected: %s", reason))
    client.setMessageReceivedCallback(on_message)
    client.startService()
    sdk.reactor.addSystemEventTrigger("before", "shutdown", on_shutdown)
    sdk.reactor.run()


def run_history_backfill(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
    symbols: list[str] | None = None,
) -> None:
    """Backfill historical BID/ASK ticks for matched symbols."""
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if symbols:
        requested = {symbol.upper() for symbol in symbols}
        matched = {key: value for key, value in matched.items() if key in requested}
    if not matched:
        raise RuntimeError("no matched symbols selected for backfill")

    sdk = load_ctrader_sdk()
    client = _new_client(settings, sdk)
    spool = TickSpool(settings.spool_path)
    batcher = TickBatcher(
        store=store,
        spool=spool,
        batch_size=settings.batch_size,
        flush_seconds=settings.flush_seconds,
    )
    ingest_run_id = store.start_ingest_run(
        "BACKFILL",
        note=f"{from_timestamp_ms}->{to_timestamp_ms}",
    )
    queue: deque[HistoryRequest] = deque()
    for target, remote in matched.values():
        for start_ms, end_ms in iter_tick_windows(from_timestamp_ms, to_timestamp_ms):
            queue.append(HistoryRequest(target, remote, "BID", start_ms, end_ms))
            queue.append(HistoryRequest(target, remote, "ASK", start_ms, end_ms))

    def on_error(failure: Any) -> None:
        logger.error("history backfill error: %s", failure)
        store.finish_ingest_run(
            ingest_run_id,
            status="FAILED",
            rows_inserted=batcher.rows_inserted,
            rows_spooled=batcher.rows_spooled,
            note=str(failure)[:1000],
        )
        _stop_reactor(sdk, client)

    def send_next() -> None:
        if not queue:
            batcher.flush()
            store.finish_ingest_run(
                ingest_run_id,
                status="DONE",
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
            )
            _stop_reactor(sdk, client)
            return

        item = queue.popleft()
        req = make_get_tick_data_req(
            sdk,
            int(settings.account_id),
            item.remote.ctrader_symbol_id,
            item.quote_type,
            item.from_timestamp_ms,
            item.to_timestamp_ms,
        )

        def on_ticks(message: Any) -> None:
            payload = _extract_payload(sdk, message)
            raw_ticks = getattr(payload, "tickData", [])
            decoded = decode_delta_ticks(raw_ticks, item.quote_type)
            records = [
                TickRecord.from_historical_tick(
                    item.target,
                    item.remote,
                    tick.quote_type,
                    tick.timestamp_ms,
                    tick.raw_price,
                    ingest_run_id=ingest_run_id,
                )
                for tick in decoded
            ]
            for record in records:
                batcher.add(record)
            batcher.flush()

            has_more = bool(getattr(payload, "hasMore", False))
            if has_more and decoded:
                oldest_ms = min(tick.timestamp_ms for tick in decoded)
                if oldest_ms > item.from_timestamp_ms:
                    queue.appendleft(
                        HistoryRequest(
                            item.target,
                            item.remote,
                            item.quote_type,
                            item.from_timestamp_ms,
                            oldest_ms - 1,
                        )
                    )
            send_next()

        client.send(req).addCallbacks(on_ticks, on_error)

    def on_authed() -> None:
        send_next()

    client.setConnectedCallback(
        lambda _client: _send_auth_chain(settings, sdk, client, on_authed, on_error)
    )
    client.setDisconnectedCallback(lambda _client, reason: on_error(Exception(str(reason))))
    client.startService()
    sdk.reactor.run()
