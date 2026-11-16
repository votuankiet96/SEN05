"""Cửa vào DUY NHẤT của dữ liệu ĐỌC VÀO từ Redis: mọi thứ đọc dữ liệu vào tiến trình nằm ở file này, và chỉ ở đây. Có HAI loại danh sách List–Hash, cùng một cơ chế nghe và đọc:
  * NẾN do Core/AEN (hoặc Redis DF của OG8) ghi, MỚI NHẤT ĐỨNG ĐẦU: đầu vào của các module (``DataEngine(modules=False)``) và của decision_engine (nến khung trigger để lấy giá và atr);
  * KẾT QUẢ MODULE do các module MA Cross và trend ghi vào db0 của DE, MỚI NHẤT ĐỨNG CUỐI: đầu vào của decision_engine (``DataEngine(modules=True)``).

Hai việc:
  1. NGHE:        keyspace notification báo cặp nào vừa bị ghi (``DataEngine.subscribe`` / ``poll_events``). Đăng ký ĐÚNG các cặp (symbol, khung[, module]) mà tiến trình cần, nên Redis lọc sẵn
                  và tiến trình không nhận cặp thừa;
  2. ĐỌC:         N mốc mới nhất của một cặp thành bảng tăng dần (OHLCV với nến; các cột của module với kết quả module), có bộ đệm gia tăng (``DataEngine.snapshot``): sự kiện mới chỉ đọc mốc mới
                  thay vì đọc lại cả cửa sổ; gặp bất thường thì đọc lại đầy đủ đúng như cách đọc cũ (``read_candles`` / ``read_module``).

Không biết chiến lược hay đầu ra nào: nhận mọi tham số (host, db, tiền tố, số mốc, danh sách cặp) từ configuration.py, không tự đọc config.

Hợp đồng nến (Core ghi): LIST ``{key_prefix}_{SYMBOL}_{TF}`` giữ các mốc, MỚI NHẤT ĐỨNG ĐẦU; HASH ``<key LIST>:{mốc}`` giữ open/high/low/close/volume (chuỗi thập phân). Mốc ``YYYY-MM-DD HH:MM:SS`` (UTC, rộng cố định
nên so sánh chuỗi đúng thứ tự thời gian). Core ghi mỗi nến bằng HMSET (Redis phát event ``hset``) rồi LPUSH/LTRIM, nên chỉ cần lớp 'K' + 'h' trong ``notify-keyspace-events``. CHANNEL là nơi duy nhất chứa
tên key vừa bị ghi (payload chỉ là tên lệnh).

Hợp đồng module (util.ModuleWriter ghi bằng ``redis_connector.write_list_hash``, RPUSH nên MỚI NHẤT ĐỨNG CUỐI): LIST ``{key_prefix}_{MODULE}_{SYMBOL}_{TF}``, vd ``L_MODSTR_MACROSS_BTCUSD_M10`` (chiến lược) hoặc ``L_MODTRE_MACD_BTCUSD_M30`` (yếu tố trend). MODULE là tên (bỏ gạch dưới, viết hoa) của một chiến lược đã đăng ký (CHỈ các nến CÓ
tín hiệu, mỗi nến một sự kiện: HASH có ``signal`` và ``reason``) hoặc của một yếu tố trend (mọi nến của khung của nó: HASH có ``trend_bias``). Cả hai HASH ``<key LIST>:{mốc}`` đều có ``close_time`` (giờ ĐÓNG nến, dùng để
ghép theo luật nến đóng), nên đọc bằng HGETALL. Chế độ module nghe MỘT mẫu cho mỗi module thay vì mỗi cặp một mẫu; cặp thừa trong cùng module bị ``_handle_message`` bỏ.

Tính đúng của bộ đệm: kết quả phải giống HỆT đọc đầy đủ. Vì vậy chỉ dùng đường gia tăng khi mọi mốc mới đều MỚI HƠN nến mới nhất đang giữ và mốc đang giữ nằm trong cửa sổ đầu LIST vừa hỏi;
event báo ghi một mốc không mới hơn nến mới nhất (ghi bù, ghi lại) hoặc thiếu HASH của một mốc mới thì đọc lại đầy đủ.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
import redis

from core_engine.src.redis_connector import KeyspaceListener, Link

LOGGER = logging.getLogger(__name__)
OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
# Chỉ bốn trường OHLC được chỉ báo đọc; HMGET vẫn xin `volume` để khung dữ liệu cùng hình dạng với khung OHLCV đọc từ SQL của strategy_lab (không module nào đọc nó).
_REQUIRED_FINITE_COLUMNS = ("open", "high", "low", "close")
_HASH_FIELDS = ("open", "high", "low", "close", "volume")
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HSET_PAYLOAD = "hset"
# Số mốc mới nhất hỏi mỗi lần cập nhật gia tăng: đủ cho vài nến mới liền nhau; mốc đang giữ không nằm trong cửa sổ này thì đọc lại đầy đủ.
HEAD_WINDOW = 16
_TIME_COLUMNS = ("bartime", "close_time")
_TEXT_COLUMNS = ("reason",)


class Pair(NamedTuple):
    """Một cặp (symbol, khung) của nến, hoặc (symbol, khung, module) của kết quả module trong db0 của DE. Một kiểu giá trị duy nhất thay cho các tham số rời: thứ tự các chuỗi này từng bị đảo ở nhiều chỗ.
    ``module`` rỗng = nến của nguồn; không rỗng = tên (token) của chiến lược hoặc yếu tố trend đã đăng ký ở config, vd ``MACROSS``, ``MACD``."""

    symbol: str
    timeframe: str
    module: str = ""


def pair_list_key(key_prefix: str, symbol: str, timeframe: str, module: str = "") -> str:
    """LIST các mốc của một cặp. Nến, mới nhất đứng đầu: ``{key_prefix}_{SYMBOL}_{TIMEFRAME}`` (SYMBOL trước, khung sau: ``L_CANDLE_US30_M10``). Kết quả module, mới nhất đứng cuối:
    ``{key_prefix}_{MODULE}_{SYMBOL}_{TIMEFRAME}``: ``L_MODSTR_MACROSS_BTCUSD_M10``. Phần nào rỗng (tiền tố, module) thì bỏ."""
    return "_".join(part for part in (key_prefix, module.upper(), symbol.upper(), timeframe.upper()) if part)


def candle_key(list_key: str, stamp: str) -> str:
    """HASH giữ một mốc: key LIST của cặp cộng ``":" + stamp``. Toàn bộ quy tắc đặt tên: một phép nối."""
    return f"{list_key}:{stamp}"


def parse_pair_key(key: str, key_prefix: str, *, modules: bool = False) -> Pair | None:
    """Trả về ``Pair`` nếu ``key`` là key LIST của một cặp, ngược lại ``None``. ``modules`` chọn khuôn tên: nến (``{prefix}_{SYMBOL}_{TF}``) hoặc module (``{prefix}_{MODULE}_{SYMBOL}_{TF}``).

    Key LIST không có dấu ':' nào; key HASH của một mốc có ít nhất một (mốc giờ cũng chứa ':'). Phép thử là SỰ HIỆN DIỆN của ':' chứ không phải số lượng.
    """
    prefix = f"{key_prefix}_" if key_prefix else ""
    if not key.startswith(prefix) or ":" in key:
        return None
    body = key[len(prefix):]
    if modules:
        parts = body.split("_")
        if len(parts) != 3 or not all(parts):
            return None
        return Pair(parts[1].upper(), parts[2].upper(), parts[0].upper())
    if "_" not in body:
        return None
    symbol, timeframe = body.rsplit("_", 1)
    if not symbol or not timeframe:
        return None
    return Pair(symbol.upper(), timeframe.upper())


def _glob_escape(text: str) -> str:
    """Thoát ký tự glob của Redis để một tên symbol lạ không biến thành mẫu."""
    return "".join(f"\\{ch}" if ch in "*?[]\\" else ch for ch in text)


def _read_stamps(client: redis.Redis, list_key: str, count: int, *, newest_first: bool) -> list[str]:
    """``count`` mốc mới nhất của LIST, luôn trả MỚI NHẤT TRƯỚC (nến: đầu LIST; module: cuối LIST, đảo lại)."""
    if newest_first:
        return client.lrange(list_key, 0, int(count) - 1)
    return list(reversed(client.lrange(list_key, -int(count), -1)))


def _fetch_rows(client: redis.Redis, list_key: str, stamps: list[str], *, modules: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """Một pipeline tới HASH của từng mốc (HMGET với nến, HGETALL với module). Trả (các dòng có dữ liệu, mốc mà HASH chưa có)."""
    pipe = client.pipeline(transaction=False)
    for stamp in stamps:
        if modules:
            pipe.hgetall(candle_key(list_key, stamp))
        else:
            pipe.hmget(candle_key(list_key, stamp), *_HASH_FIELDS)
    values = pipe.execute()
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for stamp, fields in zip(stamps, values, strict=True):
        if modules:
            if not fields:
                missing.append(str(stamp))
                continue
            row = dict(fields)
        else:
            if fields is None or all(field is None for field in fields):
                missing.append(str(stamp))
                continue
            row = dict(zip(_HASH_FIELDS, fields, strict=True))
        row["bartime"] = stamp
        rows.append(row)
    return rows, missing


def _frame_from_rows(rows: list[dict[str, Any]], *, symbol: str, timeframe: str) -> pd.DataFrame:
    """Dựng, kiểm và sắp tăng dần bảng OHLCV từ các dòng (thứ tự mới nhất trước như LIST). Giá trị sai thì raise ValueError sau khi ghi log chính các dòng thô."""
    frame = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    # Định dạng tường minh loại mọi giá trị không phải mốc của Core (thành NaT, bị bắt dưới) và cho datetime64[us] -- cùng độ phân giải với bộ nạp SQL của strategy_lab.
    frame["bartime"] = pd.to_datetime(frame["bartime"], format=STAMP_FORMAT, errors="coerce").astype("datetime64[us]")
    for column in _HASH_FIELDS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    invalid_bartime = frame["bartime"].isna()
    invalid_ohlc = ~np.isfinite(frame[list(_REQUIRED_FINITE_COLUMNS)].to_numpy(dtype="float64")).all(axis=1)
    invalid_mask = invalid_bartime | invalid_ohlc
    if invalid_mask.any():
        LOGGER.error(
            "event=candle_snapshot_invalid symbol=%s timeframe=%s invalid_rows=%d total_rows=%d rows=%s",
            symbol, timeframe, int(invalid_mask.sum()), len(frame), [rows[i] for i in frame.index[invalid_mask]],
        )
        raise ValueError(f"Candle snapshot {symbol}/{timeframe} contains invalid values")
    duplicated_mask = frame["bartime"].duplicated()
    if duplicated_mask.any():
        # Core thỉnh thoảng LPUSH cùng mốc của nến vừa đóng hai lần liền (cả hai phần tử trỏ tới cùng một HASH): bỏ bản thừa, không làm hỏng cả cửa sổ.
        LOGGER.warning(
            "event=candle_stamp_duplicate symbol=%s timeframe=%s count=%d stamps=%s",
            symbol, timeframe, int(duplicated_mask.sum()), frame.loc[duplicated_mask, "bartime"].dt.strftime(STAMP_FORMAT).tolist(),
        )
        frame = frame.drop_duplicates(subset="bartime", keep="first")
    # LIST là mới nhất trước; chỉ báo cần nến tăng dần.
    return frame.sort_values("bartime").reset_index(drop=True)


def _exact_float(value: Any) -> float:
    """Chuỗi thập phân thành số thực ĐÚNG TỪNG BIT (``float()``); thiếu hoặc không phải số thì NaN. ``pandas.to_numeric`` dùng bộ phân tích nhanh có thể lệch 1 ulp với chuỗi ``repr`` đầy đủ 17 chữ số."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _module_frame(rows: list[dict[str, Any]], *, symbol: str, timeframe: str, module: str) -> pd.DataFrame:
    """Dựng bảng kết quả của một module từ các dòng HGETALL, tăng dần theo ``bartime``: ``bartime`` / ``close_time`` thành datetime64[us], cột chữ (``reason``) giữ nguyên, mọi cột còn lại thành số
    (``nan`` / thiếu thành NaN). Dòng có mốc hoặc giờ đóng hỏng bị BỎ và ghi log lỗi chính các dòng đó: một dòng hỏng không được chặn cả cửa sổ."""
    frame = pd.DataFrame(rows)
    for column in _TIME_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NaT
        frame[column] = pd.to_datetime(frame[column], format=STAMP_FORMAT, errors="coerce").astype("datetime64[us]")
    for column in frame.columns:
        if column not in _TIME_COLUMNS and column not in _TEXT_COLUMNS:
            frame[column] = frame[column].map(_exact_float).astype("float64")
    invalid = frame["bartime"].isna() | frame["close_time"].isna()
    if invalid.any():
        LOGGER.error(
            "event=module_rows_invalid symbol=%s timeframe=%s module=%s invalid_rows=%d total_rows=%d rows=%s",
            symbol, timeframe, module, int(invalid.sum()), len(frame), [rows[i] for i in frame.index[invalid]],
        )
        frame = frame.loc[~invalid]
    frame = frame.drop_duplicates(subset="bartime", keep="first")
    return frame.sort_values("bartime").reset_index(drop=True)


