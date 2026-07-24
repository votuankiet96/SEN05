"""Local read-only Chart & Data Health server."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core_engine.util.dashboard import chart_queries, health_queries
from core_engine.util.logkit import get_logger, log_activity, operation_line
from core_engine.settings import RUN_DIR, ensure_runtime_dirs


MODULE_DIR = Path(__file__).resolve().parent
STATIC_DIR = MODULE_DIR / "static"
INDEX_FILE = STATIC_DIR / "chart.html"
PID_FILE = RUN_DIR / "chart_datacheck.pid"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("CHART_DATACHECK_PORT", "8050") or "8050")

logger = get_logger("chart_datacheck", stream="system")


def _clog(event: str, *details: str, **fields: object) -> str:
    return operation_line("CHART", event, *details, **fields)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


class ChartDataCheckHandler(BaseHTTPRequestHandler):
    server_version = "DPChartDataCheck/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        text = fmt % args
        match = re.search(r'"([A-Z]+)\s+([^"]+?)\s+HTTP/[^"]+"\s+(\d+)', text)
        if match:
            method, target, status = match.groups()
            parsed = urlparse(target)
            logger.info(
                "%s",
                _clog(
                    "API request",
                    client=self.client_address[0],
                    method=method,
                    endpoint=parsed.path,
                    query=parsed.query or "-",
                    status=status,
                    result="ok" if str(status).startswith(("2", "3")) else "warning",
                ),
            )
        else:
            logger.info("%s", _clog("HTTP request", client=self.client_address[0], detail=text, result="recorded"))

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = _json_bytes(payload)
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _safe_static_path(self, raw_path: str) -> Path | None:
        relative = raw_path.removeprefix("/static/").replace("/", os.sep)
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return None
        return candidate

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/chart", "/chart/"}:
                self._send_file(INDEX_FILE)
            elif path.startswith("/static/"):
                static_path = self._safe_static_path(path)
                if static_path is None:
                    self._send_json({"error": "Invalid static path"}, HTTPStatus.BAD_REQUEST)
                else:
                    self._send_file(static_path)
            elif path == "/api/status":
                self._send_json(
                    {
                        "status": "ok",
                        "name": "DP Program Chart & Data Health",
                        "pid": os.getpid(),
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
            elif path == "/api/symbols":
                self._send_json(chart_queries.load_symbols())
            elif path == "/api/timeframes":
                self._send_json(chart_queries.load_timeframes())
            elif path == "/api/candles":
                symbol = _query_value(query, "symbol", "EURUSD")
                timeframe = _query_value(query, "tf", "H1")
                bars_raw = _query_value(query, "bars", "500")
                try:
                    bars = int(bars_raw)
                except ValueError:
                    bars = 500
                self._send_json(chart_queries.load_candles(symbol, timeframe, bars))
            elif path == "/api/health/summary":
                self._send_json(health_queries.load_summary())
            elif path == "/api/health/matrix":
                self._send_json(health_queries.load_matrix())
            elif path == "/api/health/staging":
                self._send_json(health_queries.load_staging())
            elif path == "/api/health/locks":
                self._send_json(health_queries.load_locks())
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("%s", _clog("Request failed", endpoint=path, reason=exc, result="failed"))
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def _write_pid_file(host: str, port: int) -> None:
    PID_FILE.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "url": f"http://{host}:{port}",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, open_browser: bool = False) -> int:
    ensure_runtime_dirs()
    if not INDEX_FILE.exists():
        raise FileNotFoundError(f"Missing chart UI file: {INDEX_FILE}")
    server = ThreadingHTTPServer((host, port), ChartDataCheckHandler)
    url = f"http://{host}:{port}"
    _write_pid_file(host, port)
    logger.info("%s", _clog("Server started", url=url, pid=os.getpid(), mode="read_only", result="running"))
    log_activity(
        "chart_datacheck_started",
        component="system",
        status="started",
        message="Chart & Data Health local viewer started.",
        pid=os.getpid(),
        url=url,
    )
    print("")
    print("DP Program Chart & Data Health")
    print(f"- URL : {url}")
    print(f"- PID : {os.getpid()}")
    print("- Mode: read-only SQL viewer; it does not start/stop data jobs.")
    print("")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nChart & Data Health stopped by operator.")
    finally:
        server.server_close()
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("%s", _clog("Server stopped", url=url, pid=os.getpid(), result="stopped"))
        log_activity(
            "chart_datacheck_stopped",
            component="system",
            status="stopped",
            message="Chart & Data Health local viewer stopped.",
            pid=os.getpid(),
            url=url,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Chart & Data Health viewer.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_server(args.host, args.port, open_browser=args.open_browser)
    except OSError as exc:
        logger.error("%s", _clog("Server start failed", host=args.host, port=args.port, reason=exc, result="failed"))
        print(f"Could not start Chart & Data Health server: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
