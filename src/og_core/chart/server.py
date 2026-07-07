"""Flask dashboard API for SEN05 strategy charts and CSV exports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory

from og_core import config
from og_core.chart.payload import build_chart_payload
from og_core.data.loader import load_bounds
from og_core.engine import (
    DateWindow,
    run_strategy_request,
)
from og_core.engine import (
    attach_window_meta as _attach_window_meta,
)
from og_core.engine import (
    date_window_from_args as _date_window_from_args,
)
from og_core.engine import (
    to_int as _to_int,
)
from og_core.engine import (
    trim_to_window as _trim_to_window,
)
from og_core.export.service import (
    build_bulk_export,
    build_single_export,
    parse_symbol_list,
)
from og_core.export.service import (
    signal_export_rows as _signal_export_rows,
)
from og_core.strategies.ai_trend.payload import build_ai_trend_payload
from og_core.strategies.combo.payload import build_combo_mtf_payload
from og_core.strategies.knn_combo.payload import build_knn_combo_payload
from og_core.strategies.registry import STRATEGIES

STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = STATIC_DIR / "vendor"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8516
AI_TREND_PREVIEW_ENTRY_BARS = 1500
_DB_REQUEST_LOCK = Lock()
STRATEGY_DEFAULTS = {
    "combo": {
        "symbol": "US30",
        "tf": "H4",
    },
}


@dataclass(frozen=True)
class _ScanPreview:
    args: dict[str, str]
    bars: int
    date_window: DateWindow | None
    warning: str | None = None


def create_app() -> Flask:
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
                "strategyDefaults": STRATEGY_DEFAULTS,
                "defaultBars": config.N_BARS,
            }
        )

    @app.route("/api/scan")
    def api_scan():
        try:
            strategy_key = request.args.get("strategy", "combo")
            defaults = _strategy_defaults(strategy_key)
            symbol = request.args.get("symbol", defaults["symbol"]).upper()
            tf = request.args.get("tf", defaults["tf"]).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            date_window = _date_window_from_args(request.args)
            with _DB_REQUEST_LOCK:
                scan = _scan_preview(strategy_key, request.args.to_dict(), bars, date_window)
                result = run_strategy_request(
                    strategy_key,
                    symbol=symbol,
                    tf=tf,
                    bars=scan.bars,
                    args=scan.args,
                    date_window=scan.date_window,
                )
                payload = _build_scan_payload(result)
            if scan.warning:
                payload.setdefault("meta", {})["previewWarning"] = scan.warning
                if date_window:
                    payload["meta"]["requestedStartDate"] = date_window.start_label
                    payload["meta"]["requestedEndDate"] = date_window.end_label
            return jsonify(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/export")
    def api_export():
        try:
            strategy_key = request.args.get("strategy", "combo")
            defaults = _strategy_defaults(strategy_key)
            symbol = request.args.get("symbol", defaults["symbol"]).upper()
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
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/export/bulk")
    def api_export_bulk():
        try:
            strategy_key = request.args.get("strategy", "combo")
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
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/data-range")
    def api_data_range():
        try:
            strategy_key = request.args.get("strategy", "combo")
            defaults = _strategy_defaults(strategy_key)
            symbol = request.args.get("symbol", defaults["symbol"]).upper()
            tf = request.args.get("tf", defaults["tf"]).upper()
            with _DB_REQUEST_LOCK:
                bounds = _strategy_data_bounds(strategy_key, request.args.to_dict(), symbol, tf)
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
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _strategy_defaults(strategy_key: str | None) -> dict[str, str]:
    defaults = STRATEGY_DEFAULTS.get(str(strategy_key or "").strip().lower(), {})
    return {
        "symbol": str(defaults.get("symbol") or config.DEFAULT_SYMBOL).upper(),
        "tf": str(defaults.get("tf") or config.DEFAULT_TF).upper(),
    }


def _csv_response(csv_data: str, filename: str) -> Response:
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _scan_preview(
    strategy_key: str,
    args: dict[str, str],
    bars: int,
    date_window: DateWindow | None,
) -> _ScanPreview:
    strategy = str(strategy_key).lower()
    if strategy not in {"ai_trend", "knn_combo"}:
        return _ScanPreview(args=args, bars=bars, date_window=date_window)

    trend_tf = str(args.get("TREND_TF") or "H3").upper()
    entry_tf = str(args.get("ENTRY_TF") or ("H1" if strategy == "knn_combo" else "M45")).upper()
    trend_minutes = config.TF_MINUTES.get(trend_tf)
    entry_minutes = config.TF_MINUTES.get(entry_tf)
    if not trend_minutes or not entry_minutes:
        return _ScanPreview(args=args, bars=bars, date_window=date_window)

    scan_args = dict(args)
    label = STRATEGIES.get(strategy).label if strategy in STRATEGIES else strategy_key
    if date_window is not None:
        estimated_entry_bars = _estimate_window_bars(date_window, entry_minutes)
        if estimated_entry_bars <= AI_TREND_PREVIEW_ENTRY_BARS:
            return _ScanPreview(args=scan_args, bars=bars, date_window=date_window)

        preview_minutes = AI_TREND_PREVIEW_ENTRY_BARS * entry_minutes
        preview_start = max(
            date_window.start,
            date_window.end - pd.Timedelta(minutes=preview_minutes),
        )
        clipped_window = DateWindow(
            start=preview_start,
            end=date_window.end,
            start_label=preview_start.strftime("%d/%m/%Y"),
            end_label=(date_window.end - pd.Timedelta(microseconds=1)).strftime("%d/%m/%Y"),
        )
        warning = (
            f"{label} chart preview is capped to the latest {AI_TREND_PREVIEW_ENTRY_BARS} {entry_tf} bars "
            f"inside the selected range. CSV export still uses the full {date_window.start_label} -> "
            f"{date_window.end_label} range."
        )
        return _ScanPreview(args=scan_args, bars=bars, date_window=clipped_window, warning=warning)

    requested_entry_bars = _to_int(
        scan_args.get("ENTRY_BARS") or bars,
        bars,
        50,
        20000,
    )
    auto_entry = str(scan_args.get("AUTO_ENTRY_BARS", "")).lower() in {"1", "true", "yes", "on"}
    if auto_entry:
        trend_bars = _to_int(scan_args.get("TREND_BARS"), bars, 50, 20000)
        requested_entry_bars = _corresponding_bars(trend_bars, trend_minutes, entry_minutes)
    if requested_entry_bars <= AI_TREND_PREVIEW_ENTRY_BARS:
        return _ScanPreview(args=scan_args, bars=bars, date_window=None)

    capped_trend_bars = max(
        50,
        min(20000, int(AI_TREND_PREVIEW_ENTRY_BARS * entry_minutes / trend_minutes)),
    )
    capped_entry_bars = _corresponding_bars(capped_trend_bars, trend_minutes, entry_minutes)
    scan_args["TREND_BARS"] = str(capped_trend_bars)
    scan_args["ENTRY_BARS"] = str(capped_entry_bars)
    scan_args["AUTO_ENTRY_BARS"] = "true"
    warning = (
        f"{label} chart preview is capped to {capped_entry_bars} {entry_tf} bars. "
        "CSV export can still use the full requested range."
    )
    return _ScanPreview(args=scan_args, bars=capped_entry_bars, date_window=None, warning=warning)


def _estimate_window_bars(date_window: DateWindow, tf_minutes: int) -> int:
    total_minutes = max(0, (date_window.end - date_window.start).total_seconds() / 60)
    return int(total_minutes // tf_minutes)


def _corresponding_bars(trend_bars: int, trend_minutes: int, entry_minutes: int) -> int:
    raw = int((int(trend_bars) * int(trend_minutes) + int(entry_minutes) - 1) // int(entry_minutes))
    return max(50, min(20000, raw))


def _strategy_data_bounds(
    strategy_key: str,
    args: dict[str, str],
    symbol: str,
    tf: str,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    requirements = _strategy_data_requirements(strategy_key, args, symbol, tf)
    bounds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    seen: set[tuple[str, str]] = set()
    for req_symbol, req_tf in requirements:
        key = (req_symbol.upper(), req_tf.upper())
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


def _strategy_data_requirements(
    strategy_key: str,
    args: dict[str, str],
    symbol: str,
    tf: str,
) -> list[tuple[str, str]]:
    symbols = parse_symbol_list(args.get("symbols") or symbol)
    if not symbols:
        symbols = [symbol.upper()]
    strategy = str(strategy_key or "").strip().lower()
    selected_tf = str(tf or config.DEFAULT_TF).strip().upper()

    if strategy in {"ai_trend", "knn_combo"}:
        trend_tf = str(args.get("TREND_TF") or "H3").strip().upper()
        entry_tf = str(args.get("ENTRY_TF") or selected_tf).strip().upper()
        return [(sym, trend_tf) for sym in symbols] + [(sym, entry_tf) for sym in symbols]

    tfs = [selected_tf]
    htf_enabled = str(args.get("HTF_TREND_ENABLED", "")).lower() in {"1", "true", "yes", "on"}
    if strategy == "combo" and htf_enabled:
        tfs.append(str(args.get("HTF_TF") or "D1").strip().upper())
    return [(sym, req_tf) for sym in symbols for req_tf in tfs]


def _build_scan_payload(result):
    if result.layout == "combo_mtf":
        if result.trend_frame is None:
            raise ValueError("Combo MTF result is missing trend frame.")
        payload = build_combo_mtf_payload(
            result.trend_frame,
            result.enriched,
            strategy=result.strategy,
            strategy_label=result.strategy_label,
            symbol=result.symbol,
            entry_tf=result.tf,
            entry_bars=result.bars,
            params=result.params,
        )
        _attach_window_meta(payload, result.date_window)
        return payload

    if result.layout == "ai_trend_mtf":
        if result.trend_frame is None:
            raise ValueError("AI Trend result is missing trend frame.")
        payload = build_ai_trend_payload(
            result.trend_frame,
            result.enriched,
            strategy=result.strategy,
            strategy_label=result.strategy_label,
            symbol=result.symbol,
            params=result.params,
        )
        if result.date_window:
            payload["meta"]["bars"] = len(result.enriched)
            payload["meta"]["trendBars"] = len(result.trend_frame)
            payload["meta"]["entryBars"] = len(result.enriched)
        _attach_window_meta(payload, result.date_window)
        return payload

    if result.layout == "knn_combo_mtf":
        if result.trend_frame is None:
            raise ValueError("KNN Combo result is missing trend frame.")
        payload = build_knn_combo_payload(
            result.trend_frame,
            result.enriched,
            strategy=result.strategy,
            strategy_label=result.strategy_label,
            symbol=result.symbol,
            params=result.params,
        )
        if result.date_window:
            payload["meta"]["bars"] = len(result.enriched)
            payload["meta"]["trendBars"] = len(result.trend_frame)
            payload["meta"]["entryBars"] = len(result.enriched)
        _attach_window_meta(payload, result.date_window)
        return payload

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
