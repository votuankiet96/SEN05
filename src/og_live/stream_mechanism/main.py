"""Command line entrypoint for the OG Live Stream mechanism."""

from __future__ import annotations

import argparse
import logging

from og_core.logging_setup import setup_logger
from og_live.stream_mechanism.app import StreamSignalApp

setup_logger("og_live_stream.log")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OG Live Stream signal generation from Redis stream events.")
    parser.add_argument("--once", action="store_true", help="Process at most one live batch, then exit.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    app = StreamSignalApp()
    if args.once:
        processed = app.run_once()
        logger.info("og_live_stream: --once complete, processed_or_delivered=%d", processed)
        return 0

    try:
        app.run_forever()
    except KeyboardInterrupt:
        logger.info("og_live_stream: shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
