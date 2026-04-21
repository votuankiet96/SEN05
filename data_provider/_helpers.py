# =============================================================================
# data_provider/_helpers.py  —  Shared helpers for data pipeline / ws_live / checker
# =============================================================================
#
# MỤC ĐÍCH:
#   Chứa tất cả hàm dùng chung cho 3 script chính của data_provider:
#     - 01_data_pipeline.py  — backfill hàng ngày
#     - 02_ws_live.py        — cập nhật realtime qua WebSocket (24/7)
#     - 04_checker.py        — kiểm tra và tự sửa dữ liệu mỗi 3 ngày
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC NHÓM CHỨC NĂNG
# ─────────────────────────────────────────────────────────────────────────────
#
#   setup_logger()        — Tạo logger ghi đồng thời ra console + file.
#                           Hỗ trợ 2 chế độ: FileHandler (script thủ công) và
#                           RotatingFileHandler (tiến trình 24/7 như ws_live).
#
#   Hằng số cấu hình      — FULL_N_BARS, SAFETY_FACTOR, MIN_PULL_BARS,
#                           RETRY_DELAYS, FILL_THRESHOLD, SLEEP_GOLD/NORMAL,
#                           OVERNIGHT_GAP_MINUTES — điều chỉnh tại đây khi cần
#                           thay đổi hành vi pull/retry/gap-detection.
#
#   Hàm tiện ích nhỏ      — now_utc(), fmt_gap(), calc_gap_n_bars(),
#                           trading_hours_in_gap(), sleep_for()
#
#   _validate_ohlcv_df()  — Làm sạch DataFrame OHLCV trước khi ghi DB:
#                           lọc null, High<Low, duplicate timestamps, thứ tự
#                           không tăng, DST alignment (GOLD/BTCUSD), M45 anchor.
#
#   Verified market gaps  — load_verified_gaps() / save_verified_gaps():
#                           Đọc/ghi JSON cache những khoảng thị trường đóng cửa
#                           đã được xác nhận → skip lần chạy sau, không pull lại.
#
#   pull_and_store()      — Kéo OHLCV từ TradingView API → validate → Staging
#                           → Fact_OHLCV. Trả về số bar mới insert.
#   pull_with_retry()     — Wrapper gọi pull_and_store() với retry + backoff.
#
#   recompute_derived()   — Tính lại 5 TF phái sinh (M10, M20, M90, H6, H8)
#                           cho các symbol vừa nhận bar mới (GROUP BY trên Fact).
#
#   repull_full_symbol()  — Xóa Fact rows cũ và pull lại từ đầu cho 1 cặp
#                           (symbol, TF). Direct TF: xóa staging + pull TV.
#                           Derived TF: re-aggregate từ source TF.
#
#   find_hole_pairs()     — Quét Fact_OHLCV bằng SQL LEAD() để tìm lỗ hổng
#                           dữ liệu bên trong timeline (không phải thiếu ở rìa).
#                           Lọc bỏ gap qua đêm, cuối tuần, đã verified.
#
# ─────────────────────────────────────────────────────────────────────────────
# THÔNG SỐ ĐIỀU CHỈNH ĐƯỢC
# ─────────────────────────────────────────────────────────────────────────────
#   SAFETY_FACTOR          — Pull dư bao nhiêu % so với cần (mặc định 1.5 = +50%)
#   MIN_PULL_BARS          — Số bar tối thiểu dù gap nhỏ (mặc định 10)
#   RETRY_DELAYS           — Thời gian chờ giữa các lần retry: [10, 30, 60] giây
#   SLEEP_GOLD/NORMAL      — Nghỉ giữa request: GOLD=10s, còn lại=5s
#   OVERNIGHT_GAP_MINUTES  — Ngưỡng gap qua đêm bình thường theo asset type
#   HOLE_LOOKBACK_DAYS     — Quét lỗ hổng trong bao nhiêu ngày gần nhất (60)
#
#   ⚠️ Thay đổi các ngưỡng này ảnh hưởng trực tiếp đến cách hệ thống phân biệt
#      "gap thật cần fill" vs "thị trường đóng cửa bình thường".
# =============================================================================

import json  # Đọc/ghi file JSON (verified_market_gaps.json)
import logging          # Hệ thống logging của Python
import logging.handlers  # RotatingFileHandler — dùng cho ws_live 24/7
import math  # math.ceil() để làm tròn lên số bar cần pull
import os  # Thao tác đường dẫn file
import sys  # Thêm đường dẫn import
import time  # time.sleep() để rate-limit giữa các request TV
from datetime import datetime, timedelta, timezone  # Xử lý thời gian UTC

