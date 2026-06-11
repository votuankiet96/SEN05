"""One-shot cTrader jobs: account-list, symbol-sync and historical backfill."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .notify import flush_notifications, notify_tick_report
from .runtime import (
    TickRuntimeSettings,
    ensure_fresh_access_token,
    extract_payload,
    load_ctrader_sdk,
    make_get_account_list_req,
    make_get_tick_data_req,
    make_symbols_list_req,
    new_client,
    remote_symbol_from_proto,
    send_auth_chain,
    stop_reactor,
)
from .spool import TickBatcher, TickSpool
from .store_sql import TickSqlStore, update_ingest_state_after_insert
from .symbols import RemoteSymbol, TargetSymbol, build_symbol_matches
from .ticks import TickRecord, decode_delta_ticks, iter_tick_windows, millis_from_utc, utc_from_millis

logger = logging.getLogger(__name__)


def _failure_summary(failure: Any) -> str:
    value = getattr(failure, "value", None)
    if value is not None:
        return f"{value.__class__.__name__}: {value}"
    if isinstance(failure, BaseException):
        return f"{failure.__class__.__name__}: {failure}"
    return str(failure)


def _with_response_timeout(
    settings: TickRuntimeSettings,
    request_timeout_seconds: float | None,
) -> TickRuntimeSettings:
    if request_timeout_seconds is None:
        if settings.response_timeout_seconds <= 0:
            raise ValueError("CTRADER_FTMO_TICK_RESPONSE_TIMEOUT_SECONDS must be greater than 0")
        return settings
    if request_timeout_seconds <= 0:
        raise ValueError("request timeout must be greater than 0")
    return replace(settings, response_timeout_seconds=float(request_timeout_seconds))


def _fmt_history_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "-"
    return utc_from_millis(timestamp_ms).strftime("%Y-%m-%d %H:%M:%SZ")


@dataclass(frozen=True)
class HistoryRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    quote_type: str
    from_timestamp_ms: int
    to_timestamp_ms: int


@dataclass(frozen=True)
class HistoryDepthRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    lookback_days: int
    from_timestamp_ms: int
    to_timestamp_ms: int


@dataclass(frozen=True)
class HistoryDepthResult:
    symbol: str
    ctrader_symbol_id: int
    ctrader_symbol_name: str
    deepest_available_days: int | None
    earliest_probe_from_ms: int | None
    earliest_probe_from_utc: str | None
    latest_probe_to_ms: int
    latest_probe_to_utc: str
    probes: list[dict[str, object]]


def fetch_remote_symbols(settings: TickRuntimeSettings, timeout_seconds: int = 45) -> list[RemoteSymbol]:
    """Authenticate and fetch the account symbol list from cTrader."""
    settings = ensure_fresh_access_token(settings, "symbol sync")
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: list[RemoteSymbol] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        errors.append(Exception(str(failure)))
        stop_reactor(sdk, client)

    def on_authed() -> None:
        req = make_symbols_list_req(sdk, int(settings.account_id))

        def on_symbols(message: Any) -> None:
            payload = extract_payload(sdk, message)
            symbols = getattr(payload, "symbol", None) or getattr(payload, "symbols", [])
            for proto_symbol in symbols:
                data = remote_symbol_from_proto(proto_symbol)
                result.append(RemoteSymbol(**data, raw=data))
            stop_reactor(sdk, client)

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_symbols, on_error)

    client.setConnectedCallback(lambda _client: send_auth_chain(settings, sdk, client, on_authed, on_error))
    client.setDisconnectedCallback(lambda _client, reason: None if result else on_error(Exception(str(reason))))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("symbol sync timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def fetch_account_list(settings: TickRuntimeSettings, timeout_seconds: int = 45) -> list[dict[str, object]]:
    """Authenticate the app and list accounts granted by the access token."""
    settings = ensure_fresh_access_token(settings, "account list")
    required = {
        "CTRADER_CLIENT_ID": settings.client_id,
        "CTRADER_CLIENT_SECRET": settings.client_secret,
        "CTRADER_ACCESS_TOKEN": settings.access_token,
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: list[dict[str, object]] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        errors.append(Exception(str(failure)))
        stop_reactor(sdk, client)

    def on_app_auth(_response: Any) -> None:
        req = make_get_account_list_req(sdk, settings.access_token)

        def on_accounts(message: Any) -> None:
            payload = extract_payload(sdk, message)
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
            stop_reactor(sdk, client)

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_accounts, on_error)

    from .runtime import make_application_auth_req

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.setConnectedCallback(
        lambda _client: client.send(
            app_req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_app_auth, on_error)
    )
    client.setDisconnectedCallback(lambda _client, reason: None if result else on_error(Exception(str(reason))))
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


def run_history_backfill(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
    symbols: list[str] | None = None,
    request_timeout_seconds: float | None = None,
) -> None:
    """Backfill historical BID/ASK ticks for matched symbols."""
    settings = ensure_fresh_access_token(settings, "history backfill")
    settings = _with_response_timeout(settings, request_timeout_seconds)
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
    client = new_client(settings, sdk)
    spool = TickSpool(settings.spool_path)

    def on_inserted(records: list[TickRecord], _inserted: int) -> None:
        update_ingest_state_after_insert(store, matched, records)

    def on_spooled(records: list[TickRecord], spooled: int, exc: Exception) -> None:
        symbols_in_batch = sorted({record.local_symbol.upper() for record in records})
        notify_tick_report(
            "WARNING",
            "Tick backfill SQL write failed; ticks were spooled",
            conclusion="The failed backfill batch was saved to the local spool. No data is lost yet.",
            action="Check SQL Server or the database connection. You can rerun backfill after the database is stable.",
            details=[
                ("Spooled ticks", spooled),
                ("Symbol", f"{','.join(symbols_in_batch[:8])}{'...' if len(symbols_in_batch) > 8 else ''}"),
                ("Spool file", settings.spool_path),
            ],
            technical=[("SQL error", str(exc)[:300])],
            throttle_key="tick-backfill-spool-write-failed",
            throttle_seconds=300,
        )

    batcher = TickBatcher(
        store=store,
        spool=spool,
        batch_size=settings.batch_size,
        flush_seconds=settings.flush_seconds,
        on_inserted=on_inserted,
        on_spooled=on_spooled,
    )
    ingest_run_id = store.start_ingest_run("BACKFILL", note=f"{from_timestamp_ms}->{to_timestamp_ms}")
    notify_tick_report(
        "INFO",
        "Tick backfill started",
        conclusion="The service started downloading historical ticks from cTrader.",
        action="Monitor the dashboard or log viewer. Large multi-year backfills can take a long time.",
        details=[
            ("Run ID", ingest_run_id),
            ("Symbol", ",".join(sorted(matched))),
            ("From", utc_from_millis(from_timestamp_ms).isoformat()),
            ("To", utc_from_millis(to_timestamp_ms).isoformat()),
        ],
        throttle_key="tick-backfill-started",
        throttle_seconds=30,
    )
    queue: deque[HistoryRequest] = deque()
    state: dict[str, object] = {"finished": False, "error": None}
    for target, remote in matched.values():
        for start_ms, end_ms in iter_tick_windows(from_timestamp_ms, to_timestamp_ms):
            queue.append(HistoryRequest(target, remote, "BID", start_ms, end_ms))
            queue.append(HistoryRequest(target, remote, "ASK", start_ms, end_ms))
    logger.info(
        "BACKFILL START | symbols=%s | window=%s -> %s | requests=%d | timeout=%.0fs | run=%s",
        ",".join(sorted(matched)),
        _fmt_history_time(from_timestamp_ms),
        _fmt_history_time(to_timestamp_ms),
        len(queue),
        settings.response_timeout_seconds,
        ingest_run_id[:8],
    )

    def on_error(failure: Any) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        summary = _failure_summary(failure)
        state["error"] = summary
        logger.error(
            "BACKFILL FAILED | symbols=%s | inserted=%s | spooled=%s | error=%s | run=%s",
            ",".join(sorted(matched)),
            f"{batcher.rows_inserted:,}",
            f"{batcher.rows_spooled:,}",
            summary,
            ingest_run_id[:8],
        )
        store.finish_ingest_run(
            ingest_run_id,
            status="FAILED",
            rows_inserted=batcher.rows_inserted,
            rows_spooled=batcher.rows_spooled,
            note=summary,
        )
        notify_tick_report(
            "ERROR",
            "Tick backfill failed",
            conclusion="Backfill failed before completion.",
            action="Check logs, cTrader token, network, and database connectivity. After fixing the issue, rerun the same time window.",
            details=[
                ("Run ID", ingest_run_id),
                ("Inserted ticks", batcher.rows_inserted),
                ("Spooled ticks", batcher.rows_spooled),
            ],
            technical=[("Error", summary[:600])],
            throttle_key="tick-backfill-failed",
            throttle_seconds=60,
        )
        flush_notifications()
        stop_reactor(sdk, client)

    def send_next() -> None:
        if not queue:
            state["finished"] = True
            batcher.flush()
            store.finish_ingest_run(
                ingest_run_id,
                status="DONE",
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
            )
            logger.info(
                "BACKFILL DONE  | inserted=%s | spooled=%s | run=%s",
                f"{batcher.rows_inserted:,}",
                f"{batcher.rows_spooled:,}",
                ingest_run_id[:8],
            )
            notify_tick_report(
                "INFO",
                "Tick backfill completed",
                conclusion="Historical tick backfill completed.",
                action="Open the dashboard or run tick_status.ps1 to review the new data.",
                details=[
                    ("Run ID", ingest_run_id),
                    ("Inserted ticks", batcher.rows_inserted),
                    ("Spooled ticks", batcher.rows_spooled),
                ],
                throttle_key="tick-backfill-completed",
                throttle_seconds=30,
            )
            flush_notifications()
            stop_reactor(sdk, client)
            return

        item = queue.popleft()
        logger.debug(
            "BACKFILL REQUEST | %s %-3s | ask API window=%s -> %s | remaining=%d | run=%s",
            item.target.local_symbol,
            item.quote_type,
            _fmt_history_time(item.from_timestamp_ms),
            _fmt_history_time(item.to_timestamp_ms),
            len(queue),
            ingest_run_id[:8],
        )
        req = make_get_tick_data_req(
            sdk,
            int(settings.account_id),
            item.remote.ctrader_symbol_id,
            item.quote_type,
            item.from_timestamp_ms,
            item.to_timestamp_ms,
        )

        def on_ticks(message: Any) -> None:
            payload = extract_payload(sdk, message)
            raw_ticks = getattr(payload, "tickData", [])
            decoded = decode_delta_ticks(raw_ticks, item.quote_type)
            earliest_ms = min((tick.timestamp_ms for tick in decoded), default=None)
            latest_ms = max((tick.timestamp_ms for tick in decoded), default=None)
            for tick in decoded:
                batcher.add(
                    TickRecord.from_historical_tick(
                        item.target,
                        item.remote,
                        tick.quote_type,
                        tick.timestamp_ms,
                        tick.raw_price,
                        ingest_run_id=ingest_run_id,
                    )
                )
            batcher.flush()

            has_more = bool(getattr(payload, "hasMore", False))
            logger.info(
                "BACKFILL %-6s %-3s | ticks=%5s | data=%s -> %s | more=%s | total=%s | spool=%s",
                item.target.local_symbol,
                item.quote_type,
                f"{len(decoded):,}",
                _fmt_history_time(earliest_ms),
                _fmt_history_time(latest_ms),
                "yes" if has_more else "no ",
                f"{batcher.rows_inserted:,}",
                f"{batcher.rows_spooled:,}",
            )
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

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_ticks, on_error)

    client.setConnectedCallback(lambda _client: send_auth_chain(settings, sdk, client, send_next, on_error))
    client.setDisconnectedCallback(
        lambda _client, reason: None if state["finished"] else on_error(Exception(str(reason)))
    )
    client.startService()
    sdk.reactor.run()
    if state["error"]:
        raise RuntimeError(f"history backfill failed: {state['error']}")


def _default_probe_days(max_days: int) -> list[int]:
    candidates = [
        1,
        7,
        30,
        90,
        180,
        365,
        730,
        1095,
        1460,
        1825,
        2555,
        3650,
        5475,
        7300,
        9125,
        10950,
        14600,
        18250,
        20000,
    ]
    max_days = max(1, int(max_days))
    days = [day for day in candidates if day <= max_days]
    if max_days not in days:
        days.append(max_days)
    return sorted(set(days))


def probe_history_depth(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    symbols: list[str] | None = None,
    max_days: int = 20000,
    to_timestamp_ms: int | None = None,
    probe_window_days: int = 7,
    timeout_seconds: int = 300,
    request_timeout_seconds: float | None = None,
) -> list[HistoryDepthResult]:
    """Probe how far back cTrader has BID ticks for matched symbols."""
    settings = ensure_fresh_access_token(settings, "history depth probe")
    settings = _with_response_timeout(settings, request_timeout_seconds)
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if symbols:
        requested = {symbol.upper() for symbol in symbols}
        matched = {key: value for key, value in matched.items() if key in requested}
    if not matched:
        raise RuntimeError("no matched symbols selected for history depth probe")

    to_ms = int(to_timestamp_ms or millis_from_utc(datetime.now(timezone.utc)))
    day_ms = 24 * 60 * 60 * 1000
    window_ms = max(1, min(7, int(probe_window_days))) * day_ms
    def build_depth_request(target: TargetSymbol, remote: RemoteSymbol, lookback_days: int) -> HistoryDepthRequest:
        start_ms = max(0, to_ms - int(lookback_days) * day_ms)
        end_ms = min(to_ms, start_ms + window_ms - 1)
        return HistoryDepthRequest(
            target=target,
            remote=remote,
            lookback_days=int(lookback_days),
            from_timestamp_ms=start_ms,
            to_timestamp_ms=end_ms,
        )

    queue: deque[HistoryDepthRequest] = deque()
    for target, remote in matched.values():
        for lookback_days in _default_probe_days(max_days):
            queue.append(build_depth_request(target, remote, lookback_days))

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    probes_by_symbol: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in matched}
    errors: list[Exception] = []
    refine_rounds = {"count": 0}

    def enqueue_refinement_requests() -> int:
        if refine_rounds["count"] >= 12:
            return 0
        added = 0
        for symbol, probes in probes_by_symbol.items():
            tested = {int(probe["lookback_days"]) for probe in probes}
            successes = sorted(int(probe["lookback_days"]) for probe in probes if int(probe["tick_count"]) > 0)
            failures = sorted(int(probe["lookback_days"]) for probe in probes if int(probe["tick_count"]) == 0)
            if not successes:
                continue
            low = successes[-1]
            higher_failures = [day for day in failures if day > low]
            high = higher_failures[0] if higher_failures else int(max_days)
            if high - low <= 1:
                continue
            mid = low + ((high - low) // 2)
            if mid in tested:
                continue
            target, remote = matched[symbol]
            queue.append(build_depth_request(target, remote, mid))
            added += 1
        if added:
            refine_rounds["count"] += 1
        return added

    def on_error(failure: Any) -> None:
        errors.append(Exception(str(failure)))
        stop_reactor(sdk, client)

    def send_next() -> None:
        if not queue:
            if enqueue_refinement_requests():
                send_next()
                return
            stop_reactor(sdk, client)
            return
        item = queue.popleft()
        req = make_get_tick_data_req(
            sdk,
            int(settings.account_id),
            item.remote.ctrader_symbol_id,
            "BID",
            item.from_timestamp_ms,
            item.to_timestamp_ms,
        )

        def on_ticks(message: Any) -> None:
            payload = extract_payload(sdk, message)
            raw_ticks = getattr(payload, "tickData", [])
            decoded = decode_delta_ticks(raw_ticks, "BID") if raw_ticks else []
            earliest_tick_ms = min((tick.timestamp_ms for tick in decoded), default=None)
            latest_tick_ms = max((tick.timestamp_ms for tick in decoded), default=None)
            probes_by_symbol[item.target.local_symbol.upper()].append(
                {
                    "lookback_days": item.lookback_days,
                    "from_ms": item.from_timestamp_ms,
                    "from_utc": utc_from_millis(item.from_timestamp_ms).isoformat(),
                    "to_ms": item.to_timestamp_ms,
                    "to_utc": utc_from_millis(item.to_timestamp_ms).isoformat(),
                    "tick_count": len(decoded),
                    "has_more": bool(getattr(payload, "hasMore", False)),
                    "earliest_tick_ms": earliest_tick_ms,
                    "earliest_tick_utc": utc_from_millis(earliest_tick_ms).isoformat()
                    if earliest_tick_ms is not None
                    else None,
                    "latest_tick_ms": latest_tick_ms,
                    "latest_tick_utc": utc_from_millis(latest_tick_ms).isoformat()
                    if latest_tick_ms is not None
                    else None,
                }
            )
            send_next()

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_ticks, on_error)

    client.setConnectedCallback(lambda _client: send_auth_chain(settings, sdk, client, send_next, on_error))
    client.setDisconnectedCallback(lambda _client, reason: None if not queue else on_error(Exception(str(reason))))
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("history-depth probe timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]

    results: list[HistoryDepthResult] = []
    for symbol, probes in sorted(probes_by_symbol.items()):
        target, remote = matched[symbol]
        found = [probe for probe in probes if int(probe["tick_count"]) > 0]
        deepest = max(found, key=lambda probe: int(probe["lookback_days"])) if found else None
        results.append(
            HistoryDepthResult(
                symbol=symbol,
                ctrader_symbol_id=remote.ctrader_symbol_id,
                ctrader_symbol_name=remote.symbol_name,
                deepest_available_days=int(deepest["lookback_days"]) if deepest else None,
                earliest_probe_from_ms=int(deepest["from_ms"]) if deepest else None,
                earliest_probe_from_utc=str(deepest["from_utc"]) if deepest else None,
                latest_probe_to_ms=to_ms,
                latest_probe_to_utc=utc_from_millis(to_ms).isoformat(),
                probes=probes,
            )
        )
    return results
