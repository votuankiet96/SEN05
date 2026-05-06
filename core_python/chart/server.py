"""
Flask server cho dashboard biểu đồ chiến lược SEN05.

Mô tả:
    Cung cấp 4 endpoint HTTP phục vụ giao diện web Lightweight Charts:
    - GET /              → Trang index.html
    - GET /assets/...    → Thư viện lightweight-charts.js
    - GET /api/config    → Metadata chiến lược, symbol, khung thời gian
    - GET /api/scan      → Chạy pipeline chiến lược, trả JSON payload biểu đồ
    - GET /api/export    → Tải file CSV các tín hiệu theo cột được chọn

Đầu vào:
    Query params từ trình duyệt (strategy, symbol, tf, bars, overrides).

Đầu ra:
    JSON response hoặc file CSV.

Phụ thuộc ngoài:
    Flask, core_python.data.loader, core_python.strategies.registry,
    core_python.chart.payload, data_provider/lightweight-charts.js (vendor lib).

Giả định giao dịch:
    Dashboard KHÔNG lọc bar đang mở — hiển thị cả bar chưa đóng.
    Watcher lọc bar đang mở (closed_only=True).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core_python import config
from core_python.chart.payload import build_chart_payload
from core_python.data.loader import load
from core_python.strategies.ai_trend.payload import build_ai_trend_payload
from core_python.strategies.ai_trend.signals import build_ai_trend_frames
from core_python.strategies.registry import STRATEGIES, get_strategy


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = ROOT / "data_provider"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8515


def create_app() -> Flask:
    """
    Tạo và cấu hình Flask app với toàn bộ route handlers.

    Returns:
        Flask app instance sẵn sàng chạy.
    """
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

    @app.route("/")
    def index():
        """Phục vụ trang web chính (index.html)."""
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/assets/lightweight-charts.js")
    def lightweight_charts():
        """Phục vụ thư viện charting vendor từ data_provider/."""
        return send_from_directory(VENDOR_DIR, "lightweight-charts.js")

    @app.route("/api/config")
    def api_config():
        """
        Trả về metadata cấu hình cho frontend khi khởi động.

        Response JSON gồm:
            strategies: dict chiến lược với label, defaults, fields (để render UI).
            defaultStrategy: Chiến lược mặc định ("combo").
            symbols: List symbol với name, asset_type, x (buffer Combo).
            timeframes: List mã TF theo thứ tự hiển thị.
            defaultSymbol, defaultTf, defaultBars: Giá trị mặc định cho dropdown.
        """
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

    @app.route("/api/export")
    def api_export():
        """
        Tạo và trả file CSV tín hiệu với các cột do người dùng chọn.

        Query params:
            strategy, symbol, tf, bars: Giống /api/scan.
            cols: Danh sách cột cần xuất, phân cách bằng dấu phẩy.
                  Cột đặc biệt: "bartime" (format chuỗi), "side" (BUY/SELL từ signal).

        Response: File CSV attachment.
        """
        import pandas as pd
        from flask import Response as _Response
        try:
            strategy_key = request.args.get("strategy", "combo")
            spec = get_strategy(strategy_key)
            symbol = request.args.get("symbol", config.DEFAULT_SYMBOL).upper()
            tf = request.args.get("tf", config.DEFAULT_TF).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)
            if spec.key == "ai_trend":
                payload, enriched = _run_ai_trend_dashboard(spec, request.args.to_dict(), symbol, bars)
                signals = enriched[enriched["signal"].fillna(0).astype(int).ne(0)].copy()

                requested = [c.strip() for c in request.args.get("cols", "").split(",") if c.strip()]
                out: dict = {}
                for col in requested:
                    if col == "bartime":
                        out["bartime"] = pd.to_datetime(signals["bartime"]).dt.strftime("%Y-%m-%d %H:%M")
                    elif col == "side":
                        out["side"] = signals["signal"].map({1: "BUY", -1: "SELL"})
                    elif col in signals.columns:
                        out[col] = signals[col].values

                csv_data = pd.DataFrame(out).to_csv(index=False)
                filename = f"{strategy_key}_{payload['meta']['symbol']}_{payload['meta']['entryTf']}_signals.csv"
                return _Response(
                    csv_data,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"},
                )
            params = spec.normalize_params(overrides, symbol)

            raw = load(symbol, tf, bars)
            enriched = spec.add_levels(
                spec.detect_signals(spec.add_indicators(raw, params), symbol=symbol, params=params),
                params,
                symbol,
            )
            # Chỉ xuất dòng có tín hiệu thực (signal != 0)
            signals = enriched[enriched["signal"].fillna(0).astype(int).ne(0)].copy()

            requested = [c.strip() for c in request.args.get("cols", "").split(",") if c.strip()]
            out: dict = {}
            for col in requested:
                if col == "bartime":
                    out["bartime"] = pd.to_datetime(signals["bartime"]).dt.strftime("%Y-%m-%d %H:%M")
                elif col == "side":
                    # Chuyển signal số (+1/-1) thành chuỗi "BUY"/"SELL"
                    out["side"] = signals["signal"].map({1: "BUY", -1: "SELL"})
                elif col in signals.columns:
                    out[col] = signals[col].values

            csv_data = pd.DataFrame(out).to_csv(index=False)
            filename = f"{strategy_key}_{symbol}_{tf}_signals.csv"
            return _Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/scan")
    def api_scan():
        """
        Chạy pipeline chiến lược và trả JSON payload cho Lightweight Charts.

        Query params:
            strategy: "combo" hoặc "ma_cross" (mặc định "combo").
            symbol: Mã TradingView (mặc định DEFAULT_SYMBOL).
            tf: Mã khung thời gian (mặc định DEFAULT_TF).
            bars: Số bar cần tải (mặc định N_BARS, tối đa 20000).
            [param overrides]: Các param của chiến lược (ví dụ: MA_PERIOD=30).

        Response JSON gồm: candles, overlays, panels, markers, levels, signals, stats, meta, params.

        Giả định giao dịch:
            Bao gồm cả bar đang mở (chưa đóng) — bar cuối cùng trong kết quả
            có thể chưa hình thành đầy đủ. Xem badge "includes live bar" trên UI.
        """
        try:
            strategy_key = request.args.get("strategy", "combo")
            spec = get_strategy(strategy_key)
            symbol = request.args.get("symbol", config.DEFAULT_SYMBOL).upper()
            tf = request.args.get("tf", config.DEFAULT_TF).upper()
            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            # Lấy tham số override từ query string, lọc chỉ các key hợp lệ của chiến lược
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)
            if spec.key == "ai_trend":
                payload, _enriched = _run_ai_trend_dashboard(spec, request.args.to_dict(), symbol, bars)
                return jsonify(payload)

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
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _run_ai_trend_dashboard(
    spec,
    args: dict[str, str],
    symbol: str,
    bars: int,
) -> tuple[dict[str, Any], Any]:
    """
    Run the AI Trend two-timeframe dashboard path.

    The common StrategySpec callables stay registered for compatibility, but the
    dashboard needs both H3 and M45 data at the same time.
    """
    overrides = _strategy_overrides(args, spec.param_fields)
    overrides.setdefault("SYMBOL", symbol or "GOLD")
    overrides.setdefault("ENTRY_BARS", bars)
    params = spec.normalize_params(overrides, symbol)

    trend_raw = load(params["SYMBOL"], params["TREND_TF"], int(params["TREND_BARS"]))
    entry_raw = load(params["SYMBOL"], params["ENTRY_TF"], int(params["ENTRY_BARS"]))
    if trend_raw.empty or entry_raw.empty:
        missing = []
        if trend_raw.empty:
            missing.append(params["TREND_TF"])
        if entry_raw.empty:
            missing.append(params["ENTRY_TF"])
        raise ValueError(
            f"AI Trend requires data for both {params['TREND_TF']} and {params['ENTRY_TF']}. "
            f"{params['SYMBOL']} has no data for: {', '.join(missing)}."
        )
    trend_frame, entry_frame = build_ai_trend_frames(trend_raw, entry_raw, params)
    enriched = spec.add_levels(entry_frame, params, params["SYMBOL"])
    payload = build_ai_trend_payload(
        trend_frame,
        enriched,
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=params["SYMBOL"],
        params=params,
    )
    return payload, enriched


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """
    Parse và giới hạn giá trị nguyên từ query string.

    Args:
        value: Giá trị thô từ request.args (có thể là None hoặc string).
        default: Giá trị mặc định nếu parse thất bại.
        min_value, max_value: Giới hạn cho phép.

    Returns:
        Số nguyên đã được clamp vào [min_value, max_value].
    """
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _strategy_overrides(args: dict[str, str], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Trích xuất các tham số override của chiến lược từ query string.

    Lọc chỉ lấy các key khớp với danh sách `fields` của chiến lược
    (không phân biệt hoa/thường). Các key không liên quan (symbol, tf...) bị bỏ qua.

    Args:
        args: Toàn bộ query params từ request.args.to_dict().
        fields: Danh sách field definition của chiến lược (từ StrategySpec.param_fields).

    Returns:
        Dict overrides với key đúng chuẩn (matching case với param name).
    """
    keys = {field["key"] for field in fields}
    lower_to_key = {key.lower(): key for key in keys}
    overrides: dict[str, Any] = {}
    for key, value in args.items():
        normalized = lower_to_key.get(key.lower())
        if normalized:
            overrides[normalized] = value
    return overrides


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, debug: bool = False) -> None:
    """
    Khởi động Flask development server.

    Args:
        host: Địa chỉ IP lắng nghe (mặc định "127.0.0.1" — chỉ local).
        port: Cổng lắng nghe (mặc định 8515).
        debug: Bật Flask debug mode và hot reload.

    Side Effects:
        Chạy HTTP server — block tiến trình cho đến khi dừng.
        use_reloader=False để tránh khởi động lại kép khi debug=True.
    """
    app = create_app()
    app.run(host=host, port=port, debug=debug, use_reloader=False)
