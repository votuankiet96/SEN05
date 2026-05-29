"""
Shared TradingView WebSocket historical provider.

This module is intentionally small and DB-free. It only knows how to:
- map SEN timeframe codes to TradingView WebSocket intervals
- open an authenticated TradingView chart WebSocket
- request historical OHLCV bars
- return a pandas DataFrame shaped like tvDatafeed output

Pipeline, checker, probes, and live code should import the interval map from here
instead of duplicating TradingView WebSocket interval strings.
"""

from __future__ import annotations

import json
import logging
import random
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import websocket

from data_provider.tv import auth as _tv_auth
from config import TV_COOKIE, TV_WS_HISTORY_ENDPOINT


TV_WS_ENDPOINT_URLS: dict[str, str] = {
    "data": "wss://data.tradingview.com/socket.io/websocket",
    "prodata": "wss://prodata.tradingview.com/socket.io/websocket",
    "widgetdata": "wss://widgetdata.tradingview.com/socket.io/websocket",
}
TV_WS_TIMEZONE = "Etc/UTC"

WS_INTERVALS: dict[str, str] = {
    "W": "1W",
    "D1": "1D",
    "H8": "480",
    "H6": "360",
    "H4": "240",
    "H3": "180",
    "H2": "120",
    "H1": "60",
    "M90": "90",
    "M45": "45",
    "M30": "30",
    "M20": "20",
    "M15": "15",
    "M10": "10",
    "M5": "5",
}


@dataclass
class WsHistoryResult:
    df: pd.DataFrame | None
    status: str
    requested: int
    returned: int
    interval: str
    error: str = ""
    endpoint: str = ""


class TradingViewWsHistoryError(RuntimeError):
    """Raised when the TradingView WebSocket historical request fails."""


def get_ws_interval_map() -> dict[str, str]:
    """Return a copy of the shared SEN TF -> TradingView WS interval map."""
    return dict(WS_INTERVALS)


def _gen_id(prefix: str = "cs") -> str:
    suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
    return f"{prefix}_{suffix}"


def _send(ws: websocket.WebSocket, method: str, params: list[Any]) -> None:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    ws.send(f"~m~{len(payload)}~m~{payload}")


def _parse_packets(raw: str) -> list[str]:
    packets: list[str] = []
    pos = 0
    while pos < len(raw):
        if raw.startswith("~h~", pos):
            packets.append(raw[pos:])
            break
        if raw[pos : pos + 3] != "~m~":
            break
        pos += 3
        sep = raw.find("~m~", pos)
        if sep == -1:
            break
        length_str = raw[pos:sep]
        pos = sep + 3
        try:
            length = int(length_str)
        except ValueError:
            break
        packets.append(raw[pos : pos + length])
        pos += length
    return packets


