"""Manual deep historical backfill: TradingView replay windows walked backward, insert-only into SQL."""
# Module này chạy tay (lệnh `deep-backfill`), mặc định TẮT (`deep_backfill.enabled`), không phải một role của engine.
# Cơ chế lấy dữ liệu: phiên replay đặt mốc neo tại thời điểm bất kỳ trong quá khứ; một cửa sổ là N nến KẾT THÚC tại
# mốc neo (N <= backfill.max_bars_per_request, trần 20.000 của gói Premium). Đi ngược từ "bây giờ": mốc neo kế là nến
# đầu của cửa sổ trước, nên các cửa sổ nối liền nhau. Backfill hằng ngày thì luôn lấy N nến gần nhất tính từ hiện tại.
# Chỉ CHÈN nến SQL còn thiếu; nến đã có thì bỏ, không bao giờ ghi đè. Không spool (spool ghi mỗi nến một file), không
# publish sự kiện Redis. Mọi truy cập SQL đi qua sql_connector, ghi dưới khóa "delivery" dùng chung với backfill, và
# backfill hằng ngày phải tắt khi chạy thật. Phần giao thức replay nằm ở đây vì websocket.py đã kín giới hạn 300 dòng.
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import websocket

from ..engine.auth import AuthError, ensure_authenticated
from ..engine.pipeline import log_pair_failure, utc, validate_candles
from ..engine.runtime import instance_lock, service_status
from ..engine.spool import atomic_write_text, interprocess_lock
from ..engine.sql_connector import (
    Pair, bulk_upsert_candles, candle_signature, fetch_existing_candles, get_connection, pair_key, select_pairs,
)
from ..engine.websocket import (
    IncompleteFetchError, ProviderRequestError, _headers, _session_id, frame_message, normalize_candle, split_messages,
)
from ..log import log_event

LOGGER = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 30.0  # trần chờ một cửa sổ replay
_PAUSE_SECONDS = 1.0  # nghỉ giữa hai cửa sổ để không dồn tải lên tài khoản TradingView
_WRITE_CHUNK = 5000  # số nến mỗi lần ghi SQL, để mỗi câu lệnh đủ ngắn
_SQL_TIMEOUT_SECONDS = 300  # loader xử lý lại staging từ FromTime nên cần timeout dài hơn mặc định 30 giây
_MAX_WINDOWS = 2000  # chặn vòng lặp vô hạn trên một cặp
_MAX_CONSECUTIVE_FAILURES = 3
_ERROR_MESSAGES = {"error", "critical_error", "series_error", "symbol_error"}
_COUNTERS = ("windows", "fetched", "inserted", "present", "changed")


class DeepBackfillError(RuntimeError):
    """A safety condition stopped the whole run (not a single pair)."""


