"""
Probe TradingView WebSocket historical depth for one symbol and all configured TFs.

This script is intentionally read-only:
- no database writes
- no staging/Fact ETL
- no production config changes

It resolves the current TradingView auth through data_provider/tv/auth.py, opens a
raw TradingView WebSocket, requests increasingly large series for each timeframe,
and reports how many bars TradingView actually returns.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import string
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websocket


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_provider.tv import auth as _tv_auth  # noqa: E402
from config import SYMBOLS, TV_COOKIE, TF_DISPLAY_ORDER  # noqa: E402


TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TV_WS_TIMEZONE = "Etc/UTC"

# Direct WS interval candidates for all 15 configured TFs. The five TFs that are
# computed in the current pipeline are probed here as direct TradingView intervals.
TF_INTERVAL_CANDIDATES: dict[str, list[str]] = {
    "W": ["1W"],
    "D1": ["1D"],
    "H8": ["480", "8H"],
    "H6": ["360", "6H"],
    "H4": ["240", "4H"],
    "H3": ["180", "3H"],
    "H2": ["120", "2H"],
    "H1": ["60", "1H"],
    "M90": ["90"],
    "M45": ["45"],
    "M30": ["30"],
    "M20": ["20"],
    "M15": ["15"],
    "M10": ["10"],
    "M5": ["5"],
}


@dataclass
class ProbeResult:
    tf_code: str
    interval: str
    requested: int
    status: str
    tv_count: int
    closed_count: int
    first_utc: str
    last_utc: str
    elapsed_sec: float
    error: str = ""


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


def _to_utc(ts: float | int | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


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


def _fetch_once(
    *,
    token: str,
    cookie: str,
    exchange: str,
    symbol: str,
    tf_code: str,
    interval: str,
    requested: int,
    timeout_sec: float,
    more_rounds: int = 0,
    more_bars: int = 10000,
) -> ProbeResult:
    cs = _gen_id("cs")
    started = time.monotonic()
    bars_by_ts: dict[float, list[Any]] = {}
    status = "timeout"
    error = ""

    try:
        ws = websocket.create_connection(
            TV_WS_URL,
            header=_headers(cookie),
            timeout=max(5.0, timeout_sec),
            origin="https://www.tradingview.com",
        )
    except Exception as exc:
        return ProbeResult(tf_code, interval, requested, "connect_error", 0, 0, "-", "-", 0.0, str(exc))

    try:
        ws.settimeout(1.0)
        _send(ws, "set_auth_token", [token])
        _send(ws, "chart_create_session", [cs, ""])
        _send(ws, "switch_timezone", [cs, TV_WS_TIMEZONE])
        sym_json = json.dumps(
            {"symbol": f"{exchange}:{symbol}", "adjustment": "splits"},
            separators=(",", ":"),
        )
        _send(ws, "resolve_symbol", [cs, "sds_sym_1", f"={sym_json}"])
        _send(ws, "create_series", [cs, "sds_1", "sds_sym_1", "sds_sym_1", interval, requested, ""])

        def drain_until_complete() -> str:
            loop_status = "timeout"
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception as exc:
                    nonlocal error
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
                        nonlocal_status = msg_type
                        error = json.dumps(params, ensure_ascii=True)[:300]
                        return nonlocal_status

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
                loop_status = "partial_timeout"
            return loop_status

        status = drain_until_complete()

        more_notes: list[str] = []
        for _ in range(max(0, more_rounds)):
            before = len(bars_by_ts)
            _send(ws, "request_more_data", [cs, "sds_1", more_bars])
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

    timestamps = sorted(bars_by_ts)
    tv_count = len(timestamps)
    # The last TV bar is usually still open; production code drops it.
    closed_count = max(0, tv_count - 1) if tv_count else 0
    elapsed = time.monotonic() - started
    return ProbeResult(
        tf_code=tf_code,
        interval=interval,
        requested=requested,
        status=status,
        tv_count=tv_count,
        closed_count=closed_count,
        first_utc=_to_utc(timestamps[0] if timestamps else None),
        last_utc=_to_utc(timestamps[-1] if timestamps else None),
        elapsed_sec=elapsed,
        error=error,
    )


def _find_symbol(name: str) -> dict[str, Any]:
    target = name.upper()
    aliases = {"XAU": "GOLD", "XAUUSD": "GOLD"}
    target = aliases.get(target, target)
    for sym in SYMBOLS:
        if sym["tv_symbol"].upper() == target:
            return sym
    raise SystemExit(f"Symbol not found in config.SYMBOLS: {name!r}")


def _resolve_auth() -> tuple[str, str, str]:
    logger = logging.getLogger("probe_ws_history_depth.auth")
    token, source = _tv_auth._resolve_auth_token(logger)
    cookie = _tv_auth._tv_cookie or TV_COOKIE or ""
    return token, source, cookie


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe TradingView WS historical depth.")
    parser.add_argument("--symbol", default="GOLD", help="config tv_symbol or alias XAU/XAUUSD")
    parser.add_argument(
        "--sizes",
        default="500,5000,10000,20000,50000",
        help="comma-separated requested bar counts",
    )
    parser.add_argument("--timeout", type=float, default=35.0, help="seconds per request")
    parser.add_argument("--plateau-stop", type=int, default=1, help="stop after this many non-growing probes")
    parser.add_argument("--timeframes", default="", help="optional comma-separated TF filter, e.g. M5,M30,D1")
    parser.add_argument("--more-rounds", type=int, default=0, help="extra request_more_data rounds per request")
    parser.add_argument("--more-bars", type=int, default=10000, help="bars to ask for in each request_more_data round")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    sym = _find_symbol(args.symbol)
    sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()]
    token, auth_source, cookie = _resolve_auth()
    auth_mode = "guest" if token == _tv_auth.GUEST_TOKEN else "authenticated"

    print(f"symbol={sym['tv_exchange']}:{sym['tv_symbol']} symbol_id={sym['symbol_id']} asset={sym['asset_type']}")
    print(f"auth_mode={auth_mode} auth_source={auth_source} cookie={'yes' if cookie else 'no'}")
    print(
        f"sizes={sizes} timeout={args.timeout:.0f}s "
        f"more_rounds={args.more_rounds} more_bars={args.more_bars}"
    )
    print()
    print(
        "tf,interval,requested,status,tv_count,closed_count,first_utc,last_utc,elapsed_sec,error"
    )

    tf_order = [tf for tf in TF_DISPLAY_ORDER if tf in TF_INTERVAL_CANDIDATES]
    if args.timeframes.strip():
        requested_tfs = {tf.strip().upper() for tf in args.timeframes.split(",") if tf.strip()}
        tf_order = [tf for tf in tf_order if tf in requested_tfs]
    summary: list[ProbeResult] = []

    for tf_code in tf_order:
        candidates = TF_INTERVAL_CANDIDATES[tf_code]
        chosen_interval = candidates[0]

        if len(candidates) > 1:
            candidate_results = [
                _fetch_once(
                    token=token,
                    cookie=cookie,
                    exchange=sym["tv_exchange"],
                    symbol=sym["tv_symbol"],
                    tf_code=tf_code,
                    interval=interval,
                    requested=min(sizes[0], 500),
                    timeout_sec=args.timeout,
                    more_rounds=args.more_rounds,
                    more_bars=args.more_bars,
                )
                for interval in candidates
            ]
            candidate_results.sort(key=lambda r: (r.tv_count, r.closed_count), reverse=True)
            chosen_interval = candidate_results[0].interval
            for result in candidate_results:
                print_result(result)
            if candidate_results[0].tv_count <= 0:
                summary.append(candidate_results[0])
                continue

        best: ProbeResult | None = None
        non_growing = 0
        last_count = -1
        for requested in sizes:
            result = _fetch_once(
                token=token,
                cookie=cookie,
                exchange=sym["tv_exchange"],
                symbol=sym["tv_symbol"],
                tf_code=tf_code,
                interval=chosen_interval,
                requested=requested,
                timeout_sec=args.timeout,
                more_rounds=args.more_rounds,
                more_bars=args.more_bars,
            )
            print_result(result)
            if best is None or result.closed_count > best.closed_count:
                best = result
            if result.closed_count <= last_count:
                non_growing += 1
            else:
                non_growing = 0
            last_count = max(last_count, result.closed_count)

            if result.tv_count <= 0 or result.status in {"error", "critical_error", "connect_error"}:
                break
            if result.closed_count < requested and non_growing >= args.plateau_stop:
                break

        if best is not None:
            summary.append(best)

    print()
    print("summary_best_closed")
    print("tf,interval,best_requested,best_closed_count,first_utc,last_utc,status")
    for result in summary:
        print(
            f"{result.tf_code},{result.interval},{result.requested},{result.closed_count},"
            f"{result.first_utc},{result.last_utc},{result.status}"
        )
    return 0


def print_result(result: ProbeResult) -> None:
    print(
        f"{result.tf_code},{result.interval},{result.requested},{result.status},"
        f"{result.tv_count},{result.closed_count},{result.first_utc},{result.last_utc},"
        f"{result.elapsed_sec:.2f},{result.error}"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
