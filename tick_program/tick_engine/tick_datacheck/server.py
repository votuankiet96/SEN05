"""Local read-only Tick Data Check server.

Serves a browser UI at http://127.0.0.1:8060 for inspecting tick data
and ingest health. Read-only: never modifies DB and never controls the
backfill service.

Usage:
    python -m tick_engine datacheck [--port 8060] [--open-browser]
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import mimetypes
import os
import sys
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tick_engine.tick_datacheck import tick_queries, health_queries

MODULE_DIR = Path(__file__).resolve().parent
STATIC_DIR = MODULE_DIR / "static"
INDEX_FILE = STATIC_DIR / "chart.html"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("TICK_DATACHECK_PORT", "8060") or "8060")

logger = logging.getLogger("tick_engine.tick_datacheck")


def _setup_logger() -> None:
    from tick_engine.settings import SYSTEM_LOG, ensure_runtime_dirs

    ensure_runtime_dirs()
    fmt = logging.Formatter(
        "%(asctime)sZ | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = time.gmtime
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            SYSTEM_LOG, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        ),
    ]
    for h in handlers:
        h.setFormatter(fmt)
    logger.handlers.clear()
    for h in handlers:
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _qval(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


class TickDataCheckHandler(BaseHTTPRequestHandler):
    server_version = "TickDataCheck/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("%s - %s", self.client_address[0], fmt % args)

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

    def _safe_static(self, raw_path: str) -> Path | None:
        relative = raw_path.removeprefix("/static/").replace("/", os.sep)
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return None
        return candidate

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/chart", "/chart/"}:
                self._send_file(INDEX_FILE)

            elif path.startswith("/static/"):
                sp = self._safe_static(path)
                if sp is None:
                    self._send_json({"error": "Invalid path"}, HTTPStatus.BAD_REQUEST)
                else:
                    self._send_file(sp)

            elif path == "/api/status":
                self._send_json({
                    "status": "ok",
                    "name": "Tick Engine Data Check",
                    "pid": os.getpid(),
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })

            elif path == "/api/symbols":
                self._send_json(tick_queries.load_symbols())

            elif path == "/api/windows":
                self._send_json(tick_queries.load_windows())

            elif path == "/api/tick-series":
                symbol = _qval(query, "symbol", "US30")
                minutes = int(_qval(query, "minutes", "60") or 60)
                max_ticks = int(_qval(query, "max_ticks", "1000") or 1000)
                self._send_json(tick_queries.load_tick_series(symbol, minutes, max_ticks))

            elif path == "/api/health/summary":
                self._send_json(health_queries.load_summary())

            elif path == "/api/health/symbols":
                self._send_json(health_queries.load_symbol_health())

            elif path == "/api/health/runs":
                limit = int(_qval(query, "limit", "25") or 25)
                self._send_json(health_queries.load_ingest_runs(limit))

            elif path == "/api/health/locks":
                self._send_json(health_queries.load_locks())

            elif path == "/api/health/spool":
                self._send_json(health_queries.load_spool())

            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("Request error for %s: %s", path, exc)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def _write_pid(host: str, port: int) -> None:
    from tick_engine.settings import RUN_DIR, ensure_runtime_dirs

    ensure_runtime_dirs()
    pid_file = RUN_DIR / "tick_datacheck.pid"
    pid_file.write_text(
        json.dumps({
            "pid": os.getpid(),
            "host": host,
            "port": port,
            "url": f"http://{host}:{port}",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=2),
        encoding="utf-8",
    )
    return pid_file


def _cleanup_stale_pid() -> None:
    from tick_engine.settings import RUN_DIR, ensure_runtime_dirs
    from tick_engine.utils_support.proc_utils import is_pid_alive

    ensure_runtime_dirs()
    pid_file = RUN_DIR / "tick_datacheck.pid"
    try:
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except FileNotFoundError:
        return
    except Exception:
        pid = 0
    if pid and is_pid_alive(pid):
        return
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove stale datacheck pid file")


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = False,
) -> int:
    _setup_logger()
    _cleanup_stale_pid()
    if not INDEX_FILE.exists():
        print(f"ERROR: Missing UI file: {INDEX_FILE}", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer((host, port), TickDataCheckHandler)
    except OSError as exc:
        logger.error("Cannot start server on %s:%s — %s", host, port, exc)
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    url = f"http://{host}:{port}"
    _write_pid(host, port)
    logger.info("Tick Data Check server started  pid=%s  url=%s", os.getpid(), url)

    print("")
    print("Tick Engine Data Check")
    print(f"  URL : {url}")
    print(f"  PID : {os.getpid()}")
    print("  Mode: read-only — does not affect the backfill service")
    print("")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopped by operator.")
    finally:
        server.server_close()
        try:
            from tick_engine.settings import RUN_DIR
            (RUN_DIR / "tick_datacheck.pid").unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("Tick Data Check server stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only tick data viewer at http://localhost:8060"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    return run_server(args.host, args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    raise SystemExit(main())
