"""Cửa vào DUY NHẤT của dữ liệu ĐỌC VÀO từ Redis: mọi thứ đọc dữ liệu vào tiến trình nằm ở file này, và chỉ ở đây. Có HAI loại danh sách List–Hash, cùng một cơ chế nghe và đọc:
  * NẾN do Core/AEN (hoặc Redis DF của OG8) ghi, MỚI NHẤT ĐỨNG ĐẦU: đầu vào của strategy_runner (``DataEngine(components=False)``);
  * CẤU PHẦN do strategy_runner ghi vào db0 (SETUP) và db1 (TRIGGER / CONTEXT / TREND) của DE, MỚI NHẤT ĐỨNG CUỐI: đầu vào của signal_publisher (``DataEngine(components=True)``).

(Thay candle_input.py của gói runner/ cũ, đã xoá 02/11/2026; tên cũ trong thiết kế là data_input.) Một lý do duy nhất để đổi file: hợp đồng List–Hash đầu vào thay đổi. Hai việc:
  1. NGHE:        keyspace notification báo cặp nào vừa bị ghi (``DataEngine.subscribe`` / ``poll_events``). Đăng ký ĐÚNG các cặp (symbol, khung[, vai trò]) mà tiến
                  trình cần, nên Redis lọc sẵn và tiến trình không nhận cặp thừa. Đây là điểm chạm đầu tiên của DE với cơ chế event-driven;
  2. ĐỌC:         N mốc mới nhất của một cặp thành bảng tăng dần (OHLCV với nến; các cột của cấu phần với db0 của DE), có bộ đệm gia tăng (``DataEngine.snapshot``):
                  sự kiện mới chỉ đọc mốc mới thay vì đọc lại cả cửa sổ; gặp bất thường thì đọc lại đầy đủ đúng như cách đọc cũ (``read_candles`` / ``read_components``);

Không biết chiến lược, trend hay đầu ra nào: nhận mọi tham số (host, db, tiền tố, số mốc, danh sách cặp) từ configuration.py lúc khởi động, không tự đọc config.

Hợp đồng db0 (Core ghi): LIST ``{key_prefix}_{SYMBOL}_{TF}`` giữ các mốc, MỚI NHẤT ĐỨNG ĐẦU; HASH ``<key LIST>:{mốc}`` giữ open/high/low/close/volume (chuỗi thập phân).
Mốc ``YYYY-MM-DD HH:MM:SS`` (UTC, rộng cố định nên so sánh chuỗi đúng thứ tự thời gian). Core ghi mỗi nến bằng HMSET (Redis phát event ``hset``) rồi LPUSH/LTRIM, nên chỉ cần lớp
'K' + 'h' trong ``notify-keyspace-events``. CHANNEL là nơi duy nhất chứa tên key vừa bị ghi (payload chỉ là tên lệnh).

Hợp đồng cấu phần của DE (strategy_runner ghi bằng ``redis_connector.write_list_hash``, RPUSH nên MỚI NHẤT ĐỨNG CUỐI): LIST ``{key_prefix}_{SYMBOL}_{VAI TRÒ}[_{ĐỊNH DANH}]_{TF}`` (tiền tố rỗng thì bỏ).
VAI TRÒ là TRIGGER (định danh = chiến lược, vd ``HK50_TRIGGER_COMBO_H1``: CHỈ các nến CÓ tín hiệu thô, mỗi nến một sự kiện), SETUP (định danh = chiến lược, ``HK50_SETUP_COMBO_H1``: trạng thái điều kiện
của chiến lược ở TỪNG nến gồm mặt nạ "nến được phép bắn", giá nến và atr, để phần dời biết nến nào còn đủ điều kiện), CONTEXT (không định danh, ``HK50_CONTEXT_H2``: macd_h, ma) hoặc TREND (định danh = phương pháp,
``HK50_TREND_KNN_H4``: trend_bias). SETUP ở db0; TRIGGER, CONTEXT và TREND ở db1 (``role_db``). Mọi danh sách chỉ giữ cửa sổ 500 nến gần nhất (``snapshot_bars`` = ``redis.setup.window_bars`` = ``retention_max_entries``); TRIGGER chỉ chứa sự kiện nằm trong cửa sổ đó. HASH ``<key LIST>:{mốc}`` có ``close_time`` (giờ ĐÓNG nến, dùng để ghép theo luật nến đóng) cộng các trường riêng của vai
trò, nên đọc bằng HGETALL thay vì HMGET theo danh sách trường. Chế độ cấu phần nghe MỘT mẫu cho mỗi (db, vai trò) thay vì mỗi cặp một mẫu (hàng nghìn cặp).

Tính đúng của bộ đệm: kết quả phải giống HỆT đọc đầy đủ. Vì vậy chỉ dùng đường gia tăng khi mọi mốc mới đều MỚI HƠN nến mới nhất đang giữ và mốc đang giữ nằm trong cửa sổ đầu LIST vừa hỏi;
event báo ghi một mốc không mới hơn nến mới nhất (ghi bù, ghi lại) hoặc thiếu HASH của một mốc mới thì đọc lại đầy đủ.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
import redis

from core_engine.src.redis_connector import KeyspaceListener, Link

LOGGER = logging.getLogger(__name__)
OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
# Chỉ bốn trường OHLC được chỉ báo/chiến lược đọc; HMGET vẫn xin `volume` để khung dữ liệu cùng hình dạng với khung OHLCV đọc từ SQL của strategy_lab (không chiến lược nào đọc nó).
_REQUIRED_FINITE_COLUMNS = ("open", "high", "low", "close")
_HASH_FIELDS = ("open", "high", "low", "close", "volume")
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HSET_PAYLOAD = "hset"
# Số mốc mới nhất hỏi mỗi lần cập nhật gia tăng: đủ cho vài nến mới liền nhau; mốc đang giữ không nằm trong cửa sổ này thì đọc lại đầy đủ.
HEAD_WINDOW = 16
COMPONENT_ROLES = ("TRIGGER", "SETUP", "CONTEXT", "TREND")
# Vai trò có giá nến trong từng dòng: giá phải hữu hạn, dòng hỏng thì bị bỏ.
_PRICED_ROLES = ("TRIGGER", "SETUP")
_TIME_COLUMNS = ("bartime", "close_time")
_TEXT_COLUMNS = ("method", "reason")


class Pair(NamedTuple):
    """Một cặp (symbol, khung) của nến, hoặc (symbol, khung, vai trò) của cấu phần trong db0 của DE. Một kiểu giá trị duy nhất thay cho các tham số rời -- thứ tự các chuỗi này từng bị đảo ở nhiều chỗ.
    ``role`` rỗng = nến của nguồn; ``TRIGGER`` / ``SETUP`` / ``CONTEXT`` / ``TREND`` = cấu phần."""

    symbol: str
    timeframe: str
    role: str = ""
    qualifier: str = ""


def pair_list_key(key_prefix: str, symbol: str, timeframe: str, role: str = "", qualifier: str = "") -> str:
    """LIST các mốc của một cặp. Nến, mới nhất đứng đầu: ``{key_prefix}_{SYMBOL}_{TIMEFRAME}`` (SYMBOL trước, khung sau: ``L_CANDLE_US30_M5``). Cấu phần của DE, mới nhất đứng cuối:
    ``{key_prefix}_{SYMBOL}_{ROLE}[_{QUALIFIER}]_{TIMEFRAME}``: ``HK50_TRIGGER_COMBO_H1`` (định danh = chiến lược), ``HK50_CONTEXT_H2`` (không định danh: chứa mọi chỉ báo),
    ``HK50_TREND_KNN_H4`` (định danh = phương pháp trend); phần nào rỗng (tiền tố, vai trò, định danh) thì bỏ."""
    return "_".join(part for part in (key_prefix, symbol.upper(), role.upper(), qualifier.upper(), timeframe.upper()) if part)


def candle_key(list_key: str, stamp: str) -> str:
    """HASH giữ một nến: key LIST của cặp cộng ``":" + stamp``. Toàn bộ quy tắc đặt tên: một phép nối."""
    return f"{list_key}:{stamp}"


def parse_pair_key(key: str, key_prefix: str, *, components: bool = False) -> Pair | None:
    """Trả về ``Pair`` nếu ``key`` là key LIST của một cặp, ngược lại ``None``. ``components`` chọn khuôn tên: nến (``{prefix}_{SYMBOL}_{TF}``) hoặc cấu phần (``{prefix}_{SYMBOL}_{ROLE}_{TF}``).

    Key LIST không có dấu ':' nào; key HASH của một mốc có ít nhất một (mốc giờ cũng chứa ':'). Phép thử là SỰ HIỆN DIỆN của ':' chứ không phải số lượng.
    """
    prefix = f"{key_prefix}_" if key_prefix else ""
    if not key.startswith(prefix) or ":" in key:
        return None
    body = key[len(prefix):]
    if components:
        parts = body.split("_")
        if len(parts) not in (3, 4) or not all(parts) or parts[1].upper() not in COMPONENT_ROLES:
            return None
        qualifier = parts[2].upper() if len(parts) == 4 else ""
        return Pair(parts[0].upper(), parts[-1].upper(), parts[1].upper(), qualifier)
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
    """``count`` mốc mới nhất của LIST, luôn trả MỚI NHẤT TRƯỚC (nến: đầu LIST; cấu phần: cuối LIST, đảo lại)."""
    if newest_first:
        return client.lrange(list_key, 0, int(count) - 1)
    return list(reversed(client.lrange(list_key, -int(count), -1)))


def _fetch_rows(client: redis.Redis, list_key: str, stamps: list[str], *, components: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """Một pipeline tới HASH của từng mốc (HMGET với nến, HGETALL với cấu phần). Trả (các dòng có dữ liệu, mốc mà HASH chưa có)."""
    pipe = client.pipeline(transaction=False)
    for stamp in stamps:
        if components:
            pipe.hgetall(candle_key(list_key, stamp))
        else:
            pipe.hmget(candle_key(list_key, stamp), *_HASH_FIELDS)
    values = pipe.execute()
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for stamp, fields in zip(stamps, values, strict=True):
        if components:
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
    """Chuỗi thập phân thành số thực ĐÚNG TỪNG BIT (``float()``); thiếu hoặc không phải số thì NaN. ``pandas.to_numeric`` dùng bộ phân tích nhanh có thể lệch 1 ulp với chuỗi ``repr`` đầy đủ 17 chữ số
    (đo: khoảng 35% giá trị), và một so sánh kiểu "close >= đỉnh close cũ" có thể đổi kết quả chỉ vì 1 ulp, nên cấu phần db0 (ghi bằng ``repr``) đọc lại bằng hàm này."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _components_frame(rows: list[dict[str, Any]], *, symbol: str, timeframe: str, role: str) -> pd.DataFrame:
    """Dựng bảng của một cấu phần db0 từ các dòng HGETALL, tăng dần theo ``bartime``: ``bartime`` / ``close_time`` thành datetime64[us], cột chữ (``method``) giữ nguyên, mọi cột còn lại thành số
    (``nan`` / thiếu thành NaN, đọc đúng từng bit, xem ``_exact_float``). Dòng có mốc hoặc giờ đóng hỏng (và với TRIGGER, giá không hữu hạn) bị BỎ và ghi log lỗi chính các dòng đó: một dòng hỏng không được chặn cả cửa sổ."""
    frame = pd.DataFrame(rows)
    for column in _TIME_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NaT
        frame[column] = pd.to_datetime(frame[column], format=STAMP_FORMAT, errors="coerce").astype("datetime64[us]")
    for column in frame.columns:
        if column not in _TIME_COLUMNS and column not in _TEXT_COLUMNS:
            frame[column] = frame[column].map(_exact_float).astype("float64")
    invalid = frame["bartime"].isna() | frame["close_time"].isna()
    if role in _PRICED_ROLES:
        prices = frame.reindex(columns=list(_REQUIRED_FINITE_COLUMNS)).to_numpy(dtype="float64")
        invalid = invalid | ~np.isfinite(prices).all(axis=1)
    if invalid.any():
        LOGGER.error(
            "event=component_rows_invalid symbol=%s timeframe=%s role=%s invalid_rows=%d total_rows=%d rows=%s",
            symbol, timeframe, role, int(invalid.sum()), len(frame), [rows[i] for i in frame.index[invalid]],
        )
        frame = frame.loc[~invalid]
    frame = frame.drop_duplicates(subset="bartime", keep="first")
    return frame.sort_values("bartime").reset_index(drop=True)


