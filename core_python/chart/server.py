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

from dataclasses import dataclass
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core_python import config
from core_python.chart.payload import build_chart_payload
from core_python.data.loader import load, load_range
from core_python.strategies.combo.payload import build_combo_mtf_payload
from core_python.strategies.combo.pipeline import build_combo_mtf_frames
from core_python.strategies.combo.trend_filter import drop_open_bar as drop_combo_open_bar
from core_python.strategies.ai_trend.payload import build_ai_trend_payload
from core_python.strategies.ai_trend.signals import build_ai_trend_frames
from core_python.strategies.registry import STRATEGIES, get_strategy


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
VENDOR_DIR = ROOT / "data_provider"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8516

DEFAULT_RANGE_WARMUP_BARS = 600


@dataclass(frozen=True)
class DateWindow:
    """UTC-naive half-open range used by dashboard scan/export requests."""

    start: pd.Timestamp
    end: pd.Timestamp
    start_label: str
    end_label: str


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
            date_window = _date_window_from_args(request.args)
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)
            row_mode = _export_row_mode(request.args.get("rows"))
            if spec.key == "ai_trend":
                payload, enriched = _run_ai_trend_dashboard(
                    spec,
                    request.args.to_dict(),
                    symbol,
                    bars,
                    date_window=date_window,
                )
                rows = _select_export_rows(enriched, row_mode)
                requested = _parse_csv_list(request.args.get("cols"))
                csv_data = _single_export_csv(rows, requested)
                suffix = "bars" if row_mode == "all" else "signals"
                filename = f"{strategy_key}_{payload['meta']['symbol']}_{payload['meta']['entryTf']}_{suffix}.csv"
                return _Response(
                    csv_data,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"},
                )
            params = spec.normalize_params(overrides, symbol)

            if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
                _payload, enriched = _run_combo_mtf_dashboard(
                    spec,
                    symbol=symbol,
                    tf=tf,
                    bars=bars,
                    params=params,
                    date_window=date_window,
                )
            else:
                raw = _load_request_frame(
                    symbol,
                    tf,
                    bars,
                    date_window,
                    spec.key,
                    params,
                    role="entry",
                    tail_bars=1,
                )
                enriched_full = spec.add_levels(
                    spec.detect_signals(spec.add_indicators(raw, params), symbol=symbol, params=params),
                    params,
                    symbol,
                )
                enriched = _trim_to_window(enriched_full, date_window)
            # Default keeps signal-only export; rows=all returns the full entry-timeframe frame.
            rows = _select_export_rows(enriched, row_mode)

            requested = _parse_csv_list(request.args.get("cols"))
            out: dict = {}
            for col in requested:
                if col == "bartime":
                    out["bartime"] = pd.to_datetime(rows["bartime"]).dt.strftime("%Y-%m-%d %H:%M")
                elif col == "side":
                    # Chuyển signal số (+1/-1) thành chuỗi "BUY"/"SELL"
                    out["side"] = rows["signal"].map({1: "BUY", -1: "SELL"}).fillna("")
                elif col in rows.columns:
                    out[col] = rows[col].values

            csv_data = pd.DataFrame(out).to_csv(index=False)
            suffix = "bars" if row_mode == "all" else "signals"
            filename = f"{strategy_key}_{symbol}_{tf}_{suffix}.csv"
            return _Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/export/bulk")
    def api_export_bulk():
        """
        Export signal-only CSV for one strategy/timeframe across multiple symbols.

        Required output columns are always: bartime, symbol, signal.
        Optional cols query values are copied from the enriched strategy frame when present.
        """
        import pandas as pd

        try:
            strategy_key = request.args.get("strategy", "combo")
            spec = get_strategy(strategy_key)
            tf = request.args.get("tf", config.DEFAULT_TF).upper()
            if tf not in config.TF_MINUTES:
                raise ValueError(f"Unsupported timeframe '{tf}'.")

            bars = _to_int(request.args.get("bars"), config.N_BARS, 50, 20000)
            date_window = _date_window_from_args(request.args)
            symbols = _parse_symbol_list(request.args.get("symbols") or request.args.get("symbol"))
            if not symbols:
                raise ValueError("Bulk export requires at least one symbol.")
            for symbol in symbols:
                config.get_symbol(symbol)

            requested_cols = _parse_csv_list(request.args.get("cols"))
            requested_cols = [col for col in requested_cols if col not in {"bartime", "symbol", "signal"}]
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)
            row_mode = _export_row_mode(request.args.get("rows"))

            frames: list[pd.DataFrame] = []
            for symbol in symbols:
                if spec.key == "ai_trend":
                    args = dict(request.args)
                    args["SYMBOL"] = symbol
                    args["ENTRY_TF"] = tf
                    args["ENTRY_BARS"] = str(bars)
                    _payload, enriched = _run_ai_trend_dashboard(
                        spec,
                        args,
                        symbol,
                        bars,
                        date_window=date_window,
                    )
                else:
                    params = spec.normalize_params(overrides, symbol)
                    if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
                        _payload, enriched = _run_combo_mtf_dashboard(
                            spec,
                            symbol=symbol,
                            tf=tf,
                            bars=bars,
                            params=params,
                            date_window=date_window,
                        )
                    else:
                        raw = _load_request_frame(
                            symbol,
                            tf,
                            bars,
                            date_window,
                            spec.key,
                            params,
                            role="entry",
                            tail_bars=1,
                        )
                        enriched_full = spec.add_levels(
                            spec.detect_signals(spec.add_indicators(raw, params), symbol=symbol, params=params),
                            params,
                            symbol,
                        )
                        enriched = _trim_to_window(enriched_full, date_window)

                rows = _select_export_rows(enriched, row_mode)
                if rows.empty:
                    continue

                out = pd.DataFrame(
                    {
                        "bartime": pd.to_datetime(rows["bartime"], errors="coerce").dt.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "symbol": symbol,
                        "signal": pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int),
                    }
                )
                for col in requested_cols:
                    if col in rows.columns:
                        out[col] = rows[col].values
                frames.append(out)

            columns = ["bartime", "symbol", "signal", *requested_cols]
            if frames:
                result = pd.concat(frames, ignore_index=True)
                result = result.reindex(columns=[col for col in columns if col in result.columns])
                result = result.sort_values(["bartime", "symbol"], kind="stable")
            else:
                result = pd.DataFrame(columns=columns)

            csv_data = result.to_csv(index=False)
            suffix = "bars" if row_mode == "all" else "signals"
            filename = f"{spec.key}_{tf}_bulk_{suffix}.csv"
            return Response(
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
            date_window = _date_window_from_args(request.args)
            # Lấy tham số override từ query string, lọc chỉ các key hợp lệ của chiến lược
            overrides = _strategy_overrides(request.args.to_dict(), spec.param_fields)
            if spec.key == "ai_trend":
                payload, _enriched = _run_ai_trend_dashboard(
                    spec,
                    request.args.to_dict(),
                    symbol,
                    bars,
                    date_window=date_window,
                )
                return jsonify(payload)

            params = spec.normalize_params(overrides, symbol)
            if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
                payload, _enriched = _run_combo_mtf_dashboard(
                    spec,
                    symbol=symbol,
                    tf=tf,
                    bars=bars,
                    params=params,
                    date_window=date_window,
                )
                return jsonify(payload)

            raw = _load_request_frame(
                symbol,
                tf,
                bars,
                date_window,
                spec.key,
                params,
                role="entry",
                tail_bars=1,
            )
            with_indicators = spec.add_indicators(raw, params)
            with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
            enriched = _trim_to_window(spec.add_levels(with_signals, params, symbol), date_window)

            payload = build_chart_payload(
                enriched,
                strategy=spec.key,
                strategy_label=spec.label,
                symbol=symbol,
                tf=tf,
                bars=len(enriched) if date_window else bars,
                params=params,
            )
            _attach_window_meta(payload, date_window)
            return jsonify(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _date_window_from_args(args: Any) -> DateWindow | None:
    start_raw = args.get("start_date") or args.get("start")
    end_raw = args.get("end_date") or args.get("end")
    if not start_raw and not end_raw:
        return None
    if not start_raw or not end_raw:
        raise ValueError("Both start_date and end_date are required for date-range mode.")

    start = _parse_range_bound(start_raw, "start_date", end_bound=False)
    end = _parse_range_bound(end_raw, "end_date", end_bound=True)
    if end <= start:
        raise ValueError("end_date must be the same date as or later than start_date.")
    return DateWindow(
        start=start,
        end=end,
        start_label=pd.Timestamp(start).strftime("%d/%m/%Y"),
        end_label=(pd.Timestamp(end) - pd.Timedelta(microseconds=1)).strftime("%d/%m/%Y"),
    )


def _parse_range_bound(value: object, label: str, *, end_bound: bool) -> pd.Timestamp:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    ts: pd.Timestamp
    date_only = False
    parts = text.split("/")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        day, month, year = (int(part) for part in parts)
        if year < 1000:
            raise ValueError(f"Invalid {label}: {text}")
        try:
            ts = pd.Timestamp(year=year, month=month, day=day)
        except ValueError as exc:
            raise ValueError(f"Invalid {label}: {text}") from exc
        date_only = True
    else:
        date_only = len(text) == 10 and text[4] == "-" and text[7] == "-"
        try:
            ts = pd.Timestamp(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {label}: {text}") from exc
    if pd.isna(ts):
        raise ValueError(f"Invalid {label}: {text}")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    if end_bound and date_only:
        ts = ts + pd.Timedelta(days=1)
    return ts


def _load_request_frame(
    symbol: str,
    tf: str,
    bars: int,
    date_window: DateWindow | None,
    strategy_key: str,
    params: dict[str, Any],
    *,
    role: str,
    tail_bars: int = 0,
):
    if date_window is None:
        return load(symbol, tf, bars)
    return load_range(
        symbol,
        tf,
        date_window.start,
        date_window.end,
        warmup_bars=_range_warmup_bars(strategy_key, params, role),
        tail_bars=tail_bars,
    )


def _trim_to_window(df, date_window: DateWindow | None, column: str = "bartime"):
    if date_window is None or df.empty or column not in df.columns:
        return df.copy()
    times = pd.to_datetime(df[column], errors="coerce")
    mask = times.ge(date_window.start) & times.lt(date_window.end)
    return df.loc[mask].reset_index(drop=True)


def _attach_window_meta(payload: dict[str, Any], date_window: DateWindow | None) -> None:
    meta = payload.setdefault("meta", {})
    if date_window is None:
        meta["rangeMode"] = "bars"
        return
    meta["rangeMode"] = "date"
    meta["startDate"] = date_window.start_label
    meta["endDate"] = date_window.end_label
    meta["startTime"] = date_window.start.strftime("%Y-%m-%d %H:%M:%S")
    meta["endTimeExclusive"] = date_window.end.strftime("%Y-%m-%d %H:%M:%S")


def _range_warmup_bars(strategy_key: str, params: dict[str, Any], role: str) -> int:
    if strategy_key == "ai_trend":
        if role == "trend":
            return max(
                DEFAULT_RANGE_WARMUP_BARS,
                _param_int(params, "AI_SMOOTH") + _param_int(params, "AI_TARGET_LEN") + 50,
            )
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            _param_int(params, "EMA_SLOW")
            + _param_int(params, "MACD_SLOW")
            + _param_int(params, "MACD_SIGNAL")
            + _param_int(params, "DOW_PIVOT_LEFT")
            + _param_int(params, "DOW_PIVOT_RIGHT")
            + 50,
        )
    if strategy_key == "combo":
        if role == "trend":
            return max(
                DEFAULT_RANGE_WARMUP_BARS,
                _param_int(params, "HTF_SLOW_EMA") + _param_int(params, "HTF_SLOPE_LOOKBACK") + 50,
            )
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            _param_int(params, "MA_PERIOD")
            + _param_int(params, "MACD_SLOW")
            + _param_int(params, "MACD_SIGNAL")
            + _param_int(params, "ATR_PERIOD")
            + 50,
        )
    if strategy_key == "ma_cross":
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            _param_int(params, "SLOW_MA")
            + _param_int(params, "ATR_PERIOD")
            + _param_int(params, "MACD_SIGNAL")
            + 50,
        )
    return DEFAULT_RANGE_WARMUP_BARS