# ---------------------------------------------------------------------------
# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
# ---------------------------------------------------------------------------
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.dirname(os.path.abspath(__file__))   # data_provider/ directory
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# ---------------------------------------------------------------------------
# Import cấu hình từ config.py và các hàm DB từ db_connector.py
# ---------------------------------------------------------------------------
from config import (
    COMPUTED_TIMEFRAMES,  # Tất cả TF (direct + derived)
    DERIVED_TFS,  # Các TF phái sinh (tính từ TF gốc): {"M10","M20","M90","H6","H8"}
    DIRECT_TFS,  # Các TF kéo trực tiếp từ TV: {"M5","M15","M30","M45","H1","H2","H3","H4","D1","W"}
    LOG_LEVEL,  # Mức log: "INFO", "DEBUG", "WARNING"...
    N_BARS_D1,
    N_BARS_H1,
    N_BARS_H2,
    N_BARS_H3,
    N_BARS_H4,
    N_BARS_M5,
    N_BARS_M15,
    N_BARS_M30,
    N_BARS_M45,
    # Số bar tối đa cần pull cho mỗi TF (dùng khi cần FULL LOAD hoặc thiếu data)
    N_BARS_W,
    SYMBOL_OVERNIGHT_MINS,  # Per-symbol overnight gap threshold (phút)
    FIXED_H_ALIGNMENT,  # DST alignment cố định: GOLD h%N==1, BTCUSD h%N==0
    SYMBOLS,  # Danh sách symbol: [{symbol_id, tv_symbol, tv_exchange, asset_type}, ...]
    TF_MINUTES,  # Map tf_code → số phút: "H1" → 60, "M15" → 15
    TF_STAGING,  # Map tf_code → tên bảng staging: "H1" → "Staging_H1"
    WEEKEND_CLOSED,  # Set các asset_type nghỉ cuối tuần: {"Indice","Metal","FOREX"}
)
from modules.db_connector import (
    aggregate_from_fact,  # Tính TF phái sinh bằng GROUP BY trên Fact_OHLCV
    clean_staging_transitions,  # Xóa transition bar do DST shift sau insert staging
    delete_fact_bars,  # Xóa rows Fact_OHLCV trước khi repull
    delete_staging_bars,  # Xóa rows staging trước khi repull
    get_candle_count,  # Đếm số bar trong Fact_OHLCV (kiểm tra source TF có data chưa)
    get_internal_gaps,  # Chạy SQL LEAD() tìm khoảng gap trong Fact_OHLCV
    insert_staging_batch,  # Ghi DataFrame OHLCV vào bảng Staging (MERGE, chống duplicate)
    run_etl_direct,  # Gọi usp_LoadDirect: chuyển Staging → Fact_OHLCV
)

# ---------------------------------------------------------------------------
# Tạo logger — ghi log ra cả console lẫn file
# ---------------------------------------------------------------------------

def setup_logger(name: str, log_file: str, rotating: bool = False) -> logging.Logger:
    """
    Tạo (hoặc lấy lại) một logger có tên `name`.
    Logger sẽ ghi ra 2 nơi đồng thời:
      - Console (stdout) — để theo dõi realtime khi chạy
      - File log — để xem lại sau

    Tham số:
      rotating=False (mặc định) — FileHandler thường, phù hợp script chạy thủ công.
      rotating=True             — RotatingFileHandler (10 MB × 5 files), phù hợp
                                  tiến trình 24/7 như ws_live để tránh log phình vô hạn.

    Nếu logger đã được cấu hình rồi (gọi lần 2) → trả về ngay, không tạo lại.
    """
    logger = logging.getLogger(name)
    if logger.handlers:          # Đã cấu hình rồi → trả về luôn
        return logger
    # Đảm bảo stdout hỗ trợ UTF-8 (tránh UnicodeEncodeError trên Windows CP1252)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)   # Handler ghi ra console
    sh.setFormatter(fmt)
    if rotating:
        # Tối đa 6 files × 10 MB = 60 MB — đủ giữ ~10-14 ngày log ws_live
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
    else:
        fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# Hằng số cấu hình
# ---------------------------------------------------------------------------

# Số bar tối đa cho mỗi TF — dùng khi cần FULL LOAD (lần đầu hoặc thiếu toàn bộ)
# Ví dụ: H1 = 5000 bar ≈ 208 ngày dữ liệu 1 giờ
FULL_N_BARS = {
    "W":   N_BARS_W,   "D1":  N_BARS_D1,
    "H4":  N_BARS_H4,  "H3":  N_BARS_H3,  "H2":  N_BARS_H2,
    "H1":  N_BARS_H1,  "M45": N_BARS_M45,
    "M30": N_BARS_M30, "M15": N_BARS_M15, "M5":  N_BARS_M5,
}

# Hệ số an toàn: pull thêm 50% so với cần thiết để đảm bảo đủ data
SAFETY_FACTOR  = 1.5

# Số bar tối thiểu — dù gap nhỏ vẫn pull ít nhất 10 bar
MIN_PULL_BARS  = 10

# Return codes từ pull_and_store()
RESULT_ERROR    = -1   # TV exception / lỗi nghiêm trọng
RESULT_TV_EMPTY = -2   # TV trả về rỗng (không có data, không phải lỗi kỹ thuật)

# Thời gian chờ (giây) giữa mỗi lần retry — tăng dần để tránh rate-limit
RETRY_DELAYS    = [10, 30, 60]

# Ngưỡng để phân biệt "gap được fill thật" vs "chỉ có vài bar mới ở rìa"
# Nếu pull thành công mà chỉ insert < FILL_THRESHOLD bar mới vào Fact,
# rất có thể gap là market gap (nghỉ lễ), các bar mới chỉ là bar trading gần đây.
FILL_THRESHOLD = 5

# ATR multiplier cho price continuity check (02_gap_fill.py --price-check).
# Spike bị phát hiện khi: |close[i] - open[i+1]| / ATR14 > SPIKE_ATR_THRESHOLD.
# Chỉ áp dụng cho các cặp bar thực sự liên tiếp (gap_minutes == TF_minutes ± 1).
SPIKE_ATR_THRESHOLD = 3.0

# Thời gian nghỉ giữa các request TradingView (giây)
# Tránh bị TV rate-limit hoặc block. GOLD cần nghỉ lâu hơn.
SLEEP_GOLD   = 10   # giây — cho Gold
SLEEP_NORMAL = 5    # giây — cho tất cả symbol khác

# Quét lỗ hổng trong bao nhiêu ngày gần nhất (mặc định 60 ngày)
HOLE_LOOKBACK_DAYS = 60

