"""signal_trans.py — tầng Input trả lời đúng 1 câu hỏi: "signal cho spec này
lấy từ đâu?". [2026-09-21 lần 3] Redis là NGUỒN DUY NHẤT cho backend CLI —
không còn CSV tĩnh (`core_python` export qua SSHFS) hay override tay nào nữa,
theo yêu cầu người dùng ("cli phải đúng dựa trên luồng redis thôi, ko qua csv
nào cả"). Đường CSV tĩnh cũ đã XOÁ khỏi module này (không phải deprecate —
xoá hẳn, xem lịch sử ở `bo-workflow-backtest-pipeline.md` nếu cần đọc lại).

`resolve_signal_path()` LUÔN vật chất hoá tươi từ Redis (List+Hash, hệ
strategy_lab/SEN05) ra 1 CSV cùng khuôn cột mà LoadSignalFile() trong
BacktestBot.cs đang đọc, ghi vào thư mục `dest_dir` do bên gọi cấp (KHÔNG
còn thư mục cố định `REDIS_CACHE_DIR` — bỏ 2026-09-22, xem điểm dưới). Không
sửa gì bên cBot — cBot tiếp tục đọc SignalFilePath như trước, chỉ khác nguồn
tạo ra file đó và file đó giờ LUÔN mới (không phải file export theo lịch cũ).

[2026-09-22] Bỏ hằng số `REDIS_CACHE_DIR` (đường cố định `runs/redis_cache/`)
— đã xác nhận với người dùng nó không mang lại giá trị gì ngoài "tiện xem
nhanh" (vì bản đóng băng thật phục vụ tái lập nằm ở `store.snapshot()` →
`inputs/<sha256>.csv`, KHÔNG phải file này), trong khi tên file CỐ ĐỊNH lại có
rủi ro race-condition thật nếu sau này chạy 2 experiment song song cùng đụng 1
symbol/timeframe/strategy. `resolve_signal_path()` giờ nhận `dest_dir` từ
facilitator — facilitator tự tạo 1 thư mục tạm riêng cho mỗi lần
`facilitator.run_experiment()` và tự xoá sau khi đã đóng băng xong.

⚠️ Hệ quả: CLI backend giờ BẮT BUỘC Redis phải sống + có dữ liệu mới chạy
được — không còn đường lùi về CSV nếu Redis lỗi/trống (có chủ đích, đã hỏi
người dùng trước khi bỏ fallback). GUI (chạy tay trong cTrader Desktop) hoàn
toàn KHÔNG đi qua module này — không bị ảnh hưởng gì.

[2026-10-23] 3 nguồn CỐ ĐỊNH cùng schema do WE (OG8) cấp: "L_PastSignal" (db0 RAW, không lọc trend, không đổi),
"L_PastSignal_Knn" (db1, lọc trend KNN) và "L_PastSignal_Breakout" (db2, lọc trend breakout) — xem RedisProfile.
Module này không tự biết gì về số db — chỉ đọc đúng `RedisProfile.key_prefix` của profile được truyền vào. Đổi nguồn
= đổi `redis_profile` của experiment (nguồn mặc định) hoặc `source` của từng channel (SignalChannel.source: nhiều
nguồn trong MỘT lượt, mỗi kênh đúng một nguồn), khai ở `config.yaml` mục `redis:`; không cần sửa gì ở đây hay thêm
cờ CLI mới.
"""
from __future__ import annotations

import hashlib
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

import redis

from .configuration import RedisProfile, StrategyProfile, load_redis_profile
from .models import RunSpec, SignalChannel

# Symbol nội bộ (đúng tên cTrader, vd "US30.cash") -> phần SYMBOL trong key
# Redis (vd "US30"). Vẫn cần dù không còn CSV tĩnh — key Redis dùng đúng
# khuôn này (xem memory cbot-cli-simulation-redis-vision.md).
SYMBOL_GROUPS = {
    "US30.cash": "US30",
    "US100.cash": "US100",
    "US500.cash": "US500",
    "UK100.cash": "UK100",
    "GER40.cash": "DE40",
    "FRA40.cash": "FR40",
    "SPN35.cash": "SP35",
    "HK50.cash": "HK50",
    "JP225.cash": "J225",
    "XAUUSD": "GOLD",
    "BTCUSD": "BTCUSD",
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "GBPUSD": "GBPUSD",
    "USDCAD": "USDCAD",
}

