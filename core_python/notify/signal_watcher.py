"""
Vòng lặp giám sát 24/7: tải bar từ DB, phát hiện tín hiệu, gửi Telegram.

Mô tả:
    Vòng lặp chính (main()) chạy liên tục, kiểm tra từng nhóm scan trong
    scan_config.py và gửi Telegram ngay khi có tín hiệu mới trên bar đã đóng.

    Lịch scan căn chỉnh theo bar-close: sau mỗi lần kiểm tra, lần tiếp theo
    được lên lịch vào thời điểm bar close kế tiếp + 30s buffer.
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
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core_python import config
from core_python.data.loader import load
from core_python.export.to_csv import export_signals
from core_python.notify.formatter import format_signal_message
from core_python.notify.notifier import Notifier
from core_python.notify.state import SignalState, signal_key
from core_python.strategies.registry import get_strategy


_DEFAULT_BAR_CLOSE_BUFFER_SECONDS = 5
_DEFAULT_POST_CLOSE_RETRY_SECONDS = 5
_DEFAULT_POST_CLOSE_WATCH_SECONDS = 10
_IDLE_SLEEP_SECONDS = 1.0


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
    raw = load(symbol, tf, bars)
    if closed_only:
        raw = _drop_open_bar(raw, tf)
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
        latest_bar = _format_bar_ts(latest_bar_ts)
        new_rows = all_new_signal_rows(frame, state, strategy, symbol, tf)
        if not new_rows:
            events.append(f"{symbol} {tf}: no new signal (latest bar {latest_bar})")
            continue

        for row in new_rows:
            key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
            if export_on_signal:
                try:
                    export_signals(frame, symbol=symbol, strategy=strategy, output_dir=output_dir)
                except Exception:
                    pass

            message = format_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf)
            result = notifier.send(message, chat_id=chat_id)
            side = "BUY" if int(row["signal"]) == 1 else "SELL"
            signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M")

            if result.sent and not notifier.dry_run:
                state.add(key)
                events.append(f"{symbol} {tf}: alerted via {result.backend} {side} {signal_time} UTC")
            elif result.sent:
                events.append(f"{symbol} {tf}: dry-run OK {side} {signal_time} UTC")
            else:
                events.append(f"{symbol} {tf}: FAILED - {result.detail}")

    return events


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
        description="24/7 signal watcher - Telegram notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Use scan_config.py defaults (Indice, H1-H4)\n"
            "  python -m core_python.notify.signal_watcher\n\n"
            "  # Override: specific symbols and TFs\n"
            "  python -m core_python.notify.signal_watcher --symbols US30,GOLD --tf H1,H4\n\n"
            "  # Dry-run one-shot check\n"
            "  python -m core_python.notify.signal_watcher --dry-run --once"
        ),
    )
    parser.add_argument("--backend", default="auto", choices=["auto", "telegram", "discord", "none"])
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
    parser.add_argument("--strategy", default="combo", choices=["combo", "ma_cross"])
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
        help="Print a health summary every N minutes. Default: 60.",
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
        groups.append(
            {
                "strategy": args.strategy,
                "symbols": symbols,
                "tf": tf,
                "bars": args.bars,
                "poll_seconds": TF_POLL_SECONDS.get(tf, 300),
                "chat_id": None,
                "overrides": {},
            }
        )
    return groups


def main() -> int:
    import sys

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

    if args.warm_up:
        print("Warm-up: marking all current signals as seen (no Telegram send)...", flush=True)
        total = 0
        from core_python.notify.state import signal_key as _sk

        for group in scan_groups:
            for symbol in group["symbols"]:
                try:
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
                    print(f"  {symbol} {group['tf']}: skip - {exc}", flush=True)
        print(f"Warm-up complete: {total} signals marked as seen. Now run without --warm-up.")
        return 0

    n_groups = len(scan_groups)
    total_slots = sum(len(g["symbols"]) for g in scan_groups)
    startup_msg = (
        "<b>SEN05 Watcher started</b>\n"
        f"{n_groups} groups / {total_slots} symbol-TF slots"
        + (" / DRY RUN" if args.dry_run else "")
    )
    notifier.send(startup_msg)
    print(startup_msg.replace("<b>", "").replace("</b>", ""), flush=True)
    print(f"Mode: {mode_desc}", flush=True)
    print(
        "Schedule: first scan "
        f"{args.bar_close_buffer_seconds}s after close, retry every "
        f"{args.post_close_retry_seconds}s for {args.post_close_watch_seconds}s",
        flush=True,
    )
    print(_health_summary(state), flush=True)

    # None -> run immediately on first tick, then align to bar close boundaries.
    next_run_at: dict[int, pd.Timestamp | None] = {i: None for i in range(len(scan_groups))}
    retry_until: dict[int, pd.Timestamp | None] = {i: None for i in range(len(scan_groups))}
    next_health_at = pd.Timestamp.now("UTC") + pd.Timedelta(
        minutes=max(1, int(args.health_interval_minutes))
    )

    while True:
        now_ts = pd.Timestamp.now("UTC")
        for i, group in enumerate(scan_groups):
            scheduled = next_run_at[i]
            if scheduled is not None and now_ts < scheduled:
                continue
            latest_bars: list[pd.Timestamp] = []
            try:
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
                )
            except Exception as exc:
                events = [f"[ERROR] group {i} {group['tf']}: {exc}"]

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
                f"[{ts}] [{group['tf']}] next check: "
                f"{next_run_at[i].strftime('%H:%M:%S')} UTC ({schedule['reason']})",
                flush=True,
            )

        if args.once:
            return 0
        if pd.Timestamp.now("UTC") >= next_health_at:
            print(_health_summary(state), flush=True)
            next_health_at = pd.Timestamp.now("UTC") + pd.Timedelta(
                minutes=max(1, int(args.health_interval_minutes))
            )
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


if __name__ == "__main__":
    raise SystemExit(main())
