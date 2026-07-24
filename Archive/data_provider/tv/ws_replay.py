"""
TradingView replay WebSocket historical crawler.

Replay is the max-depth companion to the normal chart history provider. Normal
create_series/request_more pulls backwards from "now" until TradingView's series
plateau. Replay can request windows around old timestamps, which lets the
pipeline bootstrap older intraday history without changing the database schema.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import websocket

from data_provider.tv import auth as _tv_auth
from data_provider.common import logfmt as _logfmt
from data_provider.tv.ws_history import (
    TV_WS_TIMEZONE,
    WS_INTERVALS,
    _current_auth,
    _gen_id,
    _headers,
    _parse_packets,
    _resolve_endpoint,
    _send,
)


@dataclass
class ReplayWindowResult:
    df: pd.DataFrame | None
    status: str
    requested: int
    returned: int
    interval: str
    endpoint: str
    anchor_utc: datetime
    error: str = ""


@dataclass
class ReplayCrawlResult:
    df: pd.DataFrame | None
    status: str
    windows: int
    returned: int
    first_utc: datetime | None
    last_utc: datetime | None
    endpoint: str
    interval: str
    error: str = ""


def _to_utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_ts(value: datetime) -> str:
    try:
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _df_from_bars(bars_by_ts: dict[float, list[Any]]) -> pd.DataFrame | None:
    if not bars_by_ts:
        return None
    rows = []
    for ts in sorted(bars_by_ts):
        values = bars_by_ts[ts]
        rows.append(
            {
                "datetime": datetime.fromtimestamp(ts, tz=timezone.utc),
                "open": float(values[1]),
                "high": float(values[2]),
                "low": float(values[3]),
                "close": float(values[4]),
                "volume": float(values[5] or 0.0),
            }
        )
    return pd.DataFrame(rows).set_index("datetime")


def fetch_replay_window(
    *,
    symbol: str,
    exchange: str,
    tf_code: str,
    anchor_utc: datetime,
    requested_bars: int,
    step_bars: int = 1000,
    logger: logging.Logger | None = None,
    timeout_sec: float = 30.0,
    token: str | None = None,
    cookie: str | None = None,
    endpoint: str | None = None,
) -> ReplayWindowResult:
    """Fetch one TradingView replay window around anchor_utc."""
    log = logger or logging.getLogger("tv_ws_replay")
    tf_code = tf_code.upper()
    interval = WS_INTERVALS.get(tf_code)
    if not interval:
        raise ValueError(f"Unsupported replay timeframe: {tf_code}")

    endpoint_name, endpoint_url = _resolve_endpoint(endpoint)
    if token is None or cookie is None:
        cur_token, cur_cookie = _current_auth()
        token = token or cur_token
        cookie = cookie if cookie is not None else cur_cookie

    anchor_utc = _to_utc_datetime(anchor_utc)
    anchor_ts = int(anchor_utc.timestamp())
    cs = _gen_id("cs")
    rs = _gen_id("rs")
    bars_by_ts: dict[float, list[Any]] = {}
    status = "timeout"
    error = ""

    try:
        ws = websocket.create_connection(
            endpoint_url,
            header=_headers(cookie or ""),
            timeout=max(5.0, timeout_sec),
            origin="https://www.tradingview.com",
        )
    except Exception as exc:
        return ReplayWindowResult(
            None, "connect_error", requested_bars, 0, interval, endpoint_name, anchor_utc, str(exc)
        )

    try:
        ws.settimeout(0.5)
        symbol_payload = {"symbol": f"{exchange}:{symbol}", "adjustment": "splits"}
        chart_symbol = {"replay": rs, "symbol": symbol_payload}

        _send(ws, "set_auth_token", [token or _tv_auth.GUEST_TOKEN])
        _send(ws, "chart_create_session", [cs, ""])
        _send(ws, "replay_create_session", [rs])
        _send(
            ws,
            "replay_add_series",
            [rs, "req_add", f"={json.dumps(symbol_payload, separators=(',', ':'))}", interval],
        )
        _send(ws, "replay_reset", [rs, "req_reset", anchor_ts])
        _send(
            ws,
            "resolve_symbol",
            [cs, "ser_1", f"={json.dumps(chart_symbol, separators=(',', ':'))}"],
        )
        _send(ws, "create_series", [cs, "$prices", "s1", "ser_1", interval, int(requested_bars)])

        def drain(max_sec: float, stop_on_completed: bool) -> str:
            nonlocal error
            deadline = time.monotonic() + max_sec
            last_change = time.monotonic()
            saw_data = False
            last_count = -1
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if saw_data and time.monotonic() - last_change > 1.5:
                        return "idle_after_data"
                    continue
                except Exception as exc:
                    error = str(exc)
                    return "recv_error"

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                if raw.startswith("~h~"):
                    try:
                        ws.send(f"~m~{len(raw)}~m~{raw}")
                    except Exception:
                        pass
                    continue

                for packet in _parse_packets(raw):
                    if packet.startswith("~h~"):
                        continue
                    try:
                        msg = json.loads(packet)
                    except Exception:
                        continue

                    msg_type = msg.get("m", "")
                    params = msg.get("p", [])
                    if msg_type in {"error", "critical_error", "series_error", "symbol_error"}:
                        error = json.dumps(params, ensure_ascii=True)[:300]
                        return msg_type

                    if msg_type in {"du", "timescale_update"} and len(params) >= 2 and params[0] == cs:
                        payload = params[1] if isinstance(params[1], dict) else {}
                        sds = payload.get("$prices") or payload.get("sds_1")
                        if isinstance(sds, dict):
                            for bar in sds.get("s", []) or []:
                                values = bar.get("v", [])
                                if len(values) >= 6:
                                    try:
                                        bars_by_ts[float(values[0])] = values
                                        saw_data = True
                                    except Exception:
                                        continue
                            if len(bars_by_ts) != last_count:
                                last_count = len(bars_by_ts)
                                last_change = time.monotonic()

                    if stop_on_completed and msg_type == "series_completed" and (not params or params[0] == cs):
                        return "completed"

            return "partial_timeout" if saw_data else "timeout"

        status = drain(timeout_sec, True)
        if step_bars > 0:
            step_rounds = 0
            total_added = 0
            idle_rounds = 0
            last_step_status = ""
            max_step_rounds = max(
                1,
                min(
                    20,
                    (max(1, requested_bars) + max(1, step_bars) - 1) // max(1, step_bars) + 10,
                ),
            )
            while len(bars_by_ts) < requested_bars and step_rounds < max_step_rounds:
                before = len(bars_by_ts)
                _send(ws, "replay_step", [rs, "req_replay_step", int(step_bars)])
                step_status = drain(min(timeout_sec, 15.0), False)
                added = len(bars_by_ts) - before
                step_rounds += 1
                total_added += max(0, added)
                last_step_status = step_status
                if added <= 0:
                    idle_rounds += 1
                else:
                    idle_rounds = 0
                if step_status in {"recv_error", "error", "critical_error", "series_error", "symbol_error"}:
                    break
                if idle_rounds >= 2:
                    break
            status = f"{status}+steps+{step_rounds}+{total_added}:{last_step_status}"

    finally:
        try:
            ws.close()
        except Exception:
            pass

    df = _df_from_bars(bars_by_ts)
    if df is None and error:
        log.debug("Replay returned no bars for %s:%s %s: %s", exchange, symbol, tf_code, error)
    return ReplayWindowResult(
        df=df,
        status=status,
        requested=requested_bars,
        returned=0 if df is None else len(df),
        interval=interval,
        endpoint=endpoint_name,
        anchor_utc=anchor_utc,
        error=error,
    )


def crawl_replay_history(
    *,
    symbol: str,
    exchange: str,
    tf_code: str,
    start_utc: str | datetime,
    end_before_utc: datetime,
    endpoint: str | None,
    window_bars: int,
    step_bars: int,
    max_windows: int,
    advance_factor: float,
    timeout_sec: float,
    logger: logging.Logger | None = None,
) -> ReplayCrawlResult:
    """
    Crawl replay windows from TradingView's earliest available replay bar up to
    end_before_utc. Returned bars are filtered to strictly before end_before_utc
    so they can be concatenated with normal series data without overlap.
    """
    log = logger or logging.getLogger("tv_ws_replay")
    tf_code = tf_code.upper()
    interval = WS_INTERVALS.get(tf_code)
    if not interval:
        raise ValueError(f"Unsupported replay timeframe: {tf_code}")
    endpoint_name, _ = _resolve_endpoint(endpoint)

    start = _to_utc_datetime(start_utc)
    end_before = _to_utc_datetime(end_before_utc)
    if end_before <= start:
        return ReplayCrawlResult(None, "skipped_empty_range", 0, 0, None, None, endpoint_name, interval)

    tf_minutes = 10080 if tf_code == "W" else 1440 if tf_code == "D1" else int(int(interval) if interval.isdigit() else 60)
    tf_delta = timedelta(minutes=tf_minutes)
    # Used only when a replay anchor returns no bars. Once TradingView returns
    # data, the crawler advances from the last returned candle to avoid gaps.
    advance_minutes = max(tf_minutes, int(tf_minutes * max(1, window_bars) * max(0.25, advance_factor)))
    advance = timedelta(minutes=advance_minutes)

    max_windows = max_windows if max_windows > 0 else 1000
    cursor = start
    bars: dict[pd.Timestamp, pd.Series] = {}
    windows = 0
    empty_windows = 0
    last_seen: datetime | None = None
    status_notes: list[str] = []
    error = ""

    while cursor <= end_before and windows < max_windows:
        result = fetch_replay_window(
            symbol=symbol,
            exchange=exchange,
            tf_code=tf_code,
            anchor_utc=cursor,
            requested_bars=window_bars,
            step_bars=step_bars,
            logger=log,
            timeout_sec=timeout_sec,
            endpoint=endpoint_name,
        )
        windows += 1
        status_notes.append(result.status)
        if result.error:
            error = result.error

        df = result.df
        if df is None or df.empty:
            empty_windows += 1
            if empty_windows >= 3 and last_seen is None:
                cursor = cursor + advance
            elif empty_windows >= 5:
                break
            else:
                cursor = cursor + advance
            continue

        empty_windows = 0
        df = df[df.index < end_before]
        window_last: datetime | None = None
        if not df.empty:
            for idx, row in df.iterrows():
                bars[pd.Timestamp(idx)] = row
            first = df.index.min().to_pydatetime()
            last = df.index.max().to_pydatetime()
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            else:
                last = last.astimezone(timezone.utc)
            window_last = last
            last_seen = last if last_seen is None else max(last_seen, last)
            if windows == 1 or windows % 25 == 0:
                _logfmt.log(
                    log,
                    "REPLAY",
                    symbol=symbol,
                    tf=tf_code,
                    action="window_loaded",
                    amount=f"rows {_logfmt.num(len(df))}",
                    range_=_logfmt.window(first, last),
                    status=f"window {windows}",
                )

        if last_seen is not None and last_seen >= end_before - tf_delta:
            break
        if cursor >= end_before:
            break
        next_cursor = cursor + advance
        if window_last is not None:
            next_cursor = window_last + tf_delta
            if next_cursor <= cursor:
                next_cursor = cursor + advance
        if next_cursor >= end_before and (
            last_seen is None or last_seen < end_before - tf_delta
        ):
            cursor = end_before
        else:
            cursor = next_cursor

    if not bars:
        status = "empty" if not status_notes else f"empty:{status_notes[-1]}"
        return ReplayCrawlResult(None, status, windows, 0, None, None, endpoint_name, interval, error)

    out = pd.DataFrame.from_dict(bars, orient="index").sort_index()
    out.index.name = "datetime"
    first_utc = out.index.min().to_pydatetime()
    last_utc = out.index.max().to_pydatetime()
    status = "completed" if windows < max_windows else "max_windows"
    return ReplayCrawlResult(
        df=out,
        status=status,
        windows=windows,
        returned=len(out),
        first_utc=first_utc,
        last_utc=last_utc,
        endpoint=endpoint_name,
        interval=interval,
        error=error,
    )
