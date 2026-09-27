"""signal_trans.py — tầng Input trả lời đúng 1 câu hỏi: "signal cho spec này
lấy từ đâu?". [2026-09-21 lần 3] Redis là NGUỒN DUY NHẤT cho backend CLI —
không còn CSV tĩnh (`core_python` export qua SSHFS) hay override tay nào nữa,
theo yêu cầu người dùng ("cli phải đúng dựa trên luồng redis thôi, ko qua csv
nào cả"). Đường CSV tĩnh cũ đã XOÁ khỏi module này (không phải deprecate —
xoá hẳn, xem lịch sử ở `bo-workflow-backtest-pipeline.md` nếu cần đọc lại).

`resolve_signal_path()` LUÔN vật chất hoá tươi từ Redis (List+Hash, hệ
strategy_lab/SEN05) ra 1 CSV cùng khuôn cột mà LoadSignalFile() trong
Combo.cs/MA Cross.cs đang đọc, ghi vào thư mục `dest_dir` do bên gọi cấp (KHÔNG
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

[2026-09-26] DB2 vs DB3: strategy_lab publish song song 2 kênh cùng schema —
DB2 "L_PastSignal" (không lọc trend, hành vi gốc, KHÔNG đổi gì) và DB3
"L_PastSignal_Trend" (lọc theo trend khung lớn H4). Module này không tự biết
gì về "DB2"/"DB3" — chỉ đọc đúng `RedisProfile.key_prefix` (mặc định
"L_PastSignal") của profile được truyền vào. Đổi kênh = đổi `redis_profile`
trong config của experiment (`config.yaml` mục `redis:`), không cần sửa gì ở
đây hay thêm cờ CLI mới.
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import redis

from .configuration import RedisProfile, StrategyProfile
from .models import RunSpec

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
}

# StrategyProfile.strategy nội bộ (config.yaml `strategy:`, vd "macross" liền
# không gạch dưới) KHÁC tên STRATEGY dùng trong key Redis (bên strategy_lab/
# SEN05 tự đặt, CÓ gạch dưới — "ma_cross") — xác nhận lại 2026-09-21 từ chính
# bảng schema trong memory cbot-cli-simulation-redis-vision.md (dòng 57-62:
# "combo | ma_cross | ema_cross"). KHÔNG được suy diễn "macross" ra key Redis
# — sai key sẽ ra LIST rỗng (materialize_signal_csv coi là "chưa có signal",
# raise, không lặng lẽ trả CSV rỗng, nhưng vẫn tốn công tra ngược nếu gõ nhầm
# ở đây thay vì tra bảng này). Cùng string này dùng để chọn cột CSV
# (STRATEGY_COLUMNS bên dưới) — 2 việc dùng chung 1 token, không lệch nhau.
REDIS_STRATEGY_TOKENS = {
    "combo": "combo",
    "macross": "ma_cross",
}

# Khớp CHÍNH XÁC tên cột LoadSignalFile() tìm theo header (không theo vị trí)
# — xem Combo.cs/MA Cross.cs. Thứ tự ở đây chỉ để ghi CSV cho dễ đọc, không
# bắt buộc đúng thứ tự này khi cBot đọc lại. Key ở đây là REDIS_STRATEGY_TOKENS
# value (token thật của strategy_lab), không phải StrategyProfile.strategy.
STRATEGY_COLUMNS: dict[str, tuple[str, ...]] = {
    "combo": ("bartime", "atr", "entry", "signal"),
    "ma_cross": ("bartime", "atr", "signal"),
}


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

    Tên file đích chỉ phụ thuộc (symbol_group, timeframe, redis_strategy) —
    KHÔNG phụ thuộc KslLevel/KtpLevel/... — nên nhiều spec cùng symbol/timeframe
    trong 1 batch (vd quét lưới 10×10 KSL×KTP) trỏ ĐÚNG 1 file. `materialized`
    (một `set` do facilitator giữ xuyên suốt cả batch) đảm bảo Redis chỉ bị
    LRANGE/HGETALL đúng 1 lần cho mỗi tổ hợp đó, không phải 1 lần/spec.
    """
    if spec.symbol not in SYMBOL_GROUPS:
        raise KeyError(f"{spec.symbol!r} has no signal Redis mapping")
    if profile.strategy not in REDIS_STRATEGY_TOKENS:
        raise KeyError(f"{profile.strategy!r} has no Redis strategy token mapping")
    symbol_group = SYMBOL_GROUPS[spec.symbol]
    redis_strategy = REDIS_STRATEGY_TOKENS[profile.strategy]
    dest_path = dest_dir / f"{symbol_group}_{spec.timeframe.upper()}_{redis_strategy}.csv"
    if materialized is None or dest_path not in materialized:
        materialize_signal_csv(redis_profile, symbol_group, spec.timeframe, redis_strategy, dest_path)
        if materialized is not None:
            materialized.add(dest_path)
    return dest_path