def read_components(client: redis.Redis, *, symbol: str, timeframe: str, role: str, key_prefix: str, snapshot_bars: int, qualifier: str = "") -> pd.DataFrame:
    """Đọc ĐẦY ĐỦ ``snapshot_bars`` mốc mới nhất của một cấu phần db0 thành bảng tăng dần (hai lượt gọi Redis: LRANGE rồi pipeline HGETALL). LIST chưa có thì bảng rỗng chỉ có bartime/close_time;
    HASH đã hết hạn của một mốc thì bỏ mốc đó và ghi cảnh báo."""
    list_key = pair_list_key(key_prefix, symbol, timeframe, role, qualifier)
    stamps = _read_stamps(client, list_key, snapshot_bars, newest_first=False)
    empty = pd.DataFrame({"bartime": pd.Series(dtype="datetime64[us]"), "close_time": pd.Series(dtype="datetime64[us]")})
    if not stamps:
        return empty
    rows, missing = _fetch_rows(client, list_key, stamps, components=True)
    if missing:
        LOGGER.warning("event=component_stamp_missing symbol=%s timeframe=%s role=%s count=%d sample=%s", symbol, timeframe, role, len(missing), missing[:5])
    if not rows:
        return empty
    return _components_frame(rows, symbol=symbol, timeframe=timeframe, role=role)


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
    """Bộ đệm một cặp: bảng nến hiện giữ và mốc của nến MỚI NHẤT trong bảng (chuỗi, đúng dạng mốc trong LIST)."""

    __slots__ = ("frame", "head")

    def __init__(self, frame: pd.DataFrame, head: str) -> None:
        self.frame = frame
        self.head = head