# Ngưỡng gap qua đêm BÌNH THƯỜNG theo loại asset (phút)
# Gap nhỏ hơn ngưỡng này = thị trường đóng cửa hàng ngày, KHÔNG phải lỗ hổng
# Gap lớn hơn ngưỡng này = có khả năng thiếu dữ liệu thực sự
OVERNIGHT_GAP_MINUTES = {
    "Indice": 1080,  # ~18 giờ — thị trường chứng khoán chỉ mở ~6h/ngày
    "Metal":  180,   # ~3 giờ  — vàng nghỉ 1 khoảng ngắn giữa phiên
    "FOREX":  150,   # ~2.5 giờ — forex gần như 24h nhưng có gap nhỏ
    "Crypto": 0,     # 0 phút  — crypto giao dịch 24/7, mọi gap đều bất thường
}

# Đường dẫn file JSON lưu market gap đã xác nhận
# (gap do thị trường đóng cửa, KHÔNG phải thiếu data → skip lần sau)
# Lưu trong cache/ ở project root để tách biệt runtime data khỏi source code
_VERIFIED_GAPS_FILE = os.path.join(_PROJ, "cache", "verified_market_gaps.json")

# Source TF cho từng derived TF — dùng để kiểm tra source có data trước khi aggregate
_SOURCE_TF = {
    "M10": "M5",
    "M20": "M5",
    "M90": "M30",
    "H6":  "H3",
    "H8":  "H4",
}


