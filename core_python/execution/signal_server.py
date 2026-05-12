"""Flask HTTP server that exposes the pending signal queue to cBot clients.

Run as a separate process (alongside signal_watcher.py):
    python -m core_python.execution.signal_server

The cTrader cBot polls GET /api/pending-signals every N seconds.
After placing an order, the cBot calls POST /api/ack/<signal_id> to consume
the signal so it is not dispatched again.

Endpoints:
    GET  /api/pending-signals         List unacked, non-expired signals
    POST /api/ack/<signal_id>         Mark a signal as consumed
    GET  /api/health                  Health check + queue size

Security: binds to 127.0.0.1 only by default. The cBot on the same machine
uses http://127.0.0.1:5050. Do not expose this port to external networks.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify

from core_python.execution.signal_queue import PendingSignalQueue

_STARTUP_UTC = pd.Timestamp.now("UTC")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_queue: PendingSignalQueue | None = None


def _get_queue() -> PendingSignalQueue:
    if _queue is None:
        raise RuntimeError("signal_server: queue not initialised")
    return _queue


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@app.get("/api/pending-signals")
def pending_signals():
    """Return list of pending (unacked, non-expired) signals."""
    signals = _get_queue().get_pending()
    return jsonify(signals)


@app.post("/api/ack/<signal_id>")
def ack_signal(signal_id: str):
    """Mark a signal as consumed by the cBot."""
    found = _get_queue().ack(signal_id)
    if not found:
        return jsonify({"ok": False, "detail": "not found or already acked"}), 404
    return jsonify({"ok": True})


@app.get("/api/health")
def health():
    """Health check: returns queue size and uptime."""
    pending = _get_queue().get_pending()
    uptime_s = int((pd.Timestamp.now("UTC") - _STARTUP_UTC).total_seconds())
    return jsonify({"ok": True, "pending": len(pending), "uptime_s": uptime_s})


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> int:
    global _queue

    parser = argparse.ArgumentParser(
        description="AI Trend signal HTTP server for cBot dispatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m core_python.execution.signal_server\n"
            "  python -m core_python.execution.signal_server --port 5050 --ttl-minutes 120\n"
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Default: 127.0.0.1 (localhost only).",
    )
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument(
        "--queue-path",
        default=None,
        help="Path to pending_signals.json. Default: core_python/runtime/pending_signals.json",
    )
    parser.add_argument(
        "--ttl-minutes",
        type=int,
        default=120,
        help="Signals older than this are excluded from /pending-signals. Default: 120 min.",
    )
    args = parser.parse_args()

    _queue = PendingSignalQueue(
        path=args.queue_path,
        ttl_seconds=args.ttl_minutes * 60,
    )

    logger.info("signal_server: queue  -> %s", _queue.path)
    logger.info("signal_server: TTL    -> %d min", args.ttl_minutes)
    logger.info("signal_server: listen -> http://%s:%d", args.host, args.port)
    logger.info("signal_server: endpoints:")
    logger.info("  GET  http://%s:%d/api/pending-signals", args.host, args.port)
    logger.info("  POST http://%s:%d/api/ack/<id>", args.host, args.port)
    logger.info("  GET  http://%s:%d/api/health", args.host, args.port)

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