def read_module(client: redis.Redis, *, symbol: str, timeframe: str, module: str, key_prefix: str, snapshot_bars: int) -> pd.DataFrame:
    """Đọc ĐẦY ĐỦ ``snapshot_bars`` mốc mới nhất của một module thành bảng tăng dần (hai lượt gọi Redis: LRANGE rồi pipeline HGETALL). LIST chưa có thì bảng rỗng chỉ có bartime/close_time;
    HASH đã hết hạn của một mốc thì bỏ mốc đó và ghi cảnh báo."""
    list_key = pair_list_key(key_prefix, symbol, timeframe, module)
    stamps = _read_stamps(client, list_key, snapshot_bars, newest_first=False)
    empty = pd.DataFrame({"bartime": pd.Series(dtype="datetime64[us]"), "close_time": pd.Series(dtype="datetime64[us]")})
    if not stamps:
        return empty
    rows, missing = _fetch_rows(client, list_key, stamps, modules=True)
    if missing:
        LOGGER.warning("event=module_stamp_missing symbol=%s timeframe=%s module=%s count=%d sample=%s", symbol, timeframe, module, len(missing), missing[:5])
    if not rows:
        return empty
    return _module_frame(rows, symbol=symbol, timeframe=timeframe, module=module)


def read_candles(client: redis.Redis, *, symbol: str, timeframe: str, key_prefix: str, snapshot_bars: int) -> pd.DataFrame:
    """Đọc ĐẦY ĐỦ các nến mới nhất của một cặp thành bảng OHLCV tăng dần, giờ UTC không múi giờ (hai lượt gọi Redis bất kể bao nhiêu nến: LRANGE rồi pipeline HMGET).

    ``symbol``/``timeframe`` là keyword-only: key đặt SYMBOL trước, một cặp bị đảo âm thầm sẽ đọc ra LIST rỗng. LIST ngắn hơn thì chỉ trả ít nến hơn; HASH chưa có của một mốc thì
    bỏ mốc đó (Core ghi HASH và LIST bằng các lệnh rời nên lượt đọc có thể chạy đua với lượt ghi).
    """
    list_key = pair_list_key(key_prefix, symbol, timeframe)
    stamps = client.lrange(list_key, 0, int(snapshot_bars) - 1)
    if not stamps:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    rows, missing = _fetch_rows(client, list_key, stamps)
    if missing:
        LOGGER.warning("event=candle_stamp_missing symbol=%s timeframe=%s count=%d sample=%s", symbol, timeframe, len(missing), missing[:5])
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return _frame_from_rows(rows, symbol=symbol, timeframe=timeframe)