def _param_int(params: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(params.get(key, default)))
    except (TypeError, ValueError):
        return default


def _run_combo_mtf_dashboard(
    spec,
    *,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
    date_window: DateWindow | None = None,
) -> tuple[dict[str, Any], Any]:
    """Run Combo with a closed HTF trend frame and entry timeframe frame."""
    entry_raw = _load_request_frame(
        symbol,
        tf,
        bars,
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
    trend_raw = _load_request_frame(
        symbol,
        params["HTF_TF"],
        int(params["HTF_BARS"]),
        date_window,
        spec.key,
        params,
        role="trend",
    )
    trend_raw = drop_combo_open_bar(trend_raw, params["HTF_TF"])
    if trend_raw.empty:
        raise ValueError(f"{symbol} has no closed {params['HTF_TF']} data for Combo HTF trend.")
    if entry_raw.empty:
        raise ValueError(f"{symbol} has no {tf} data for Combo entry.")

    trend_full, enriched_full = build_combo_mtf_frames(trend_raw, entry_raw, params, symbol)
    trend_frame = _trim_to_window(trend_full, date_window)
    enriched = _trim_to_window(enriched_full, date_window)
    if date_window and enriched.empty:
        raise ValueError(f"{symbol} has no {tf} data in the selected date range.")
    payload = build_combo_mtf_payload(
        trend_frame,
        enriched,
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=symbol,
        entry_tf=tf,
        entry_bars=len(enriched) if date_window else bars,
        params=params,
    )
    _attach_window_meta(payload, date_window)
    return payload, enriched


def _run_ai_trend_dashboard(
    spec,
    args: dict[str, str],
    symbol: str,
    bars: int,
    *,
    date_window: DateWindow | None = None,
) -> tuple[dict[str, Any], Any]:
    """
    Run the AI Trend two-timeframe dashboard path.

    The common StrategySpec callables stay registered for compatibility, but the
    dashboard needs trend-timeframe and entry-timeframe data at the same time.
    """
    overrides = _strategy_overrides(args, spec.param_fields)
    overrides.setdefault("SYMBOL", symbol or "GOLD")
    if _to_bool(args.get("AUTO_ENTRY_BARS"), False):
        trend_bars = overrides.get("TREND_BARS", spec.default_params.get("TREND_BARS", bars))
        trend_tf = overrides.get("TREND_TF", spec.default_params.get("TREND_TF", "H3"))
        entry_tf = overrides.get("ENTRY_TF", spec.default_params.get("ENTRY_TF", "M45"))
        overrides["ENTRY_BARS"] = _corresponding_bars(trend_bars, trend_tf, entry_tf)
    else:
        overrides.setdefault("ENTRY_BARS", bars)
    params = spec.normalize_params(overrides, symbol)

    trend_raw = _load_request_frame(
        params["SYMBOL"],
        params["TREND_TF"],
        int(params["TREND_BARS"]),
        date_window,
        spec.key,
        params,
        role="trend",
    )
    entry_raw = _load_request_frame(
        params["SYMBOL"],
        params["ENTRY_TF"],
        int(params["ENTRY_BARS"]),
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
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
    trend_full, entry_full = build_ai_trend_frames(trend_raw, entry_raw, params)
    enriched_full = spec.add_levels(entry_full, params, params["SYMBOL"])
    trend_frame = _trim_to_window(trend_full, date_window)
    enriched = _trim_to_window(enriched_full, date_window)
    if date_window and enriched.empty:
        raise ValueError(f"{params['SYMBOL']} has no {params['ENTRY_TF']} data in the selected date range.")
    payload = build_ai_trend_payload(
        trend_frame,
        enriched,
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=params["SYMBOL"],
        params=params,
    )
    if date_window:
        payload["meta"]["bars"] = len(enriched)
        payload["meta"]["trendBars"] = len(trend_frame)
        payload["meta"]["entryBars"] = len(enriched)
    _attach_window_meta(payload, date_window)
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


def _parse_csv_list(value: object) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _corresponding_bars(
    trend_bars: object,
    trend_tf: object,
    entry_tf: object,
    *,
    min_value: int = 50,
    max_value: int = 20000,
) -> int:
    trend_code = str(trend_tf or "").strip().upper()
    entry_code = str(entry_tf or "").strip().upper()
    if trend_code not in config.TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{trend_code}'.")
    if entry_code not in config.TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{entry_code}'.")
    base_bars = _to_int(trend_bars, min_value, min_value, max_value)
    trend_minutes = int(config.TF_MINUTES[trend_code])
    entry_minutes = int(config.TF_MINUTES[entry_code])
    raw_bars = (base_bars * trend_minutes + entry_minutes - 1) // entry_minutes
    return max(min_value, min(int(raw_bars), max_value))


def _export_row_mode(value: object) -> str:
    mode = str(value or "signals").strip().lower()
    return "all" if mode in {"all", "bars", "full"} else "signals"


def _select_export_rows(df, row_mode: str):
    if row_mode == "all":
        return df.copy()
    return df[df["signal"].fillna(0).astype(int).ne(0)].copy()


def _single_export_csv(rows, requested: list[str]) -> str:
    import pandas as pd

    out: dict = {}
    for col in requested:
        if col == "bartime":
            out["bartime"] = pd.to_datetime(rows["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        elif col == "side":
            signal = pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int)
            out["side"] = signal.map({1: "BUY", -1: "SELL"}).fillna("")
        elif col == "signal":
            out["signal"] = pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int)
        elif col in rows.columns:
            out[col] = rows[col].values
    return pd.DataFrame(out).to_csv(index=False)


def _parse_symbol_list(value: object) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for item in _parse_csv_list(value):
        symbol = item.upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


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
        port: Cổng lắng nghe (mặc định 8516).
        debug: Bật Flask debug mode và hot reload.

    Side Effects:
        Chạy HTTP server — block tiến trình cho đến khi dừng.
        use_reloader=False để tránh khởi động lại kép khi debug=True.
    """
    app = create_app()
    app.run(host=host, port=port, debug=debug, use_reloader=False)