def _replay_window(tv: dict[str, Any], symbol: dict[str, Any], timeframe: dict[str, Any],
                   anchor: datetime, count: int) -> list[dict[str, Any]]:
    """Open one replay socket and return the `count` candles ending at `anchor`, oldest first."""
    chart, replay = _session_id("cs"), _session_id("rs")
    asset = {"symbol": f"{symbol['exchange']}:{symbol['symbol']}", "adjustment": "splits"}
    socket = websocket.create_connection(
        tv["websocket_url"], header=_headers(tv.get("cookie", "")),
        origin="https://www.tradingview.com", timeout=_TIMEOUT_SECONDS)
    bars: dict[float, list[Any]] = {}
    try:
        socket.settimeout(1.0)
        for method, params in (
            ("set_auth_token", [tv["auth_token"]]),
            ("chart_create_session", [chart, ""]),
            ("switch_timezone", [chart, tv.get("timezone", "Etc/UTC")]),
            ("replay_create_session", [replay]),
            ("replay_add_series", [replay, "req_add", "=" + json.dumps(asset, separators=(",", ":")),
                                   timeframe["interval"]]),
            ("replay_reset", [replay, "req_reset", int(anchor.timestamp())]),
            ("resolve_symbol", [chart, "ser_1", "=" + json.dumps(
                {"replay": replay, "symbol": asset}, separators=(",", ":"))]),
            ("create_series", [chart, "$prices", "s1", "ser_1", timeframe["interval"], int(count)]),
        ):
            socket.send(frame_message(method, params))
        deadline, completed = time.monotonic() + _TIMEOUT_SECONDS, False
        while not completed and time.monotonic() < deadline:
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            for packet in split_messages(raw):
                if packet.startswith("~h~"):
                    socket.send(f"~m~{len(packet)}~m~{packet}")
                    continue
                message = json.loads(packet)
                kind, params = message.get("m"), message.get("p") or []
                if kind in _ERROR_MESSAGES:
                    raise ProviderRequestError(f"TradingView error: {str(params)[:200]}")
                if (kind in {"du", "timescale_update"} and len(params) >= 2 and params[0] == chart
                        and isinstance(params[1], dict)):
                    series = params[1].get("$prices") or {}
                    for bar in series.get("s") or []:
                        values = bar.get("v") or []
                        if len(values) >= 6:
                            bars[float(values[0])] = values
                elif kind == "series_completed" and (not params or params[0] == chart):
                    completed = True
        if not completed:
            raise IncompleteFetchError("replay window did not complete before the deadline")
    finally:
        try:
            socket.close()
        except Exception:
            pass
    return [normalize_candle(bars[stamp], symbol, timeframe) for stamp in sorted(bars)]


def _fetch_window(config: dict[str, Any], symbol: dict[str, Any], timeframe: dict[str, Any],
                  anchor: datetime, count: int) -> list[dict[str, Any]]:
    # Thử lại có giới hạn như websocket.py; lỗi đăng nhập thì dừng ngay.
    tv, attempts = config["tradingview"], int(config["tradingview"]["retry_count"])
    for attempt in range(1, attempts + 1):
        try:
            ensure_authenticated(config)
            return _replay_window(tv, symbol, timeframe, anchor, count)
        except AuthError:
            raise
        except Exception as exc:
            if attempt >= attempts:
                raise
            log_event(LOGGER, logging.WARNING, "DEEP_BACKFILL_WINDOW_RETRY", "LOW", component="deep_backfill",
                      attempt=attempt, max_attempts=attempts, error_type=type(exc).__name__, error=exc)
            time.sleep(float(tv.get("retry_delay_seconds", 1)))
    raise IncompleteFetchError("no replay attempt was made")


def _walk(config: dict[str, Any], symbol: dict[str, Any], timeframe: dict[str, Any],
          start: datetime, window: int) -> Iterator[list[dict[str, Any]]]:
    """Yield validated candle windows from now back to `start`; each window ends where the previous began."""
    now = cursor = datetime.now(timezone.utc)
    closed_only, boundary = bool(config["live"]["closed_candles_only"]), None
    for _ in range(_MAX_WINDOWS):
        raw = _fetch_window(config, symbol, timeframe, cursor, window)
        if not raw:
            return
        first = raw[0]["timestamp"]
        if boundary is not None and first >= boundary:
            # Cửa sổ không lùi được thêm nến nào: lịch sử đã hết (cửa sổ ngắn) hoặc replay bị kẹt (cửa sổ đầy).
            if len(raw) < window:
                return
            raise IncompleteFetchError("replay made no older progress")
        # Khung lớn (D1, W...) trả cả lịch sử từ 1999: chỉ kiểm nến trong phạm vi, nến lỗi cũ ngoài phạm vi không được chặn cặp.
        candles = validate_candles([c for c in raw if c["timestamp"] >= start], timeframe, closed_only=closed_only, now=now)
        # Nến cuối của cửa sổ này chính là nến đầu của cửa sổ trước: bỏ để không đếm hai lần.
        yield [c for c in candles if boundary is None or c["timestamp"] < boundary]
        if first <= start or len(raw) < window:
            return
        cursor = boundary = first
        time.sleep(_PAUSE_SECONDS)
    raise IncompleteFetchError("deep backfill exceeded the window budget for one pair")


