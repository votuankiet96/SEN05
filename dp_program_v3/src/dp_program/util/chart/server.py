"""Local offline chart server backed by read-only warehouse queries."""
# Tool chart offline cho operator xem dữ liệu đã ghi trong SQL.
# File này không gọi TradingView và không ghi SQL.

from __future__ import annotations

import argparse
import json
import logging
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ...configuration import load_config
from ...engine.sql_connector import fetch_universe, read_chart_rows

LOGGER = logging.getLogger(__name__)
ASSET = Path(__file__).with_name("lightweight-charts.js")
PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DP Program Chart</title>
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
    <h1>DP Program Chart</h1>
    <select id="symbol"></select><select id="timeframe"></select>
    <input id="bars" type="number" min="50" max="5000" value="500">
    <button id="load">Load</button><span id="status">Ready</span>
  </header>
  <div id="chart"></div>
  <script src="/assets/lightweight-charts.js"></script>
  <script>
    const el=id=>document.getElementById(id), container=el('chart');
    const chart=LightweightCharts.createChart(container,{layout:{background:{color:'#0d1117'},
      textColor:'#b8c5d1'},grid:{vertLines:{color:'#1c2734'},horzLines:{color:'#1c2734'}},
      rightPriceScale:{borderColor:'#344256'},timeScale:{borderColor:'#344256',timeVisible:true}});
    const candle=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',
      borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
    const volume=chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:''});
    volume.priceScale().applyOptions({scaleMargins:{top:.82,bottom:0}});
    new ResizeObserver(()=>chart.applyOptions({width:container.clientWidth})).observe(container);
    async function metadata(){
      const data=await (await fetch('/api/meta')).json();
      for(const group of data.symbols){const node=document.createElement('optgroup');node.label=group.name;
        for(const value of group.values){const option=document.createElement('option');
          option.value=value;option.textContent=value;node.appendChild(option)}el('symbol').appendChild(node)}
      for(const value of data.timeframes){const option=document.createElement('option');
        option.value=value;option.textContent=value;el('timeframe').appendChild(option)}
      el('symbol').value='CAPITALCOM:GOLD';el('timeframe').value='M5';
    }
    async function load(){
      el('status').textContent='Loading…';
      const query=new URLSearchParams({symbol:el('symbol').value,timeframe:el('timeframe').value,
        bars:el('bars').value});
      const response=await fetch('/api/candles?'+query),data=await response.json();
      if(!response.ok)throw new Error(data.error||'Request failed');
      candle.setData(data.candles);volume.setData(data.candles.map(x=>({time:x.time,value:x.volume,
        color:x.close>=x.open?'#26a69a88':'#ef535088'})));chart.timeScale().fitContent();
      el('status').textContent=`${data.symbol} · ${data.timeframe} · ${data.candles.length} candles`;
    }
    el('load').onclick=()=>load().catch(e=>el('status').textContent=e.message);
    metadata().then(load).catch(e=>el('status').textContent=e.message);
  </script>
</body>
</html>"""
_Rows = Callable[[dict[str, Any], str, str, int], list[tuple[Any, ...]]]


def _meta(
    symbols: list[dict[str, Any]], timeframes: list[dict[str, Any]]
) -> dict[str, Any]:
    # Tạo danh sách symbol/timeframe cho dropdown trên browser.
    groups: dict[str, list[str]] = {}
    for item in symbols:
        if item["enabled"]:
            groups.setdefault(str(item["asset_type"]), []).append(
                f"{item['exchange']}:{item['symbol']}"
            )
    return {
        "symbols": [
            {"name": name, "values": sorted(values)}
            for name, values in sorted(groups.items())
        ],
        "timeframes": [item["code"] for item in timeframes],
    }


def _unix_seconds(value: Any) -> int:
    # lightweight-charts dùng Unix seconds UTC cho trục thời gian.
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def _load_candles(
    symbols: list[dict[str, Any]],
    timeframes: list[dict[str, Any]],
    config: dict[str, Any],
    tv_symbol: str,
    timeframe: str,
    bars: int,
    *,
    row_loader: _Rows = read_chart_rows,
) -> list[dict[str, Any]]:
    """Validate the reviewed contract, then map committed SQL rows for charts."""
    # Kiểm request từ browser, rồi đọc nến đã commit trong warehouse.
    tv_symbol = str(tv_symbol or "").strip().upper()
    timeframe = str(timeframe or "").strip().upper()
    allowed_symbols = {
        f"{item['exchange']}:{item['symbol']}" for item in symbols if item["enabled"]
    }
    allowed_timeframes = {item["code"] for item in timeframes}
    if tv_symbol not in allowed_symbols:
        raise ValueError(f"Unknown symbol: {tv_symbol}")
    if timeframe not in allowed_timeframes:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    if not 50 <= int(bars) <= 5000:
        raise ValueError("bars must be between 50 and 5000")
    symbol = tv_symbol.split(":", 1)[1]
    rows = row_loader(config, symbol, timeframe, int(bars))
    return [
        {
            "time": _unix_seconds(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": 0.0 if row[5] is None else float(row[5]),
        }
        for row in reversed(rows)
    ]


class ChartServer(ThreadingHTTPServer):
    # Server giữ config và universe đã đọc lúc khởi động.
    config: dict[str, Any]
    symbols: list[dict[str, Any]]
    timeframes: list[dict[str, Any]]


class Handler(BaseHTTPRequestHandler):
    # HTTP handler phục vụ HTML, JS asset và hai API JSON read-only.
    server_version = "DPProgramChart/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], fmt % args)

    def _send(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        # Gửi response với header an toàn cơ bản cho tool local.
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
        # API luôn trả JSON gọn, dễ đọc từ browser/devtool.
        body = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
        self._send(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        # Router nhỏ cho trang chart và API đọc dữ liệu.
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/chart"}:
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif parsed.path == "/assets/lightweight-charts.js":
                self._send(ASSET.read_bytes(), "text/javascript; charset=utf-8")
            elif parsed.path == "/api/meta":
                self._json(_meta(self.server.symbols, self.server.timeframes))
            elif parsed.path == "/api/candles":
                query = parse_qs(parsed.query)
                symbol = (query.get("symbol") or [""])[0]
                timeframe = (query.get("timeframe") or [""])[0]
                bars = int((query.get("bars") or ["500"])[0])
                candles = _load_candles(
                    self.server.symbols,
                    self.server.timeframes,
                    self.server.config,
                    symbol,
                    timeframe,
                    bars,
                )
                self._json({
                    "symbol": symbol.upper(),
                    "timeframe": timeframe.upper(),
                    "candles": candles,
                })
            else:
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            LOGGER.error("Chart request failed: %s", type(exc).__name__)
            self._json({"error": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run_server(host: str = "127.0.0.1", port: int = 8050, *, open_browser: bool = False) -> None:
    # Khởi động chart server local, read-only.
    if not ASSET.is_file():
        raise FileNotFoundError(f"Offline chart asset is missing: {ASSET.name}")
    server = ChartServer((host, port), Handler)
    server.config = load_config()
    server.symbols, server.timeframes = fetch_universe(server.config)
    url = f"http://{host}:{server.server_address[1]}"
    LOGGER.info("Read-only offline chart started at %s", url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        LOGGER.info("Read-only offline chart stopped")


def main() -> int:
    # CLI thủ công cho operator mở chart khi cần kiểm dữ liệu.
    parser = argparse.ArgumentParser(description="Run the local read-only DP Program chart.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_server(args.host, args.port, open_browser=args.open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
