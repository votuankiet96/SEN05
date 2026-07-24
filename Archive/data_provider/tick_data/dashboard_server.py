"""Small isolated Flask dashboard for cTrader/FTMO tick_data."""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError as exc:  # pragma: no cover - operator dependency guard
    raise SystemExit("Flask is required for the tick dashboard. Install with: pip install flask") from exc

from .checker import run_tick_check
from .runtime import load_settings
from .spool import TickSpool
from .store_sql import TickSqlStore, qualified_tick_table, quote_ident

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "dashboard"
DEFAULT_HOST = os.environ.get("CTRADER_FTMO_TICK_DASHBOARD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("CTRADER_FTMO_TICK_DASHBOARD_PORT", "8061"))

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")


def _settings_store() -> tuple[Any, TickSqlStore]:
    settings = load_settings()
    store = TickSqlStore(
        settings.schema,
        settings.symbols,
        environment=settings.env,
        account_id=settings.account_id,
    )
    return settings, store


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    names = [column[0] for column in cursor.description]
    return {name: _json_value(value) for name, value in zip(names, row)}


def _query(store: TickSqlStore, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return [_row_dict(cursor, row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _limit(value: str | None, default: int, maximum: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        parsed = default
    return max(1, min(maximum, parsed))


def _tail_file_lines(path: Path, lines: int, max_bytes: int = 262_144) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
        text = handle.read().decode("utf-8", errors="replace")
    result = text.splitlines()
    if size > max_bytes and result:
        result = result[1:]
    return result[-lines:]


def _symbol(store: TickSqlStore, raw: str | None) -> str:
    symbol = str(raw or "").upper()
    if symbol not in store.allowed_symbols:
        raise ValueError(f"Unknown tick symbol: {symbol}")
    return symbol


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/tick/health")
def api_health():
    settings, store = _settings_store()
    report = run_tick_check(settings, store)
    return jsonify(asdict(report))


@app.get("/api/tick/symbols")
def api_symbols():
    settings, store = _settings_store()
    rows = _query(
        store,
        f"""
        SELECT SenSymbol, AssetType, MappingStatus, Enabled, CTraderSymbolId, CTraderSymbolName
        FROM {quote_ident(settings.schema)}.[SymbolMap]
        ORDER BY SenSymbol
        """,
    )
    return jsonify({"symbols": rows})


@app.get("/api/tick/runs")
def api_runs():
    settings, store = _settings_store()
    limit = _limit(request.args.get("limit"), 20, 200)
    rows = _query(
        store,
        f"""
        SELECT TOP {limit}
            IngestRunID, AppName, Environment, StartedAtUtc, StoppedAtUtc,
            Status, RowsInserted, RowsSpooled, StopReason, HostName, ProcessID
        FROM {quote_ident(settings.schema)}.[IngestRun]
        ORDER BY StartedAtUtc DESC
        """,
    )
    return jsonify({"runs": rows})


@app.get("/api/tick/latest")
def api_latest():
    settings, store = _settings_store()
    symbol = request.args.get("symbol")
    limit = _limit(request.args.get("limit"), 50, 1000)
    symbols = [_symbol(store, symbol)] if symbol else sorted(store.allowed_symbols)
    payload: dict[str, Any] = {}
    for item in symbols:
        table = qualified_tick_table(settings.schema, item, store.allowed_symbols)
        rows = _query(
            store,
            f"""
            SELECT TOP {limit}
                TickTimeUtc, SourceTimestampMs, Bid, Ask, Spread, QuoteType, SourceMode, ReceivedAtUtc
            FROM {table}
            ORDER BY TickTimeUtc DESC, TickID DESC
            """,
        )
        payload[item] = rows
    return jsonify({"latest": payload})


@app.get("/api/tick/chart")
def api_chart():
    settings, store = _settings_store()
    symbol = _symbol(store, request.args.get("symbol") or settings.symbols[0].local_symbol)
    limit = _limit(request.args.get("limit"), 2000, 20000)
    table = qualified_tick_table(settings.schema, symbol, store.allowed_symbols)
    rows = _query(
        store,
        f"""
        SELECT *
        FROM (
            SELECT TOP {limit}
                TickTimeUtc, Bid, Ask, Spread, QuoteType, SourceMode
            FROM {table}
            ORDER BY TickTimeUtc DESC, TickID DESC
        ) q
        ORDER BY TickTimeUtc ASC
        """,
    )
    return jsonify({"symbol": symbol, "ticks": rows})


@app.get("/api/tick/spool")
def api_spool():
    settings, _store = _settings_store()
    spool = TickSpool(settings.spool_path)
    return jsonify({"path": str(settings.spool_path), "count": spool.count()})


@app.get("/api/tick/log-tail")
def api_log_tail():
    settings, _store = _settings_store()
    lines = _limit(request.args.get("lines"), 300, 2000)
    path = settings.log_path
    if not path.exists():
        return jsonify({"path": str(path), "lines": []})
    return jsonify({"path": str(path), "lines": _tail_file_lines(path, lines)})


def main() -> None:
    host = os.environ.get("CTRADER_FTMO_TICK_DASHBOARD_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CTRADER_FTMO_TICK_DASHBOARD_PORT", str(DEFAULT_PORT)))
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