def _assert_backfill_idle(config: dict[str, Any]) -> None:
    # Backfill hằng ngày và deep backfill không được cùng ghi SQL thật: từ chối thay vì tin vào việc operator đã tắt.
    status = service_status(config, "backfill")
    if status.get("status") == "running" and status.get("process_alive"):
        raise DeepBackfillError("daily backfill is running; stop it before a deep backfill writes SQL")


def _store(config: dict[str, Any], symbol: dict[str, Any], timeframe: dict[str, Any],
           candles: list[dict[str, Any]], start: datetime, connection: Any, dry_run: bool) -> Counter:
    """Insert only the candles missing from Fact; existing candles are counted, never rewritten."""
    rows = [candle for candle in candles if candle["timestamp"] >= start]
    result: Counter = Counter(fetched=len(rows))
    if not rows:
        return result
    symbol_id = int(symbol["symbol_id"])
    existing = fetch_existing_candles(
        config, symbol_id, timeframe["code"], rows[0]["timestamp"], rows[-1]["timestamp"], connection=connection)
    missing = []
    for candle in rows:
        known = existing.get(candle["timestamp"].replace(tzinfo=None))
        if known is None:
            missing.append(candle)
        elif tuple(known[:4]) != tuple(candle_signature(candle)[:4]):  # chỉ so OHLC, volume không quan trọng
            result["changed"] += 1
    result.update(inserted=len(missing), present=len(rows) - len(missing))
    if dry_run:
        return result
    lock_timeout = float(config["sql_server"]["command_timeout_seconds"]) + 5.0
    for offset in range(0, len(missing), _WRITE_CHUNK):
        _assert_backfill_idle(config)
        with interprocess_lock(config, "delivery", timeout_seconds=lock_timeout):
            bulk_upsert_candles(config, timeframe, missing[offset:offset + _WRITE_CHUNK],
                                symbol_id=symbol_id, connection=connection)
    return result


def _run_pair(config: dict[str, Any], pair: Pair, start: datetime, window: int, dry_run: bool) -> Counter:
    symbol, timeframe = pair
    totals, started = Counter(), time.monotonic()
    connection = get_connection(config)
    connection.timeout = _SQL_TIMEOUT_SECONDS
    try:
        for candles in _walk(config, symbol, timeframe, start, window):
            totals["windows"] += 1
            part = _store(config, symbol, timeframe, candles, start, connection, dry_run)
            totals.update(part)
            # Log từng cửa sổ để sau này dò cửa sổ có tỷ lệ `changed` bất thường (TradingView đôi lúc trả giá lệch nhẹ).
            log_event(LOGGER, logging.INFO, "DEEP_BACKFILL_WINDOW", "NONE", component="deep_backfill",
                      pair=pair_key(pair), window=totals["windows"], first=candles[0]["timestamp"] if candles else None,
                      last=candles[-1]["timestamp"] if candles else None, inserted=part["inserted"],
                      present=part["present"], changed=part["changed"])
    finally:
        connection.close()
    log_event(LOGGER, logging.INFO, "DEEP_BACKFILL_PAIR_COMPLETED", "NONE", component="deep_backfill",
              pair=pair_key(pair), dry_run=dry_run, duration_seconds=round(time.monotonic() - started, 1),
              **{name: totals[name] for name in _COUNTERS})
    return totals


def _select(config: dict[str, Any], symbols: str | None, timeframes: str | None) -> list[Pair]:
    # CLI ghi đè cấu hình; timeframes rỗng nghĩa là cả 15 khung. Tên lạ báo lỗi ngay, không lặng lẽ bỏ qua.
    deep = config["deep_backfill"]
    wanted = {n.strip().upper() for n in (symbols or "").split(",") if n.strip()} or set(deep["symbols"])
    codes = {n.strip().upper() for n in (timeframes or "").split(",") if n.strip()} or set(deep["timeframes"])
    pairs = select_pairs(config, live=False)
    unknown = (wanted - {p[0]["symbol"].upper() for p in pairs}) | (codes - {p[1]["code"].upper() for p in pairs})
    if not wanted or unknown:
        raise ValueError(f"unknown or missing deep backfill selection: {', '.join(sorted(unknown)) or 'no symbols'}")
    return [p for p in pairs if p[0]["symbol"].upper() in wanted and (not codes or p[1]["code"].upper() in codes)]


