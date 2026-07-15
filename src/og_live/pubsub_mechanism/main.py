"""Command line entrypoint for the OG Live Pub/Sub mechanism."""

from __future__ import annotations

import argparse
import logging

from og_core.logging_setup import setup_logger
from og_live.pubsub_mechanism.app import PubSubSignalApp

setup_logger("og_live_pubsub.log")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OG Live Pub/Sub signal generation from Redis Pub/Sub messages.")
    parser.add_argument("--once", action="store_true", help="Process at most one Pub/Sub message, then exit.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="Max wait time for --once.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    app = PubSubSignalApp()
    if args.once:
        processed = app.run_once(timeout_seconds=args.timeout_seconds)
        logger.info("og_live_pubsub: --once complete, processed_or_delivered=%d", processed)
        return 0

    try:
        app.run_forever()
    except KeyboardInterrupt:
        logger.info("og_live_pubsub: shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
