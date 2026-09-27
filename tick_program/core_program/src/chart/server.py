"""Local, read-only offline chart server for tick_program -- shows raw/thinned
ticks (not candles) for a symbol over a chosen time window, straight from
tick.<SYMBOL>. Never writes SQL, never calls cTrader. Adapted from
dp_program_v3's util/chart/server.py (same http.server + lightweight-charts
approach, no new pip dependency), reworked for ticks: two line series
(Bid/Ask) instead of candlesticks, a time-window picker instead of a
timeframe picker.

Whatever `tick.<SYMBOL>` currently holds for the chosen window is what gets
plotted: raw tick-by-tick data for anything within the last ~24h (before
thin-history reaches it), or only the surviving zigzag-pivot rows for
anything older (thin-history already deleted the rest, permanently) -- the
server does not need to know or branch on which case applies.
"""
from __future__ import annotations

import argparse
import json
import logging
import webbrowser
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger(__name__)
ASSET = Path(__file__).with_name("lightweight-charts.js")

# label -> hours of lookback. "all" is capped by the row LIMIT below, not by
# how far back tick.<SYMBOL> actually goes.
_WINDOWS: dict[str, float | None] = {
    "1h": 1, "6h": 6, "24h": 24, "3d": 72, "7d": 168, "30d": 720, "all": None,
}
_DEFAULT_LIMIT = 5000
_MAX_LIMIT = 20000

PAGE = """<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>tick_program Chart</title>
  <style>
    :root{color-scheme:dark;background:#0d1117;color:#dce3ea;font:14px Segoe UI,sans-serif}
    *{box-sizing:border-box}body{margin:0}header{height:58px;display:flex;gap:10px;align-items:center;
    padding:10px 18px;border-bottom:1px solid #273241;background:#111823}h1{font-size:17px;margin:0 18px 0 0}
    select,input,button{height:36px;border:1px solid #344256;border-radius:6px;background:#172131;
    color:#e7edf4;padding:0 10px}button{background:#1769aa;cursor:pointer;font-weight:600}
    #status{margin-left:auto;color:#9eb0c2}#chart{height:calc(100vh - 58px);min-height:480px}
    @media(max-width:760px){header{height:auto;flex-wrap:wrap}#status{width:100%;margin:0}#chart{height:75vh}}
  </style>
</head>
<body>
  <header>
    <h1>tick_program Chart</h1>
    <select id="symbol"></select>
    <select id="window">
      <option value="1h">1 gio</option><option value="6h">6 gio</option>
      <option value="24h" selected>24 gio</option><option value="3d">3 ngay</option>
      <option value="7d">7 ngay</option><option value="30d">30 ngay</option>
      <option value="all">Toan bo</option>
    </select>
    <button id="load">Xem</button><span id="status">San sang</span>
  </header>
  <div id="chart"></div>
  <script src="/assets/lightweight-charts.js"></script>
  <script>
    const el=id=>document.getElementById(id), container=el('chart');
    const chart=LightweightCharts.createChart(container,{layout:{background:{color:'#0d1117'},
      textColor:'#b8c5d1'},grid:{vertLines:{color:'#1c2734'},horzLines:{color:'#1c2734'}},
      rightPriceScale:{borderColor:'#344256'},timeScale:{borderColor:'#344256',timeVisible:true,secondsVisible:true}});
    const bidLine=chart.addLineSeries({color:'#26a69a',lineWidth:1,title:'Bid'});
    const askLine=chart.addLineSeries({color:'#ef5350',lineWidth:1,title:'Ask'});
    new ResizeObserver(()=>chart.applyOptions({width:container.clientWidth})).observe(container);
    async function metadata(){
      const data=await (await fetch('/api/meta')).json();
      for(const group of data.symbols){const node=document.createElement('optgroup');node.label=group.name;
        for(const value of group.values){const option=document.createElement('option');
          option.value=value;option.textContent=value;node.appendChild(option)}el('symbol').appendChild(node)}
    }
    async function load(){
      el('status').textContent='Dang tai...';
      const query=new URLSearchParams({symbol:el('symbol').value,window:el('window').value});
      const response=await fetch('/api/ticks?'+query),data=await response.json();
      if(!response.ok)throw new Error(data.error||'Request failed');
      bidLine.setData(data.ticks.map(t=>({time:t.time,value:t.bid})));
      askLine.setData(data.ticks.map(t=>({time:t.time,value:t.ask})));
      chart.timeScale().fitContent();
      const note=data.truncated?` (co the con nhieu hon, thu thu hep khung gio)`:'';
      el('status').textContent=`${data.symbol} · ${data.window} · ${data.ticks.length} tick${note}`;
    }
    el('load').onclick=()=>load().catch(e=>el('status').textContent=e.message);
    metadata().then(load).catch(e=>el('status').textContent=e.message);
  </script>
</body>
</html>"""