def _headers(cookie: str) -> list[str]:
    headers = [
        "Origin: https://www.tradingview.com",
        "Referer: https://www.tradingview.com/",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
    if cookie:
        headers.append(f"Cookie: {cookie}")
    return headers


def _resolve_endpoint(endpoint: str | None) -> tuple[str, str]:
    endpoint_name = (endpoint or TV_WS_HISTORY_ENDPOINT or "data").strip().lower()
    if endpoint_name.startswith("wss://"):
        return endpoint_name, endpoint_name
    url = TV_WS_ENDPOINT_URLS.get(endpoint_name)
    if not url:
        valid = ", ".join(sorted(TV_WS_ENDPOINT_URLS))
        raise ValueError(f"Unsupported TradingView WS endpoint: {endpoint_name!r}. Valid: {valid}")
    return endpoint_name, url


def _current_auth() -> tuple[str, str]:
    log = logging.getLogger("tv_ws_history.auth")
    try:
        _tv_auth.check_and_refresh(log)
    except Exception as exc:
        log.debug("TradingView auth preflight refresh skipped: %s", exc)
    token = _tv_auth.get_current_token()
    if not token or token == _tv_auth.GUEST_TOKEN:
        token, _ = _tv_auth._resolve_auth_token(log)
        _tv_auth.set_current_token(token)
    cookie = _tv_auth._tv_cookie or TV_COOKIE or ""
    return token, cookie


def fetch_history(
    *,
    symbol: str,
    exchange: str,
    tf_code: str,
    n_bars: int,
    logger: logging.Logger | None = None,
    timeout_sec: float = 45.0,
    token: str | None = None,
    cookie: str | None = None,
    endpoint: str | None = None,
    request_more_rounds: int = 0,
    request_more_bars: int = 10000,
) -> WsHistoryResult:
    """
    Fetch historical OHLCV bars from TradingView WebSocket.

    The returned DataFrame index is timezone-aware UTC. Existing validators then
    normalize it to naive UTC before DB writes.
    """
    log = logger or logging.getLogger("tv_ws_history")
    tf_code = tf_code.upper()
    interval = WS_INTERVALS.get(tf_code)
    if not interval:
        raise ValueError(f"Unsupported WS timeframe: {tf_code}")
    endpoint_name, endpoint_url = _resolve_endpoint(endpoint)

    if token is None or cookie is None:
        cur_token, cur_cookie = _current_auth()
        token = token or cur_token
        cookie = cookie if cookie is not None else cur_cookie

    cs = _gen_id("cs")
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
        return WsHistoryResult(None, "connect_error", n_bars, 0, interval, str(exc), endpoint_name)

    try:
        ws.settimeout(1.0)
        _send(ws, "set_auth_token", [token or _tv_auth.GUEST_TOKEN])
        _send(ws, "chart_create_session", [cs, ""])
        _send(ws, "switch_timezone", [cs, TV_WS_TIMEZONE])
        sym_json = json.dumps(
            {"symbol": f"{exchange}:{symbol}", "adjustment": "splits"},
            separators=(",", ":"),
        )
        _send(ws, "resolve_symbol", [cs, "sds_sym_1", f"={sym_json}"])
        _send(ws, "create_series", [cs, "sds_1", "sds_sym_1", "sds_sym_1", interval, int(n_bars), ""])

        def drain_until_complete() -> str:
            nonlocal error
            deadline = time.monotonic() + timeout_sec
            local_status = "timeout"
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
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

                    if msg_type in {"error", "critical_error"}:
                        error = json.dumps(params, ensure_ascii=True)[:300]
                        return msg_type

                    if msg_type in {"du", "timescale_update"} and len(params) >= 2 and params[0] == cs:
                        sds = params[1].get("sds_1") if isinstance(params[1], dict) else None
                        if isinstance(sds, dict):
                            for bar in sds.get("s", []) or []:
                                values = bar.get("v", [])
                                if len(values) >= 6:
                                    try:
                                        bars_by_ts[float(values[0])] = values
                                    except Exception:
                                        continue

                    if msg_type == "series_completed" and (not params or params[0] == cs):
                        return "completed"

            if bars_by_ts:
                local_status = "partial_timeout"
            return local_status

        status = drain_until_complete()

        more_notes: list[str] = []
        for _ in range(max(0, request_more_rounds)):
            before = len(bars_by_ts)
            _send(ws, "request_more_data", [cs, "sds_1", int(request_more_bars)])
            more_status = drain_until_complete()
            after = len(bars_by_ts)
            if after > before:
                more_notes.append(f"more+{after - before}")
                status = more_status
            else:
                more_notes.append("no_more")
                status = more_status
                break
        if more_notes:
            status = f"{status}+{'+'.join(more_notes)}"

    finally:
        try:
            ws.close()
        except Exception:
            pass

    if not bars_by_ts:
        if error:
            log.debug("WS history returned no bars for %s:%s %s: %s", exchange, symbol, tf_code, error)
        return WsHistoryResult(None, status, n_bars, 0, interval, error, endpoint_name)

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

    df = pd.DataFrame(rows).set_index("datetime")
    return WsHistoryResult(df, status, n_bars, len(df), interval, error, endpoint_name)


def get_hist(
    symbol: str,
    exchange: str,
    tf_code: str,
    n_bars: int,
    logger: logging.Logger | None = None,
    **kwargs,
) -> pd.DataFrame | None:
    """Convenience wrapper returning only the DataFrame."""
    result = fetch_history(
        symbol=symbol,
        exchange=exchange,
        tf_code=tf_code,
        n_bars=n_bars,
        logger=logger,
        **kwargs,
    )
    return result.df
