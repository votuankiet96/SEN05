"""TradingView framing and finite one-symbol multi-series WebSocket fetches."""
# File này là lớp giao thức thấp nhất, nói chuyện trực tiếp với TradingView
# qua WebSocket thô: tự đóng gói/giải gói khung tin theo định dạng riêng
# của TradingView (~m~<len>~m~<json>), tự dựng phiên chart, tự parse candle
# từ payload JSON. Một lần "fetch" ở đây LUÔN hữu hạn (finite) — mở socket,
# xin đúng số nến cần, chờ tới khi chứng minh được đã nhận đủ (hoặc hết
# thời gian/hết lượt mở rộng), rồi đóng socket lại — khác hẳn kiểu socket
# streaming sống mãi. Toàn bộ module chỉ phục vụ MỘT symbol mỗi lần gọi,
# nhưng có thể gộp nhiều timeframe ("series") của symbol đó vào chung một
# kết nối để tiết kiệm số lần mở socket.
from __future__ import annotations
import gc, json, logging, math, secrets, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple
import websocket
from ..log import log_event
from .auth import USER_AGENT, ensure_authenticated
LOGGER = logging.getLogger(__name__)
# Tên method trong giao thức TradingView báo hiệu tin nhắn này MANG dữ
# liệu candle thật sự ("du" = data update, "timescale_update" = cập nhật
# theo mốc thời gian).
_DATA_MESSAGES = {"du", "timescale_update"}
# Tên method báo hiệu TradingView chủ động TỪ CHỐI yêu cầu (khác với việc
# không hiểu được phản hồi — đây là provider nói thẳng "không được").
_ERROR_MESSAGES = {"error", "critical_error", "series_error", "symbol_error"}
# _MAX_SERIES: số lượng timeframe tối đa gộp chung một socket cho một
# symbol — đúng bằng tổng số timeframe hệ thống hỗ trợ (M5..W), nên trong
# thực tế mỗi symbol chỉ cần đúng một kết nối để lấy hết mọi timeframe.
# _MAX_EXTENSION_ROUNDS: số lượt "xin thêm dữ liệu cũ hơn" tối đa cho một
# lần fetch, để chặn vòng lặp mở rộng chạy vô hạn nếu provider không hợp
# tác.
_MAX_SERIES, _MAX_EXTENSION_ROUNDS = 15, 3
# Bốn loại lỗi tách biệt theo đúng NGUYÊN NHÂN, để nơi gọi (fetch_candles_batch
# bên dưới, và pipeline.py ở lớp trên) có thể phản ứng khác nhau tùy loại:
class IncompleteFetchError(TimeoutError): """A requested series was not proven complete."""
# -> không chứng minh được đã nhận đủ dữ liệu yêu cầu (hết giờ, hết lượt
#    mở rộng, hoặc request_more_data không tiến triển).
class MalformedResponseError(RuntimeError): """A provider frame cannot be trusted."""
# -> khung tin/cấu trúc JSON không đúng như giao thức mong đợi, không thể
#    tin để xử lý tiếp (không cố đoán/khôi phục dữ liệu sai định dạng).
class InvalidCandleError(RuntimeError): """A provider bar cannot be represented."""
# -> cấu trúc tin nhắn thì đúng, nhưng GIÁ TRỊ của một candle cụ thể
#    không thể chuyển đổi được (không parse được số/thời gian).
class ProviderRequestError(RuntimeError): """The provider explicitly rejected a request."""
# -> TradingView chủ động gửi một trong các _ERROR_MESSAGES, tức từ chối
#    yêu cầu một cách tường minh (khác timeout, khác lỗi parse).
class FetchRequest(NamedTuple):
    # Mô tả đúng một series (một symbol + một timeframe) cần lấy.
    # bars: số nến xin lúc đầu. max_bars: trần tuyệt đối được phép xin
    # thêm qua các lượt mở rộng. oldest_required: nếu có, buộc phải chứng
    # minh đã lấy được nến cũ tới tận mốc thời gian này (dùng cho backfill
    # cần phủ đủ cửa sổ lịch sử; để None thì không đòi mở rộng gì cả, hợp
    # với các fetch kiểu live chỉ cần vài nến mới nhất).
    symbol: dict[str, Any]; timeframe: dict[str, Any]
    bars: int; max_bars: int
    oldest_required: datetime | None = None
class FetchResult(NamedTuple):
    # candles: danh sách nến đã chuẩn hóa, sắp theo thời gian tăng dần.
    # requested_bars: tổng số nến cuối cùng đã xin (sau mọi lượt mở rộng).
    # extension_rounds: đã cần bao nhiêu lượt mở rộng để đủ dữ liệu — dùng
    # để log/giám sát, số càng cao càng đáng chú ý về hiệu năng provider.
    candles: list[dict[str, Any]]; requested_bars: int; extension_rounds: int
