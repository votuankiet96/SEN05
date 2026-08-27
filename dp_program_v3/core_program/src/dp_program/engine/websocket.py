"""TradingView framing and finite one-symbol multi-series WebSocket fetches."""
# File này nói chuyện trực tiếp với WebSocket TradingView.
# Một lần fetch mở một socket cho một symbol, có thể gộp nhiều timeframe,
# nhận đủ dữ liệu cần lấy rồi đóng socket. Nếu thiếu/sai format thì báo lỗi.
from __future__ import annotations
import gc, json, logging, math, secrets, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple
import websocket
from ..log import log_event
from .auth import USER_AGENT, ensure_authenticated
LOGGER = logging.getLogger(__name__)
# Message có dữ liệu candle thật.
_DATA_MESSAGES = {"du", "timescale_update"}
# Message báo TradingView từ chối request.
_ERROR_MESSAGES = {"error", "critical_error", "series_error", "symbol_error"}
# Giới hạn số series trên một socket và số lượt xin thêm dữ liệu cũ hơn.
_MAX_SERIES, _MAX_EXTENSION_ROUNDS = 15, 3
# Các loại lỗi được tách theo nguyên nhân để lớp trên xử lý đúng.
class IncompleteFetchError(TimeoutError): """A requested series was not proven complete."""
# Chưa chứng minh được đã nhận đủ dữ liệu yêu cầu.
class MalformedResponseError(RuntimeError): """A provider frame cannot be trusted."""
# Frame/JSON sai cấu trúc, không thể tin để xử lý tiếp.
class InvalidCandleError(RuntimeError): """A provider bar cannot be represented."""
# Candle có giá trị không parse được.
class ProviderRequestError(RuntimeError): """The provider explicitly rejected a request."""
# TradingView chủ động từ chối request.
class FetchRequest(NamedTuple):
    # Một request cho một symbol/timeframe.
    # oldest_required dùng khi backfill cần chắc chắn đã kéo đủ về quá khứ.
    symbol: dict[str, Any]; timeframe: dict[str, Any]
    bars: int; max_bars: int
    oldest_required: datetime | None = None
class FetchResult(NamedTuple):
    # Kết quả đã chuẩn hóa của một series sau khi fetch xong.
    candles: list[dict[str, Any]]; requested_bars: int; extension_rounds: int
