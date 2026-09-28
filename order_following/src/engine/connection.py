"""Kết nối tới cTrader Open API: TCP+TLS, đóng gói/mở gói message, xác thực, heartbeat, reconnect.

Không biết gì về order, symbol, hay signal — chỉ cung cấp send()/receive() cho engine khác dùng.
Framing xác nhận từ chính source code SDK Spotware (OpenApiPy/tcpProtocol.py dùng Twisted
Int32StringReceiver): 4 byte độ dài (big-endian, uint32) + payload là ProtoMessage đã serialize.
"""

import json
import logging
import os
import random
import socket
import ssl
import struct
import sys
import time
import uuid
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

_PROTO_DIR = os.path.join(os.path.dirname(__file__), "proto", "generated")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import OpenApiCommonMessages_pb2 as common_messages  # noqa: E402
import OpenApiMessages_pb2 as messages  # noqa: E402

from engine.log import log_event

_LOGGER = logging.getLogger(__name__)

_LENGTH_PREFIX = struct.Struct(">I")
# Giới hạn CHÍNH THỨC: 50 request/giây cho dữ liệu non-historical, tính THEO CONNECTION (không phải
# theo account) — help.ctrader.com/open-api/. OF không gửi request historical nào nên chỉ cần mốc này.
_MAX_REQUESTS_PER_SECOND = 50
# Access token sống ~30 ngày; refresh sớm trước hạn để không bị hết hạn giữa phiên chạy 24/7.
_TOKEN_REFRESH_MARGIN_SECONDS = 7 * 24 * 3600

_RESPONSE_CLASSES = [
    messages.ProtoOAApplicationAuthRes,
    messages.ProtoOAAccountAuthRes,
    messages.ProtoOAExecutionEvent,
    messages.ProtoOAOrderErrorEvent,
    messages.ProtoOAErrorRes,
    messages.ProtoOASymbolsListRes,
    messages.ProtoOASymbolByIdRes,
    messages.ProtoOAReconcileRes,
    messages.ProtoOATraderRes,
    messages.ProtoOASymbolChangedEvent,
    messages.ProtoOASymbolsForConversionRes,
    messages.ProtoOASpotEvent,
    messages.ProtoOAGetPositionUnrealizedPnLRes,
    messages.ProtoOAOrderDetailsRes,
    messages.ProtoOADealListByPositionIdRes,
    common_messages.ProtoErrorRes,
    common_messages.ProtoHeartbeatEvent,
]
_PAYLOAD_TYPE_TO_CLASS = {cls().payloadType: cls for cls in _RESPONSE_CLASSES}


@dataclass
class IncomingMessage:
    payload_type: int
    message: object  # đã parse đúng class, hoặc None nếu payload_type lạ (chưa biết/chưa cần)
    raw_payload: bytes
    client_msg_id: Optional[str]  # echo lại từ envelope — cách khớp response với request đã gửi


class AuthError(Exception):
    pass