# Chiến lược -> các field Redis bắt buộc của nó. Key là TOKEN của strategy_lab trong key Redis (có gạch dưới:
# "ma_cross", "macd_break"), cũng là token ở cột `strategy` của file tín hiệu và trong `signal_strategies`. Dòng Redis
# thiếu field bắt buộc bị bỏ khi ghi file. Bảng chiến lược trong BacktestBot.cs phải khớp các key này (có test chặn lệch).
STRATEGY_COLUMNS: dict[str, tuple[str, ...]] = {
    "combo": ("bartime", "atr", "entry", "signal"),
    "ma_cross": ("bartime", "atr", "signal"),
    "macd_break": ("bartime", "atr", "signal"),
}

# Cot file tin hieu CUA BACKTESTBOT (nhieu chien luoc): moi dong co du 7 cot. `entry` de TRONG voi chien luoc lenh MARKET
# (ma_cross, macd_break); chien luoc lenh STOP (combo) BAT BUOC co entry. Ten cot khop cung ten bot tim theo header.
SIGNAL_FILE_COLUMNS = ("bartime", "atr", "entry", "signal", "tf", "symbol", "strategy", "risk_percent")


def resolve_signal_path(
    redis_profile: RedisProfile,
    profile: StrategyProfile,
    spec: RunSpec,
    dest_dir: Path,
    *,
    materialized: set[Path] | None = None,
) -> Path:
    """Đường dẫn CSV tín hiệu cho 1 spec — 1 điểm hội tụ DUY NHẤT cho câu hỏi
    "signal đến từ đâu", giờ LUÔN đi qua Redis (dời từ facilitator._signal_path()
    2026-09-21, đổi hẳn sang Redis-only 2026-09-21 lần 3).

    `dest_dir` do bên gọi cấp — facilitator tự tạo 1 thư mục tạm riêng cho mỗi
    lần `facilitator.run_experiment()` (2026-09-22, thay cho hằng số REDIS_CACHE_DIR cố
    định cũ — tên cố định có rủi ro race-condition nếu 2 experiment song song
    cùng đụng 1 symbol/timeframe/strategy; bản đóng băng thật phục vụ tái lập
    đã nằm ở store.snapshot()/inputs/ nên thư mục này chỉ cần sống hết 1 batch).

    Profile BẮT BUỘC đã chọn chiến lược (`with_signal_overrides` với `signal_strategies` của cấu hình lần chạy):
    MỘT file ghép mọi (chiến lược, symbol, khung) đã chọn. Symbol lấy từ `signal_symbols` (mặc định chỉ symbol của spec);
    `spec.symbol`/`spec.timeframe` chỉ là symbol/khung biểu đồ của CLI. Tên file chỉ phụ thuộc đúng tập (chiến lược,
    symbol, khung) — KHÔNG phụ thuộc KslLevel/KtpLevel/... — nên nhiều spec cùng tập trong 1 batch (vd quét lưới
    KSL×KTP) trỏ ĐÚNG 1 file. `materialized` (một `set` do facilitator giữ xuyên suốt cả batch) đảm bảo Redis chỉ bị
    LRANGE/HGETALL đúng 1 lần cho mỗi tổ hợp đó, không phải 1 lần/spec.
    """
    if spec.symbol not in SYMBOL_GROUPS:
        raise KeyError(f"{spec.symbol!r} has no signal Redis mapping")
    if not profile.signal_strategies:
        raise ValueError(f"{profile.id}: chưa chọn chiến lược — cấu hình lần chạy phải khai signal_strategies")
    channels = spec.channels
    symbols = tuple(dict.fromkeys(item.symbol for item in channels)) if channels else (profile.signal_symbols or (spec.symbol,))
    unknown = [name for name in symbols if name not in SYMBOL_GROUPS]
    if unknown:
        raise KeyError(f"{unknown!r} has no signal Redis mapping")
    if spec.symbol not in symbols:
        raise ValueError(f"chart symbol {spec.symbol!r} must be one of signal_symbols {list(symbols)!r}")
    frames = {token: tuple(profile.strategy_frames[token]) for token in profile.signal_strategies}
    identity = (
        # Nguồn của kênh chỉ vào tên file khi KHÁC nguồn mặc định của lượt (cùng kênh nhưng khác nguồn là khác nội dung).
        ";".join(
            f"{item.strategy}|{item.symbol}|{item.timeframe}|{item.risk_percent:g}"
            + (f"|{item.source}" if item.source and item.source != redis_profile.id else "")
            for item in channels
        )
        if channels
        else "|".join(symbols) + "/" + ";".join(f"{token}:{','.join(values)}" for token, values in frames.items())
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    dest_path = dest_dir / f"MULTI_{len(symbols)}sym_{'-'.join(frames)}_{digest}.csv"
    if materialized is None or dest_path not in materialized:
        if channels:
            materialize_channel_csv(redis_profile, channels, dest_path)
        else:
            materialize_strategy_csv(
                redis_profile, symbols, frames, dest_path,
                risk_percent=float(spec.params.get("RiskPercent", 0.2)),
            )
        if materialized is not None:
            materialized.add(dest_path)
    return dest_path


def redis_client(profile: RedisProfile) -> "redis.Redis":
    """Client Redis cho 1 profile trong config.yaml, dùng chung 1 ConnectionPool
    mỗi profile. KHÔNG bao giờ log/in giá trị mật khẩu ra bất cứ đâu.

    [2026-09-27] Mỗi kết nối mới tốn TCP + AUTH tới host strategy_lab (đo
    15,6 ms, trong khi LRANGE trên kết nối sẵn có chỉ 2,3 ms) — dashboard gọi
    hàm này 1 lần/cặp symbol×timeframe. `client.close()` của bên gọi chỉ trả
    kết nối về pool (redis-py không tự đóng pool truyền từ ngoài vào)."""
    password_stamp = Path(profile.pwd_file).stat().st_mtime_ns
    return redis.Redis(connection_pool=_connection_pool(profile, password_stamp))


@lru_cache(maxsize=8)
def _connection_pool(profile: RedisProfile, password_stamp: int) -> "redis.ConnectionPool":
    """`password_stamp` chỉ là 1 phần khoá cache: đổi file mật khẩu thì mở pool
    mới, pool cũ không bị dùng lại với mật khẩu đã hết hiệu lực."""
    del password_stamp
    return redis.ConnectionPool(
        host=profile.host,
        port=profile.port,
        db=profile.db,
        username=profile.username or None,
        password=profile.password(),
        decode_responses=True,
    )


def signal_list_key(
    symbol_group: str, timeframe: str, strategy: str, *, key_prefix: str = "L_PastSignal"
) -> str:
    """Khuôn key LIST theo đúng tài liệu strategy_lab:
    {key_prefix}_{SYMBOL}_{TIMEFRAME}_{STRATEGY} — viết hoa cả 3 phần.

    `key_prefix` mặc định "L_PastSignal" (nguồn RAW, không lọc trend). Hai nguồn còn lại (WE, 2026-10-23) cùng khuôn
    key, chỉ khác tiền tố "L_PastSignal_Knn" / "L_PastSignal_Breakout" — gọi hàm này với key_prefix khác để đọc
    nguồn đó, xem RedisProfile.key_prefix trong configuration.py."""
    return f"{key_prefix}_{symbol_group.upper()}_{timeframe.upper()}_{strategy.upper()}"


def fetch_signal_rows(
    client: "redis.Redis",
    symbol_group: str,
    timeframe: str,
    strategy: str,
    *,
    key_prefix: str = "L_PastSignal",
) -> list[dict[str, str]]:
    """Đọc TOÀN BỘ signal hiện có cho (symbol_group, timeframe, strategy).

    LIST không tồn tại (LLEN=0) trả về [] — đúng nghĩa "chưa có signal" theo
    tài liệu strategy_lab, KHÔNG phải lỗi, không tự suy diễn thêm ở đây.
    Dùng pipeline cho các lần HGETALL để tránh N round-trip riêng lẻ khi số
    stamp lớn (đúng khuyến nghị trong tài liệu nguồn)."""
    list_key = signal_list_key(symbol_group, timeframe, strategy, key_prefix=key_prefix)
    stamps = client.lrange(list_key, 0, -1)
    if not stamps:
        return []
    pipeline = client.pipeline(transaction=False)
    for stamp in stamps:
        pipeline.hgetall(f"{list_key}:{stamp}")
    hashes = pipeline.execute()
    rows: list[dict[str, str]] = []
    for row in hashes:
        if not row:
            # Stamp có trong LIST nhưng HASH rỗng/mất — bỏ qua, không đoán
            # giá trị (có thể do race-condition ghi đè giữa lúc đọc).
            continue
        rows.append(row)
    return rows


def signal_window_sha256(path: Path, end: date) -> str:
    """sha256 of the CSV header plus every row whose bartime date is on or
    before `end` (inclusive: ctrader-cli runs the whole `--end` day — measured
    2026-10-13, equity points reach 23:59 of the end day and the last trade came
    from a signal of that same day).

    strategy_lab keeps appending new signals to Redis, so the full CSV changes
    every few hours even when nothing inside a past backtest window changed.
    Rows after `end` can never influence a backtest that stops at `end`, so
    they are excluded from this identity. A row whose bartime cannot be parsed
    is kept (conservative: an unreadable row still invalidates the identity)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return hashlib.sha256(b"").hexdigest()
    header = lines[0]
    columns = [column.strip() for column in header.split(",")]
    index = columns.index("bartime") if "bartime" in columns else 0
    kept = [header]
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        try:
            day = date.fromisoformat(cells[index].strip()[:10])
        except (IndexError, ValueError):
            kept.append(line)
            continue
        if day <= end:
            kept.append(line)
    return hashlib.sha256(("\n".join(kept) + "\n").encode("utf-8")).hexdigest()


def materialize_strategy_csv(
    profile: RedisProfile,
    symbols: Sequence[str],
    strategy_frames: Mapping[str, Sequence[str]],
    dest_path: Path,
    *,
    risk_percent: float = 0.2,
) -> int:
    """File tín hiệu của BacktestBot: đọc Redis của TỪNG (chiến lược, symbol, khung) bằng cùng một kết nối, ghi MỘT file
    `SIGNAL_FILE_COLUMNS` sắp theo bartime (hoà thì theo thứ tự chiến lược, khung, symbol đã khai). `strategy_frames` là
    `token Redis -> các khung`; `symbols` là tên cTrader (vd "US30.cash", đổi sang tên Redis qua SYMBOL_GROUPS).
    Thiếu tín hiệu của bất kỳ (chiến lược, symbol, khung) nào là lỗi rõ ràng — không âm thầm chạy thiếu kênh. Dòng thiếu
    field bắt buộc của chiến lược đó (`STRATEGY_COLUMNS`) bị bỏ. Giữ NGUYÊN giá trị thô từ Redis (kể cả cột signal "1"/"2" theo ProtoOATradeSide) — bot tự chuẩn hoá khi đọc
    file. Cột `entry` chỉ lấy từ Redis với
    chiến lược có entry (combo); chiến lược MARKET để trống."""
    strategy_order = {token: index for index, token in enumerate(strategy_frames)}
    frame_order = {token: {frame.upper(): index for index, frame in enumerate(frames)} for token, frames in strategy_frames.items()}
    symbol_order = {name: index for index, name in enumerate(symbols)}
    client = redis_client(profile)
    try:
        rows: list[dict[str, str]] = []
        for token, frames in strategy_frames.items():
            columns = STRATEGY_COLUMNS.get(token)
            if columns is None:
                raise ValueError(f"signal_trans: không biết khuôn cột cho chiến lược {token!r}")
            for name in symbols:
                group = SYMBOL_GROUPS[name]
                for timeframe in frames:
                    channel_rows = fetch_signal_rows(client, group, timeframe, token, key_prefix=profile.key_prefix)
                    if not channel_rows:
                        list_key = signal_list_key(group, timeframe, token, key_prefix=profile.key_prefix)
                        raise RuntimeError(
                            f"signal_trans: không có signal nào cho {name} ({group})/{timeframe}/{token} "
                            f"trong Redis (key {list_key!r} rỗng hoặc không tồn tại)"
                        )
                    for row in channel_rows:
                        if any(column not in row for column in columns):
                            continue
                        rows.append({
                            "bartime": row["bartime"],
                            "atr": row["atr"],
                            "entry": row["entry"] if "entry" in columns else "",
                            "signal": row["signal"],
                            "tf": timeframe.upper(),
                            "symbol": name,
                            "strategy": token,
                            "risk_percent": f"{risk_percent:g}",
                        })
    finally:
        client.close()
    rows.sort(key=lambda row: (
        row["bartime"], strategy_order[row["strategy"]], frame_order[row["strategy"]][row["tf"]], symbol_order[row["symbol"]],
    ))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(SIGNAL_FILE_COLUMNS)]
    lines.extend(",".join(str(row[column]) for column in SIGNAL_FILE_COLUMNS) for row in rows)
    dest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def materialize_channel_csv(
    profile: RedisProfile,
    channels: Sequence[SignalChannel],
    dest_path: Path,
) -> int:
    """Write exactly the selected channels and their total risk per leg group.

    `profile` là nguồn MẶC ĐỊNH của lượt; kênh có `source` khác thì đọc từ đúng profile đó (mỗi kênh đúng một nguồn —
    trùng kênh đã bị chặn ở with_signal_overrides). Mở một kết nối cho mỗi nguồn dùng đến."""
    if not channels:
        raise ValueError("signal_trans: channels cannot be empty")
    channel_order = {
        (item.strategy, item.symbol, item.timeframe.upper()): index
        for index, item in enumerate(channels)
    }
    profiles: dict[str, RedisProfile] = {profile.id: profile}
    clients: dict[str, "redis.Redis"] = {}
    try:
        rows: list[dict[str, str]] = []
        for channel in channels:
            token = channel.strategy
            columns = STRATEGY_COLUMNS.get(token)
            if columns is None:
                raise ValueError(f"signal_trans: unknown strategy schema {token!r}")
            name = channel.symbol
            if name not in SYMBOL_GROUPS:
                raise KeyError(f"{name!r} has no signal Redis mapping")
            timeframe = channel.timeframe.upper()
            group = SYMBOL_GROUPS[name]
            source_id = channel.source or profile.id
            if source_id not in profiles:
                profiles[source_id] = load_redis_profile(source_id)
            source = profiles[source_id]
            if source_id not in clients:
                clients[source_id] = redis_client(source)
            channel_rows = fetch_signal_rows(clients[source_id], group, timeframe, token, key_prefix=source.key_prefix)
            if not channel_rows:
                list_key = signal_list_key(group, timeframe, token, key_prefix=source.key_prefix)
                raise RuntimeError(
                    f"signal_trans: no signals for {name} ({group})/{timeframe}/{token} "
                    f"in Redis (key {list_key!r} is empty or missing)"
                )
            for row in channel_rows:
                if any(column not in row for column in columns):
                    continue
                rows.append({
                    "bartime": row["bartime"],
                    "atr": row["atr"],
                    "entry": row["entry"] if "entry" in columns else "",
                    "signal": row["signal"],
                    "tf": timeframe,
                    "symbol": name,
                    "strategy": token,
                    "risk_percent": f"{channel.risk_percent:g}",
                })
    finally:
        for client in clients.values():
            client.close()
    rows.sort(key=lambda row: (
        row["bartime"], channel_order[(row["strategy"], row["symbol"], row["tf"])],
    ))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(SIGNAL_FILE_COLUMNS)]
    lines.extend(",".join(str(row[column]) for column in SIGNAL_FILE_COLUMNS) for row in rows)
    dest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)
