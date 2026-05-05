"""Flask server for the simplified Lightweight Charts strategy scanner."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from simplified_core_python import config
from simplified_core_python.chart.payload import build_chart_payload
from simplified_core_python.data.loader import load
from simplified_core_python.strategies.registry import STRATEGIES, get_strategy


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = ROOT / "data_provider"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8515


def create_app() -> Flask:
    """Create the chart Flask app."""
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

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
                "defaultBars": config.N_BARS,
            }
        )

    @app.route("/api/scan")
    def api_scan():
        try:
            strategy_key = request.args.get("strategy", "combo")
            spec = get_strategy(strategy_key)
            symbol = request.args.get("symbol", config.DEFAULT_SYMBOL).upper()
            tf = request.args.get("tf", config.DEFAULT_TF).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)

            params = spec.normalize_params(overrides, symbol)
            raw = load(symbol, tf, bars)
            with_indicators = spec.add_indicators(raw, params)
            with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
            enriched = spec.add_levels(with_signals, params, symbol)

            return jsonify(
                build_chart_payload(
                    enriched,
                    strategy=spec.key,
                    strategy_label=spec.label,
                    symbol=symbol,
                    tf=tf,
                    bars=bars,
                    params=params,
                )
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _strategy_overrides(args: dict[str, str], fields: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {field["key"] for field in fields}
    lower_to_key = {key.lower(): key for key in keys}
    overrides: dict[str, Any] = {}
    for key, value in args.items():
        normalized = lower_to_key.get(key.lower())
        if normalized:
            overrides[normalized] = value
    return overrides


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, debug: bool = False) -> None:
    """Run the chart server."""
    app = create_app()
    app.run(host=host, port=port, debug=debug, use_reloader=False)