class Connection:
    def __init__(self, host: str, port: int, heartbeat_seconds: int, token_file: str,
                 oauth_token_url: str, client_id: str, client_secret: str):
        self._host = host
        self._port = port
        self._heartbeat_seconds = heartbeat_seconds
        self._token_file = token_file
        self._oauth_token_url = oauth_token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._sock: Optional[ssl.SSLSocket] = None
        self._recv_buffer = b""
        self._last_sent_at = 0.0
        # Message đọc được trong lúc wait_for() đang chờ thứ khác — KHÔNG được vứt đi (sẽ mất
        # ORDER_FILLED/CANCELLED vĩnh viễn). Giữ lại đây, receive() trả ra trước khi đọc socket,
        # nên vòng lặp chính vẫn xử lý được chúng ở lượt sau.
        self._deferred: list = []
        self._send_times: deque = deque()  # mốc thời gian các request đã gửi trong 1 giây gần nhất
        # Giá spot mới nhất theo symbolId — cập nhật ở ĐÚNG 1 chỗ (_extract_one_message), bất kể
        # lúc đó code đang wait_for() loại message nào khác. Lý do bắt buộc: nếu chỉ cập nhật giá
        # trong nhánh "đây đúng là cái tôi đang chờ", một ProtoOASpotEvent tới đúng lúc sizing.py/
        # orders.py đang bận wait_for() một phản hồi khác sẽ bị coi là "không khớp" và bị bỏ qua —
        # tỷ giá dùng để tính risk sẽ cũ hơn thực tế đúng vào những lúc bận rộn nhất (lúc xử lý lệnh).
        self._latest_spot_raw: dict[int, tuple[Optional[int], Optional[int]]] = {}
        # Chỉ ĐẾM spot event, không ghi 1 dòng log/tick: subscribe spot cho conversion leg là cách
        # Spotware hướng dẫn (help.ctrader.com/open-api/symbol-rate-conversion/) và luồng tick đẩy
        # theo MỖI cập nhật giá của LP — log từng tick làm RotatingFileHandler (10MB x 5) xoay mất
        # lịch sử giao dịch chỉ sau vài giờ.
        self._spot_event_count = 0

    # --- Kết nối vật lý ---

    def connect(self) -> None:
        raw_sock = socket.create_connection((self._host, self._port), timeout=10)
        context = ssl.create_default_context()
        self._sock = context.wrap_socket(raw_sock, server_hostname=self._host)
        # Timeout ngắn để receive() không chặn vô hạn — vòng lặp chính còn phải kiểm tra heartbeat.
        self._sock.settimeout(1.0)
        self._recv_buffer = b""
        log_event(_LOGGER, "INFO", "CONNECTED", "NONE", component="connection", host=self._host, port=self._port)

    def connect_with_backoff(self, max_backoff_seconds: int = 60) -> None:
        """Không có tài liệu chính thức nào của Spotware về thuật toán backoff — đây là thiết kế
        riêng của OF: exponential backoff + jitter, không giới hạn số lần thử."""
        backoff = 1.0
        while True:
            try:
                self.connect()
                return
            except OSError as exc:
                log_event(_LOGGER, "WARNING", "RECONNECTING", "LOW", component="connection",
                          error=exc, backoff_seconds=round(backoff, 1))
                time.sleep(backoff + random.uniform(0, 1))
                backoff = min(backoff * 2, max_backoff_seconds)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            log_event(_LOGGER, "INFO", "DISCONNECTED", "NONE", component="connection")

    # --- Gửi/nhận message có framing ---

    def send(self, message, client_msg_id: Optional[str] = None) -> str:
        """Trả về clientMsgId đã dùng — caller giữ lại để khớp đúng phản hồi của CHÍNH request này,
        thay vì nhận nhầm 1 message cùng kiểu của việc khác."""
        if client_msg_id is None:
            client_msg_id = uuid.uuid4().hex
        self._throttle()
        envelope = common_messages.ProtoMessage()
        envelope.payloadType = int(message.payloadType)
        envelope.payload = message.SerializeToString()
        envelope.clientMsgId = client_msg_id
        data = envelope.SerializeToString()
        self._sock.sendall(_LENGTH_PREFIX.pack(len(data)) + data)
        self._last_sent_at = time.monotonic()
        log_event(_LOGGER, "INFO", "MESSAGE_SENT", "NONE", component="connection",
                  message_type=type(message).__name__, size_bytes=len(data))
        return client_msg_id

    def _throttle(self) -> None:
        """Giữ dưới giới hạn 50 request/giây của cTrader. Vượt sẽ nhận REQUEST_FREQUENCY_EXCEEDED —
        tự chặn ở đây rẻ hơn nhiều so với để server từ chối 1 lệnh thật."""
        now = time.monotonic()
        while self._send_times and now - self._send_times[0] >= 1.0:
            self._send_times.popleft()
        if len(self._send_times) >= _MAX_REQUESTS_PER_SECOND:
            time.sleep(1.0 - (now - self._send_times[0]))
            self._send_times.popleft()
        self._send_times.append(time.monotonic())

    def receive(self) -> Optional[IncomingMessage]:
        """Trả về 1 message hoàn chỉnh, hoặc None nếu chưa có gì mới (None KHÔNG phải lỗi).

        Thứ tự ưu tiên: message đã hoãn lại từ wait_for() -> message đã nằm sẵn trong buffer ->
        mới đọc socket. Chỉ chờ socket khi buffer thật sự chưa đủ 1 message, nếu không mỗi message
        đều phải tốn trọn 1 giây timeout dù dữ liệu đã nằm sẵn trong bộ nhớ.
        """
        if self._deferred:
            return self._deferred.pop(0)
        buffered = self._extract_one_message()
        if buffered is not None:
            return buffered
        self._pump_socket()
        return self._extract_one_message()

    def _pump_socket(self) -> None:
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout:
            return
        if not chunk:
            raise ConnectionError("cTrader closed the connection")
        self._recv_buffer += chunk

    def _extract_one_message(self) -> Optional[IncomingMessage]:
        if len(self._recv_buffer) < _LENGTH_PREFIX.size:
            return None
        (length,) = _LENGTH_PREFIX.unpack(self._recv_buffer[:_LENGTH_PREFIX.size])
        total = _LENGTH_PREFIX.size + length
        if len(self._recv_buffer) < total:
            return None
        body = self._recv_buffer[_LENGTH_PREFIX.size:total]
        self._recv_buffer = self._recv_buffer[total:]

        envelope = common_messages.ProtoMessage()
        envelope.ParseFromString(body)
        cls = _PAYLOAD_TYPE_TO_CLASS.get(envelope.payloadType)
        parsed = None
        if cls is not None:
            parsed = cls()
            parsed.ParseFromString(envelope.payload)
            if isinstance(parsed, messages.ProtoOASpotEvent):
                # Ghi ngay tại đây, KHÔNG đợi caller xử lý message — xem lý do ở __init__.
                # bid/ask đều OPTIONAL — tài liệu: "you may not necessarily see ProtoOASpotEvent
                # messages where both are specified" (help.ctrader.com/open-api/symbol-data/), code
                # mẫu Spotware kiểm HasBid/HasAsk. Chỉ cập nhật field CÓ MẶT, giữ nguyên phía còn lại
                # — ghi thẳng (bid, ask) sẽ biến field vắng thành 0 (mặc định proto) và làm tỷ giá
                # dùng tính lot bằng 0 / chia 0.
                prev_bid, prev_ask = self._latest_spot_raw.get(parsed.symbolId, (None, None))
                self._latest_spot_raw[parsed.symbolId] = (
                    parsed.bid if parsed.HasField("bid") else prev_bid,
                    parsed.ask if parsed.HasField("ask") else prev_ask,
                )
                self._spot_event_count += 1
        if not isinstance(parsed, messages.ProtoOASpotEvent):
            log_event(_LOGGER, "INFO", "MESSAGE_RECEIVED", "NONE", component="connection",
                      message_type=type(parsed).__name__ if parsed is not None else "unknown",
                      payload_type=envelope.payloadType, size_bytes=len(body))
        return IncomingMessage(
            payload_type=envelope.payloadType,
            message=parsed,
            raw_payload=envelope.payload,
            client_msg_id=envelope.clientMsgId if envelope.HasField("clientMsgId") else None,
        )

    def get_latest_spot(self, symbol_id: int) -> Optional[Tuple[Optional[int], Optional[int]]]:
        """Giá (bid, ask) THÔ (chưa chia 1/100000) mới nhất từng nhận cho symbol này, hoặc None
        nếu chưa từng nhận event nào — luôn mới bất kể lúc nhận nó code đang bận wait_for() việc
        khác. Từng phần tử có thể là None nếu chưa từng nhận field đó (bid/ask đều optional)."""
        return self._latest_spot_raw.get(symbol_id)

    @property
    def spot_event_count(self) -> int:
        """Tổng số spot event đã nhận trên kết nối này (không log từng tick — xem __init__)."""
        return self._spot_event_count

    # --- Heartbeat ---

    def maybe_send_heartbeat(self) -> None:
        if time.monotonic() - self._last_sent_at >= self._heartbeat_seconds:
            self.send(common_messages.ProtoHeartbeatEvent())

    # --- Xác thực ---
    # Luồng OAuth2 lấy access_token/refresh_token lần đầu (qua trình duyệt, có thể có 2FA) là bước
    # setup thủ công 1 lần, KHÔNG nằm trong file này — connection.py chỉ dùng token đã có sẵn.

    def authenticate(self, ctid_trader_account_id: int) -> None:
        access_token = self._load_tokens()["access_token"]

        app_auth = messages.ProtoOAApplicationAuthReq()
        app_auth.clientId = self._client_id
        app_auth.clientSecret = self._client_secret
        self.send(app_auth)
        response = self.wait_for(messages.ProtoOAApplicationAuthRes, common_messages.ProtoErrorRes)
        if isinstance(response, common_messages.ProtoErrorRes):
            raise AuthError(f"App auth failed: {response.errorCode} {response.description}")
        log_event(_LOGGER, "INFO", "AUTH_APP_OK", "NONE", component="connection")

        account_auth = messages.ProtoOAAccountAuthReq()
        account_auth.ctidTraderAccountId = ctid_trader_account_id
        account_auth.accessToken = access_token
        self.send(account_auth)
        response = self.wait_for(
            messages.ProtoOAAccountAuthRes, common_messages.ProtoErrorRes, messages.ProtoOAErrorRes
        )
        if isinstance(response, (common_messages.ProtoErrorRes, messages.ProtoOAErrorRes)):
            raise AuthError(f"Account auth failed: {response.errorCode} {response.description}")
        log_event(_LOGGER, "INFO", "AUTH_ACCOUNT_OK", "NONE", component="connection",
                  ctid_trader_account_id=ctid_trader_account_id)

    def refresh_access_token_if_expiring(self) -> bool:
        """Refresh TRƯỚC khi token hết hạn thay vì đợi lỗi giữa phiên. Trả True nếu đã refresh.

        Chỉ chạy khi `expires_at` có trong file token (được ghi từ lần refresh trước); token lấy
        lần đầu bằng tay qua trình duyệt có thể chưa có field này — khi đó bỏ qua, đường phản ứng
        (AuthError -> refresh -> auth lại) vẫn là lưới an toàn.
        """
        expires_at = self._load_tokens().get("expires_at")
        if expires_at is None or time.time() < float(expires_at) - _TOKEN_REFRESH_MARGIN_SECONDS:
            return False
        self.refresh_access_token()
        return True

    def refresh_access_token(self) -> None:
        """Chỉ 1 lệnh gọi HTTP đơn giản — không phải luồng OAuth2 đầy đủ qua trình duyệt."""
        tokens = self._load_tokens()
        params = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        })
        url = f"{self._oauth_token_url}?{params}"
        with urllib.request.urlopen(url, timeout=10) as response:
            new_tokens = json.loads(response.read().decode("utf-8"))
        self._save_tokens({
            "access_token": new_tokens["accessToken"],
            "refresh_token": new_tokens["refreshToken"],
            # Refresh token dùng 1 lần (mỗi lần refresh phát hành cặp mới, vô hiệu cặp cũ) nên phải
            # ghi đè ngay; kèm hạn dùng để lần sau biết khi nào cần refresh trước.
            "expires_at": time.time() + float(new_tokens["expiresIn"]),
        })
        log_event(_LOGGER, "INFO", "TOKEN_REFRESHED", "NONE", component="connection",
                  expires_in_seconds=int(new_tokens["expiresIn"]))

    def _load_tokens(self) -> dict:
        with open(self._token_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_tokens(self, tokens: dict) -> None:
        directory = os.path.dirname(self._token_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._token_file, "w", encoding="utf-8") as f:
            json.dump(tokens, f)

    def wait_for(self, *expected_classes, predicate=None, timeout_seconds: float = 15.0):
        """Chờ đúng response của request vừa gửi.

        `predicate(IncomingMessage) -> bool` để lọc thêm ngoài kiểu message — bắt buộc dùng khi
        nhiều message CÙNG KIỂU có thể bay về (vd ProtoOAExecutionEvent của lệnh cũ khớp đúng lúc
        đang chờ ACCEPTED của lệnh mới); chỉ lọc theo kiểu sẽ nhận nhầm.

        Mọi message không khớp đều được ĐẨY LẠI vào hàng đợi (không vứt) để vòng lặp chính vẫn xử
        lý được — nếu vứt, một ORDER_FILLED bay về đúng lúc đây đang chờ ProtoOATraderRes sẽ mất
        vĩnh viễn, state.py và exposure.py không bao giờ biết lệnh đã khớp.
        """
        deferred = []
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                incoming = self.receive()
                if incoming is None:
                    continue
                if isinstance(incoming.message, expected_classes) and (
                    predicate is None or predicate(incoming)
                ):
                    return incoming.message
                deferred.append(incoming)
            raise TimeoutError(f"Expected response not received within {timeout_seconds}s")
        finally:
            self._deferred.extend(deferred)
