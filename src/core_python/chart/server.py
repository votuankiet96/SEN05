"""Flask dashboard API for core_python strategy charts and CSV exports."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

import pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory

from core_python.util import config
from core_python.chart.payloads.registry import build_chart_payload
from core_python.data.loader import load_bounds
from core_python.engine import (
    attach_window_meta as _attach_window_meta,
    date_window_from_args as _date_window_from_args,
    run_strategy_request,
    to_int as _to_int,
    validate_strategy_timeframe,
)
from core_python.export.service import (
    build_bulk_export,
    build_single_export,
    parse_symbol_list,
)
from core_python.util.logging_setup import setup_logger
from core_python.strategies.registry import STRATEGIES

# Cấu hình logging một lần khi module này được import — áp dụng cho cả
# `python -m core_python.main` (dev) lẫn gunicorn
# (`core_python.chart.server:create_app()` — prod), vì cả hai đường chạy
# đều import module này đúng một lần. Ghi ra console + runtime/logs/
# core_python.log (xem core_python/util/logging_setup.py).
setup_logger("core_python.log")

STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = STATIC_DIR / "vendor"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8516
_DB_REQUEST_LOCK = Lock()
STRATEGY_DEFAULTS = {
    "combo": {
        "symbol": "US30",
        "tf": "H4",
    },
}


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

    @app.route("/health")
    def health():
        # Liveness only (process is up) — không query DB ở đây, để health-check
        # không bị fail dây chuyền khi SQL Server chậm/tạm gián đoạn.
        return jsonify({"status": "ok"})

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/assets/lightweight-charts.js")
    def lightweight_charts():
        return send_from_directory(VENDOR_DIR, "lightweight-charts.js")

    @app.route("/api/config")
    def api_config():
        strategies = {
            key: {
                "label": spec.label,
                "defaults": spec.default_params,
                "fields": spec.param_fields,
                "recommendedTimeframes": list(spec.recommended_timeframes),
                "supportedTimeframes": list(spec.supported_timeframes),
                "defaultTimeframe": spec.default_timeframe,
                "symbolDefaults": spec.symbol_defaults,
            }
            for key, spec in STRATEGIES.items()
        }
        return jsonify(
            {
                "strategies": strategies,
                "defaultStrategy": "combo",
                "symbols": [
                    {"name": sym, "asset_type": meta["asset_type"], "x": meta["x"]}
                    for sym, meta in config.SYMBOLS.items()
                ],
                "timeframes": config.timeframe_codes(),
                "defaultSymbol": config.DEFAULT_SYMBOL,
                "defaultTf": config.DEFAULT_TF,
                "strategyDefaults": STRATEGY_DEFAULTS,
                "defaultBars": config.N_BARS,
            }
        )

    @app.route("/api/scan")
    def api_scan():
        try:
            strategy_key = _strategy_key(request.args.get("strategy", "combo"))
            defaults = _strategy_defaults(strategy_key)
            symbol = _symbol_code(request.args.get("symbol", defaults["symbol"]))
            tf = request.args.get("tf", defaults["tf"]).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            date_window = _date_window_from_args(request.args)
            with _DB_REQUEST_LOCK:
                result = run_strategy_request(
                    strategy_key,
                    symbol=symbol,
                    tf=tf,
                    bars=bars,
                    args=request.args.to_dict(),
                    date_window=date_window,
                )
                payload = _build_scan_payload(result)
            return jsonify(payload)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": _error_text(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/export")
    def api_export():
        try:
            strategy_key = _strategy_key(request.args.get("strategy", "combo"))
            defaults = _strategy_defaults(strategy_key)
            symbol = _symbol_code(request.args.get("symbol", defaults["symbol"]))
            tf = request.args.get("tf", defaults["tf"]).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            date_window = _date_window_from_args(request.args)
            with _DB_REQUEST_LOCK:
                export = build_single_export(
                    strategy_key,
                    symbol=symbol,
                    tf=tf,
                    bars=bars,
                    args=request.args.to_dict(),
                    date_window=date_window,
                )
            return _csv_response(export.data, export.filename)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": _error_text(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/export/bulk")
    def api_export_bulk():
        try:
            strategy_key = _strategy_key(request.args.get("strategy", "combo"))
            defaults = _strategy_defaults(strategy_key)
            tf = request.args.get("tf", defaults["tf"]).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            date_window = _date_window_from_args(request.args)
            with _DB_REQUEST_LOCK:
                export = build_bulk_export(
                    strategy_key,
                    tf=tf,
                    bars=bars,
                    args=request.args.to_dict(),
                    date_window=date_window,
                )
            return _csv_response(export.data, export.filename)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": _error_text(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/data-range")
    def api_data_range():
        try:
            strategy_key = _strategy_key(request.args.get("strategy", "combo"))
            defaults = _strategy_defaults(strategy_key)
            symbol = _symbol_code(request.args.get("symbol", defaults["symbol"]))
            tf = request.args.get("tf", defaults["tf"]).upper()
            validate_strategy_timeframe(STRATEGIES[strategy_key], tf)
            with _DB_REQUEST_LOCK:
                bounds = _strategy_data_bounds(request.args.to_dict(), symbol, tf)
            if bounds is None:
                return jsonify({"available": False})
            start, end = bounds
            return jsonify(
                {
                    "available": True,
                    "startDate": start.strftime("%d/%m/%Y"),
                    "endDate": end.strftime("%d/%m/%Y"),
                    "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        except (KeyError, ValueError) as exc:
            return jsonify({"error": _error_text(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _strategy_key(value: str | None) -> str:
    key = str(value or "combo").strip().lower()
    if key not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{value}'. Available: {', '.join(STRATEGIES)}")
    return key


def _error_text(exc: BaseException) -> str:
    return str(exc).strip('"')


def _symbol_code(value: str | None) -> str:
    symbol = str(value or "").strip().upper()
    try:
        config.get_symbol(symbol)
    except KeyError as exc:
        raise ValueError(str(exc).strip('"')) from exc
    return symbol


def _strategy_defaults(strategy_key: str | None) -> dict[str, str]:
    key = str(strategy_key or "").strip().lower()
    defaults = STRATEGY_DEFAULTS.get(key, {})
    spec = STRATEGIES.get(key)
    strategy_tf = spec.default_timeframe if spec is not None else None
    return {
        "symbol": str(defaults.get("symbol") or config.DEFAULT_SYMBOL).upper(),
        "tf": str(defaults.get("tf") or strategy_tf or config.DEFAULT_TF).upper(),
    }


def _csv_response(csv_data: str, filename: str) -> Response:
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _strategy_data_bounds(
    args: dict[str, str],
    symbol: str,
    tf: str,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    symbols = parse_symbol_list(args.get("symbols") or symbol) or [symbol.upper()]
    selected_tf = str(tf or config.DEFAULT_TF).strip().upper()

    bounds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    seen: set[tuple[str, str]] = set()
    for sym in symbols:
        key = (sym.upper(), selected_tf)
        if key in seen:
            continue
        seen.add(key)
        item = load_bounds(*key)
        if item is None:
            return None
        bounds.append(item)
    if not bounds:
        return None

    start = max(item[0] for item in bounds)
    end = min(item[1] for item in bounds)
    if end < start:
        return None
    return start, end


def _build_scan_payload(result):
    payload = build_chart_payload(
        result.enriched,
        strategy=result.strategy,
        strategy_label=result.strategy_label,
        symbol=result.symbol,
        tf=result.tf,
        bars=result.bars,
        params=result.params,
    )
    _attach_window_meta(payload, result.date_window)
    return payload


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, debug: bool = False) -> None:
    app = create_app()
    app.run(host=host, port=port, debug=debug, use_reloader=False)
