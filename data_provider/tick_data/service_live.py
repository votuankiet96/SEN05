"""Continuous cTrader live tick ingest."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .notify import flush_notifications, notify_tick_report
from .runtime import (
    TickRuntimeSettings,
    ensure_fresh_access_token,
    extract_payload,
    load_ctrader_sdk,
    make_subscribe_spots_req,
    new_client,
    send_auth_chain,
    stop_reactor,
)
from .runtime_lock import TickLiveRuntimeGuard, is_tick_live_shutdown_requested
from .spool import TickBatcher, TickSpool
from .store_sql import TickSqlStore, update_ingest_state_after_insert
from .ticks import TickRecord

logger = logging.getLogger(__name__)


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reconnect_delay_seconds(attempt: int, min_seconds: float, max_seconds: float) -> float:
    """Compute bounded exponential backoff for live cTrader reconnects."""
    attempt = max(1, int(attempt))
    return min(float(max_seconds), float(min_seconds) * (2 ** (attempt - 1)))


def run_live_ingest(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    run_label: str = "LIVE",
    note: str | None = None,
    duration_seconds: int | None = None,
    handoff_existing: bool = True,
) -> None:
    """Run continuous cTrader spot subscription and SQL/spool persistence."""
    settings = ensure_fresh_access_token(settings, "live ingest startup")
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if not matched:
        raise RuntimeError("no MATCHED symbols in tick.SymbolMap; run symbol-sync --apply first")

    sdk = load_ctrader_sdk()
    guard = TickLiveRuntimeGuard(logger, allow_handoff=handoff_existing)
    if not guard.acquire():
        raise RuntimeError("another tick live runtime is already active")

    client_ref: dict[str, Any | None] = {"client": None}
    state = {
        "stopping": False,
        "finished": False,
        "flush_loop_started": False,
        "heartbeat_log_loop_started": False,
        "discord_report_loop_started": False,
        "shutdown_loop_started": False,
        "reconnect_attempt": 0,
        "stop_reason": None,
    }
    spool = TickSpool(settings.spool_path)

    def on_inserted(records: list[TickRecord], _inserted: int) -> None:
        update_ingest_state_after_insert(store, matched, records)

    def on_spooled(records: list[TickRecord], spooled: int, exc: Exception) -> None:
        symbols = sorted({record.local_symbol.upper() for record in records})
        notify_tick_report(
            "WARNING",
            "Tick SQL write failed; ticks were spooled",
            conclusion="The ticks were saved to the local spool. No data is lost yet.",
            action="Check SQL Server or the database connection. When the database recovers, the service will retry the spool automatically.",
            details=[
                ("Spooled ticks", spooled),
                ("Symbol", f"{','.join(symbols[:8])}{'...' if len(symbols) > 8 else ''}"),
                ("Spool file", settings.spool_path),
            ],
            technical=[("SQL error", str(exc)[:300])],
            throttle_key="tick-spool-write-failed",
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
    by_remote_id = {remote.ctrader_symbol_id: (target, remote) for target, remote in matched.values()}
    symbol_codes = tuple(sorted({target.local_symbol.upper() for target, _remote in by_remote_id.values()}))
    live_metrics: dict[str, Any] = {
        "records_received": 0,
        "symbol_counts": {symbol: 0 for symbol in symbol_codes},
        "last_tick_by_symbol": {},
    }
    heartbeat_snapshot: dict[str, Any] = {
        "records_received": 0,
        "rows_inserted": 0,
        "rows_spooled": 0,
        "symbol_counts": {symbol: 0 for symbol in symbol_codes},
    }
    discord_snapshot: dict[str, Any] = {
        "records_received": 0,
        "rows_inserted": 0,
        "rows_spooled": 0,
        "symbol_counts": {symbol: 0 for symbol in symbol_codes},
    }
    ingest_run_id = store.start_ingest_run(
        run_label,
        note=note or f"cTrader {settings.env} {settings.endpoint_label}",
    )
    notify_tick_report(
        "INFO",
        "Tick live ingest started",
        conclusion="The service has started collecting realtime ticks from cTrader.",
        action="No action is needed if the dashboard and checker stay OK.",
        details=[
            ("Environment", settings.env),
            ("Symbols", len(by_remote_id)),
            ("Run ID", ingest_run_id),
            ("Allow takeover", handoff_existing),
        ],
        throttle_key="tick-live-started",
        throttle_seconds=30,
    )

    def finish_run_once() -> None:
        if state["finished"]:
            return
        state["finished"] = True
        status = "STOPPED"
        reason = str(state["stop_reason"] or "reactor shutdown")
        try:
            batcher.flush()
            store.finish_ingest_run(
                ingest_run_id,
                status=status,
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
                note=reason,
            )
            notify_tick_report(
                "INFO",
                "Tick live ingest stopped",
                conclusion="Tick live stopped cleanly.",
                action="If this was not intentional, check the supervisor/watchdog and confirm a new process was started.",
                details=[
                    ("Run ID", ingest_run_id),
                    ("Reason", reason),
                    ("Inserted ticks", batcher.rows_inserted),
                    ("Spooled ticks", batcher.rows_spooled),
                ],
                throttle_key="tick-live-stopped",
                throttle_seconds=30,
            )
        except Exception as exc:
            logger.exception("failed to finish tick live ingest run")
            notify_tick_report(
                "ERROR",
                "Tick live ingest shutdown failed",
                conclusion="The service failed while closing the tick live run.",
                action="Run tick_status.ps1 to check the PID file, DB lock, and supervisor state.",
                details=[("Run ID", ingest_run_id)],
                technical=[("Error", str(exc)[:400])],
                throttle_key="tick-live-finish-failed",
                throttle_seconds=60,
            )
        finally:
            guard.release()
            flush_notifications()

    def current_spool_backlog() -> int | None:
        try:
            return spool.count()
        except Exception as exc:
            logger.warning("spool count skipped in live summary: %s", exc)
            return None

    def live_interval_summary(previous: dict[str, Any]) -> dict[str, Any]:
        current_symbol_counts = dict(live_metrics["symbol_counts"])
        previous_symbol_counts = dict(previous.get("symbol_counts") or {})
        symbol_deltas = {
            symbol: int(current_symbol_counts.get(symbol, 0))
            - int(previous_symbol_counts.get(symbol, 0))
            for symbol in symbol_codes
        }
        last_by_symbol = live_metrics["last_tick_by_symbol"]
        last_tick = (
            max(last_by_symbol.values())
            if last_by_symbol
            else batcher.last_tick_utc
        )
        summary = {
            "records_received": int(live_metrics["records_received"])
            - int(previous.get("records_received", 0)),
            "rows_inserted": int(batcher.rows_inserted)
            - int(previous.get("rows_inserted", 0)),
            "rows_spooled": int(batcher.rows_spooled)
            - int(previous.get("rows_spooled", 0)),
            "spool_backlog": current_spool_backlog(),
            "pending_batch": len(getattr(batcher, "_pending", [])),
            "active_symbols": sum(1 for count in symbol_deltas.values() if count > 0),
            "total_symbols": len(symbol_codes),
            "last_tick_utc": last_tick,
            "symbol_deltas": symbol_deltas,
        }
        previous["records_received"] = int(live_metrics["records_received"])
        previous["rows_inserted"] = int(batcher.rows_inserted)
        previous["rows_spooled"] = int(batcher.rows_spooled)
        previous["symbol_counts"] = current_symbol_counts
        return summary

    def format_top_symbols(symbol_deltas: dict[str, int], limit: int = 6) -> str:
        top = [
            (symbol, count)
            for symbol, count in sorted(
                symbol_deltas.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if count > 0
        ][:limit]
        return ", ".join(f"{symbol}:{count:,}" for symbol, count in top) or "-"

    def format_no_tick_symbols(symbol_deltas: dict[str, int], limit: int = 8) -> str:
        symbols = [symbol for symbol in symbol_codes if symbol_deltas.get(symbol, 0) <= 0]
        if not symbols:
            return "-"
        suffix = f"... (+{len(symbols) - limit})" if len(symbols) > limit else ""
        return ",".join(symbols[:limit]) + suffix

    def heartbeat_log_loop() -> None:
        if state["stopping"]:
            return
        summary = live_interval_summary(heartbeat_snapshot)
        logger.info(
            "LIVE HEARTBEAT | run=%s | window=%ss | received=%s | inserted=%s | "
            "spooled=%s | spool_backlog=%s | pending=%s | active_symbols=%s/%s | "
            "last_tick=%s | top=%s",
            ingest_run_id,
            settings.heartbeat_log_seconds,
            f"{summary['records_received']:,}",
            f"{summary['rows_inserted']:,}",
            f"{summary['rows_spooled']:,}",
            "-" if summary["spool_backlog"] is None else f"{summary['spool_backlog']:,}",
            f"{summary['pending_batch']:,}",
            summary["active_symbols"],
            summary["total_symbols"],
            _format_utc(summary["last_tick_utc"]),
            format_top_symbols(summary["symbol_deltas"]),
        )
        sdk.reactor.callLater(settings.heartbeat_log_seconds, heartbeat_log_loop)

    def discord_report_loop() -> None:
        if state["stopping"]:
            return
        summary = live_interval_summary(discord_snapshot)
        window = f"{max(1, int(settings.discord_report_seconds / 60))}m"
        no_new_ticks = summary["records_received"] <= 0
        level = "WARNING" if no_new_ticks else "INFO"
        notify_tick_report(
            level,
            f"Tick live {window} data report",
            conclusion=(
                "Live tick ingest is running, but no new tick records were received in this window."
                if no_new_ticks
                else (
                    "Live tick ingest is running and wrote "
                    f"{summary['rows_inserted']:,} tick rows in the last {window}."
                )
            ),
            action=(
                "Run tick_status.ps1 and check cTrader connectivity if this repeats."
                if no_new_ticks
                else "No action is needed."
            ),
            details=[
                ("Run ID", ingest_run_id),
                ("Window", window),
                ("Received records", f"{summary['records_received']:,}"),
                ("SQL inserted", f"{summary['rows_inserted']:,}"),
                ("Spooled", f"{summary['rows_spooled']:,}"),
                (
                    "Spool backlog",
                    "-" if summary["spool_backlog"] is None else f"{summary['spool_backlog']:,}",
                ),
                (
                    "Active symbols",
                    f"{summary['active_symbols']}/{summary['total_symbols']}",
                ),
                ("Last tick UTC", _format_utc(summary["last_tick_utc"])),
            ],
            technical=[
                ("Top symbols", format_top_symbols(summary["symbol_deltas"])),
                ("No tick symbols", format_no_tick_symbols(summary["symbol_deltas"])),
                ("Pending batch", f"{summary['pending_batch']:,}"),
            ],
        )
        flush_notifications()
        sdk.reactor.callLater(settings.discord_report_seconds, discord_report_loop)

    def request_stop(reason: str) -> None:
        if state["stopping"]:
            return
        state["stopping"] = True
        state["stop_reason"] = reason
        logger.info("stopping cTrader live ingest: %s", reason)
        stop_reactor(sdk, client_ref.get("client"))

    def on_error(failure: Any) -> None:
        logger.error("live ingest error: %s", failure)
        notify_tick_report(
            "ERROR",
            "Tick live ingest error",
            conclusion="Tick live hit an error while running.",
            action="The supervisor will try to restart it. If this repeats, open the log viewer and check token, cTrader, and database connectivity.",
            technical=[("Error", str(failure)[:600])],
            throttle_key="tick-live-error",
            throttle_seconds=300,
        )
        client = client_ref.get("client")
        if client is not None and not state["stopping"]:
            try:
                client.stopService()
            except Exception:
                logger.exception("failed to stop cTrader client after live ingest error")

    def flush_loop() -> None:
        if state["stopping"]:
            return
        batcher.flush()
        try:
            drained = batcher.drain_spool()
            if drained:
                logger.info("drained %d spooled ticks", drained)
        except Exception as exc:
            logger.warning("spool drain skipped: %s", exc)
        sdk.reactor.callLater(settings.flush_seconds, flush_loop)

    def shutdown_signal_loop() -> None:
        if state["stopping"]:
            return
        if is_tick_live_shutdown_requested():
            request_stop("graceful shutdown requested by newer tick live instance")
            return
        sdk.reactor.callLater(10, shutdown_signal_loop)

    def on_message(_client: Any, message: Any) -> None:
        payload = extract_payload(sdk, message)
        if payload.__class__.__name__ != "ProtoOASpotEvent":
            return
        symbol_id = int(getattr(payload, "symbolId"))
        if symbol_id not in by_remote_id:
            return
        target, remote = by_remote_id[symbol_id]
        records = TickRecord.from_live_spot_updates(
            target=target,
            remote=remote,
            event=payload,
            received_at_utc=datetime.now(timezone.utc),
            ingest_run_id=ingest_run_id,
        )
        for record in records:
            batcher.add(record)
        if records:
            symbol = target.local_symbol.upper()
            live_metrics["records_received"] = int(live_metrics["records_received"]) + len(records)
            live_metrics["symbol_counts"][symbol] = int(
                live_metrics["symbol_counts"].get(symbol, 0)
            ) + len(records)
            live_metrics["last_tick_by_symbol"][symbol] = max(
                record.tick_time_utc.astimezone(timezone.utc)
                for record in records
            )

    def on_authed() -> None:
        symbol_ids = sorted(by_remote_id)
        client = client_ref.get("client")
        if client is None:
            on_error(RuntimeError("cTrader client is not available after auth"))
            return
        req = make_subscribe_spots_req(sdk, int(settings.account_id), symbol_ids)
        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addErrback(on_error)
        if not state["flush_loop_started"]:
            state["flush_loop_started"] = True
            flush_loop()
        if settings.heartbeat_log_seconds > 0 and not state["heartbeat_log_loop_started"]:
            state["heartbeat_log_loop_started"] = True
            sdk.reactor.callLater(settings.heartbeat_log_seconds, heartbeat_log_loop)
        if settings.discord_report_seconds > 0 and not state["discord_report_loop_started"]:
            state["discord_report_loop_started"] = True
            sdk.reactor.callLater(settings.discord_report_seconds, discord_report_loop)
        if not state["shutdown_loop_started"]:
            state["shutdown_loop_started"] = True
            shutdown_signal_loop()
        state["reconnect_attempt"] = 0
        logger.info("subscribed to %d cTrader spot symbols", len(symbol_ids))
        notify_tick_report(
            "INFO",
            "Tick live subscription active",
            conclusion="The cTrader connection is active and realtime tick subscription is running.",
            action="No action is needed.",
            details=[
                ("Symbols", len(symbol_ids)),
                ("Run ID", ingest_run_id),
            ],
            throttle_key="tick-live-subscription-active",
            throttle_seconds=60,
        )

    def on_connected(_client: Any) -> None:
        nonlocal settings
        settings = ensure_fresh_access_token(settings, "live reconnect", logger)
        send_auth_chain(settings, sdk, _client, on_authed, on_error)

    def start_client() -> None:
        if state["stopping"]:
            return
        client = new_client(settings, sdk)
        client_ref["client"] = client
        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message)
        client.startService()

    def on_disconnected(_client: Any, reason: Any) -> None:
        if state["stopping"]:
            logger.info("cTrader disconnected during shutdown: %s", reason)
            return
        state["reconnect_attempt"] = int(state["reconnect_attempt"]) + 1
        delay = reconnect_delay_seconds(
            int(state["reconnect_attempt"]),
            settings.reconnect_min_seconds,
            settings.reconnect_max_seconds,
        )
        logger.warning(
            "cTrader disconnected: %s; reconnecting in %.1f seconds (attempt=%d)",
            reason,
            delay,
            int(state["reconnect_attempt"]),
        )
        notify_tick_report(
            "WARNING",
            "Tick live disconnected; reconnect scheduled",
            conclusion="The cTrader connection was interrupted.",
            action="No immediate action is needed. The service will reconnect automatically.",
            details=[
                ("Reconnect in", f"{delay:.1f} seconds"),
                ("Attempt", state["reconnect_attempt"]),
            ],
            technical=[("Reason", reason)],
            throttle_key="tick-live-disconnected",
            throttle_seconds=300,
        )
        sdk.reactor.callLater(delay, start_client)

    def on_shutdown() -> None:
        try:
            finish_run_once()
        finally:
            state["stopping"] = True

    if duration_seconds is not None:
        sdk.reactor.callLater(int(duration_seconds), request_stop, "duration elapsed")
    sdk.reactor.addSystemEventTrigger("before", "shutdown", on_shutdown)
    try:
        start_client()
        sdk.reactor.run()
    finally:
        finish_run_once()