def _load_state(path: Path, start: datetime, fresh: bool) -> dict[str, Any]:
    # Checkpoint theo cặp: chỉ cặp đã chạy xong mới được bỏ qua; đổi mốc bắt đầu thì làm lại từ đầu.
    try:
        state = {} if fresh else json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("start_utc") != start.isoformat():
        state = {"start_utc": start.isoformat(), "pairs": {}}
    return state


def run_deep_backfill(config: dict[str, Any], *, symbols: str | None = None, timeframes: str | None = None,
                      start: str | None = None, dry_run: bool = False, fresh: bool = False) -> dict[str, Any]:
    """Run one finite deep backfill; `dry_run` fetches and compares but writes nothing."""
    deep = config["deep_backfill"]
    if not deep["enabled"]:
        raise RuntimeError("deep backfill is disabled in config.yaml (deep_backfill.enabled)")
    start_at = utc(datetime.fromisoformat(start)) if start else deep["start_utc"]
    pairs = _select(config, symbols, timeframes)
    if not dry_run:
        _assert_backfill_idle(config)
    window = int(config["backfill"]["max_bars_per_request"])
    path = Path(config["app"]["runtime_dir"]) / "deep_backfill" / "state.json"
    summary: dict[str, Any] = {
        "ok": True, "dry_run": dry_run, "config_path": config["app"].get("config_path"),
        "start_utc": start_at.isoformat(), "pairs": len(pairs), "completed": 0,
        "skipped": 0, "failed": 0, "deferred": 0, "failed_pairs": [], "deferred_pairs": [],
        **dict.fromkeys(_COUNTERS, 0)}
    with instance_lock(config, "deep_backfill"):
        state = _load_state(path, start_at, fresh or dry_run)
        log_event(LOGGER, logging.INFO, "DEEP_BACKFILL_STARTED", "NONE", component="deep_backfill",
                  pairs=len(pairs), start_utc=start_at, dry_run=dry_run)
        consecutive = 0
        for index, pair in enumerate(pairs):
            key = pair_key(pair)
            if not dry_run and key in state["pairs"]:
                summary["skipped"] += 1
                continue
            if consecutive >= _MAX_CONSECUTIVE_FAILURES:
                summary["deferred_pairs"] = [pair_key(p) for p in pairs[index:] if pair_key(p) not in state["pairs"]]
                summary["deferred"] = len(summary["deferred_pairs"])
                break
            try:
                result = _run_pair(config, pair, start_at, window, dry_run)
            except (AuthError, DeepBackfillError):
                raise
            except Exception as exc:
                consecutive += 1
                summary["failed"] += 1
                summary["failed_pairs"].append(key)
                log_pair_failure("deep_backfill", pair[0], pair[1], exc, stage="fetch")
                continue
            consecutive = 0
            summary["completed"] += 1
            for name in _COUNTERS:
                summary[name] += result[name]
            if not dry_run:
                state["pairs"][key] = {"completed_at": datetime.now(timezone.utc).isoformat(), **result}
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(path, json.dumps(state, indent=1, default=str))
    summary["ok"] = not summary["failed"] and not summary["deferred"]
    log_event(LOGGER, logging.INFO, "DEEP_BACKFILL_FINISHED", "NONE" if summary["ok"] else "HIGH",
              component="deep_backfill", completed=summary["completed"], failed=summary["failed"],
              deferred=summary["deferred"], inserted=summary["inserted"], dry_run=dry_run)
    return summary