def request_key(request: FetchRequest) -> tuple[int, str]: return int(request.symbol["symbol_id"]), str(request.timeframe["code"])
def frame_message(method: str, params: list[Any]) -> str:
    # Đóng gói một lệnh theo đúng định dạng khung tin riêng của
    # TradingView: "~m~<số byte của payload>~m~<payload JSON>". Đây không
    # phải khung WebSocket chuẩn — WS đã tự đóng khung tin nhắn của nó rồi
    # — mà là một lớp đóng khung THÊM của riêng TradingView bên trong nội
    # dung text của một khung WS, để gộp nhiều lệnh logic hoặc heartbeat
    # vào cùng luồng.
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"
def split_messages(raw: str | bytes) -> list[str]:
    """Split length-prefixed messages and bare heartbeat packets."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError as exc:
        raise MalformedResponseError("provider frame is not valid UTF-8") from exc
    # Provider đóng socket mà không gửi gì cả — đây là một sự kiện mạng
    # hợp lệ (không phải dữ liệu sai định dạng), nên coi là "chưa lấy đủ"
    # (IncompleteFetchError) thay vì "dữ liệu hỏng" (MalformedResponseError).
    if not text: raise IncompleteFetchError("provider closed the WebSocket connection")
    packets, position = [], 0
    while position < len(text):
        if text.startswith("~h~", position):
            # Gói heartbeat: chỉ có số thứ tự, không có payload JSON đi
            # kèm — giữ nguyên dạng thô, để _receive() echo lại y hệt.
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
    # Chuyển mảng thô của TradingView [time, open, high, low, close, volume]
    # thành đúng hình dạng candle chuẩn dùng xuyên suốt engine (khớp với
    # những gì pipeline.py::validate_candles() mong đợi nhận vào). Giá và
    # volume LUÔN parse qua Decimal (str(value) trước, không qua float) để
    # tránh sai số dấu phẩy động nhị phân trên dữ liệu giá tiền thật.
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
    # Kiểm tra nghiêm ngặt từng lớp cấu trúc JSON mong đợi — bất kỳ chỗ
    # nào không đúng hình dạng đều raise MalformedResponseError ngay, theo
    # đúng triết lý "không tin, không đoán" xuyên suốt codebase này.
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
    # Referer/User-Agent giả lập một tab trình duyệt thật — TradingView từ
    # chối kết nối không có các header này. Cookie chỉ gửi kèm nếu có
    # (phiên đã xác thực); không có cookie vẫn kết nối được nhưng ở dạng
    # guest (xem GUEST_TOKEN trong configuration.py).
    values = ["Referer: https://www.tradingview.com/", f"User-Agent: {USER_AGENT}"]
    return values + ([f"Cookie: {cookie}"] if cookie else [])
def _validate_batch(requests: list[FetchRequest], cap: int) -> None:
    # Kiểm tra hình dạng của cả batch TRƯỚC KHI mở socket, để một batch sai
    # (rỗng, quá nhiều series, khác symbol, trùng timeframe, vượt trần bar)
    # bị từ chối ngay tại chỗ gọi thay vì lãng phí một lần kết nối thật.
    if not requests or len(requests) > _MAX_SERIES:
        raise ValueError(f"batch must contain 1..{_MAX_SERIES} series")
    # Một socket/batch chỉ phục vụ đúng MỘT symbol — kiểm tra bằng cách
    # gom (exchange, symbol) của mọi request vào set, phải còn đúng 1 phần
    # tử.
    symbols = {(r.symbol["exchange"], r.symbol["symbol"]) for r in requests}
    keys = [request_key(request) for request in requests]
    if len(symbols) != 1 or len(keys) != len(set(keys)):
        raise ValueError("batch requires unique timeframes for exactly one symbol")
    if any(not 0 < r.bars <= r.max_bars <= cap for r in requests):
        raise ValueError("series request exceeds its bounded bar range")
    if sum(r.bars for r in requests) > cap:
        raise ValueError("batch initial request exceeds aggregate bar cap")
def _register(socket: Any, tv: dict[str, Any], request: FetchRequest, index: int) -> dict:
    # Gửi đúng trình tự 4 lệnh giao thức TradingView để mở một phiên chart
    # và bắt đầu một series mới: tạo phiên -> đặt timezone -> resolve
    # symbol thành alias -> tạo series với số nến ban đầu. `index` (vị trí
    # trong batch) dùng để sinh alias/series id duy nhất trong cùng một
    # socket (sym1/s1, sym2/s2...).
    session, alias, series = _session_id("cs"), f"sym{index}", f"s{index}"
    # Dấu "=" ở đầu là quy ước riêng của TradingView, báo đây là một mô tả
    # resolve thô (JSON), không phải một symbol ID đã resolve sẵn.
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
    # State theo dõi tiến độ của riêng series này trong suốt vòng đời của
    # lần fetch: candles tích lũy theo timestamp (tự khử trùng lặp nếu
    # cùng một mốc thời gian được cập nhật nhiều lần), requested/rounds
    # phục vụ logic mở rộng và log.
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
    # Bộ định tuyến trung tâm cho mọi tin nhắn đã giải mã: quyết định đây
    # là lỗi, là dữ liệu candle, là tín hiệu "series đã xong", hay là loại
    # tin nhắn khác mà chương trình này không quan tâm (bị bỏ qua có chủ
    # đích, không raise lỗi — vì TradingView còn gửi nhiều loại tin khác
    # không liên quan tới candle, ví dụ quote realtime).
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
        # Tín hiệu TradingView báo "series này đã gửi hết dữ liệu cho lượt
        # này" — trả về key (session, series_id) để _receive() gỡ khỏi
        # tập pending.
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
    # Vòng lặp nhận dữ liệu cho một lượt: chạy tới khi hết pending hoặc hết
    # deadline (mốc thời gian tuyệt đối, tính theo time.monotonic()).
    received_bytes = 0
    while pending and time.monotonic() < deadline:
        try:
            raw = socket.recv()
        except websocket.WebSocketTimeoutException:
            # Timeout ngắn (1 giây, set ở _fetch_batch_once) của riêng một
            # lần recv() KHÔNG phải lỗi thật — chỉ là "chưa có gì mới",
            # vòng lặp quay lại kiểm tra deadline tổng. Cách này giữ vòng
            # lặp phản hồi nhanh thay vì bị block cứng vào một recv() dài.
            continue
        received_bytes += len(raw)
        for packet in split_messages(raw):
            if packet.startswith("~h~"):
                # Phải echo lại đúng gói heartbeat để giữ phiên sống —
                # không trả lời thì TradingView sẽ coi client là "chết" và
                # ngắt kết nối.
                socket.send(f"~m~{len(packet)}~m~{packet}")
            else:
                completed = _route(_decode(packet), lookup)
                if completed:
                    pending.discard(completed)
    if pending:
        # Vẫn còn series chưa xong sau khi hết deadline: KHÔNG được coi
        # như đã lấy đủ — báo lỗi rõ ràng để lớp gọi bên trên (require_coverage
        # ở pipeline.py) không bao giờ âm thầm nhận dữ liệu thiếu.
        names = ",".join(lookup[key]["request"].timeframe["code"]
                         for key in sorted(pending))
        raise IncompleteFetchError(f"provider response incomplete for series={names}")
    return received_bytes
def _extension_count(state: dict, aggregate_left: int) -> int:
    # Tính xem series này còn cần xin thêm bao nhiêu nến CŨ HƠN để chạm
    # tới oldest_required — dùng cho các lượt "request_more_data" bên dưới.
    request, candles = state["request"], state["candles"]
    if request.oldest_required is None:
        # Không đòi phủ lịch sử sâu (ví dụ fetch kiểu live) -> không bao
        # giờ cần mở rộng.
        return 0
    earliest = min(candles) if candles else None
    if earliest is not None and earliest <= request.oldest_required:
        # Đã chạm hoặc vượt mốc yêu cầu -> coi như đã đủ, không cần thêm.
        return 0
    # Ngân sách còn lại bị giới hạn bởi CẢ HAI: trần riêng của series này
    # (max_bars trừ đi số đã xin) LẪN ngân sách chung còn lại của cả batch
    # (aggregate_left, do caller truyền vào dựa trên cap tổng).
    available = min(request.max_bars - state["requested"], aggregate_left)
    if available <= 0:
        # Hết ngân sách mà vẫn chưa đủ phủ -> fail-closed ngay, không âm
        # thầm trả về dữ liệu thiếu.
        raise IncompleteFetchError(
            f"coverage cap reached for {request.timeframe['code']}")
    if earliest is None:
        # Chưa có nến nào cả (trường hợp hiếm, lượt đầu không trả gì) ->
        # không có cơ sở ước lượng, xin trọn phần ngân sách còn lại.
        return available
    # Ước lượng số nến cần thêm dựa trên khoảng cách thời gian còn thiếu
    # chia cho độ dài một nến, làm tròn lên và cộng dư 1 nến — tránh xin
    # thừa toàn bộ ngân sách mỗi lượt khi chỉ còn thiếu một khoảng nhỏ.
    seconds = int(request.timeframe["minutes"]) * 60
    gap = math.ceil((earliest - request.oldest_required).total_seconds() / seconds) + 1
    return min(available, max(1, gap))
def _fetch_batch_once(
    tv: dict[str, Any], requests: list[FetchRequest], cap: int,
) -> tuple[dict[tuple[int, str], FetchResult], dict[str, Any]]:
    # Toàn bộ vòng đời MỘT lần thử fetch: mở socket -> xác thực phiên ->
    # đăng ký từng series -> nhận lượt đầu -> mở rộng tối đa
    # _MAX_EXTENSION_ROUNDS lượt cho series nào còn thiếu -> đóng socket.
    _validate_batch(requests, cap)
    opened = time.monotonic()
    socket = websocket.create_connection(
        tv["websocket_url"], header=_headers(tv.get("cookie", "")),
        origin="https://www.tradingview.com", timeout=float(tv["timeout_seconds"]))
    connected, received = time.monotonic(), 0
    try:
        # Timeout ngắn cho từng lần recv() riêng lẻ, để vòng lặp ở
        # _receive() luôn kiểm tra lại được deadline tổng thay vì bị chặn
        # cứng.
        socket.settimeout(1.0)
        # set_auth_token PHẢI gửi trước mọi lệnh dựng phiên/series khác,
        # đúng thứ tự bắt buộc của giao thức.
        socket.send(frame_message("set_auth_token", [tv["auth_token"]]))
        states = [_register(socket, tv, request, index)
                  for index, request in enumerate(requests, 1)]
        lookup = {(s["session"], s["series"]): s for s in states}
        # Deadline tính cho TOÀN BỘ lần fetch (không phải riêng từng lượt),
        # dựa trên tradingview.timeout_seconds.
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
                # Ghi lại mốc "trước khi xin thêm" để sau lượt nhận có thể
                # kiểm tra provider có thực sự trả về dữ liệu CŨ HƠN hay
                # không.
                before[key] = min(state["candles"]) if state["candles"] else None
                state["requested"] += count
                state["rounds"] += 1
                total += count
                pending.add(key)
                socket.send(frame_message(
                    "request_more_data", [*key, int(count)]))
            if not pending:
                # Không series nào cần mở rộng nữa -> đã đủ, thoát sớm.
                break
            received += _receive(socket, lookup, pending, deadline)
            for key, prior in before.items():
                candles = lookup[key]["candles"]
                current = min(candles) if candles else None
                if current is None or (prior is not None and current >= prior):
                    # Lưới an toàn chống "đứng yên": nếu request_more_data
                    # không thực sự đẩy mốc cũ nhất lùi thêm, coi như
                    # provider không tiến triển thật, fail ngay ở lượt đầu
                    # tiên phát hiện thay vì lặng lẽ chạy hết cả 3 lượt rồi
                    # mới báo lỗi.
                    raise IncompleteFetchError("request_more_data made no older progress")
        if any(_extension_count(state, cap) for state in states):
            # Sau khi hết lượt mở rộng (hoặc dừng sớm) mà vẫn còn series
            # chưa đủ phủ -> đây là thất bại dứt điểm, không còn cách nào
            # khác trong ngân sách cho phép.
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
        # Luôn cố đóng socket; lỗi lúc đóng không được che mất lỗi/kết quả
        # thật sự của lần fetch, và cũng không có gì để xử lý thêm nếu đóng
        # thất bại.
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
                # Chủ động gọi gc sau mỗi lần thử: tiến trình này chạy nền
                # 24/7, liên tục mở/đóng socket và tạo payload candle lớn —
                # chủ động dọn rác mỗi vòng để tránh bộ nhớ phình dần theo
                # thời gian thay vì chỉ trông chờ chu kỳ GC tự động.
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
            # Đoán theo từ khóa trong nội dung lỗi xem có phải do phiên
            # xác thực hết hạn/bị từ chối hay không — TradingView không
            # luôn trả về một loại exception riêng cho lỗi auth, nên phải
            # dò chữ trong message.
            refresh = any(word in text for word in (
                "auth", "permission", "forbidden", "unauthorized"))
            # TradingView đã từ chối tường minh (không phải do auth) ->
            # thử lại cũng vô ích, dừng ngay.
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
    # bars == max_bars: đường tắt dùng cho fetch đơn lẻ, không cho phép mở
    # rộng thêm (không đặt oldest_required) — muốn phủ lịch sử sâu và có
    # kiểm soát mở rộng thì phải gọi thẳng fetch_candles_batch với
    # FetchRequest đầy đủ (xem backfill.py).
    request = FetchRequest(symbol, timeframe, int(bars), int(bars))
    return fetch_candles_batch(config, [request])[request_key(request)].candles