# ---------------------------------------------------------------------------
# Hàm tiện ích nhỏ
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    """Lấy thời gian hiện tại theo UTC (không kèm timezone info để tương thích SQL Server)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fmt_gap(hours: float) -> str:
    """
    Format số giờ gap thành chuỗi dễ đọc:
      0.5 giờ  → "30m"
      3.5 giờ  → "3.5h"
      72 giờ   → "3.0d"
    """
    if hours < 1:
        return f"{hours*60:.0f}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours/24:.1f}d"


def calc_gap_n_bars(gap_hours: float, tf_code: str, asset_type: str) -> int:
    """
    Ước tính số bar cần kéo từ TradingView để lấp đầy 1 lỗ hổng.

    Công thức:
      bars_needed = (gap_hours × 60 / tf_minutes) × trading_ratio × safety_factor

    Giải thích:
      - gap_hours × 60 / tf_minutes = số bar lý thuyết cần để phủ toàn bộ gap
      - trading_ratio = 5/7 nếu asset nghỉ cuối tuần (bớt 2 ngày nghỉ)
                       = 1.0 nếu crypto (giao dịch 24/7)
      - safety_factor = 1.5 (pull dư 50% để chắc chắn đủ, TV có thể thiếu bar)
      - Tối thiểu pull 10 bar, tối đa = FULL_N_BARS của TF đó
    """
    tf_mins = TF_MINUTES[tf_code]
    trading_ratio = 5 / 7 if asset_type in WEEKEND_CLOSED else 1.0
    bars_needed = (gap_hours * 60 / tf_mins) * trading_ratio
    n = max(MIN_PULL_BARS, math.ceil(bars_needed * SAFETY_FACTOR))
    return min(n, FULL_N_BARS.get(tf_code, 10000))


def trading_hours_in_gap(start: datetime, end: datetime) -> float:
    """
    Ước tính số giờ GIAO DỊCH THỰC trong khoảng [start, end].
    Trừ đi giờ Saturday + Sunday (thị trường nghỉ cuối tuần).

    Ví dụ: gap từ thứ 6 → thứ 2 = 72h tổng, nhưng chỉ ~24h trading
    (trừ 48h cuối tuần).

    Dùng để đánh giá: gap này có thực sự lớn không, hay chỉ do cuối tuần?
    """
    total_hours = max(0.0, (end - start).total_seconds() / 3600)
    # Tính nhanh: mỗi tuần đầy đủ có 48h cuối tuần (Sat + Sun)
    full_weeks  = int(total_hours // 168)  # 168 = 7 ngày × 24h
    weekend_h   = full_weeks * 48.0

    # Phần lẻ (không đủ 1 tuần): đếm từng giờ xem rơi vào Sat/Sun không
    t         = start + timedelta(weeks=full_weeks)
    walked    = 0.0
    remaining = total_hours - full_weeks * 168
    while walked < remaining and t < end:
        if t.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
            weekend_h += 1.0   # Giờ này là cuối tuần → đếm vào weekend
        t      += timedelta(hours=1)
        walked += 1.0
    return max(0.0, total_hours - weekend_h)  # Tổng - cuối tuần = trading hours


def sleep_for(tv_symbol: str) -> None:
    """
    Nghỉ giữa các request TradingView để tránh bị rate-limit.
    GOLD nghỉ 10 giây (API nặng hơn), các mã khác nghỉ 5 giây.
    """
    time.sleep(SLEEP_GOLD if tv_symbol == "GOLD" else SLEEP_NORMAL)


def _validate_ohlcv_df(df, tv_symbol: str, tf_code: str,
                       logger: logging.Logger):
    """
    Kiểm tra tính hợp lệ của DataFrame OHLCV trước khi ghi vào DB.

    Các kiểm tra (theo thứ tự):
      1. Null trong Open/High/Low/Close → loại bỏ row đó
      2. High < Low (giá đảo ngược) → loại bỏ row đó
      3. Timestamp trùng lặp → giữ lại row đầu tiên
      4. Timestamp không tăng dần → sắp xếp lại
      5. DST alignment cho GOLD/BTCUSD H2/H3/H4 — các symbol này có alignment cố
         định quanh năm; bars lệch giờ do DST sẽ bị loại (xem FIXED_H_ALIGNMENT)
      6. M45 alignment — tự phát hiện offset anchor từ bar đầu tiên; bars có
         remainder % 45 khác anchor (DST-glitch) sẽ bị loại

    Trả về (cleaned_df, had_issues):
      - cleaned_df  : DataFrame sau khi làm sạch (có thể empty nếu tất cả đều lỗi)
      - had_issues  : True nếu có bất kỳ vấn đề nào được phát hiện
    """
    if df is None or df.empty:
        return df, False

    original_len = len(df)
    had_issues   = False

    # 1. Kiểm tra null trong các cột OHLC bắt buộc
    ohlc_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if ohlc_cols:
        null_mask = df[ohlc_cols].isnull().any(axis=1)
        if null_mask.any():
            logger.warning("  VALIDATE %s %s: %d rows with null OHLC → dropped",
                           tv_symbol, tf_code, int(null_mask.sum()))
            df         = df[~null_mask]
            had_issues = True

    if df.empty:
        return df, had_issues

    # 2. Kiểm tra High >= Low (giá trị hợp lệ)
    if "high" in df.columns and "low" in df.columns:
        invalid_hl = df["high"] < df["low"]
        if invalid_hl.any():
            logger.warning("  VALIDATE %s %s: %d rows with High < Low → dropped",
                           tv_symbol, tf_code, int(invalid_hl.sum()))
            df         = df[~invalid_hl]
            had_issues = True

    if df.empty:
        return df, had_issues

    # 3. Kiểm tra timestamp trùng lặp
    if df.index.duplicated().any():
        n_dupes = int(df.index.duplicated().sum())
        logger.warning("  VALIDATE %s %s: %d duplicate timestamps → kept first",
                       tv_symbol, tf_code, n_dupes)
        df         = df[~df.index.duplicated(keep="first")]
        had_issues = True

    # 4. Kiểm tra timestamp tăng dần (monotonic)
    if not df.index.is_monotonic_increasing:
        logger.warning("  VALIDATE %s %s: timestamps not monotonic → sorted",
                       tv_symbol, tf_code)
        df         = df.sort_index()
        had_issues = True

    # 5. Kiểm tra DST alignment cho GOLD và BTCUSD (H2/H3/H4)
    # Các symbol này có alignment cố định quanh năm — lọc bars lệch giờ trước khi insert.
    if tf_code in FIXED_H_ALIGNMENT.get(tv_symbol, {}):
        tf_hours    = int(tf_code[1:])  # H4→4, H3→3, H2→2
        expected    = FIXED_H_ALIGNMENT[tv_symbol][tf_code]
        wrong_align = df.index.hour % tf_hours != expected
        if wrong_align.any():
            n_bad = int(wrong_align.sum())
            logger.warning(
                "  VALIDATE %s %s: %d bars alignment sai (h%%%d != %d) → dropped",
                tv_symbol, tf_code, n_bad, tf_hours, expected,
            )
            df         = df[~wrong_align]
            had_issues = True

    # 6. M45 alignment check — tự phát hiện anchor từ bar đầu tiên, không hardcode.
    # TV M45 không nhất thiết bắt đầu tại bội số 45 từ UTC midnight.
    # DST-glitch bars có remainder khác anchor → bị loại bỏ.
    if tf_code == "M45":
        total_min   = df.index.hour * 60 + df.index.minute
        anchor      = int(total_min[0]) % 45
        wrong_align = total_min % 45 != anchor
        if wrong_align.any():
            n_bad = int(wrong_align.sum())
            logger.warning(
                "  VALIDATE %s M45: %d bars alignment sai (anchor=%d, not consistent) → dropped",
                tv_symbol, n_bad, anchor,
            )
            df         = df[~wrong_align]
            had_issues = True

    if had_issues:
        dropped = original_len - len(df)
        if dropped > 0:
            logger.warning("  VALIDATE %s %s: %d/%d rows removed total",
                           tv_symbol, tf_code, dropped, original_len)

    return df, had_issues


# ---------------------------------------------------------------------------
# Cache market gap đã xác nhận (dùng bởi gap_fill)
# ---------------------------------------------------------------------------
# Khi gap_fill pull dữ liệu TV cho 1 hole mà DB đã có đủ (0 bar mới),
# nó xác nhận hole đó là "market gap" (thị trường đóng cửa, không phải thiếu data).
# Lưu vào file JSON để lần chạy sau SKIP, không pull TV lãng phí.
# File tự hết hạn sau 30 ngày.
#
# Format mới (v2): lưu theo gap window cụ thể (sym_id, tf_code, gap_start, gap_end)
# thay vì chỉ lưu pair — tránh skip nhầm hole thật mới phát sinh sau gap cũ.
# ---------------------------------------------------------------------------

def load_verified_gaps() -> dict:
    """
    Đọc các gap window đã xác nhận là market gap từ verified_market_gaps.json.

    Trả về:
      dict: (symbol_id, tf_code) → list[(gap_start, gap_end)]
      Mỗi entry là một khoảng thời gian cụ thể đã xác nhận là market gap.

    Trả về {} nếu:
      - File không tồn tại
      - File đã quá 30 ngày
      - File bị lỗi JSON / format cũ không tương thích
    """
    try:
        with open(_VERIFIED_GAPS_FILE) as f:
            data = json.load(f)
        saved = datetime.fromisoformat(data["verified_at"])
        # Hết hạn sau 30 ngày → quét lại từ đầu
        if (now_utc() - saved).days > 30:
            return {}
        # Format mới: "windows" key
        if "windows" not in data:
            # File format cũ ("pairs") — treat as expired để force re-verify
            return {}
        result: dict = {}
        for entry in data["windows"]:
            key = (entry[0], entry[1])  # (sym_id, tf_code)
            window = (
                datetime.fromisoformat(entry[2]),
                datetime.fromisoformat(entry[3]),
            )
            result.setdefault(key, []).append(window)
        return result
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return {}


def save_verified_gaps(windows: set, logger: logging.Logger) -> None:
    """
    Ghi các gap window đã xác nhận vào verified_market_gaps.json.

    Tham số:
      windows — set of (symbol_id, tf_code, gap_start: datetime, gap_end: datetime)
    """
    data = {
        "verified_at": now_utc().isoformat(),
        "windows": sorted([
            [sid, tfc, gs.isoformat(), ge.isoformat()]
            for sid, tfc, gs, ge in windows
        ]),
    }
    try:
        os.makedirs(os.path.dirname(_VERIFIED_GAPS_FILE), exist_ok=True)
        with open(_VERIFIED_GAPS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        unique_pairs = {(s, t) for s, t, _, _ in windows}
        logger.info("Saved %d verified gap windows (%d pairs).",
                    len(windows), len(unique_pairs))
    except OSError as e:
        logger.warning("Could not save verified gaps: %s", e)


# ---------------------------------------------------------------------------
# HÀM CHÍNH: Kéo dữ liệu 1 cặp (symbol, TF) từ TradingView và ghi vào DB
# ---------------------------------------------------------------------------
#
# Đây là hàm quan trọng nhất — được gọi cho MỖI hole cần fill.
# Flow: TradingView API → DataFrame → Staging table → Fact_OHLCV
# ---------------------------------------------------------------------------

def pull_and_store(tv, sym: dict, tf_code: str,
                   n_bars: int, interval,
                   logger: logging.Logger,
                   skip_etl: bool = False) -> int:
    """
    Kéo n_bars nến OHLCV từ TradingView cho 1 cặp (symbol, timeframe),
    sau đó ghi vào database qua 2 bước: Staging → Fact.

    Tham số:
      tv        — kết nối TradingView (tvDatafeed instance)
      sym       — dict thông tin symbol: {symbol_id, tv_symbol, tv_exchange, asset_type}
      tf_code   — mã timeframe: "H1", "M15", "M30"...
      n_bars    — số bar cần kéo (đã nhân hệ số an toàn)
      interval  — interval object mà TV API cần (ví dụ: Interval.in_1_hour)
      logger    — logger để ghi log
      skip_etl  — nếu True: chỉ pull vào Staging, KHÔNG chạy ETL sang Fact.
                  Dùng trong _repair_pair() để đảm bảo data an toàn trong staging
                  trước khi xóa bars sai trong Fact.

    Trả về (skip_etl=False, mặc định):
      ≥ 1  — số bar MỚI được insert vào Fact_OHLCV (thành công, có data mới)
        0  — pull thành công nhưng 0 bar mới (DB đã có đủ = market gap)
       -1  — thất bại do exception (TV lỗi kỹ thuật, timeout...)
       -2  — TV trả về rỗng (không có data cho khoảng này)

    Trả về (skip_etl=True):
      ≥ 0  — số bar đã stage vào Staging (thành công; 0 = không có bar mới)
       -1  — thất bại do exception
       -2  — TV trả về rỗng
    """
    symbol_id   = sym["symbol_id"]
    tv_symbol   = sym["tv_symbol"]
    tv_exchange = sym["tv_exchange"]
    staging     = TF_STAGING[tf_code]  # Tên bảng staging: "Staging_H1", "Staging_M15"...

    # ----- BƯỚC A: Kéo dữ liệu từ TradingView API -----
    # Pull thêm 5 bar dự phòng (n_bars + 5) vì bar cuối sẽ bị bỏ
    try:
        df = tv.get_hist(
            symbol   = tv_symbol,
            exchange = tv_exchange,
            interval = interval,
            n_bars   = n_bars + 5,
        )
    except Exception as e:
        logger.error("  TV pull FAIL — %s %s: %s", tv_symbol, tf_code, e)
        return RESULT_ERROR

    # Nếu TV trả về rỗng → TV không có data cho khoảng thời gian này
    # (khác với lỗi kỹ thuật — đây có thể là market gap thật)
    if df is None or df.empty:
        logger.warning("  TV returned empty — %s %s", tv_symbol, tf_code)
        return RESULT_TV_EMPTY

    # Cảnh báo nếu TV trả về < 50% số bar yêu cầu (có thể TV bị giới hạn)
    returned_bars = len(df)
    if returned_bars < n_bars * 0.5:
        try:
            from _tv_auth import get_auth_mode as _get_auth_mode
            _mode = _get_auth_mode()
        except Exception:
            _mode = "unknown"
        _auth_hint = " — ⚠️ đang GUEST MODE (bar limit ~500)" if _mode == "guest" else ""
        logger.warning(
            "  TV returned only %d/%d bars (%.0f%%) — %s %s%s",
            returned_bars, n_bars,
            returned_bars / n_bars * 100,
            tv_symbol, tf_code, _auth_hint,
        )

    # ----- BỎ BAR CUỐI CÙNG -----
    # Bar cuối rất có thể ĐANG MỞ (chưa đóng xong) → dữ liệu OHLCV chưa chính xác.
    # Ví dụ: nến H1 lúc 14:30 mới chạy được nửa → O/H/L/C chưa phải giá trị cuối.
    # → Bỏ đi để chỉ giữ các bar đã đóng hoàn toàn.
    df = df.iloc[:-1]
    if df.empty:
        logger.warning("  Only 1 bar returned (open) — %s %s", tv_symbol, tf_code)
        return RESULT_TV_EMPTY

    # ----- BƯỚC A2: Kiểm tra chất lượng dữ liệu trước khi ghi -----
    df, _ = _validate_ohlcv_df(df, tv_symbol, tf_code, logger)
    if df.empty:
        logger.warning("  All bars failed validation — %s %s", tv_symbol, tf_code)
        return RESULT_TV_EMPTY

    # ----- BƯỚC B: Ghi vào Staging (MERGE — chống duplicate) -----
    # insert_staging_batch() tạo temp table → bulk insert → MERGE vào staging
    # Nếu row đã tồn tại (cùng SymbolID + BarTime) → bỏ qua, chỉ insert row mới
    staged = insert_staging_batch(df, symbol_id, staging)

    # ----- BƯỚC B2: Dọn transition bar DST / anchor drift khỏi staging -----
    # M45/H2/H3/H4: Capital.com dịch chuyển UTC offset qua DST → bar anchor drift.
    # Xoá ngay sau insert để Fact không nhận bar nhiễm.
    if tf_code in ('M45', 'H2', 'H3', 'H4'):
        from config import TF_MINUTES
        n_cleaned = clean_staging_transitions(symbol_id, staging, TF_MINUTES[tf_code])
        if n_cleaned > 0:
            logger.info("  ANCHOR_CLEAN %s %s: removed %d transition bar(s) from staging",
                        tv_symbol, tf_code, n_cleaned)

    # ----- BƯỚC C: Chuyển Staging → Fact_OHLCV (stored procedure) -----
    # skip_etl=True: caller (VD: _repair_pair) sẽ tự gọi ETL sau khi đã xóa
    # bars sai khỏi Fact. Đây là cơ chế đảm bảo không mất data: staging được
    # điền trước, chỉ sau đó mới xóa Fact và chạy ETL.
    if skip_etl:
        logger.info("  ○ %s %s: %d staged (ETL deferred to caller)",
                    tv_symbol, tf_code, staged)
        return staged

    # run_etl_direct() gọi DWH.usp_LoadDirect → chuyển row từ staging sang Fact
    # Cũng dùng NOT EXISTS chống duplicate. Trả về số row mới insert vào Fact.
    try:
        etl_inserted = run_etl_direct(symbol_id, tf_code, staging)
    except Exception as e:
        logger.error("  ETL FAIL — %s %s: %s — cleaning staging", tv_symbol, tf_code, e)
        try:
            delete_staging_bars(symbol_id, staging)
        except Exception as e2:
            logger.warning("  Staging cleanup FAIL — %s %s: %s", tv_symbol, tf_code, e2)
        return RESULT_ERROR

    # ----- Ghi log kết quả -----
    if etl_inserted > 0:
        # CÓ bar mới → ghi ✓
        logger.info("  ✓ %s %s: +%d bars → Fact_OHLCV (staged %d)",
                    tv_symbol, tf_code, etl_inserted, staged)
    elif staged > 0:
        # Staging có row mới nhưng Fact đã có đủ → ○ (market gap)
        logger.info("  ○ %s %s: %d staged, 0 new in Fact (already existed)",
                    tv_symbol, tf_code, staged)
    else:
        # Cả staging lẫn Fact đều không có gì mới → đã up to date
        logger.info("  ○ %s %s: 0 new bars (already up to date)",
                    tv_symbol, tf_code)
    return etl_inserted


def pull_with_retry(tv, sym: dict, tf_code: str, n_bars: int, interval,
                    logger: logging.Logger, max_retries: int = 3) -> int:
    """
    Gọi pull_and_store() tối đa (1 + max_retries) lần.
    Retry khi kết quả là RESULT_TV_EMPTY hoặc RESULT_ERROR (< 0).
    Backoff tăng dần theo RETRY_DELAYS: 10s → 30s → 60s.
    Trả về kết quả của lần thử cuối cùng.
    """
    result = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)

    for attempt in range(1, max_retries + 1):
        if result >= 0:
            break
        delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
        logger.warning(
            "  [Retry %d/%d] %s %s — waiting %ds...",
            attempt, max_retries, sym["tv_symbol"], tf_code, delay,
        )
        time.sleep(delay)
        result = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)

    if result < 0:
        logger.error(
            "  [FINAL FAIL] %s %s — failed after %d attempt(s).",
            sym["tv_symbol"], tf_code, max_retries + 1,
        )
    return result


# ---------------------------------------------------------------------------
# Tính lại timeframe phái sinh từ dữ liệu đã có trong Fact_OHLCV
# ---------------------------------------------------------------------------
# 5 TF này KHÔNG kéo trực tiếp từ TradingView mà tính từ TF gốc:
#   M5  → gộp thành M10 (2 nến M5 = 1 nến M10)
#   M5  → gộp thành M20 (4 nến M5 = 1 nến M20)
#   M30 → gộp thành M90 (3 nến M30 = 1 nến M90)
#   H3  → gộp thành H6  (2 nến H3 = 1 nến H6)
#   H4  → gộp thành H8  (2 nến H4 = 1 nến H8)
# Chỉ tính lại cho symbol nào vừa nhận bar mới (tiết kiệm thời gian).
# ---------------------------------------------------------------------------

def recompute_derived(updated_sym_ids: set,
                      logger: logging.Logger) -> tuple[int, int]:
    """
    Tính lại M10/M20/M90/H6/H8 cho các symbol vừa nhận bar mới.

    Duyệt qua từng symbol trong updated_sym_ids × từng TF phái sinh,
    gọi aggregate_from_fact() để GROUP BY trên Fact_OHLCV và insert kết quả.

    Trả về (ok, fail): số cặp thành công / thất bại.
    """
    if not updated_sym_ids:
        return 0, 0

    derived_list = list(DERIVED_TFS)  # ["M10", "M20", "M90", "H6", "H8"]
    ok = fail = 0

    logger.info("Recomputing %d derived TFs for %d symbol(s)...",
                len(derived_list), len(updated_sym_ids))

    # Duyệt: mỗi symbol đã cập nhật × mỗi TF phái sinh
    for sym in SYMBOLS:
        if sym["symbol_id"] not in updated_sym_ids:
            continue  # Symbol này không có bar mới → bỏ qua
        sym_id   = sym["symbol_id"]
        _src_cnt: dict = {}  # Cache số bar source TF để tránh query DB nhiều lần
        for target_tf in derived_list:
            # Kiểm tra source TF có data trước khi aggregate
            src_tf = _SOURCE_TF.get(target_tf)
            if src_tf:
                if src_tf not in _src_cnt:
                    _src_cnt[src_tf] = get_candle_count(sym_id, src_tf)
                if _src_cnt[src_tf] == 0:
                    logger.warning("  derived SKIP %s %s: source %s has 0 bars",
                                   sym["tv_symbol"], target_tf, src_tf)
                    continue
            try:
                # aggregate_from_fact() chạy SQL GROUP BY trên Fact_OHLCV
                # để tạo nến TF lớn hơn từ TF nhỏ hơn (ví dụ M5 → M10)
                rows = aggregate_from_fact(sym_id, target_tf)
                ok  += 1
                if rows > 0:
                    logger.debug("  derived %s %s: +%d rows",
                                 sym["tv_symbol"], target_tf, rows)
            except Exception as e:
                logger.error("  derived FAIL %s %s: %s",
                             sym["tv_symbol"], target_tf, e)
                fail += 1

    logger.info("Derived TFs done: %d OK, %d failed.", ok, fail)
    return ok, fail


# ---------------------------------------------------------------------------
# Full re-pull một (symbol, TF) sau khi phát hiện dữ liệu bị hỏng
# ---------------------------------------------------------------------------

def repull_full_symbol(tv, sym: dict, tf_code: str, interval,
                       logger: logging.Logger) -> tuple[int, int]:
    """
    Xóa toàn bộ dữ liệu Fact_OHLCV cho (symbol, TF) và lấy lại từ đầu.

    DIRECT TFs (M5, M15, M30, M45, H1, H2, H3, H4, D1, W):
      1. Xóa staging cũ (tránh MERGE giữ lại giá trị sai cũ)
      2. Xóa Fact_OHLCV
      3. Pull FULL_N_BARS từ TradingView → Staging → Fact

    DERIVED TFs (M10, M20, M90, H6, H8):
      1. Xóa Fact_OHLCV (không có staging cho derived TFs)
      2. Re-aggregate từ source TF đang có trong Fact_OHLCV

    Trả về (n_deleted, n_inserted).
    """
    symbol_id = sym["symbol_id"]
    tv_symbol = sym["tv_symbol"]

    # ----- Bước 1: Xóa Fact rows cũ -----
    n_deleted = delete_fact_bars(symbol_id, tf_code)
    logger.info("  repull %s %s: deleted %d Fact rows",
                tv_symbol, tf_code, n_deleted)

    # ----- Bước 2a: DERIVED TF → re-aggregate từ source -----
    if tf_code in DERIVED_TFS:
        try:
            n_inserted = aggregate_from_fact(symbol_id, tf_code)
            logger.info("  repull %s %s: re-aggregated %d bars",
                        tv_symbol, tf_code, n_inserted)
            return n_deleted, n_inserted
        except Exception as e:
            logger.error("  repull %s %s re-aggregate FAIL: %s",
                         tv_symbol, tf_code, e)
            return n_deleted, 0

    # ----- Bước 2b: DIRECT TF → xóa staging + pull từ TV -----
    staging = TF_STAGING.get(tf_code)
    if staging:
        delete_staging_bars(symbol_id, staging)

    n_bars  = FULL_N_BARS.get(tf_code, 5000)
    result  = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)
    n_inserted = result if result > 0 else 0
    return n_deleted, n_inserted


# ---------------------------------------------------------------------------
# Tìm lỗ hổng dữ liệu trong Fact_OHLCV (dùng bởi gap_fill)
# ---------------------------------------------------------------------------
#
# FLOW:
#   1. Chạy SQL LEAD() trên Fact_OHLCV → tìm mọi gap > 10 phút (raw gaps)
#   2. Lọc bỏ: gap đã verified, gap < ngưỡng overnight, gap cuối tuần
#   3. Với gap còn lại (hole thực): tính số bar cần pull
#   4. Trả danh sách [{sym, tf_code, last_bar, n_bars, ...}]
# ---------------------------------------------------------------------------

def find_hole_pairs(stale: list, logger: logging.Logger,
                    verified_gaps: set | None = None,
                    lookback_days: int = HOLE_LOOKBACK_DAYS) -> list:
    """
    Quét Fact_OHLCV tìm lỗ hổng dữ liệu (bars thiếu giữa timeline).

    Tham số:
      stale          — danh sách pair đang chờ pull (từ backfill pipeline).
                       Nếu hole trùng pair đã có trong stale → nâng n_bars.
      logger         — logger để ghi log
      verified_gaps  — dict (symbol_id, tf_code) → list[(gap_start, gap_end)]
                       đã xác nhận là market gap → bỏ qua gap window đó

    Trả về:
      Danh sách hole MỚI (không trùng stale) cần pull từ TradingView.
    """

    # ----- BƯỚC 1: Chạy SQL tìm raw gaps -----
    # get_internal_gaps() dùng LEAD() so sánh BarTime với NextBarTime.
    # Mọi khoảng cách > 10 phút giữa 2 bar liên tiếp đều được trả về.
    raw = get_internal_gaps(list(DIRECT_TFS), lookback_days=lookback_days)
    if not raw:
        logger.info("Hole check: no internal gaps detected.")
        return []

    now = now_utc()

    # Index bảng stale để tra nhanh: (sym_id, tf_code) → vị trí trong list
    stale_index = {(x["sym"]["symbol_id"], x["tf_code"]): i
                   for i, x in enumerate(stale)}
    # Map symbol_id → dict symbol đầy đủ (để tra thông tin asset_type, tv_symbol...)
    sym_map = {s["symbol_id"]: s for s in SYMBOLS}

    new_holes       = []   # Kết quả: danh sách hole mới cần trả về
    n_raw           = sum(len(v) for v in raw.values())  # Tổng raw gaps
    n_excluded      = 0    # Đếm gap bị loại (non-trading)
    n_upgraded      = 0    # Đếm pair trong stale được nâng n_bars
    n_new           = 0    # Đếm hole mới
    n_skip_verified = 0    # Đếm pair được skip do đã verified

    # ----- BƯỚC 2: Duyệt từng cặp (symbol_id, tf_code) có raw gap -----
    for (sym_id, tf_code), gaps in raw.items():
        tf_mins = TF_MINUTES.get(tf_code)  # Số phút của TF: H1 → 60, M15 → 15
        sym     = sym_map.get(sym_id)       # Thông tin symbol
        if tf_mins is None or sym is None:
            continue  # TF hoặc symbol không hợp lệ → bỏ qua

        # Lấy danh sách gap window đã verified cho pair này (nếu có)
        verified_windows = (verified_gaps or {}).get((sym_id, tf_code), [])

        # ----- BƯỚC 3: Tính ngưỡng (threshold) để phân biệt gap thật vs giả -----
        asset_type = sym["asset_type"]  # "Indice", "Metal", "FOREX", "Crypto"

        # Ngưỡng cơ bản: gap phải > 3× khoảng cách TF mới coi là bất thường
        # Ví dụ H1 = 60 phút → threshold = 180 phút (3 giờ)
        threshold  = tf_mins * 3

        # Per-symbol overnight: dùng SYMBOL_OVERNIGHT_MINS nếu có,
        # fallback sang OVERNIGHT_GAP_MINUTES theo asset_type
        tv_sym    = sym["tv_symbol"]
        overnight = SYMBOL_OVERNIGHT_MINS.get(
            tv_sym,
            OVERNIGHT_GAP_MINUTES.get(asset_type, 0)
        )
        if overnight > 0:
            threshold = max(threshold, overnight + tf_mins)

        # ----- BƯỚC 4: Lọc từng gap — giữ lại chỉ hole thật -----
        real_gaps = []
        for gap_start, gap_end, gap_raw_min in gaps:
            # Bỏ qua nếu gap window này đã được xác nhận là market gap trước đó
            # (kiểm tra per-window thay vì skip cả pair — tránh bỏ sót hole thật mới)
            if any(vs <= gap_start and gap_end <= ve
                   for vs, ve in verified_windows):
                n_skip_verified += 1
                continue

            # Nếu asset nghỉ cuối tuần → trừ giờ Sat/Sun khỏi gap
            # (vì gap dài 60h nhưng 48h là cuối tuần → thực chỉ 12h)
            if asset_type in WEEKEND_CLOSED:
                trading_min = trading_hours_in_gap(gap_start, gap_end) * 60
            else:
                # Crypto: 24/7 → gap thô = gap thực
                trading_min = float(gap_raw_min)

            # So sánh với ngưỡng: nếu vượt → hole thật, nếu không → loại
            if trading_min > threshold:
                real_gaps.append((gap_start, gap_end, trading_min))
            else:
                n_excluded += 1  # Gap bình thường (qua đêm/cuối tuần) → loại

        # Nếu tất cả gap của cặp này đều bị loại → không cần xử lý
        if not real_gaps:
            continue

        # ----- BƯỚC 5: Tính số bar cần pull cho hole này -----
        # Lấy thời điểm sớm nhất của gap → tính khoảng cách đến hiện tại
        earliest_start = min(g[0] for g in real_gaps)
        hole_hours     = (now - earliest_start).total_seconds() / 3600
        # Tính số bar cần pull (có hệ số an toàn ×1.5, tối thiểu 10)
        n_bars_needed  = calc_gap_n_bars(hole_hours, tf_code, asset_type)

        # ----- BƯỚC 6: Thêm vào kết quả hoặc nâng cấp stale -----
        key = (sym_id, tf_code)
        if key in stale_index:
            # Cặp này đã có trong stale (từ backfill pipeline)
            # → Nâng n_bars nếu hole cần pull nhiều hơn
            idx = stale_index[key]
            if stale[idx]["n_bars"] < n_bars_needed:
                stale[idx]["n_bars"]  = n_bars_needed
                stale[idx]["reason"] += "+HOLE"
                n_upgraded += 1
        else:
            # Cặp mới → thêm vào danh sách hole cần xử lý
            new_holes.append({
                "sym":         sym,          # Thông tin symbol
                "tf_code":     tf_code,      # Mã timeframe
                "last_bar":    earliest_start,  # Bar ngay trước lỗ hổng
                "gap_hours":   round(hole_hours, 1),  # Khoảng cách (giờ)
                "n_bars":      n_bars_needed,   # Số bar cần pull
                "reason":      "HOLE",          # Lý do: lỗ hổng dữ liệu
                "gap_windows": [(gs, ge) for gs, ge, _ in real_gaps],  # Danh sách gap windows cụ thể
            })
            n_new += 1

    # Ghi log tổng kết quá trình lọc
    logger.info(
        "Hole check: %d raw gaps | %d excluded (non-trading) | "
        "%d verified-skip | %d stale upgraded | %d new pairs queued.",
        n_raw, n_excluded, n_skip_verified, n_upgraded, n_new,
    )
    return new_holes