class _Entry:
    """Bộ đệm một cặp: bảng hiện giữ và mốc của dòng MỚI NHẤT trong bảng (chuỗi, đúng dạng mốc trong LIST)."""

    __slots__ = ("frame", "head")

    def __init__(self, frame: pd.DataFrame, head: str) -> None:
        self.frame = frame
        self.head = head


class DataEngine:
    """Đầu vào của một tiến trình DE: nghe keyspace, đọc nến (hoặc kết quả module), giữ bộ đệm của các cặp tiến trình đó cần.

    ``modules=False``: cặp là (symbol, khung), nến mới nhất đứng đầu LIST. ``modules=True``: cặp là (symbol, khung, module), kết quả mới nhất đứng cuối LIST. ``snapshot_bars`` là số mốc tối đa giữ cho mọi cặp.

    Việc NỐI Redis (tạo kết nối, ping, đóng, kiểm cờ keyspace, đăng ký nghe, lấy message) do redis_connector làm; file này chỉ làm việc với dữ liệu trên kết nối đó.
    ``client`` / ``events_client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối (hai kết nối riêng: đọc và nghe event). Kết nối nghe event chỉ được tạo khi cần.
    Bảng ``snapshot`` trả về là bản đang giữ trong bộ đệm: người gọi KHÔNG được sửa nó (chỉ báo đều sao chép trước khi tính).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        snapshot_bars: int,
        pairs: Iterable[tuple[str, ...]],
        modules: bool = False,
        socket_timeout: float = 5.0,
        connect_timeout: float = 5.0,
        healthcheck_interval: int = 30,
        client: redis.Redis | None = None,
        events_client: redis.Redis | None = None,
    ) -> None:
        self._conn = {
            "host": host, "port": int(port), "password": password, "db": int(db),
            "socket_timeout": float(socket_timeout), "connect_timeout": float(connect_timeout), "healthcheck_interval": int(healthcheck_interval),
        }
        self._db = int(db)
        self._prefix = key_prefix
        self._modules = bool(modules)
        self._bars = int(snapshot_bars)
        if self._bars <= 0:
            raise ValueError("snapshot_bars must be positive")
        self._pairs: tuple[Pair, ...] = tuple(sorted({_normalized_pair(item) for item in pairs}))
        self._pair_set = frozenset(self._pairs)
        self._link = Link(**self._conn, client=client)
        self._reader = self._link.client
        self._events_client = events_client
        self._listener: KeyspaceListener | None = None
        self._cache: dict[Pair, _Entry] = {}
        self._reload: set[Pair] = set()

    @property
    def pairs(self) -> tuple[Pair, ...]:
        """Các cặp tiến trình này nghe và đọc (đã chuẩn hoá chữ hoa, sắp xếp, không trùng)."""
        return self._pairs

    # -- kết nối -----------------------------------------------------------------------------------------------------------------------------------------

    def _events(self) -> KeyspaceListener:
        if self._listener is None:
            self._listener = KeyspaceListener(**self._conn, client=self._events_client)
        return self._listener

    def ping(self) -> None:
        self._link.ping()
        self._events().ping()

    def close(self) -> None:
        self._link.close()
        if self._listener is not None:
            self._listener.close()

    # -- nghe keyspace -----------------------------------------------------------------------------------------------------------------------------------

    def check_keyspace_flags(self) -> str:
        """Redis phải bật đúng lớp 'K' + 'h' của notify-keyspace-events (redis_connector kiểm): thiếu thì RuntimeError rõ ràng ngay. Trả về chuỗi cờ hiện tại."""
        return self._events().check_flags()

    def _patterns(self) -> list[str]:
        """Mẫu keyspace cần nghe. Nến: mỗi cặp một mẫu (Redis lọc sẵn). Module: chỉ MỖI module một mẫu -- Redis so mỗi lần ghi với từng mẫu đã đăng ký, quá nhiều mẫu làm chậm chính Redis;
        cặp thừa trong cùng module bị ``_handle_message`` bỏ."""
        if self._modules:
            prefix = f"{_glob_escape(self._prefix)}_" if self._prefix else ""
            return sorted({f"__keyspace@{self._db}__:{prefix}{pair.module}_*:*" for pair in self._pairs})
        return [f"__keyspace@{self._db}__:{_glob_escape(pair_list_key(self._prefix, pair.symbol, pair.timeframe))}:*" for pair in self._pairs]

    def subscribe(self) -> int:
        """Đăng ký nghe (mẫu tên key do file này dựng, redis_connector đăng ký và chờ Redis xác nhận ĐỦ). Trả về số mẫu đã đăng ký."""
        return self._events().subscribe(self._patterns())

    def poll_events(self, timeout: float) -> list[Pair]:
        """Chờ tối đa ``timeout`` giây cho event đầu tiên rồi hút hết event đang chờ; trả các cặp vừa bị ghi (không trùng, giữ thứ tự tới).

        Event báo ghi một mốc KHÔNG mới hơn mốc mới nhất đang giữ (ghi bù, ghi lại) thì cặp đó bị đánh dấu đọc lại đầy đủ ở ``snapshot`` kế tiếp.
        """
        if self._listener is None or not self._listener.subscribed:
            raise RuntimeError("poll_events before subscribe")
        seen: dict[Pair, None] = {}
        for message in self._listener.poll(timeout):
            self._handle_message(message, seen)
        return list(seen)

    def _handle_message(self, message: dict[str, Any], seen: dict[Pair, None]) -> None:
        if message.get("type") != "pmessage" or _text(message.get("data", "")) != _HSET_PAYLOAD:
            return
        channel = _text(message.get("channel", ""))
        head, _, rest = channel.partition("__:")
        if not head.startswith("__keyspace@") or head[len("__keyspace@"):] != str(self._db):
            return
        list_key, _, stamp = rest.partition(":")
        pair = parse_pair_key(list_key, self._prefix, modules=self._modules)
        if pair is None or pair not in self._pair_set:
            return
        seen.setdefault(pair)
        entry = self._cache.get(pair)
        if entry is not None and stamp and stamp <= entry.head:
            self._reload.add(pair)

    # -- đọc ---------------------------------------------------------------------------------------------------------------------------------------------

    def snapshot(self, pair: Pair) -> pd.DataFrame | None:
        """Bảng mới nhất của cặp (tối đa ``snapshot_bars`` mốc, tăng dần), hoặc None nếu LIST chưa có mốc nào của cặp (coi như không có, không cảnh báo)."""
        entry = self._cache.get(pair)
        if entry is None or pair in self._reload:
            return self._load_full(pair)
        list_key = pair_list_key(self._prefix, pair.symbol, pair.timeframe, pair.module)
        head = _read_stamps(self._reader, list_key, HEAD_WINDOW, newest_first=not self._modules)
        if not head:
            self._cache.pop(pair, None)
            return None
        if head[0] == entry.head:
            return entry.frame
        if entry.head not in head:
            return self._load_full(pair)
        new = list(dict.fromkeys(head[: head.index(entry.head)]))  # mốc mới, mới nhất trước, bỏ trùng liền nhau
        if any(stamp <= entry.head for stamp in new):
            return self._load_full(pair)
        rows, missing = _fetch_rows(self._reader, list_key, new, modules=self._modules)
        if missing:
            return self._load_full(pair)  # đọc đầy đủ cho đúng cách đọc cũ (bỏ mốc thiếu HASH và cảnh báo)
        extra = self._frame_of(rows, pair)
        frame = pd.concat([entry.frame, extra], ignore_index=True)
        if len(frame) > self._bars:
            frame = frame.iloc[len(frame) - self._bars:].reset_index(drop=True)
        entry.frame = frame
        entry.head = frame["bartime"].iloc[-1].strftime(STAMP_FORMAT)
        return frame

    def _frame_of(self, rows: list[dict[str, Any]], pair: Pair) -> pd.DataFrame:
        if self._modules:
            return _module_frame(rows, symbol=pair.symbol, timeframe=pair.timeframe, module=pair.module)
        return _frame_from_rows(rows, symbol=pair.symbol, timeframe=pair.timeframe)

    def _load_full(self, pair: Pair) -> pd.DataFrame | None:
        if self._modules:
            frame = read_module(self._reader, symbol=pair.symbol, timeframe=pair.timeframe, module=pair.module, key_prefix=self._prefix, snapshot_bars=self._bars)
        else:
            frame = read_candles(self._reader, symbol=pair.symbol, timeframe=pair.timeframe, key_prefix=self._prefix, snapshot_bars=self._bars)
        self._reload.discard(pair)
        if frame.empty:
            self._cache.pop(pair, None)
            return None
        self._cache[pair] = _Entry(frame, frame["bartime"].iloc[-1].strftime(STAMP_FORMAT))
        return frame


def _normalized_pair(item: tuple[str, ...]) -> Pair:
    """(symbol, khung) hoặc (symbol, khung, module) thành ``Pair`` chữ hoa."""
    return Pair(*(str(part).upper() for part in item))


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
