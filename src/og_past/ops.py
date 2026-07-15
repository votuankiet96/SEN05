"""Operational helpers for OG Past launcher actions."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8516"


def check_dashboard(base_url: str) -> int:
    """Check the dashboard health endpoint."""
    url = f"{base_url.rstrip('/')}/health"
    print("OG Past - dashboard health")
    print(f"URL: {url}")
    try:
        body = _http_get(url, timeout=5)
    except OSError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("PASSED")
    print(body)
    return 0


def smoke_dashboard(base_url: str) -> int:
    """Run a small dashboard API scan to prove DB + strategy calculation work."""
    query = urlencode({"strategy": "combo", "symbol": "US30", "tf": "H1", "bars": "50"})
    url = f"{base_url.rstrip('/')}/api/scan?{query}"
    print("OG Past - dashboard API smoke test")
    print("Scenario: combo / US30 / H1 / latest 50 bars")
    try:
        body = _http_get(url, timeout=15)
    except OSError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("PASSED")
    print(f"Response bytes: {len(body.encode('utf-8'))}")
    return 0


def list_exports(export_dir: Path, limit: int) -> int:
    """Print the latest CSV exports."""
    print("OG Past - latest CSV exports")
    print(f"Directory: {export_dir}")
    if not export_dir.exists():
        print("No export directory found yet.")
        return 0

    files = sorted(
        (path for path in export_dir.glob("*.csv") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        print("No CSV exports found yet.")
        return 0

    for path in files[:limit]:
        stat = path.stat()
        print(f"  {path.name:60} {stat.st_size:>10} bytes")
    return 0


def _http_get(url: str, *, timeout: int) -> str:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise OSError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise OSError(str(exc.reason)) from exc


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OG Past operational helper commands.")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Check the dashboard /health endpoint.")
    health.add_argument("--base-url", default=DEFAULT_BASE_URL)

    smoke = sub.add_parser("smoke", help="Run a small dashboard API scan.")
    smoke.add_argument("--base-url", default=DEFAULT_BASE_URL)

    exports = sub.add_parser("exports", help="List latest exported CSV files.")
    exports.add_argument("--dir", default="runtime/exports")
    exports.add_argument("--limit", type=int, default=20)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "health":
        return check_dashboard(args.base_url)
    if args.command == "smoke":
        return smoke_dashboard(args.base_url)
    if args.command == "exports":
        return list_exports(Path(args.dir), max(1, args.limit))
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