def request_key(request: FetchRequest) -> tuple[int, str]: return int(request.symbol["symbol_id"]), str(request.timeframe["code"])
def frame_message(method: str, params: list[Any]) -> str:
    # TradingView bọc JSON bằng khung riêng: ~m~length~m~payload.
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"
def split_messages(raw: str | bytes) -> list[str]:
    """Split length-prefixed messages and bare heartbeat packets."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError as exc:
        raise MalformedResponseError("provider frame is not valid UTF-8") from exc
    # Socket đóng mà không có dữ liệu nghĩa là chưa lấy đủ.
    if not text: raise IncompleteFetchError("provider closed the WebSocket connection")
    packets, position = [], 0
    while position < len(text):
        if text.startswith("~h~", position):
            # Heartbeat không có JSON; giữ nguyên để echo lại cho provider.
            start, position = position, position + 3
            digit_start = position
            while position < len(text) and text[position].isdigit():
                position += 1
            if position == digit_start: raise MalformedResponseError("provider heartbeat has no sequence")
            packets.append(text[start:position])
            continue
        if not text.startswith("~m~", position): raise MalformedResponseError("provider frame has an unknown prefix")
        position += 3
        separator = text.find("~m~", position)
        if separator < 0: raise MalformedResponseError("provider frame has no length separator")
        try:
            length = int(text[position:separator])
        except ValueError as exc:
            raise MalformedResponseError("provider frame length is invalid") from exc
        position = separator + 3
        if length < 0 or position + length > len(text): raise MalformedResponseError("provider frame payload is truncated")
        packets.append(text[position:position + length])
        position += length
    return packets
def normalize_candle(values: list[Any], symbol: dict[str, Any],
                     timeframe: dict[str, Any]) -> dict[str, Any]:
    # Đổi candle thô của TradingView về dict chuẩn của engine.
    # Giá/volume dùng Decimal để tránh sai số float trên dữ liệu giá.
    if len(values) < 6: raise InvalidCandleError("provider candle has fewer than six fields")
    try:
        timestamp = datetime.fromtimestamp(float(values[0]), tz=timezone.utc)
        prices = [Decimal(str(value)) for value in values[1:5]]
        volume = None if values[5] is None else Decimal(str(values[5]))
    except (InvalidOperation, TypeError, ValueError, OSError) as exc:
        raise InvalidCandleError("provider candle contains invalid values") from exc
    return {"symbol_id": int(symbol["symbol_id"]), "symbol": symbol["symbol"],
        "exchange": symbol["exchange"], "timeframe": timeframe["code"],
        "timestamp": timestamp, "open": prices[0], "high": prices[1],
        "low": prices[2], "close": prices[3], "volume": volume}
def parse_series_message(message: str | dict[str, Any], symbol: dict[str, Any],
                         timeframe: dict[str, Any], series_id: str = "s1"
                         ) -> list[dict[str, Any]]:
    """Extract one selected series from a provider data message."""
    # Đọc đúng series cần lấy. Sai format thì báo lỗi, không đoán dữ liệu.
    try:
        payload = json.loads(message) if isinstance(message, str) else message
    except json.JSONDecodeError as exc:
        raise MalformedResponseError("provider data message is invalid JSON") from exc
    if not isinstance(payload, dict): raise MalformedResponseError("provider message is not an object")
    if payload.get("m") not in _DATA_MESSAGES: return []
    params = payload.get("p")
    if not isinstance(params, list) or len(params) < 2 or not isinstance(params[1], dict):
        raise MalformedResponseError("provider data message has invalid parameters")
    if series_id not in params[1]: raise MalformedResponseError("provider data message omits the requested series")
    series = params[1][series_id]
    if not isinstance(series, dict): raise MalformedResponseError("provider series is not an object")
    bars = series.get("s")
    if not isinstance(bars, list): raise MalformedResponseError("provider candle collection is not a list")
    if any(not isinstance(bar, dict) for bar in bars):
        raise InvalidCandleError("provider series contains a non-object candle")
    return [normalize_candle(bar.get("v") or [], symbol, timeframe) for bar in bars]
def _session_id(prefix: str) -> str: return f"{prefix}_{secrets.token_hex(6)}"
def _headers(cookie: str) -> list[str]:
    # Header mô phỏng browser thật; cookie chỉ gắn khi có phiên đăng nhập.
    values = ["Referer: https://www.tradingview.com/", f"User-Agent: {USER_AGENT}"]
    return values + ([f"Cookie: {cookie}"] if cookie else [])
def _validate_batch(requests: list[FetchRequest], cap: int) -> None:
    # Kiểm batch trước khi mở socket để tránh lãng phí kết nối thật.
    if not requests or len(requests) > _MAX_SERIES:
        raise ValueError(f"batch must contain 1..{_MAX_SERIES} series")
    # Một socket chỉ phục vụ đúng một symbol, nhưng có thể nhiều timeframe.
    symbols = {(r.symbol["exchange"], r.symbol["symbol"]) for r in requests}
    keys = [request_key(request) for request in requests]
    if len(symbols) != 1 or len(keys) != len(set(keys)):
        raise ValueError("batch requires unique timeframes for exactly one symbol")
    if any(not 0 < r.bars <= r.max_bars <= cap for r in requests):
        raise ValueError("series request exceeds its bounded bar range")
    if sum(r.bars for r in requests) > cap:
        raise ValueError("batch initial request exceeds aggregate bar cap")
def _register(socket: Any, tv: dict[str, Any], request: FetchRequest, index: int) -> dict:
    # Đăng ký một series: tạo chart session, resolve symbol, rồi xin số nến đầu.
    session, alias, series = _session_id("cs"), f"sym{index}", f"s{index}"
    # Dấu "=" báo TradingView đây là mô tả symbol dạng JSON thô.
    resolved = "=" + json.dumps({
        "symbol": f"{request.symbol['exchange']}:{request.symbol['symbol']}",
        "adjustment": "splits"}, separators=(",", ":"))
    commands = (
        ("chart_create_session", [session, ""]),
        ("switch_timezone", [session, tv.get("timezone", "Etc/UTC")]),
        ("resolve_symbol", [session, alias, resolved]),
        ("create_series", [session, series, series, alias,
                           request.timeframe["interval"], int(request.bars), ""]),
    )
    for method, params in commands:
        socket.send(frame_message(method, params))
    # State lưu candle theo timestamp để tự khử trùng lặp trong lần fetch.
    return {"request": request, "session": session, "series": series,
            "candles": {}, "requested": int(request.bars), "rounds": 0}
def _decode(packet: str) -> dict[str, Any]:
    try:
        message = json.loads(packet)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError("provider framed packet is invalid JSON") from exc
    if not isinstance(message, dict):
        raise MalformedResponseError("provider packet is not an object")
    return message
def _route(message: dict[str, Any], lookup: dict[tuple[str, str], dict]) -> tuple | None:
    # Phân loại message: lỗi, dữ liệu candle, hoàn tất series, hoặc bỏ qua.
    method, params = message.get("m"), message.get("p")
    if method in _ERROR_MESSAGES:
        raise ProviderRequestError(f"TradingView error: {str(params)[:200]}")
    if method in _DATA_MESSAGES:
        if not isinstance(params, list) or len(params) < 2 or not isinstance(params[1], dict):
            raise MalformedResponseError("provider data message has invalid parameters")
        session, values = params[0], params[1]
        if not values:
            raise MalformedResponseError("provider data message contains no active series")
        for series_id, series in values.items():
            state = lookup.get((session, series_id))
            if state is None:
                raise MalformedResponseError("provider data targets an unknown series")
            request = state["request"]
            selected = {"m": method, "p": [session, {series_id: series}]}
            for candle in parse_series_message(
                selected, request.symbol, request.timeframe, series_id):
                state["candles"][candle["timestamp"]] = candle
        return None
    if method == "series_completed":
        # TradingView báo series đã gửi xong lượt này.
        if not isinstance(params, list) or len(params) < 2:
            raise MalformedResponseError("provider completion has invalid parameters")
        key = (params[0], params[1])
        if key not in lookup:
            raise MalformedResponseError("provider completed an unknown series")
        return key
    return None
def _receive(
    socket: Any, lookup: dict[tuple[str, str], dict],
    pending: set[tuple[str, str]], deadline: float,
) -> int:
    # Nhận dữ liệu tới khi mọi series xong hoặc tới deadline tổng.
    received_bytes = 0
    while pending and time.monotonic() < deadline:
        try:
            raw = socket.recv()
        except websocket.WebSocketTimeoutException:
            # Timeout ngắn chỉ nghĩa là chưa có packet mới; tiếp tục chờ.
            continue
        received_bytes += len(raw)
        for packet in split_messages(raw):
            if packet.startswith("~h~"):
                # Echo heartbeat để TradingView giữ socket sống.
                socket.send(f"~m~{len(packet)}~m~{packet}")
            else:
                completed = _route(_decode(packet), lookup)
                if completed:
                    pending.discard(completed)
    if pending:
        # Còn series chưa xong thì báo lỗi, không trả dữ liệu thiếu.
        names = ",".join(lookup[key]["request"].timeframe["code"]
                         for key in sorted(pending))
        raise IncompleteFetchError(f"provider response incomplete for series={names}")
    return received_bytes
def _extension_count(state: dict, aggregate_left: int) -> int:
    # Tính cần xin thêm bao nhiêu nến cũ hơn để chạm oldest_required.
    request, candles = state["request"], state["candles"]
    if request.oldest_required is None:
        # Live không cần phủ sâu về quá khứ nên không mở rộng.
        return 0
    earliest = min(candles) if candles else None
    if earliest is not None and earliest <= request.oldest_required:
        # Đã chạm mốc yêu cầu thì đủ.
        return 0
    # Ngân sách còn lại bị giới hạn bởi cả series riêng và batch chung.
    available = min(request.max_bars - state["requested"], aggregate_left)
    if available <= 0:
        # Hết ngân sách mà vẫn thiếu thì báo lỗi, không trả thiếu.
        raise IncompleteFetchError(
            f"coverage cap reached for {request.timeframe['code']}")
    if earliest is None:
        # Chưa có nến thì chưa ước lượng được, xin hết phần còn lại.
        return available
    # Ước lượng số nến thiếu theo khoảng thời gian còn lại.
    seconds = int(request.timeframe["minutes"]) * 60
    gap = math.ceil((earliest - request.oldest_required).total_seconds() / seconds) + 1
    return min(available, max(1, gap))
def _fetch_batch_once(
    tv: dict[str, Any], requests: list[FetchRequest], cap: int,
) -> tuple[dict[tuple[int, str], FetchResult], dict[str, Any]]:
    # Một lần thử fetch: mở socket, auth, đăng ký series, nhận dữ liệu, đóng socket.
    _validate_batch(requests, cap)
    opened = time.monotonic()
    socket = websocket.create_connection(
        tv["websocket_url"], header=_headers(tv.get("cookie", "")),
        origin="https://www.tradingview.com", timeout=float(tv["timeout_seconds"]))
    connected, received = time.monotonic(), 0
    try:
        # Timeout ngắn để _receive() còn kiểm tra được deadline tổng.
        socket.settimeout(1.0)
        # Auth token phải gửi trước khi tạo chart session/series.
        socket.send(frame_message("set_auth_token", [tv["auth_token"]]))
        states = [_register(socket, tv, request, index)
                  for index, request in enumerate(requests, 1)]
        lookup = {(s["session"], s["series"]): s for s in states}
        # Deadline áp dụng cho toàn bộ lần fetch này.
        deadline = time.monotonic() + float(tv["timeout_seconds"])
        received += _receive(socket, lookup, set(lookup), deadline)
        for _round in range(_MAX_EXTENSION_ROUNDS):
            pending, before = set(), {}
            total = sum(state["requested"] for state in states)
            for state in states:
                count = _extension_count(state, cap - total)
                if not count:
                    continue
                key = (state["session"], state["series"])
                # Ghi mốc cũ nhất trước khi xin thêm để kiểm provider có tiến triển.
                before[key] = min(state["candles"]) if state["candles"] else None
                state["requested"] += count
                state["rounds"] += 1
                total += count
                pending.add(key)
                socket.send(frame_message(
                    "request_more_data", [*key, int(count)]))
            if not pending:
                # Không còn series cần mở rộng thì đã đủ.
                break
            received += _receive(socket, lookup, pending, deadline)
            for key, prior in before.items():
                candles = lookup[key]["candles"]
                current = min(candles) if candles else None
                if current is None or (prior is not None and current >= prior):
                    # Nếu xin thêm mà mốc cũ nhất không lùi lại thì dừng.
                    raise IncompleteFetchError("request_more_data made no older progress")
        if any(_extension_count(state, cap) for state in states):
            # Hết lượt mở rộng mà vẫn thiếu thì coi là fetch thất bại.
            raise IncompleteFetchError("coverage incomplete after bounded extension")
        results = {}
        for state in states:
            request, candles = state["request"], state["candles"]
            results[request_key(request)] = FetchResult(
                [candles[key] for key in sorted(candles)],
                state["requested"], state["rounds"])
        return results, {"connect_seconds": round(connected - opened, 3),
                         "received_bytes": received}
    finally:
        # Luôn cố đóng socket; lỗi lúc đóng không che lỗi chính.
        try:
            socket.close()
        except Exception:
            pass
def fetch_candles_batch(
    config: dict[str, Any], requests: list[FetchRequest],
) -> dict[tuple[int, str], FetchResult]:
    """Fetch one bounded same-symbol batch with a shared authenticated socket."""
    ensure_authenticated(config)
    tv, attempts = config["tradingview"], int(config["tradingview"]["retry_count"])
    cap, started, last_error = int(config["backfill"]["max_bars_per_request"]), time.monotonic(), None
    for attempt in range(1, attempts + 1):
        try:
            try:
                results, metrics = _fetch_batch_once(tv, requests, cap)
            finally:
                # Dọn rác sau mỗi lần thử để process 24/7 không phình bộ nhớ.
                gc.collect()
            log_event(
                LOGGER, logging.INFO, "FETCH_BATCH_COMPLETED", "NONE",
                component="websocket",
                symbol=f"{requests[0].symbol['exchange']}:{requests[0].symbol['symbol']}",
                series=len(requests),
                requested_bars=sum(result.requested_bars for result in results.values()),
                extension_rounds=sum(result.extension_rounds for result in results.values()),
                duration_seconds=round(time.monotonic() - started, 3), **metrics)
            return results
        except Exception as exc:
            last_error = exc
            text = str(exc).lower()
            # Một số lỗi auth chỉ nằm trong text exception, nên kiểm từ khóa.
            refresh = any(word in text for word in (
                "auth", "permission", "forbidden", "unauthorized"))
            # TradingView từ chối rõ ràng, không phải auth, thì retry cũng vô ích.
            rejected = isinstance(exc, ProviderRequestError) and not refresh
            if attempt >= attempts or rejected: break
            log_event(
                LOGGER, logging.WARNING, "FETCH_BATCH_RETRY", "LOW",
                component="websocket", series=len(requests), attempt=attempt,
                max_attempts=attempts, error_type=type(exc).__name__, error=exc,
                action="refresh auth and retry" if refresh else "bounded batch retry")
            if refresh:
                ensure_authenticated(config, force=True)
            time.sleep(float(tv.get("retry_delay_seconds", 1)))
    symbol = requests[0].symbol if requests else {"exchange": "?", "symbol": "?"}
    raise RuntimeError(
        f"TradingView batch failed for {symbol['exchange']}:{symbol['symbol']}: "
        f"{last_error}") from last_error
def fetch_candles(
    config: dict[str, Any], symbol: dict[str, Any],
    timeframe: dict[str, Any], bars: int,
) -> list[dict[str, Any]]:
    """Single-series facade over the canonical finite batch transport."""
    # Hàm tiện ích cho fetch một series; không tự mở rộng lịch sử.
    request = FetchRequest(symbol, timeframe, int(bars), int(bars))
    return fetch_candles_batch(config, [request])[request_key(request)].candles
