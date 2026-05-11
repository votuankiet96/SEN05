"""
Vòng lặp giám sát 24/7: tải bar từ DB, phát hiện tín hiệu, gửi Telegram.

Mô tả:
    Vòng lặp chính (main()) chạy liên tục, kiểm tra từng nhóm scan trong
    scan_config.py và gửi Telegram ngay khi có tín hiệu mới trên bar đã đóng.

    Lịch scan căn chỉnh theo bar-close: sau mỗi lần kiểm tra, lần tiếp theo
    được lên lịch vào thời điểm bar close kế tiếp + buffer cấu hình (mặc định 5s).
    Lần đầu tiên khi khởi động: chạy ngay lập tức.

Chế độ chạy:
    python -m core_python.notify.signal_watcher              # 24/7, đọc scan_config.py
    python -m core_python.notify.signal_watcher --once       # chạy một lần rồi thoát
    python -m core_python.notify.signal_watcher --dry-run    # in ra màn hình, không gửi
    python -m core_python.notify.signal_watcher --warm-up    # seed state, không gửi

Chống gửi trùng:
    Mỗi tín hiệu có key duy nhất (strategy|symbol|tf|bartime|direction).
    Key đã gửi lưu vào state.json (TTL 60 ngày).
    Key tồn tại trong state → bỏ qua, không gửi lại.

Xử lý lỗi:
    Mỗi nhóm scan bọc trong try/except riêng — lỗi một nhóm không ảnh hưởng nhóm khác.

Phụ thuộc:
    data.loader, strategies.registry, notify.state, notify.formatter, notify.notifier.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core_python import config
from core_python.data.loader import load
from core_python.export.to_csv import export_signals
from core_python.notify.formatter import format_combo_raw_signal_message, format_signal_message
from core_python.notify.notifier import Notifier
from core_python.notify.state import SignalState, signal_key
from core_python.strategies.ai_trend.alerts import (
    H3_TREND_CHANGE,
    M45_ENTRY_SIGNAL,
    ai_trend_alert_key,
    extract_h3_trend_alerts,
    extract_m45_entry_alerts,
    format_ai_trend_alert,
)
from core_python.strategies.ai_trend.signals import (
    build_ai_trend_frames,
    prepare_trend_frame,
)
from core_python.strategies.combo.pipeline import build_combo_mtf_frames
from core_python.strategies.registry import get_strategy


_DEFAULT_BAR_CLOSE_BUFFER_SECONDS = 5
_DEFAULT_POST_CLOSE_RETRY_SECONDS = 5
_DEFAULT_POST_CLOSE_WATCH_SECONDS = 10
_IDLE_SLEEP_SECONDS = 1.0
_STALE_WARNING_LAG_MINUTES = 30
_STALE_WARNING_THROTTLE_MINUTES = 30
_HISTORICAL_ALERT_AGE_MULTIPLIER = 3
_HISTORICAL_ALERT_MIN_AGE_MINUTES = 120

logger = logging.getLogger(__name__)
_DB_LOAD_COUNT = 0
_LAST_STALE_LOGGED: dict[tuple[str, str], pd.Timestamp] = {}


@dataclass(frozen=True)
class SentSignalEvent:
    """A production Telegram signal event used for hourly summaries."""

    strategy: str
    symbol: str
    tf: str
    side: str
    event_time: pd.Timestamp
    sent_at: pd.Timestamp
    kind: str = "signal"


def scan_fingerprint(scan_groups: list[dict], closed_only: bool) -> str:
    """Return a stable hash of the scan universe covered by warm-up."""
    universe = []
    for group in scan_groups:
        overrides = _canonical_json_value(group.get("overrides") or {})
        symbols = sorted(str(symbol).strip().upper() for symbol in group.get("symbols", []) if str(symbol).strip())
        item = {
            "strategy": str(group.get("strategy", "")).strip().lower(),
            "event_type": str(group.get("event_type") or "").strip().lower(),
            "tf": str(group.get("tf", "")).strip().upper(),
            "bars": int(group.get("bars", config.N_BARS)),
            "symbols": symbols,
            "overrides": overrides,
            "closed_only": bool(closed_only),
        }
        universe.append(item)
    universe.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    payload = json.dumps(universe, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical_json_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def _scan_fingerprint_error(state: SignalState, expected_fingerprint: str) -> str | None:
    stored = state.get_scan_fingerprint()
    if stored == expected_fingerprint:
        return None
    if not stored:
        return (
            "[ERROR] Watcher state file has no scan fingerprint. "
            "Run warm-up before starting production:\n"
            "  python -m core_python.notify.signal_watcher --warm-up\n"
            f"  (expected state path: {state.path})"
        )
    return (
        "[ERROR] Watcher scan config changed since warm-up. "
        "Run warm-up again before starting production:\n"
        "  python -m core_python.notify.signal_watcher --warm-up\n"
        f"  stored fingerprint:  {stored}\n"
        f"  current fingerprint: {expected_fingerprint}"
    )


def _load_ohlcv(symbol: str, tf: str, bars: int) -> pd.DataFrame:
    global _DB_LOAD_COUNT
    _DB_LOAD_COUNT += 1
    return load(symbol, tf, bars)


def _warn_if_stale_bar(
    symbol: str,
    tf: str,
    latest_bartime: object,
    *,
    now_utc: pd.Timestamp | None = None,
    allowed_lag_minutes: int = _STALE_WARNING_LAG_MINUTES,
    throttle_minutes: int = _STALE_WARNING_THROTTLE_MINUTES,
) -> bool:
    """Log a throttled warning when the latest closed bar is too old."""
    tf_code = str(tf).strip().upper()
    minutes = config.TF_MINUTES.get(tf_code)
    if latest_bartime is None or not minutes:
        return False
    latest = pd.Timestamp(latest_bartime)
    if pd.isna(latest):
        return False
    if latest.tzinfo is None:
        latest = latest.tz_localize("UTC")
    else:
        latest = latest.tz_convert("UTC")
    now = pd.Timestamp.now("UTC") if now_utc is None else pd.Timestamp(now_utc)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")

    latest_close = latest + pd.Timedelta(minutes=int(minutes))
    age = now - latest_close
    key = (str(symbol).strip().upper(), tf_code)
    if age <= pd.Timedelta(minutes=int(allowed_lag_minutes)):
        _LAST_STALE_LOGGED.pop(key, None)
        return False

    last_logged = _LAST_STALE_LOGGED.get(key)
    if last_logged is None or now - last_logged >= pd.Timedelta(minutes=int(throttle_minutes)):
        logger.warning(
            "stale data: %s %s latest_close=%s age=%s threshold=%sm",
            key[0],
            key[1],
            latest_close.strftime("%Y-%m-%d %H:%M:%S UTC"),
            _format_timedelta(age),
            int(allowed_lag_minutes),
        )
        _LAST_STALE_LOGGED[key] = now
    return True


def _format_timedelta(delta: pd.Timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _alert_age_limit(tf: str, max_alert_age_minutes: int | None = None) -> pd.Timedelta:
    if max_alert_age_minutes is not None:
        return pd.Timedelta(minutes=max(1, int(max_alert_age_minutes)))
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return pd.Timedelta(minutes=_HISTORICAL_ALERT_MIN_AGE_MINUTES)
    return pd.Timedelta(
        minutes=max(
            _HISTORICAL_ALERT_MIN_AGE_MINUTES,
            _HISTORICAL_ALERT_AGE_MULTIPLIER * int(minutes),
        )
    )


def _event_close_from_bar_open(bartime: object, tf: str) -> pd.Timestamp:
    ts = _as_utc_ts(bartime)
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return ts
    return ts + pd.Timedelta(minutes=int(minutes))


def _is_historical_alert(
    event_time: object,
    tf: str,
    *,
    now_utc: pd.Timestamp | None = None,
    max_alert_age_minutes: int | None = None,
) -> tuple[bool, pd.Timedelta, pd.Timedelta]:
    now = pd.Timestamp.now("UTC") if now_utc is None else _as_utc_ts(now_utc)
    event_ts = _as_utc_ts(event_time)
    age = now - event_ts
    limit = _alert_age_limit(tf, max_alert_age_minutes=max_alert_age_minutes)
    return age > limit, age, limit


def _mark_historical_seen(state: SignalState, key: str, *, dry_run: bool) -> str:
    if dry_run:
        return "dry-run; not marked seen"
    state.add(key)
    return "marked seen"


def run_strategy_frame(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
) -> tuple[pd.DataFrame, Any, dict[str, Any]]:
    """
    Tải bar từ DB và chạy toàn bộ pipeline chiến lược, trả về DataFrame đã enriched.

    Pipeline: load → (lọc bar mở) → add_indicators → detect_signals → add_levels.

    Args:
        strategy: Key chiến lược ("combo", "ma_cross").
        symbol: Mã symbol (ví dụ: "US30").
        tf: Mã khung thời gian (ví dụ: "H1").
        bars: Số bar tải về từ DB.
        overrides: Dict tham số override (None = dùng defaults).
        closed_only: Nếu True, lọc bỏ bar đang mở (chưa đóng) trước khi tính tín hiệu.

    Returns:
        Tuple (enriched_df, strategy_spec, validated_params).

    Side Effects:
        Mở và đóng kết nối DB (qua data.loader.load()).

    Giả định giao dịch:
        closed_only=True (mặc định) đảm bảo không phát tín hiệu sớm trên bar chưa hoàn thành.
    """
    spec = get_strategy(strategy)
    params = spec.normalize_params(overrides or {}, symbol)
    raw = _load_ohlcv(symbol, tf, bars)
    if closed_only:
        raw = _drop_open_bar(raw, tf)
    if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
        trend_raw = _load_ohlcv(symbol, params["HTF_TF"], int(params["HTF_BARS"]))
        if closed_only:
            trend_raw = _drop_open_bar(trend_raw, params["HTF_TF"])
        if trend_raw.empty:
            raise ValueError(f"{symbol} has no closed {params['HTF_TF']} data for Combo HTF trend")
        trend_frame, enriched = build_combo_mtf_frames(trend_raw, raw, params, symbol)
        _ = trend_frame
        return enriched, spec, params
    with_indicators = spec.add_indicators(raw, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    enriched = spec.add_levels(with_signals, params, symbol)
    return enriched, spec, params


def all_new_signal_rows(
    df: pd.DataFrame,
    state: SignalState,
    strategy: str,
    symbol: str,
    tf: str,
) -> list[pd.Series]:
    """
    Trả về tất cả dòng tín hiệu chưa được gửi, theo thứ tự thời gian.

    Lọc từ DataFrame các dòng có signal != 0, sau đó kiểm tra với state
    để loại các dòng có key đã có trong state.json (đã gửi trước đó).

    Args:
        df: DataFrame đã enriched đầy đủ.
        state: SignalState chứa các key đã gửi.
        strategy: Key chiến lược (dùng để tạo signal_key).
        symbol: Mã symbol.
        tf: Mã khung thời gian.

    Returns:
        List pd.Series (các dòng signal mới), có thể rỗng nếu không có tín hiệu mới.
        Thứ tự: chronological (giữ nguyên thứ tự trong df).
    """
    if df.empty or "signal" not in df:
        return []
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]
    result = []
    for _, row in signals.iterrows():
        key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
        if not state.has(key):
            result.append(row)
    return result


def check_once(
    *,
    strategy: str,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    output_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    export_on_signal: bool = True,
    chat_id: str | None = None,
    show_progress: bool = True,
    latest_bars: list[pd.Timestamp] | None = None,
    sent_signals: list[SentSignalEvent] | None = None,
    max_alert_age_minutes: int | None = None,
) -> list[str]:
    """
    Kiểm tra tất cả symbol trong một nhóm, gửi tín hiệu mới, trả về log events.

    Với mỗi symbol:
        1. Chạy run_strategy_frame() để lấy DataFrame enriched.
        2. Lọc tín hiệu mới (all_new_signal_rows — chưa có key trong state).
        3. Với mỗi tín hiệu mới: format → gửi → ghi key vào state.
        4. Tùy chọn: export CSV.

    Args:
        strategy, symbols, tf, bars, overrides: Thông số scan.
        state: SignalState để dedup.
        notifier: Gửi tin nhắn.
        output_dir: Thư mục xuất CSV.
        closed_only: Lọc bar đang mở.
        export_on_signal: Xuất CSV khi có tín hiệu mới.
        chat_id: Telegram chat ID riêng (None = env default).
        show_progress: In trạng thái đang scan ra console.
        latest_bars: Dùng để dedup bar cuối (tùy chọn, có thể None).

    Returns:
        List chuỗi log events — một dòng per symbol.

    Side Effects:
        Gửi Telegram/Discord, cập nhật state.json, ghi CSV.

    Giả định giao dịch:
        Chỉ ghi key vào state nếu gửi thành công (sent=True, không dry-run).
        Lỗi một symbol không dừng các symbol tiếp theo.
    """
    events: list[str] = []
    for symbol in symbols:
        if show_progress:
            _print_status(f"scanning {strategy} {symbol} {tf} ({bars} bars)")
        try:
            frame, spec, _params = run_strategy_frame(
                strategy=strategy,
                symbol=symbol,
                tf=tf,
                bars=bars,
                overrides=overrides,
                closed_only=closed_only,
            )
        except Exception as exc:
            events.append(f"{symbol} {tf}: load error - {exc}")
            continue

        latest_bar_ts = _latest_bar_ts(frame)
        if latest_bar_ts is not None and latest_bars is not None:
            latest_bars.append(latest_bar_ts)
        if latest_bar_ts is not None:
            _warn_if_stale_bar(symbol, tf, latest_bar_ts)
        latest_bar = _format_bar_ts(latest_bar_ts)
        new_rows = all_new_signal_rows(frame, state, strategy, symbol, tf)
        if not new_rows:
            events.append(f"{symbol} {tf}: no new signal (latest bar {latest_bar})")
            continue

        for row in new_rows:
            key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
            event_close = _event_close_from_bar_open(row["bartime"], tf)
            is_historical, age, age_limit = _is_historical_alert(
                event_close,
                tf,
                max_alert_age_minutes=max_alert_age_minutes,
            )
            side = "BUY" if int(row["signal"]) == 1 else "SELL"
            signal_time = event_close.strftime("%Y-%m-%d %H:%M")
            if is_historical:
                mark_status = _mark_historical_seen(state, key, dry_run=notifier.dry_run)
                events.append(
                    f"{symbol} {tf}: skipped historical {side} {signal_time} UTC "
                    f"age={_format_timedelta(age)} limit={_format_timedelta(age_limit)} ({mark_status})"
                )
                continue

            if export_on_signal:
                try:
                    export_signals(frame, symbol=symbol, strategy=strategy, output_dir=output_dir)
                except Exception as exc:
                    logger.warning(
                        "CSV export failed for %s %s %s: %s",
                        strategy,
                        symbol,
                        tf,
                        exc,
                        exc_info=True,
                    )

            if strategy.lower() == "combo":
                message = format_combo_raw_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf)
                result = notifier.send(
                    message,
                    backend="discord",
                    discord_webhook=getattr(notifier, "combo_discord_webhook", None),
                )
            else:
                message = format_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf)
                result = notifier.send(
                    message,
                    backend="discord",
                    discord_webhook=getattr(notifier, "discord_webhook", None),
                )

            if result.sent and not notifier.dry_run:
                state.add(key)
                if sent_signals is not None:
                    sent_signals.append(
                        SentSignalEvent(
                            strategy=strategy,
                            symbol=symbol.upper(),
                            tf=tf.upper(),
                            side=side,
                            event_time=event_close,
                            sent_at=pd.Timestamp.now("UTC"),
                        )
                    )
                events.append(f"{symbol} {tf}: alerted via {result.backend} {side} {signal_time} UTC")
            elif result.sent:
                events.append(f"{symbol} {tf}: dry-run OK {side} {signal_time} UTC")
            else:
                events.append(f"{symbol} {tf}: FAILED - {result.detail}")

    return events


def check_ai_trend_once(
    *,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    event_type: str | None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    chat_id: str | None = None,
    show_progress: bool = True,
    latest_bars: list[pd.Timestamp] | None = None,
    sent_signals: list[SentSignalEvent] | None = None,
    max_alert_age_minutes: int | None = None,
) -> list[str]:
    """Check AI Trend H3 trend-change or M45 entry alerts for one scan group."""
    normalized_event_type = _normalize_ai_trend_event_type(event_type, tf)
    events: list[str] = []
    for symbol in symbols:
        if show_progress:
            _print_status(f"scanning ai_trend {normalized_event_type} {symbol} {tf} ({bars} bars)")
        try:
            alerts, latest_bar_ts = run_ai_trend_alerts(
                symbol=symbol,
                tf=tf,
                bars=bars,
                event_type=normalized_event_type,
                overrides=overrides,
                closed_only=closed_only,
            )
        except Exception as exc:
            events.append(f"{symbol} {tf}: AI Trend load error - {exc}")
            continue

        if latest_bar_ts is not None and latest_bars is not None:
            latest_bars.append(latest_bar_ts)
        latest_bar = _format_bar_ts(latest_bar_ts)
        new_alerts = [alert for alert in alerts if not state.has(ai_trend_alert_key(alert))]
        if not new_alerts:
            events.append(f"{symbol} {tf}: no new AI Trend alert (latest bar {latest_bar})")
            continue

        for alert in new_alerts:
            key = ai_trend_alert_key(alert)
            direction = _ai_trend_alert_side(alert.direction, alert.kind)
            event_time = _as_utc_ts(alert.event_time)
            signal_time = event_time.strftime("%Y-%m-%d %H:%M")
            if not _ai_trend_m45_matches_h3(alert):
                mark_status = _mark_historical_seen(state, key, dry_run=notifier.dry_run)
                h3_bias = getattr(alert, "h3_bias", None)
                events.append(
                    f"{symbol} {tf}: skipped AI Trend M45 {direction} {signal_time} UTC "
                    f"because H3 bias is {h3_bias} ({mark_status})"
                )
                continue

            is_historical, age, age_limit = _is_historical_alert(
                event_time,
                tf,
                max_alert_age_minutes=max_alert_age_minutes,
            )
            if is_historical:
                mark_status = _mark_historical_seen(state, key, dry_run=notifier.dry_run)
                events.append(
                    f"{symbol} {tf}: skipped historical AI Trend {normalized_event_type} "
                    f"{direction} {signal_time} UTC age={_format_timedelta(age)} "
                    f"limit={_format_timedelta(age_limit)} ({mark_status})"
                )
                continue

            message = format_ai_trend_alert(alert)
            result = notifier.send(message, chat_id=chat_id, backend="telegram")

            if result.sent and not notifier.dry_run:
                state.add(key)
                if sent_signals is not None:
                    sent_signals.append(
                        SentSignalEvent(
                            strategy="ai_trend",
                            symbol=symbol.upper(),
                            tf=tf.upper(),
                            side=direction,
                            event_time=event_time,
                            sent_at=pd.Timestamp.now("UTC"),
                            kind=normalized_event_type,
                        )
                    )
                events.append(
                    f"{symbol} {tf}: alerted via {result.backend} "
                    f"{normalized_event_type} {direction} {signal_time} UTC"
                )
            elif result.sent:
                events.append(
                    f"{symbol} {tf}: dry-run OK {normalized_event_type} {direction} {signal_time} UTC"
                )
            else:
                events.append(f"{symbol} {tf}: FAILED - {result.detail}")

    return events


def run_ai_trend_alerts(
    *,
    symbol: str,
    tf: str,
    bars: int,
    event_type: str | None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
) -> tuple[list[Any], pd.Timestamp | None]:
    """Build closed AI Trend frames and extract alerts for the requested event type."""
    spec = get_strategy("ai_trend")
    local_overrides = dict(overrides or {})
    tf_code = str(tf).strip().upper()
    normalized_event_type = _normalize_ai_trend_event_type(event_type, tf_code)
    if normalized_event_type == H3_TREND_CHANGE:
        local_overrides.setdefault("TREND_BARS", bars)
    elif normalized_event_type == M45_ENTRY_SIGNAL:
        local_overrides.setdefault("ENTRY_BARS", bars)

    params = spec.normalize_params(local_overrides, symbol)
    selected_symbol = params["SYMBOL"]

    trend_raw = _load_ohlcv(selected_symbol, params["TREND_TF"], int(params["TREND_BARS"]))
    if closed_only:
        trend_raw = _drop_open_bar(trend_raw, params["TREND_TF"])
    trend_latest_ts = _latest_bar_ts(trend_raw)
    if trend_latest_ts is not None:
        _warn_if_stale_bar(selected_symbol, params["TREND_TF"], trend_latest_ts)
    if trend_raw.empty:
        raise ValueError(f"{selected_symbol} has no closed {params['TREND_TF']} data")

    if normalized_event_type == H3_TREND_CHANGE:
        trend_frame = prepare_trend_frame(trend_raw, params)
        return extract_h3_trend_alerts(trend_frame, selected_symbol), trend_latest_ts

    entry_raw = _load_ohlcv(selected_symbol, params["ENTRY_TF"], int(params["ENTRY_BARS"]))
    if closed_only:
        entry_raw = _drop_open_bar(entry_raw, params["ENTRY_TF"])
    entry_latest_ts = _latest_bar_ts(entry_raw)
    if entry_latest_ts is not None:
        _warn_if_stale_bar(selected_symbol, params["ENTRY_TF"], entry_latest_ts)
    if entry_raw.empty:
        raise ValueError(f"{selected_symbol} has no closed {params['ENTRY_TF']} data")

    _trend_frame, entry_frame = build_ai_trend_frames(trend_raw, entry_raw, params)
    entry_frame = spec.add_levels(entry_frame, params, selected_symbol)
    return extract_m45_entry_alerts(entry_frame, selected_symbol), entry_latest_ts


def _normalize_ai_trend_event_type(event_type: str | None, tf: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {H3_TREND_CHANGE, M45_ENTRY_SIGNAL}:
        return normalized
    tf_code = str(tf).strip().upper()
    if tf_code == "H3":
        return H3_TREND_CHANGE
    if tf_code == "M45":
        return M45_ENTRY_SIGNAL
    raise ValueError("AI Trend notify supports only H3 trend-change and M45 entry-signal groups")


def _ai_trend_alert_side(direction: int, kind: str) -> str:
    if kind == H3_TREND_CHANGE:
        return "BULLISH" if int(direction) == 1 else "BEARISH"
    return "BUY" if int(direction) == 1 else "SELL"


def _ai_trend_m45_matches_h3(alert: Any) -> bool:
    if getattr(alert, "kind", None) != M45_ENTRY_SIGNAL:
        return True
    h3_bias = getattr(alert, "h3_bias", None)
    if h3_bias is None or pd.isna(h3_bias):
        return False
    return int(h3_bias) == int(alert.direction)


def _drop_open_bar(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """
    Loại bỏ bar cuối nếu nó có thể chưa đóng (đang mở).

    So sánh bartime với (now_UTC - duration_of_tf). Bar nào có bartime
    sau ngưỡng này được coi là "đang mở" và bị loại.

    Args:
        df: DataFrame OHLCV với cột bartime (UTC-naive).
        tf: Mã khung thời gian — dùng để tính độ dài bar (TF_MINUTES).

    Returns:
        DataFrame sau khi loại bar đang mở. Có thể trả về df gốc nếu:
        - tf không có trong TF_MINUTES.
        - Tất cả bar đã đủ cũ (không có bar đang mở).

    Giả định giao dịch:
        bartime là UTC-naive — hàm tự localize về UTC trước khi so sánh.
        Nếu TF không nhận dạng được, không lọc (trả về df gốc) — safe default.
    """
    if df.empty or "bartime" not in df:
        return df
    tf_code = str(tf).upper()
    minutes = config.TF_MINUTES.get(tf_code)
    if not minutes:
        return df
    now_utc = pd.Timestamp.now("UTC")
    if now_utc.tzinfo is None:
        now_utc = now_utc.tz_localize("UTC")
    cutoff = now_utc - pd.Timedelta(minutes=int(minutes))
    bar_times = pd.to_datetime(df["bartime"], errors="coerce")
    if getattr(bar_times.dt, "tz", None) is None:
        bar_times = bar_times.dt.tz_localize("UTC")
    else:
        bar_times = bar_times.dt.tz_convert("UTC")
    return df.loc[bar_times <= cutoff].reset_index(drop=True)


def _next_bar_close_utc(
    tf: str,
    buffer_seconds: int = _DEFAULT_BAR_CLOSE_BUFFER_SECONDS,
) -> pd.Timestamp:
    """
    Tính thời điểm đóng bar tiếp theo + buffer (giây) theo UTC.

    Ví dụ lúc 09:15 UTC, TF=H1, buffer=30s:
        floor(09:15, 1h) = 09:00
        next_close = 09:00 + 1h + 30s = 10:00:30 UTC

    Args:
        tf: Mã khung thời gian. Phải có trong config.TF_MINUTES.
        buffer_seconds: Giây chờ thêm sau bar close để DB có thời gian commit.

    Returns:
        pd.Timestamp UTC-aware. Fallback 5 phút nếu tf không nhận dạng được.

    Giả định giao dịch:
        Bar alignment theo midnight UTC — chuẩn broker Capital.com/MT5.
    """
    minutes = config.TF_MINUTES.get(tf.upper())
    now = pd.Timestamp.now("UTC")
    if not minutes:
        return now + pd.Timedelta(seconds=300)
    period = pd.Timedelta(minutes=minutes)
    floored = now.floor(period)
    next_close = floored + period + pd.Timedelta(seconds=max(0, int(buffer_seconds)))
    if next_close <= now:
        next_close += period
    return next_close


def _latest_bar_text(df: pd.DataFrame) -> str:
    return _format_bar_ts(_latest_bar_ts(df))


def _latest_bar_ts(df: pd.DataFrame) -> pd.Timestamp | None:
    if df.empty or "bartime" not in df:
        return None
    latest = pd.to_datetime(df["bartime"], errors="coerce").dropna()
    if latest.empty:
        return None
    return pd.Timestamp(latest.iloc[-1])


def _format_bar_ts(value: pd.Timestamp | None) -> str:
    if value is None:
        return "-"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _print_status(message: str) -> None:
    now = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="24/7 signal watcher - fixed-route notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Use scan_config.py defaults (AI Trend + Combo production groups)\n"
            "  python -m core_python.notify.signal_watcher\n\n"
            "  # Override: specific symbols and TFs\n"
            "  python -m core_python.notify.signal_watcher --symbols US30,GOLD --tf H1,H4\n\n"
            "  # Dry-run one-shot check\n"
            "  python -m core_python.notify.signal_watcher --dry-run --once"
        ),
    )
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "telegram", "discord", "none"],
        help=(
            "Compatibility option. Signal routing is fixed by strategy "
            "(Combo/MA Cross -> Discord, AI Trend -> Telegram); use 'none' as a send kill switch."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending.")
    parser.add_argument("--once", action="store_true", help="Run one check per group and exit.")
    parser.add_argument(
        "--warm-up",
        action="store_true",
        help="Mark all current signals as seen without sending. Run once before production start.",
    )
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--bars", type=int, default=config.N_BARS)
    parser.add_argument("--include-open-bar", action="store_true")
    parser.add_argument("--no-export", action="store_true", help="Skip CSV export on signal.")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols to scan. Overrides scan_config.py groups.",
    )
    parser.add_argument(
        "--tf",
        default=None,
        help="Comma-separated timeframes to scan. Overrides scan_config.py groups.",
    )
    parser.add_argument("--strategy", default="combo", choices=["combo", "ma_cross", "ai_trend"])
    parser.add_argument(
        "--bar-close-buffer-seconds",
        type=int,
        default=_DEFAULT_BAR_CLOSE_BUFFER_SECONDS,
        help="First scan delay after a candle close. Default: 5 seconds.",
    )
    parser.add_argument(
        "--post-close-retry-seconds",
        type=int,
        default=_DEFAULT_POST_CLOSE_RETRY_SECONDS,
        help="Retry interval after the first post-close scan. Default: 5 seconds.",
    )
    parser.add_argument(
        "--post-close-watch-seconds",
        type=int,
        default=_DEFAULT_POST_CLOSE_WATCH_SECONDS,
        help="How long to keep retrying after the first post-close scan. Default: 10 seconds.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Append console output to this file while still printing to screen.",
    )
    parser.add_argument(
        "--health-interval-minutes",
        type=int,
        default=60,
        help="Deprecated; hourly Telegram health summary is disabled.",
    )
    parser.add_argument(
        "--max-alert-age-minutes",
        type=int,
        default=None,
        help=(
            "Do not send unseen signals older than this. "
            "Default: max(3x timeframe, 120 minutes); skipped signals are marked seen."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Hide per-symbol scan progress.")
    return parser.parse_args()


def _build_groups_from_args(args: argparse.Namespace) -> list[dict]:
    """Build scan groups from CLI --symbols and --tf overrides."""
    from core_python.notify.scan_config import TF_POLL_SECONDS

    symbols = [s.strip().upper() for s in args.symbols.replace(";", ",").split(",") if s.strip()]
    tfs = [t.strip().upper() for t in args.tf.replace(";", ",").split(",") if t.strip()]
    groups = []
    for tf in tfs:
        group_bars = args.bars
        overrides: dict[str, Any] = {}
        event_type = None
        if args.strategy == "ai_trend":
            event_type = _normalize_ai_trend_event_type(None, tf)
            if event_type == H3_TREND_CHANGE:
                group_bars = min(int(args.bars), 400)
                overrides = {"TREND_BARS": group_bars}
            elif event_type == M45_ENTRY_SIGNAL:
                group_bars = int(args.bars)
                overrides = {"TREND_BARS": 400, "ENTRY_BARS": group_bars}
        groups.append(
            {
                "strategy": args.strategy,
                "symbols": symbols,
                "tf": tf,
                "bars": group_bars,
                "poll_seconds": TF_POLL_SECONDS.get(tf, 300),
                "chat_id": None,
                "overrides": overrides,
                "event_type": event_type,
            }
        )
    return groups


class _WatcherLock:
    """Best-effort single-instance lock for the production watcher process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            self._lock_handle(handle)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"another signal_watcher instance appears to be running (lock: {self.path})"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nstarted_utc={pd.Timestamp.now('UTC').isoformat()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()
            self._handle = None

    @staticmethod
    def _lock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    import sys
    import atexit

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        pass

    args = _parse_args()

    if args.log_file:
        _tee_console_to_file(args.log_file)

    if args.symbols and args.tf:
        scan_groups = _build_groups_from_args(args)
        mode_desc = f"CLI override - symbols: {args.symbols} | TF: {args.tf}"
    else:
        from core_python.notify.scan_config import SCAN_GROUPS

        scan_groups = SCAN_GROUPS
        mode_desc = "scan_config.py defaults"

    state = SignalState(args.state_path)
    notifier = Notifier(backend=args.backend, dry_run=args.dry_run)
    closed_only = not args.include_open_bar
    current_scan_fingerprint = scan_fingerprint(scan_groups, closed_only)

    if args.warm_up:
        print("Warm-up: marking all current signals as seen (no Telegram send)...", flush=True)
        total = 0
        n_errors = 0
        from core_python.notify.state import signal_key as _sk

        for group in scan_groups:
            for symbol in group["symbols"]:
                try:
                    if group["strategy"] == "ai_trend":
                        alerts, _latest = run_ai_trend_alerts(
                            symbol=symbol,
                            tf=group["tf"],
                            bars=group.get("bars", args.bars),
                            event_type=group.get("event_type"),
                            overrides=group.get("overrides") or {},
                            closed_only=closed_only,
                        )
                        for alert in alerts:
                            key = ai_trend_alert_key(alert)
                            if not state.has(key):
                                state.add(key)
                                total += 1
                        continue

                    frame, _spec, _params = run_strategy_frame(
                        strategy=group["strategy"],
                        symbol=symbol,
                        tf=group["tf"],
                        bars=group.get("bars", args.bars),
                        overrides=group.get("overrides") or {},
                        closed_only=closed_only,
                    )
                    if frame.empty or "signal" not in frame.columns:
                        continue
                    for _, row in frame[frame["signal"].fillna(0).astype(int).ne(0)].iterrows():
                        key = _sk(
                            group["strategy"],
                            symbol,
                            group["tf"],
                            row["bartime"],
                            int(row["signal"]),
                        )
                        if not state.has(key):
                            state.add(key)
                            total += 1
                except Exception as exc:
                    n_errors += 1
                    print(f"  [ERROR] {symbol} {group['tf']}: {exc}", flush=True)

        if n_errors > 0:
            print(
                f"[WARNING] Warm-up had {n_errors} scan error(s). "
                "State sentinel NOT written — DB may be unavailable or symbols incorrect. "
                "Fix the errors and re-run warm-up before starting the watcher.",
                flush=True,
            )
            return 1

        state.set_scan_fingerprint(current_scan_fingerprint)
        print(f"Warm-up complete: {total} signals marked as seen. Now run without --warm-up.")
        return 0

    if scan_groups and not state.path.exists():
        print(
            "[ERROR] Watcher state file not found. Run warm-up before starting production:\n"
            f"  python -m core_python.notify.signal_watcher --warm-up\n"
            f"  (expected state path: {state.path})",
            flush=True,
        )
        return 1
    if scan_groups:
        fingerprint_error = _scan_fingerprint_error(state, current_scan_fingerprint)
        if fingerprint_error:
            print(fingerprint_error, flush=True)
            return 1

    watcher_lock = _WatcherLock(state.path.parent / "signal_watcher.lock")
    try:
        watcher_lock.acquire()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 1
    atexit.register(watcher_lock.release)

    n_groups = len(scan_groups)
    total_slots = sum(len(g["symbols"]) for g in scan_groups)
    startup_msg = (
        "<b>SEN05 Watcher started</b>\n"
        f"{n_groups} groups / {total_slots} symbol-TF slots"
        + (" / DRY RUN" if args.dry_run else "")
    )
    print(startup_msg.replace("<b>", "").replace("</b>", ""), flush=True)
    print(f"Mode: {mode_desc}", flush=True)
    print(
        "Schedule: first scan "
        f"{args.bar_close_buffer_seconds}s after close, retry every "
        f"{args.post_close_retry_seconds}s for {args.post_close_watch_seconds}s",
        flush=True,
    )
    print(
        "Signal routing: Combo/MA Cross -> Discord, AI Trend -> Telegram; "
        "--backend none disables sending.",
        flush=True,
    )
    print(_health_summary(state), flush=True)

    # None -> run immediately on first tick, then align to bar close boundaries.
    next_run_at: dict[int, pd.Timestamp | None] = {i: None for i in range(len(scan_groups))}
    retry_until: dict[int, pd.Timestamp | None] = {i: None for i in range(len(scan_groups))}
    while True:
        now_ts = pd.Timestamp.now("UTC")
        for i, group in enumerate(scan_groups):
            scheduled = next_run_at[i]
            if scheduled is not None and now_ts < scheduled:
                continue
            latest_bars: list[pd.Timestamp] = []
            group_started = time.perf_counter()
            db_loads_before = _DB_LOAD_COUNT
            try:
                if group["strategy"] == "ai_trend":
                    events = check_ai_trend_once(
                        symbols=group["symbols"],
                        tf=group["tf"],
                        bars=group.get("bars", args.bars),
                        state=state,
                        notifier=notifier,
                        event_type=group.get("event_type"),
                        overrides=group.get("overrides") or {},
                        closed_only=closed_only,
                        chat_id=group.get("chat_id"),
                        show_progress=not args.quiet,
                        latest_bars=latest_bars,
                        max_alert_age_minutes=args.max_alert_age_minutes,
                    )
                else:
                    events = check_once(
                        strategy=group["strategy"],
                        symbols=group["symbols"],
                        tf=group["tf"],
                        bars=group.get("bars", args.bars),
                        state=state,
                        notifier=notifier,
                        output_dir=args.output_dir,
                        overrides=group.get("overrides") or {},
                        closed_only=closed_only,
                        export_on_signal=not args.no_export,
                        chat_id=group.get("chat_id"),
                        show_progress=not args.quiet,
                        latest_bars=latest_bars,
                        max_alert_age_minutes=args.max_alert_age_minutes,
                    )
            except Exception as exc:
                events = [f"[ERROR] group {i} {group['tf']}: {exc}"]
            group_elapsed = time.perf_counter() - group_started
            group_db_loads = _DB_LOAD_COUNT - db_loads_before

            schedule = _schedule_next_run(
                tf=group["tf"],
                now_ts=pd.Timestamp.now("UTC"),
                previous_scheduled=scheduled,
                retry_until=retry_until[i],
                buffer_seconds=args.bar_close_buffer_seconds,
                post_close_retry_seconds=args.post_close_retry_seconds,
                post_close_watch_seconds=args.post_close_watch_seconds,
                latest_closed_bars=latest_bars,
            )
            next_run_at[i] = schedule["next_run_at"]
            retry_until[i] = schedule["retry_until"]

            ts = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
            for event in events:
                print(f"[{ts}] {event}", flush=True)
            print(
                f"[{ts}] [{group['tf']}] group done: "
                f"symbols={len(group.get('symbols', []))}, db_loads={group_db_loads}, "
                f"elapsed={group_elapsed:.2f}s",
                flush=True,
            )
            print(
                f"[{ts}] [{group['tf']}] next check: "
                f"{next_run_at[i].strftime('%H:%M:%S')} UTC ({schedule['reason']})",
                flush=True,
            )

        if args.once:
            return 0
        time.sleep(_IDLE_SLEEP_SECONDS)


def _schedule_next_run(
    *,
    tf: str,
    now_ts: pd.Timestamp,
    previous_scheduled: pd.Timestamp | None,
    retry_until: pd.Timestamp | None,
    buffer_seconds: int,
    post_close_retry_seconds: int,
    post_close_watch_seconds: int,
    latest_closed_bars: list[pd.Timestamp] | None = None,
) -> dict[str, Any]:
    latest_closed_bars = latest_closed_bars or []
    if previous_scheduled is None:
        if latest_closed_bars:
            return {
                "next_run_at": _next_close_from_latest_bars(tf, latest_closed_bars, buffer_seconds),
                "retry_until": None,
                "reason": "next candle close from DB anchor",
            }
        return {
            "next_run_at": _next_bar_close_utc(tf, buffer_seconds),
            "retry_until": None,
            "reason": "next candle close",
        }

    active_retry_until = retry_until
    if active_retry_until is None and post_close_watch_seconds > 0:
        active_retry_until = previous_scheduled + pd.Timedelta(seconds=post_close_watch_seconds)

    retry_at = now_ts + pd.Timedelta(seconds=max(1, int(post_close_retry_seconds)))
    if (
        active_retry_until is not None
        and retry_at <= active_retry_until
        and _db_bar_has_not_advanced(
            tf=tf,
            latest_closed_bars=latest_closed_bars,
            previous_scheduled=previous_scheduled,
            buffer_seconds=buffer_seconds,
        )
    ):
        return {
            "next_run_at": retry_at,
            "retry_until": active_retry_until,
            "reason": "post-close retry",
        }

    if latest_closed_bars:
        return {
            "next_run_at": _next_close_from_latest_bars(tf, latest_closed_bars, buffer_seconds),
            "retry_until": None,
            "reason": "next candle close from DB anchor",
        }

    return {
        "next_run_at": _next_bar_close_utc(tf, buffer_seconds),
        "retry_until": None,
        "reason": "next candle close",
    }


def _next_close_from_latest_bars(
    tf: str,
    latest_closed_bars: list[pd.Timestamp],
    buffer_seconds: int,
) -> pd.Timestamp:
    return min(
        _next_close_from_latest_bar(tf, latest_bar, buffer_seconds)
        for latest_bar in latest_closed_bars
    )


def _next_close_from_latest_bar(
    tf: str,
    latest_closed_bar: pd.Timestamp,
    buffer_seconds: int,
) -> pd.Timestamp:
    minutes = config.TF_MINUTES.get(tf.upper())
    if not minutes:
        return _next_bar_close_utc(tf, buffer_seconds)
    now = pd.Timestamp.now("UTC")
    latest = pd.Timestamp(latest_closed_bar)
    if latest.tzinfo is None:
        latest = latest.tz_localize("UTC")
    else:
        latest = latest.tz_convert("UTC")
    period = pd.Timedelta(minutes=int(minutes))
    next_run = latest + period + pd.Timedelta(seconds=max(0, int(buffer_seconds)))
    while next_run <= now:
        next_run += period
    return next_run


def _db_bar_has_not_advanced(
    *,
    tf: str,
    latest_closed_bars: list[pd.Timestamp],
    previous_scheduled: pd.Timestamp,
    buffer_seconds: int,
) -> bool:
    minutes = config.TF_MINUTES.get(tf.upper())
    if not latest_closed_bars or not minutes:
        return False
    normalized = []
    for latest_bar in latest_closed_bars:
        latest = pd.Timestamp(latest_bar)
        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        else:
            latest = latest.tz_convert("UTC")
        normalized.append(latest)
    expected_latest_open = (
        previous_scheduled
        - pd.Timedelta(seconds=max(0, int(buffer_seconds)))
        - pd.Timedelta(minutes=int(minutes))
    )
    return max(normalized) < expected_latest_open


def _health_summary(state: SignalState, minutes: int = 60) -> str:
    now = pd.Timestamp.now("UTC")
    cutoff = now - pd.Timedelta(minutes=int(minutes))
    recent: list[tuple[str, pd.Timestamp]] = []
    for key, sent_at in state.sent.items():
        try:
            ts = pd.Timestamp(sent_at)
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        if ts >= cutoff:
            recent.append((key, ts))
    recent.sort(key=lambda item: item[1])
    last = recent[-1][0] if recent else "-"
    return (
        f"[{now.strftime('%H:%M:%S')}] HEALTH last {minutes}m: "
        f"signals={len(recent)}, last={last}"
    )


def _format_hourly_symbol_summary(
    sent_events: list[SentSignalEvent],
    scan_groups: list[dict],
    minutes: int = 60,
) -> str:
    """Build a Telegram summary grouped by symbol for production-sent signals."""
    now = pd.Timestamp.now("UTC")
    cutoff = now - pd.Timedelta(minutes=int(minutes))
    recent = [
        event
        for event in sent_events
        if _as_utc_ts(event.sent_at) >= cutoff and _include_in_hourly_summary(event)
    ]
    symbols = _summary_symbols(scan_groups)
    by_symbol: dict[str, list[SentSignalEvent]] = {symbol: [] for symbol in symbols}
    for event in recent:
        by_symbol.setdefault(event.symbol.upper(), []).append(event)

    lines = [
        "<b>SEN05 Hourly Signal Summary</b>",
        "",
        f"Window: <code>{_fmt_summary_time(cutoff)} - {_fmt_summary_time(now)} UTC</code>",
        f"Signals: <b>{len(recent)}</b>",
        "",
        "<b>By symbol</b>",
    ]
    for symbol in sorted(by_symbol):
        symbol_events = sorted(by_symbol[symbol], key=lambda item: item.sent_at)
        if not symbol_events:
            lines.append(f"{html.escape(symbol)}: <code>no new signal</code>")
            continue
        details = "; ".join(_summary_event_text(event) for event in symbol_events)
        lines.append(f"{html.escape(symbol)}: {details}")
    return "\n".join(lines)


def _summary_symbols(scan_groups: list[dict]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for group in scan_groups:
        for symbol in group.get("symbols", []):
            normalized = str(symbol).upper()
            if normalized in seen:
                continue
            seen.add(normalized)
            symbols.append(normalized)
    return symbols


def _summary_event_text(event: SentSignalEvent) -> str:
    strategy = "AI Trend" if event.strategy == "ai_trend" else event.strategy.title()
    kind = ""
    if event.strategy == "ai_trend":
        if event.kind == H3_TREND_CHANGE:
            kind = " H3"
        elif event.kind == M45_ENTRY_SIGNAL:
            kind = " M45"
    else:
        kind = f" {event.tf.upper()}"
    return (
        f"<code>{html.escape(strategy + kind)} "
        f"{html.escape(event.side.upper())} "
        f"{_fmt_summary_time(event.event_time)}</code>"
    )


def _include_in_hourly_summary(event: SentSignalEvent) -> bool:
    if event.strategy != "ai_trend":
        return True
    return event.kind == M45_ENTRY_SIGNAL


def _fmt_summary_time(value: object) -> str:
    return _as_utc_ts(value).strftime("%Y-%m-%d %H:%M")


def _as_utc_ts(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _prune_sent_signal_events(sent_events: list[SentSignalEvent], hours: int = 24) -> None:
    cutoff = pd.Timestamp.now("UTC") - pd.Timedelta(hours=int(hours))
    sent_events[:] = [event for event in sent_events if _as_utc_ts(event.sent_at) >= cutoff]


class _TeeStream:
    def __init__(self, stream: Any, log_file: Any) -> None:
        self.stream = stream
        self.log_file = log_file
        self.encoding = getattr(stream, "encoding", "utf-8")

    def write(self, data: str) -> int:
        written = self.stream.write(data)
        self.log_file.write(data)
        return written

    def flush(self) -> None:
        self.stream.flush()
        self.log_file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())


def _tee_console_to_file(path: str | Path) -> None:
    import sys

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _TeeStream(sys.stdout, log_file)  # type: ignore[assignment]
    sys.stderr = _TeeStream(sys.stderr, log_file)  # type: ignore[assignment]


def _ensure_state_file(state: SignalState) -> None:
    """Write an empty state file if none exists yet — serves as warm-up sentinel."""
    import json

    if state.path.exists():
        return
    state.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.path.with_suffix(state.path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"sent": state.sent, "meta": state.meta}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(state.path)


if __name__ == "__main__":
    raise SystemExit(main())
