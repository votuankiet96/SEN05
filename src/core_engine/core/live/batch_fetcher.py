"""TradingView WebSocket connection-group worker for one live batch."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

from core_engine.core.live import runtime as _runtime
from core_engine.core.live.batch_metrics import record_accepted as _record_batch_accepted
from core_engine.core.live.db_worker import (
    _enqueue_or_buffer,
    _fmt_bar_time_utc,
)
from core_engine.core.live.logging_support import (
    format_pair_label as _fmt_pair_label,
    log_candle_row as _log_candle_row,
    logger,
    operation_line as _llog,
    summarize_backlog as _summarize_backlog,
    summarize_counts_by_symbol as _summarize_counts_by_symbol,
    summarize_counts_by_tf as _summarize_counts_by_tf,
)
from core_engine.core.live.reporter import live_tv_line
from core_engine.core.live.runtime import (
    _backlog,
    _backlog_lock,
    _db_queue,
    _hourly_lock,
    _hourly_stats,
    _last_bar_ts,
    _overflow_buf,
    _overflow_lock,
    _requires_backfill,
    _shutdown,
    _source_bar_ts,
    _spool,
    _state_lock,
    _stats,
    _ws_cooldown_lock,
)
from core_engine.shared.time import future_cutoff_ts as _future_cutoff_ts
from core_engine.settings import LIVE, RUN_DIR, SYMBOLS, TF_STAGING
from core_engine.shared.tradingview import auth as _tv_auth
from core_engine.shared.tradingview import history_client as _tv_ws_history
from core_engine.shared.tradingview import protocol as live_protocol
from core_engine.util.notify.discord import QUICK_COMMANDS_HINT, send_alert as _send_alert


TV_BASE_URL = "wss://data.tradingview.com/socket.io/websocket"
WS_SYMBOLS = [symbol for symbol in SYMBOLS if symbol["asset_type"] in set(LIVE.asset_types)]
WS_TF_INTERVAL = _tv_ws_history.get_ws_interval_map()
TV_WS_TIMEZONE = LIVE.timezone
N_BARS_WS = LIVE.n_bars
BATCH_FETCH_TIMEOUT = LIVE.batch_fetch_timeout_sec
WS_THREAD_JOIN_GRACE_SEC = LIVE.ws_thread_join_grace_sec
BATCH_MAX_RETRIES = LIVE.batch_max_retries
RECONNECT_BASE_SEC = LIVE.reconnect_base_sec
RECONNECT_MAX_SEC = LIVE.reconnect_max_sec
TV_WS_RATE_LIMIT_COOLDOWN_SEC = LIVE.rate_limit_cooldown_sec
TV_WS_FORBIDDEN_COOLDOWN_SEC = LIVE.forbidden_cooldown_sec
SESSION_THROTTLE = LIVE.session_throttle_sec
MAX_MISS_RETRIES = LIVE.max_miss_retries
N_BARS_WS_BACKLOG = LIVE.n_bars_backlog
MAX_BACKLOG_BATCHES = LIVE.max_backlog_batches
TOKEN_EXPIRY_KEYWORDS = _tv_auth.TOKEN_EXPIRY_KEYWORDS

_gen_id = live_protocol.gen_session_id
_send = live_protocol.send_tv_message
_parse_packets = live_protocol.parse_packets
_bars_to_df = live_protocol.bars_to_df


def _claim_ws_callback_stall_fault(group_id: int) -> Path | None:
    """Claim the exact one-shot maintenance marker for a socket stall."""
    request = RUN_DIR / f"fault_inject_ws_callback_stall_g{group_id}.request"
    try:
        if request.read_text(encoding="utf-8").strip() != "STALL_ONCE":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        active = request.with_name(f"{request.stem}.active.{os.getpid()}.{stamp}")
        request.replace(active)
        return active
    except (FileNotFoundError, OSError):
        return None


def _classify_ws_error(error) -> tuple[str, int | None, int]:
    return live_protocol.classify_ws_error(
        error,
        reconnect_max_sec=RECONNECT_MAX_SEC,
        rate_limit_cooldown_sec=TV_WS_RATE_LIMIT_COOLDOWN_SEC,
        forbidden_cooldown_sec=TV_WS_FORBIDDEN_COOLDOWN_SEC,
        token_expiry_keywords=TOKEN_EXPIRY_KEYWORDS,
    )


def _set_ws_cooldown(seconds: int, reason: str) -> None:
    if seconds <= 0:
        return
    until = time.time() + seconds
    notify = False
    with _ws_cooldown_lock:
        if until > _runtime._ws_cooldown_until + 5:
            _runtime._ws_cooldown_until = until
            _runtime._ws_cooldown_reason = reason
            notify = True
    if notify:
        logger.warning(
            "%s",
            _llog(
                "TradingView reconnect cooldown started",
                wait_seconds=seconds,
                reason=reason,
                result="waiting",
            ),
        )
        _send_alert(
            "WARNING",
            "Live feed is waiting before reconnecting\n"
            f"Waiting time: {seconds}s\n"
            f"Reason: {reason}",
        )


def _wait_for_ws_cooldown(label: str) -> None:
    with _ws_cooldown_lock:
        remaining = max(0.0, _runtime._ws_cooldown_until - time.time())
        reason = _runtime._ws_cooldown_reason
    if remaining <= 0:
        return
    logger.warning(
        "%s",
        _llog(
            "TradingView reconnect cooldown active",
            worker=label,
            remaining_seconds=round(remaining),
            reason=reason,
            result="waiting",
        ),
    )
    _shutdown.wait(remaining)


def _handle_ws_transport_error(group_id: int, error) -> tuple[str, int | None]:
    kind, status, cooldown = _classify_ws_error(error)
    if kind == "normal_close":
        logger.info(
            "%s",
            _llog(
                "TradingView WebSocket closed normally",
                group=group_id,
                code=status or 1000,
                result="closed",
            ),
        )
        return kind, status

    logger.error(
        "%s",
        _llog(
            "TradingView WebSocket error",
            group=group_id,
            type=kind,
            status=status or "n/a",
            reason=error,
            result="failed",
        ),
    )
    with _state_lock:
        _stats["ws_errors"] += 1
        if kind == "auth":
            _stats["ws_auth_errors"] += 1
        elif kind == "rate_limit":
            _stats["ws_rate_limits"] += 1
        elif kind == "server":
            _stats["ws_server_errors"] += 1
    with _hourly_lock:
        _hourly_stats["ws_errors"] = int(_hourly_stats.get("ws_errors", 0)) + 1

    if kind == "auth":
        _tv_auth.set_current_token(_tv_auth.GUEST_TOKEN)
        threading.Thread(
            target=_tv_auth.renew,
            args=(logger,),
            daemon=True,
            name="ws-auth-renew",
        ).start()
        if cooldown:
            _set_ws_cooldown(cooldown, f"TradingView WS auth/forbidden status={status}")
    elif kind == "rate_limit":
        _set_ws_cooldown(cooldown, f"TradingView WS rate limit status={status or 'unknown'}")
    return kind, status


def _set_source_watermark(key: tuple[int, str], max_ts: float) -> None:
    with _state_lock:
        _source_bar_ts[key] = max(max_ts, _source_bar_ts.get(key, 0.0))


def _is_token_error(msg_type: str, data: str) -> bool:
    return msg_type in ("error", "critical_error") and any(
        keyword in data.lower() for keyword in TOKEN_EXPIRY_KEYWORDS
    )


class BatchFetcher:
    def __init__(self, group_id: int, symbols: list) -> None:
        self.group_id = group_id

        self.symbols = symbols

        self._cs_map: dict[str, tuple[int, str, str, str]] = {}

        self._expected: set[str] = set()

        self._received: set[str] = set()

        self._new_bars_count = 0

        self._pair_new_bars: dict[tuple[int, str], int] = {}

        self._batch_id = 0

        self._registering: bool = False

        self._done = threading.Event()

        self._lock = threading.Lock()

        self._ws: websocket.WebSocketApp | None = None

        self._fetch_token = 0

        self._fault_marker_checked_token = 0

        self._timed_out = False

        self._report_title = ""

        self._report_lines: list[str] = []

        self._report_level = logging.INFO

        # Persistent-worker plumbing (High-11 redesign): one dedicated
        # thread per group, started once in main() and reused for the
        # process lifetime, instead of _run_batch spawning a fresh
        # batch-g{id} thread every scheduled cycle. request_batch()
        # signals this event; the worker loop clears it, runs the fetch
        # retry loop, then signals _batch_complete. This also guarantees
        # at most one fetch attempt in flight per group at a time - the
        # previous design could start a brand-new batch-g{id}/ws-g{id}
        # thread pair for a group whose previous cycle's thread was still
        # running past BATCH_GROUP_JOIN_TIMEOUT_SEC.
        self._batch_request = threading.Event()

        self._batch_complete = threading.Event()

        # Set by the scheduler when the outer batch deadline has already
        # published/released this cycle.  A worker must not keep retrying an
        # old batch after that point: late callbacks mutate backlog/metrics
        # after the report and can race the next request's completion Event.
        self._batch_cancel = threading.Event()

        self._pending_batch_id: int | None = None

        self._busy = False

        self._worker_thread: threading.Thread | None = None

        # Consecutive scheduled batches this group's worker was still
        # busy (wedged) at BATCH_GROUP_JOIN_TIMEOUT_SEC. Reset to 0 the
        # moment the worker completes a batch. See GROUP_WEDGE_HARD_
        # DEADLINE_BATCHES and _run_batch()'s use of this counter.
        self._consecutive_stuck_batches = 0

        # Once a ws-g{id} thread has ignored both WebSocketApp.close() and a
        # forced raw-socket close, Python cannot reclaim it.  The persistent
        # group worker itself may already be idle at that point, so this flag
        # must survive independently of _busy until the whole child process
        # is recycled by the supervisor.
        self._requires_process_recycle = False

    def start_worker(self) -> None:
        """Start this group's persistent worker thread. Called once from
        main() for every group, before the batch scheduler loop starts."""
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name=f"worker-g{self.group_id}", daemon=True
        )
        self._worker_thread.start()

    def request_batch(self, batch_id: int) -> bool:
        """Hand this group's persistent worker the next batch id to fetch.
        Return False without mutating the pending request when the prior
        cycle is still busy or this process must be recycled."""
        if self._busy or self._requires_process_recycle:
            return False

        self._pending_batch_id = batch_id
        self._batch_cancel.clear()
        self._batch_complete.clear()
        self._batch_request.set()
        return True

    def abandon_batch(self, batch_id: int) -> None:
        """Cancel retry/backoff work after the scheduler released a batch.

        ``fetch()`` has its own bounded socket timeout/force-close path.  The
        event here handles the later retry/backoff loop, ensuring that an old
        batch cannot continue receiving callbacks after its summary and live
        state have already been finalized.
        """

        if self._pending_batch_id == batch_id:
            self._batch_cancel.set()

    def _worker_loop(self) -> None:
        while not _shutdown.is_set():
            if not self._batch_request.wait(timeout=1.0):
                continue

            if _shutdown.is_set():
                return

            self._batch_request.clear()

            batch_id = self._pending_batch_id

            self._busy = True

            try:
                if batch_id is not None:
                    self._fetch_with_retry(batch_id)
            except Exception as exc:  # noqa: BLE001 - a wedged/crashed
                # worker must not silently stop answering future batches.
                logger.error(
                    "%s",
                    _llog(
                        "Connection group worker loop raised",
                        group=self.group_id,
                        reason=exc,
                        result="continuing",
                    ),
                )
            finally:
                self._busy = False

                self._batch_complete.set()

    def _fetch_with_retry(self, batch_id: int) -> None:
        """Attempt this group's batch fetch with retry/backoff. Moved out
        of _run_batch's per-cycle closure into a method so the persistent
        worker thread (see _worker_loop) can call it directly instead of
        a new thread being created to run it every cycle."""
        delay = RECONNECT_BASE_SEC

        for attempt in range(1, BATCH_MAX_RETRIES + 1):
            if _shutdown.is_set() or self._requires_process_recycle or self._batch_cancel.is_set():
                return

            try:
                _wait_for_ws_cooldown(f"G{self.group_id}")

                if _shutdown.is_set():
                    return

                success = self.fetch(batch_id)

                if success or self._batch_cancel.is_set():
                    return

                logger.warning(
                    "%s",
                    _llog(
                        "Connection group fetch incomplete",
                        group=self.group_id,
                        attempt=f"{attempt}/{BATCH_MAX_RETRIES}",
                        result="retrying",
                    ),
                )

            except Exception as exc:
                logger.error(
                    "%s",
                    _llog(
                        "Connection group fetch failed",
                        group=self.group_id,
                        attempt=f"{attempt}/{BATCH_MAX_RETRIES}",
                        reason=exc,
                        result="failed",
                    ),
                )

            if attempt < BATCH_MAX_RETRIES:
                logger.info("%s", _llog("Connection group retry scheduled", group=self.group_id, wait_seconds=delay, result="waiting"))

                if self._batch_cancel.wait(delay):
                    return

                if _shutdown.is_set():
                    return

                delay = min(delay * 2, RECONNECT_MAX_SEC)

    def _next_fetch_token(self) -> int:
        with self._lock:
            self._fetch_token += 1

            self._timed_out = False

            return self._fetch_token

    def _is_current_fetch(self, token: int) -> bool:
        with self._lock:
            return token == self._fetch_token and not self._timed_out and not _shutdown.is_set()

    def _is_current_ws(self, ws) -> bool:
        with self._lock:
            return ws is self._ws and not self._timed_out and not _shutdown.is_set()

    def _build_headers(self) -> list[str]:
        headers = [
            "Origin: https://www.tradingview.com",
            "Referer: https://www.tradingview.com/",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

            "AppleWebKit/537.36 (KHTML, like Gecko) "

            "Chrome/124.0.0.0 Safari/537.36",
        ]

        active_cookie = _tv_auth.get_current_cookie()

        if active_cookie:
            headers.append(f"Cookie: {active_cookie}")

        return headers

    def _on_open(self, ws, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        logger.debug("%s", _llog("Connection group connected", group=self.group_id, result="registering"))

        threading.Thread(
            target=self._register_sessions,
            args=(ws, token),
            daemon=True,
            name=f"reg-g{self.group_id}",
        ).start()

    def _register_sessions(self, ws, token: int) -> None:
        def _ws_alive() -> bool:
            try:
                return ws.sock is not None and ws.sock.connected

            except Exception:
                return False

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        with self._lock:
            self._cs_map.clear()

            self._expected.clear()

            self._received.clear()

        if not _ws_alive() or not self._is_current_fetch(token):
            logger.warning("%s", _llog("Connection group closed before registration", group=self.group_id, result="warning"))

            self._done.set()

            return

        try:
            _send(ws, ["set_auth_token", _tv_auth.get_current_token()])

        except Exception as exc:
            logger.warning("%s", _llog("Connection group auth send failed", group=self.group_id, reason=exc, result="warning"))

            self._done.set()

            return

        time.sleep(0.5)

        with self._lock:
            self._registering = True

        for sym in self.symbols:
            for tf_code, interval in WS_TF_INTERVAL.items():
                if _shutdown.is_set() or not _ws_alive() or not self._is_current_fetch(token):
                    with self._lock:
                        self._registering = False

                    self._done.set()

                    return

                cs = _gen_id("cs")

                staging_table = TF_STAGING[tf_code]

                try:
                    _send(ws, ["chart_create_session", cs, ""])

                    time.sleep(0.1)

                    _send(ws, ["switch_timezone", cs, TV_WS_TIMEZONE])

                    time.sleep(0.05)

                    sym_json = json.dumps(
                        {
                            "symbol": f"{sym['tv_exchange']}:{sym['tv_symbol']}",
                            "adjustment": "splits",
                        }
                    )

                    _send(ws, ["resolve_symbol", cs, "sds_sym_1", f"={sym_json}"])

                    time.sleep(0.1)

                    with _backlog_lock:
                        n_req = (
                            N_BARS_WS_BACKLOG

                            if (sym["symbol_id"], tf_code) in _backlog

                            else N_BARS_WS
                        )

                    _send(
                        ws,
                        [
                            "create_series",
                            cs,
                            "sds_1",
                            "sds_sym_1",
                            "sds_sym_1",
                            interval,
                            n_req,
                            "",
                        ],
                    )

                    self._cs_map[cs] = (sym["symbol_id"], tf_code, staging_table, sym["tv_symbol"])

                    self._expected.add(cs)

                    time.sleep(SESSION_THROTTLE)

                except Exception as exc:
                    logger.warning("%s", _llog("Session registration failed", group=self.group_id, reason=exc, result="warning"))

                    with self._lock:
                        self._registering = False

                    self._done.set()

                    return

        with self._lock:
            self._registering = False

            already_done = self._expected and self._received >= self._expected

        if already_done:
            logger.debug(
                "%s",
                _llog(
                    "Connection group completed",
                    group=self.group_id,
                    sessions=len(self._expected),
                    trigger="post_registration_check",
                    result="closing",
                ),
            )

            self._done.set()

            try:
                ws.close()

            except Exception:
                pass

        logger.debug(
            "%s",
            _llog(
                "Connection group registered",
                group=self.group_id,
                sessions=len(self._expected),
                symbols=len(self.symbols),
                timeframes=len(WS_TF_INTERVAL),
                result="waiting_for_data",
            ),
        )

    def _on_message(self, ws, raw: str, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        with self._lock:
            check_fault_marker = self._fault_marker_checked_token != token
            if check_fault_marker:
                self._fault_marker_checked_token = token

        fault_evidence = _claim_ws_callback_stall_fault(self.group_id) if check_fault_marker else None
        if fault_evidence is not None:
            logger.warning(
                "%s",
                _llog(
                    "Controlled WebSocket callback stall injected",
                    group=self.group_id,
                    evidence=fault_evidence,
                    action="exercise_force_close_and_process_recycle",
                    result="fault_injected",
                ),
            )
            # Deliberately ignore shutdown: the purpose of this maintenance
            # hook is to simulate a native callback that Python cannot
            # reclaim.  ws-g{id} is daemonized and the live child must exit.
            while True:
                time.sleep(60)

        with _state_lock:
            _stats["events"] += 1

        for data in _parse_packets(raw):
            if data.startswith("~h~"):
                try:
                    ws.send(f"~m~{len(data)}~m~{data}")

                except Exception:
                    pass

                continue

            try:
                msg = json.loads(data)

            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("m", "")

            p = msg.get("p", [])

            if _is_token_error(msg_type, data):
                logger.warning(
                    "%s",
                    _llog("TradingView auth error detected", group=self.group_id, action="renew_token", result="recovering"),
                )

                with _state_lock:
                    _stats["ws_auth_errors"] += 1

                _tv_auth.set_current_token(_tv_auth.GUEST_TOKEN)

                threading.Thread(
                    target=_tv_auth.renew, args=(logger,), daemon=True, name="ws-auth-renew"
                ).start()

                self._done.set()

                try:
                    ws.close()

                except Exception:
                    pass

                return

            if msg_type in ("du", "timescale_update") and len(p) >= 2:
                self._handle_series(p[0], p[1], ws, token)

    def _handle_series(self, cs: str, series_data: dict, ws, token: int) -> None:
        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        if cs not in self._cs_map:
            return

        symbol_id, tf_code, staging_table, tv_symbol = self._cs_map[cs]

        sds = series_data.get("sds_1")

        if sds is None:
            return

        bars = [b for b in sds.get("s", []) if len(b.get("v", [])) >= 6]

        with self._lock:
            self._received.add(cs)

        _new_count = 0

        if bars:
            bars.sort(key=lambda b: b["v"][0])

            closed_bars = bars[:-1]

            if closed_bars:
                key = (symbol_id, tf_code)

                _set_source_watermark(key, closed_bars[-1]["v"][0])

                with _state_lock:
                    last_ts = _last_bar_ts.get(key, 0.0)

                with _backlog_lock:
                    miss_count = _backlog.get(key, 0)

                if miss_count > 0:
                    from core_engine.settings import TF_MINUTES as _TF_MIN

                    tf_min = _TF_MIN.get(tf_code, 5)

                    effective_wm = max(0.0, last_ts - miss_count * tf_min * 60 * 2)

                    logger.debug(
                        "%s",
                        _llog(
                            "Backlog catch-up watermark adjusted",
                            group=self.group_id,
                            symbol=tv_symbol,
                            timeframe=tf_code,
                            minutes=miss_count * tf_min * 2,
                            result="adjusted",
                        ),
                    )

                else:
                    effective_wm = last_ts

                new_bars = [b for b in closed_bars if b["v"][0] > effective_wm]

                if new_bars:
                    df = _bars_to_df(new_bars)

                    if not df.empty:
                        future_cutoff = _future_cutoff_ts()

                        safe_new_bars = [b for b in new_bars if b["v"][0] <= future_cutoff]

                        if not safe_new_bars:
                            logger.warning(
                                "%s",
                                _llog(
                                    "Future-only candle batch ignored",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    result="ignored",
                                ),
                            )

                            return

                        if len(safe_new_bars) != len(new_bars):
                            logger.warning(
                                "%s",
                                _llog(
                                    "Future candles dropped before enqueue",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    dropped=len(new_bars) - len(safe_new_bars),
                                    result="cleaned",
                                ),
                            )

                            df = _bars_to_df(safe_new_bars)

                            if df.empty:
                                return

                        item = (self._batch_id, symbol_id, tf_code, staging_table, tv_symbol, df)

                        enqueue_status = _enqueue_or_buffer(item, self.group_id, tv_symbol, tf_code)

                        if enqueue_status == "rejected":
                            with _overflow_lock:
                                overflow_depth = len(_overflow_buf)

                            spool_depth = _spool.count()

                            logger.error(
                                "%s",
                                _llog(
                                    "Write queue rejected candles",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    queue_depth=_db_queue.qsize(),
                                    memory_buffer=overflow_depth,
                                    disk_buffer="n/a" if spool_depth is None else spool_depth,
                                    result="failed",
                                ),
                            )

                            return

                        with self._lock:
                            self._new_bars_count += len(safe_new_bars)

                            self._pair_new_bars[(symbol_id, tf_code)] = self._pair_new_bars.get(
                                (symbol_id, tf_code), 0
                            ) + len(safe_new_bars)

                        _new_count = len(safe_new_bars)

                        _record_batch_accepted(self._batch_id, key, len(safe_new_bars))

                        with _state_lock:
                            _stats["queue_depth"] = _db_queue.qsize()

                        _log_candle_row(
                            live_tv_line(
                                logged_at=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                                symbol=tv_symbol,
                                timeframe=tf_code,
                                candles=len(safe_new_bars),
                                first_utc=_fmt_bar_time_utc(safe_new_bars[0]["v"][0]) if len(safe_new_bars) > 1 else None,
                                latest_utc=_fmt_bar_time_utc(safe_new_bars[-1]["v"][0]),
                                queue_depth=_db_queue.qsize(),
                            )
                        )

        if _new_count <= 0:
            logger.debug(
                "%s",
                _llog(
                    "No new closed candle",
                    group=self.group_id,
                    symbol=tv_symbol,
                    timeframe=tf_code,
                    received=len(bars),
                    result="no_change",
                ),
            )

        with self._lock:
            if (
                not self._registering

                and self._expected

                and self._received >= self._expected

                and not self._done.is_set()
            ):
                logger.debug(
                    "%s",
                    _llog(
                        "Connection group completed",
                        group=self.group_id,
                        sessions=len(self._expected),
                        result="closing",
                    ),
                )

                self._done.set()

                try:
                    ws.close()

                except Exception:
                    pass

    def _on_error(self, _ws, error, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_fetch(token):
            return

        _handle_ws_transport_error(self.group_id, error)

        self._done.set()

    def _on_close(self, _ws, status_code, _msg, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_fetch(token):
            return

        logger.debug("%s", _llog("Connection group disconnected", group=self.group_id, code=status_code or "-", result="closed"))

        self._done.set()

    def fetch(self, batch_id: int, timeout: int = BATCH_FETCH_TIMEOUT) -> bool:
        self._next_fetch_token()

        self._done.clear()

        with self._lock:
            self._new_bars_count = 0

            self._pair_new_bars.clear()

            self._cs_map.clear()

            self._expected.clear()

            self._received.clear()

            self._registering = False

            self._batch_id = batch_id

            self._report_title = ""

            self._report_lines = []

            self._report_level = logging.INFO

        ts = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")

        url = f"{TV_BASE_URL}?from=chart%2F&date={ts}"

        ws = websocket.WebSocketApp(
            url,
            header=self._build_headers(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws = ws

        ws_thread = threading.Thread(
            target=ws.run_forever, daemon=True, name=f"ws-g{self.group_id}"
        )

        ws_thread.start()

        completed = self._done.wait(timeout=timeout)

        if not completed:
            with self._lock:
                self._timed_out = True

                expected_snapshot = set(self._expected)

                received_snapshot = set(self._received)

                cs_map_timeout_snapshot = dict(self._cs_map)

            self._done.set()

            try:
                ws.keep_running = False

            except Exception:
                pass

            # keep_running=False is only checked between reads by
            # websocket-client's own loop - it does not unblock a thread
            # that is already stuck inside a blocking socket recv() (e.g.
            # TradingView stopped sending data without closing the TCP
            # connection). Without forcing the underlying socket closed
            # too, that ws_thread can linger indefinitely; over many days
            # of repeated timeouts, these accumulate as leaked threads and
            # open file descriptors even though _is_current_fetch()
            # already prevents a stale thread's callbacks from corrupting
            # newer batches' data.
            try:
                sock = getattr(ws, "sock", None)
                raw_sock = getattr(sock, "sock", None) if sock is not None else None
                if raw_sock is not None:
                    raw_sock.close()
                    with _state_lock:
                        _stats["ws_forced_socket_closes"] = _stats.get("ws_forced_socket_closes", 0) + 1
            except Exception:
                pass

            missing = [
                f"{cs_map_timeout_snapshot[cs][3]} {cs_map_timeout_snapshot[cs][1]}"

                for cs in (expected_snapshot - received_snapshot)

                if cs in cs_map_timeout_snapshot
            ]

            logger.warning(
                "%s",
                _llog(
                    "Connection group fetch timeout",
                    group=self.group_id,
                    timeout_seconds=timeout,
                    sessions=f"{len(received_snapshot)}/{len(expected_snapshot)}",
                    missing=", ".join(missing[:8]) if missing else "none",
                    result="warning",
                ),
            )

            if not expected_snapshot:
                _send_alert(
                    "ERROR",
                    "Live feed could not open TradingView chart sessions\n"
                    f"Connection group: {self.group_id}\n"
                    f"Waited: {timeout}s\n"
                    "Meaning: TradingView login, network, or chart-session setup failed before data could be requested.",
                )

            else:
                _send_alert(
                    "WARNING",
                    "Live feed batch timed out\n"
                    f"Connection group: {self.group_id}\n"
                    f"Answered sessions: {len(received_snapshot)}/{len(expected_snapshot)}\n"
                    + (f"Missing pairs: {', '.join(missing)}" if missing else "Missing pairs: none"),
                )

            try:
                ws.close()

            except Exception:
                pass

        ws_thread.join(timeout=WS_THREAD_JOIN_GRACE_SEC)

        if ws_thread.is_alive():
            log_method = logger.info if _shutdown.is_set() else logger.error
            log_method(
                "%s",
                _llog(
                    "WebSocket thread did not stop after timeout",
                    group=self.group_id,
                    action="stale_callback_guard",
                    result="warning" if _shutdown.is_set() else "failed",
                ),
            )

            if not _shutdown.is_set():
                # This specific OS thread is now genuinely unreclaimable
                # from Python (it ignored keep_running=False AND a forced
                # raw-socket close). _run_batch's consecutive-stuck-batch
                # counter is what decides whether to recycle the whole
                # process; this counter is the operator-facing metric for
                # "how often has that actually happened" (see doctor/
                # status output and _stats).
                with _state_lock:
                    _stats["ws_orphaned_threads"] = _stats.get("ws_orphaned_threads", 0) + 1

                self._requires_process_recycle = True

        self._ws = None

        with self._lock:
            cs_map_snapshot = dict(self._cs_map)

            received_final = set(self._received)

            expected_final = set(self._expected)

        received_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]

            for cs in received_final

            if cs in cs_map_snapshot
        }

        missed_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]

            for cs in (expected_final - received_final)

            if cs in cs_map_snapshot
        }

        _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}

        repeated_miss_alerts: list[tuple[tuple[int, str], int]] = []

        with _backlog_lock:
            for pair in received_pairs:
                if pair in _backlog:
                    logger.info(
                        "%s",
                        _llog(
                            "Pair recovered from retry list",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            result="recovered",
                        ),
                    )

                _backlog.pop(pair, None)
                _requires_backfill.discard(pair)

            for pair in missed_pairs:
                count = _backlog.get(pair, 0) + 1

                if count <= MAX_BACKLOG_BATCHES:
                    _backlog[pair] = count

                    if MAX_MISS_RETRIES > 0 and count % MAX_MISS_RETRIES == 0:
                        repeated_miss_alerts.append((pair, count))

                    logger.info(
                        "%s",
                        _llog(
                            "Pair missed this batch",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            next_request_bars=N_BARS_WS_BACKLOG,
                            result="retry_next_batch",
                        ),
                    )

                    logger.info(
                        "%s",
                        _llog(
                            "Pair miss audit",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            result="tracked",
                        ),
                    )

                else:
                    logger.error(
                        "%s",
                        _llog(
                            "Pair missed too many batches",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            result="gap_requires_backfill",
                        ),
                    )

                    _send_alert(
                        "ERROR",
                        "Live feed stopped retrying one missing pair\n"
                        f"Pair: {_sym_name.get(pair[0], str(pair[0]))}/{pair[1]}\n"
                        f"Missed batches: {count} in a row (about {count * 5} minutes)\n"
                        "Meaning: this pair likely has a real data gap now.\n"
                        "Suggested action: run historical backfill/replay to repair the gap." + QUICK_COMMANDS_HINT,
                    )

                    _backlog.pop(pair, None)
                    _requires_backfill.add(pair)

        for (symbol_id, tf_code), count in repeated_miss_alerts:
            symbol_name = _sym_name.get(symbol_id, str(symbol_id))
            logger.warning(
                "[MISS] %s [%s] missed %d batch(es) in a row - sending alert.",
                symbol_name,
                tf_code,
                count,
            )
            _send_alert(
                "WARNING",
                "Live feed is missing repeated candles\n"
                f"Pair: {symbol_name}/{tf_code}\n"
                f"Repeated misses: {count} live batches\n"
                "Suggested action: check TradingView availability for this pair, "
                "then run historical backfill if a gap remains.",
            )

        with _backlog_lock:
            backlog_snap = dict(_backlog)

        pair_new_bars_snap: dict[tuple[int, str], int]

        with self._lock:
            pair_new_bars_snap = dict(self._pair_new_bars)

        with self._lock:
            expected_count = len(self._expected)

            received_count = len(self._received)

        changed_pairs = [key for key, count in pair_new_bars_snap.items() if int(count or 0) > 0]

        changed_pairs.sort(key=lambda key: (-pair_new_bars_snap[key], _fmt_pair_label(key)))

        changed_text = []

        for key in changed_pairs[:12]:
            with _state_lock:
                wm_ts = _last_bar_ts.get(key)

            latest = (
                datetime.fromtimestamp(wm_ts, tz=timezone.utc).strftime("%H:%M UTC")

                if wm_ts

                else "-"
            )

            changed_text.append(f"{_fmt_pair_label(key)} +{pair_new_bars_snap[key]} ({latest})")

        missed_sorted = sorted(missed_pairs, key=_fmt_pair_label)

        missed_text = ", ".join(_fmt_pair_label(key) for key in missed_sorted[:12]) or "none"

        if len(missed_sorted) > 12:
            missed_text += f", ... +{len(missed_sorted) - 12} more"

        if expected_count == 0:
            analysis = (
                "No TradingView chart sessions were established; this is a connection/auth "

                "failure, not a normal no-bar cycle."
            )

        elif missed_pairs:
            analysis = (
                f"{len(missed_pairs)} pair(s) did not answer; next batch requests "

                f"{N_BARS_WS_BACKLOG} bars for backlog recovery."
            )

        elif self._new_bars_count == 0:
            analysis = (
                "OK  no new closed bar - all sessions answered; no new closed bars were available."
            )

        else:
            analysis = "Group is healthy; accepted bars were queued for database writes."

        report_lines = [
            f"Sessions : {received_count}/{expected_count} answered",
            f"Accepted : {self._new_bars_count:,} bars across {len(changed_pairs)} pair(s)",
            f"Symbols  : {_summarize_counts_by_symbol(pair_new_bars_snap)}",
            f"TFs      : {_summarize_counts_by_tf(pair_new_bars_snap)}",
            f"Changed  : {'; '.join(changed_text) if changed_text else '-'}",
            f"Missing  : {missed_text}",
        ]

        if backlog_snap:
            report_lines.append(f"Backlog  : {_summarize_backlog(backlog_snap)}")

        report_lines.append(f"Analysis : {analysis}")

        with self._lock:
            self._report_title = f"WS LIVE GROUP G{self.group_id} REPORT"
            self._report_lines = list(report_lines)
            self._report_level = logging.ERROR if expected_count == 0 else logging.WARNING if missed_pairs else logging.INFO

        logger.debug(
            "%s",
            _llog(
                "Connection group audit",
                group=self.group_id,
                sessions_answered=f"{received_count}/{expected_count}",
                closed_candles_received=self._new_bars_count,
                missed_pairs=len(missed_pairs),
                checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                result="recorded",
            ),
        )

        return completed and expected_count > 0 and received_count >= expected_count