class DataEngine:
    """Đầu vào của một tiến trình DE: nghe keyspace, đọc nến (hoặc cấu phần db0), giữ bộ đệm của các cặp tiến trình đó cần.

    ``components=False`` (strategy_runner): cặp là (symbol, khung), nến mới nhất đứng đầu LIST. ``components=True`` (signal_publisher): cặp là (symbol, khung, vai trò), cấu phần mới nhất đứng cuối LIST.
    ``snapshot_bars`` là một số chung cho mọi cặp, hoặc ánh xạ cặp -> số mốc (cặp không có trong ánh xạ dùng giá trị lớn nhất).
    ``role_db`` (chỉ chế độ cấu phần): vai trò -> số db khi một vai trò nằm ở db KHÁC ``db`` của Redis đó (DE: SETUP ở db0; TRIGGER, CONTEXT và TREND ở db1). Một kết nối nghe event duy nhất vì keyspace
    notification của mọi db đi qua cùng một Pub/Sub; mỗi db có kết nối đọc riêng (``clients``: db -> kết nối, cho test thay bản giả).

    Việc NỐI Redis (tạo kết nối, ping, đóng, kiểm cờ keyspace, đăng ký nghe, lấy message) do redis_connector làm; file này chỉ làm việc với dữ liệu nến trên kết nối đó.
    ``client`` / ``events_client`` cho test thay bằng bản giả; không truyền thì redis_connector tự nối db0 (hai kết nối riêng: đọc và nghe event). Kết nối nghe event chỉ được tạo khi cần.
    Bảng ``snapshot`` trả về là bản đang giữ trong bộ đệm: người gọi KHÔNG được sửa nó (chỉ báo/chiến lược đều sao chép trước khi tính).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        key_prefix: str,
        snapshot_bars: int | Mapping[Pair, int],
        pairs: Iterable[tuple[str, ...]],
        components: bool = False,
        role_db: Mapping[str, int] | None = None,
        clients: Mapping[int, redis.Redis] | None = None,
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
        self._components = bool(components)
        self._bars_by_pair: dict[Pair, int] = {}
        if isinstance(snapshot_bars, Mapping):
            self._bars_by_pair = {Pair(*pair): int(count) for pair, count in snapshot_bars.items()}
            self._bars = max(self._bars_by_pair.values(), default=0)
        else:
            self._bars = int(snapshot_bars)
        if self._bars <= 0 or any(count <= 0 for count in self._bars_by_pair.values()):
            raise ValueError("snapshot_bars must be positive")
        self._pairs: tuple[Pair, ...] = tuple(sorted({_normalized_pair(item) for item in pairs}))
        self._pair_set = frozenset(self._pairs)
        self._role_db = {str(role).upper(): int(number) for role, number in (role_db or {}).items()}
        self._link = Link(**self._conn, client=client)
        self._reader = self._link.client
        self._readers: dict[int, redis.Redis] = {self._db: self._reader}
        self._extra_links: list[Link] = []
        for number in sorted({number for number in self._role_db.values() if number != self._db}):
            link = Link(**{**self._conn, "db": number}, client=(clients or {}).get(number))
            self._extra_links.append(link)
            self._readers[number] = link.client
        self._events_client = events_client
        self._listener: KeyspaceListener | None = None
        self._cache: dict[Pair, _Entry] = {}
        self._reload: set[Pair] = set()

    @property
    def pairs(self) -> tuple[Pair, ...]:
        """Các cặp tiến trình này nghe và đọc (đã chuẩn hoá chữ hoa, sắp xếp, không trùng)."""
        return self._pairs

    def _bars_of(self, pair: Pair) -> int:
        return self._bars_by_pair.get(pair, self._bars)

    def _db_of(self, pair: Pair) -> int:
        return self._role_db.get(pair.role, self._db)

    def _reader_of(self, pair: Pair) -> redis.Redis:
        return self._readers[self._db_of(pair)]

    # -- kết nối -----------------------------------------------------------------------------------------------------------------------------------------

    def _events(self) -> KeyspaceListener:
        if self._listener is None:
            self._listener = KeyspaceListener(**self._conn, client=self._events_client)
        return self._listener

    def ping(self) -> None:
        self._link.ping()
        for link in self._extra_links:
            link.ping()
        self._events().ping()

    def close(self) -> None:
        self._link.close()
        for link in self._extra_links:
            link.close()
        if self._listener is not None:
            self._listener.close()

    # -- nghe keyspace -----------------------------------------------------------------------------------------------------------------------------------

    def check_keyspace_flags(self) -> str:
        """Redis phải bật đúng lớp 'K' + 'h' của notify-keyspace-events (redis_connector kiểm): thiếu thì RuntimeError rõ ràng ngay. Trả về chuỗi cờ hiện tại."""
        return self._events().check_flags()

    def _patterns(self) -> list[str]:
        """Mẫu keyspace cần nghe. Nến: mỗi cặp một mẫu (Redis lọc sẵn). Cấu phần: hàng nghìn cặp nên chỉ MỖI (db, vai trò) một mẫu -- Redis so mỗi lần ghi với từng mẫu đã đăng ký, quá nhiều mẫu làm chậm
        chính Redis; cặp thừa trong cùng vai trò bị ``_handle_message`` bỏ."""
        if self._components:
            prefix = f"{_glob_escape(self._prefix)}_" if self._prefix else ""
            return sorted({f"__keyspace@{self._db_of(pair)}__:{prefix}*_{pair.role}_*:*" for pair in self._pairs})
        return [f"__keyspace@{self._db}__:{_glob_escape(pair_list_key(self._prefix, pair.symbol, pair.timeframe, pair.role, pair.qualifier))}:*" for pair in self._pairs]

    def subscribe(self) -> int:
        """Đăng ký nghe từng cặp một (mẫu tên key do file này dựng, redis_connector đăng ký và chờ Redis xác nhận ĐỦ). Trả về số cặp đã đăng ký."""
        return self._events().subscribe(self._patterns())

    def poll_events(self, timeout: float) -> list[Pair]:
        """Chờ tối đa ``timeout`` giây cho event đầu tiên rồi hút hết event đang chờ; trả các cặp vừa bị ghi (không trùng, giữ thứ tự tới).

        Event báo ghi một mốc KHÔNG mới hơn nến mới nhất đang giữ (ghi bù, ghi lại) thì cặp đó bị đánh dấu đọc lại đầy đủ ở ``snapshot`` kế tiếp.
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
        if not head.startswith("__keyspace@") or not head[len("__keyspace@"):].isdigit():
            return
        number = int(head[len("__keyspace@"):])
        list_key, _, stamp = rest.partition(":")
        pair = parse_pair_key(list_key, self._prefix, components=self._components)
        if pair is None or pair not in self._pair_set or self._db_of(pair) != number:
            return
        seen.setdefault(pair)
        entry = self._cache.get(pair)
        if entry is not None and stamp and stamp <= entry.head:
            self._reload.add(pair)

    # -- đọc nến -----------------------------------------------------------------------------------------------------------------------------------------

    def snapshot(self, pair: Pair) -> pd.DataFrame | None:
        """Bảng mới nhất của cặp (tối đa ``snapshot_bars`` mốc, tăng dần), hoặc None nếu LIST chưa có mốc nào của cặp (coi như không có, không cảnh báo)."""
        entry = self._cache.get(pair)
        if entry is None or pair in self._reload:
            return self._load_full(pair)
        list_key = pair_list_key(self._prefix, pair.symbol, pair.timeframe, pair.role, pair.qualifier)
        reader = self._reader_of(pair)
        head = _read_stamps(reader, list_key, HEAD_WINDOW, newest_first=not self._components)
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
        rows, missing = _fetch_rows(reader, list_key, new, components=self._components)
        if missing:
            return self._load_full(pair)  # đọc đầy đủ cho đúng cách đọc cũ (bỏ mốc thiếu HASH và cảnh báo)
        extra = self._frame_of(rows, pair)
        frame = pd.concat([entry.frame, extra], ignore_index=True)
        bars = self._bars_of(pair)
        if len(frame) > bars:
            frame = frame.iloc[len(frame) - bars:].reset_index(drop=True)
        entry.frame = frame
        entry.head = frame["bartime"].iloc[-1].strftime(STAMP_FORMAT)
        return frame

    def _frame_of(self, rows: list[dict[str, Any]], pair: Pair) -> pd.DataFrame:
        if self._components:
            return _components_frame(rows, symbol=pair.symbol, timeframe=pair.timeframe, role=pair.role)
        return _frame_from_rows(rows, symbol=pair.symbol, timeframe=pair.timeframe)

    def _load_full(self, pair: Pair) -> pd.DataFrame | None:
        if self._components:
            frame = read_components(
                self._reader_of(pair), symbol=pair.symbol, timeframe=pair.timeframe, role=pair.role, key_prefix=self._prefix, snapshot_bars=self._bars_of(pair), qualifier=pair.qualifier,
            )
        else:
            frame = read_candles(
                self._reader, symbol=pair.symbol, timeframe=pair.timeframe, key_prefix=self._prefix, snapshot_bars=self._bars_of(pair),
            )
        self._reload.discard(pair)
        if frame.empty:
            self._cache.pop(pair, None)
            return None
        self._cache[pair] = _Entry(frame, frame["bartime"].iloc[-1].strftime(STAMP_FORMAT))
        return frame


def _normalized_pair(item: tuple[str, ...]) -> Pair:
    """(symbol, khung) hoặc (symbol, khung, vai trò) thành ``Pair`` chữ hoa."""
    return Pair(*(str(part).upper() for part in item))


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