def _meta(store: Any) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for target in store.targets.values():
        groups.setdefault(str(target.asset_type), []).append(target.local_symbol)
    return {"symbols": [{"name": name, "values": sorted(values)} for name, values in sorted(groups.items())]}


def _unix_seconds(value: Any) -> int:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def _load_ticks(store: Any, symbol: str, window: str, *, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    window = str(window or "").strip().lower()
    if symbol not in store.allowed_symbols:
        raise ValueError(f"Unknown symbol: {symbol}")
    if window not in _WINDOWS:
        raise ValueError(f"Unknown window: {window}")
    hours = _WINDOWS[window]
    since_utc = datetime(2000, 1, 1, tzinfo=timezone.utc) if hours is None else datetime.now(timezone.utc) - timedelta(hours=hours)
    limit = max(50, min(int(limit), _MAX_LIMIT))
    rows = store.read_chart_ticks(symbol, since_utc, limit)
    ticks = [
        {"time": _unix_seconds(row[0]), "bid": float(row[1]) if row[1] is not None else None,
         "ask": float(row[2]) if row[2] is not None else None}
        for row in rows
    ]
    return {"symbol": symbol, "window": window, "ticks": ticks, "truncated": len(rows) >= limit}


class ChartServer(ThreadingHTTPServer):
    store: Any


class Handler(BaseHTTPRequestHandler):
    server_version = "TickProgramChart/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], fmt % args)

    def _send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str).encode()
        self._send(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/chart"}:
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif parsed.path == "/assets/lightweight-charts.js":
                self._send(ASSET.read_bytes(), "text/javascript; charset=utf-8")
            elif parsed.path == "/api/meta":
                self._json(_meta(self.server.store))
            elif parsed.path == "/api/ticks":
                query = parse_qs(parsed.query)
                symbol = (query.get("symbol") or [""])[0]
                window = (query.get("window") or ["24h"])[0]
                self._json(_load_ticks(self.server.store, symbol, window))
            else:
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            LOGGER.error("Chart request failed: %s", type(exc).__name__)
            self._json({"error": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run_server(host: str = "127.0.0.1", port: int = 8060, *, open_browser: bool = False) -> None:
    if not ASSET.is_file():
        raise FileNotFoundError(f"Offline chart asset is missing: {ASSET.name}")
    from src.configuration import load_settings
    from src.sql_store import TickSqlStore, resolve_target_symbols

    settings = load_settings()
    targets = resolve_target_symbols(settings.symbols)
    store = TickSqlStore(settings.schema, targets, environment=settings.env, account_id=settings.account_id)

    server = ChartServer((host, port), Handler)
    server.store = store
    url = f"http://{host}:{server.server_address[1]}"
    LOGGER.info("Read-only tick chart started at %s", url)
    print(f"Chart dang chay tai {url} -- nhan Ctrl+C de dung.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        LOGGER.info("Read-only tick chart stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local read-only tick_program chart.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8060)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_server(args.host, args.port, open_browser=args.open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