def redis_client(profile: RedisProfile) -> "redis.Redis":
    """Tạo kết nối Redis từ profile trong config.yaml. Đọc pwd_file đúng 1
    lần ở đây — KHÔNG bao giờ log/in giá trị mật khẩu ra bất cứ đâu."""
    return redis.Redis(
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

    `key_prefix` mặc định "L_PastSignal" (DB2, không lọc trend). strategy_lab
    (2026-09-26) publish thêm DB3 song song, cùng khuôn key nhưng tiền tố
    "L_PastSignal_Trend" — gọi hàm này với key_prefix khác để đọc kênh đó,
    xem RedisProfile.key_prefix trong configuration.py."""
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


def write_signal_csv(rows: Sequence[Mapping[str, str]], dest_path: Path, strategy: str) -> int:
    """Ghi `rows` ra CSV đúng khuôn cột LoadSignalFile() cần cho `strategy`.

    Giữ NGUYÊN giá trị thô từ Redis (kể cả cột signal "1"/"2" theo
    ProtoOATradeSide) — không tự chuẩn hoá, vì cBot đã tự làm việc đó khi đọc
    file (xem LoadSignalFile() 2026-09-19/20). Trả về số dòng đã ghi."""
    columns = STRATEGY_COLUMNS.get(strategy)
    if columns is None:
        raise ValueError(f"signal_trans: không biết khuôn cột cho strategy {strategy!r}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    written = 0
    for row in rows:
        if any(col not in row for col in columns):
            # Dòng thiếu field bắt buộc — bỏ qua thay vì ghi dòng lỗi vào CSV
            # (LoadSignalFile() cũng tự bỏ dòng thiếu cột, nhưng lọc sớm ở
            # đây để dest_path phản ánh đúng số signal THẬT SỰ dùng được).
            continue
        lines.append(",".join(str(row[col]) for col in columns))
        written += 1
    dest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def signal_window_sha256(path: Path, end: date) -> str:
    """sha256 of the CSV header plus every row whose bartime date is before
    `end` (exclusive, same convention as the backtest end).

    strategy_lab keeps appending new signals to Redis, so the full CSV changes
    every few hours even when nothing inside a past backtest window changed.
    Rows at/after `end` can never influence a backtest that stops at `end`, so
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
        if day < end:
            kept.append(line)
    return hashlib.sha256(("\n".join(kept) + "\n").encode("utf-8")).hexdigest()


def materialize_signal_csv(
    profile: RedisProfile, symbol_group: str, timeframe: str, strategy: str, dest_path: Path
) -> int:
    """Hàm tiện dùng cho use-case chính: kết nối Redis, đọc toàn bộ signal,
    ghi CSV — 1 lần gọi duy nhất. Raise rõ ràng nếu không có signal nào, thay
    vì âm thầm ghi CSV rỗng khiến backtest "chạy thành công" nhưng 0 lệnh mà
    không ai biết vì sao.

    Đọc đúng kênh Redis mà `profile.key_prefix` chỉ định — DB2 ("L_PastSignal",
    mặc định) hoặc DB3 ("L_PastSignal_Trend", có lọc trend) tuỳ profile được
    truyền vào (xem `redis:` trong config.yaml)."""
    client = redis_client(profile)
    try:
        rows = fetch_signal_rows(client, symbol_group, timeframe, strategy, key_prefix=profile.key_prefix)
    finally:
        client.close()
    if not rows:
        list_key = signal_list_key(symbol_group, timeframe, strategy, key_prefix=profile.key_prefix)
        raise RuntimeError(
            f"signal_trans: không có signal nào cho {symbol_group}/{timeframe}/{strategy} "
            f"trong Redis (key {list_key!r} rỗng hoặc không tồn tại)"
        )
    return write_signal_csv(rows, dest_path, strategy)
