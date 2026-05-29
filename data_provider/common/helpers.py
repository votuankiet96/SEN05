"""
Tap helper dung chung cho pipeline, ws_live va checker.

Nhom chuc nang chinh:
- logging va console sanitizing de log doc duoc tren Windows terminal
- validate / normalize OHLCV truoc khi ghi DB
- retry, sleep, tinh so bar can pull va xu ly khoang trong du lieu
- safe repull / recompute derived timeframe tu Fact_OHLCV
- quet "internal gaps" va luu verified market gaps de tranh sua nham luc thi truong dong

Day la noi gom cac quy tac xu ly nho nhung co tac dong lon den chat luong du lieu.
Khi thay doi nguong, can doc ky vi no anh huong truc tiep den cach he thong phan biet:
"gap that can sua" va "khoang dong cua binh thuong".
"""

# =============================================================================
# data_provider/common/helpers.py  -  Shared helpers for data pipeline / ws_live / checker
# =============================================================================
#
# MỤC ĐÍCH:
#   Chứa tất cả hàm dùng chung cho 3 script chính của data_provider:
#     - pipeline.py  - backfill hàng ngày
#     - ws_live.py        - cập nhật realtime qua WebSocket (24/7)
#     - checker.py        - kiểm tra và tự sửa dữ liệu mỗi 3 ngày
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC NHÓM CHỨC NĂNG
# ─────────────────────────────────────────────────────────────────────────────
#
#   setup_logger()        - Tạo logger ghi đồng thời ra console + file.
#                           Hỗ trợ 2 chế độ: FileHandler (script thủ công) và
#                           RotatingFileHandler (tiến trình 24/7 như ws_live).
#
#   Hằng số cấu hình      - FULL_N_BARS, SAFETY_FACTOR, MIN_PULL_BARS,
#                           RETRY_DELAYS, FILL_THRESHOLD, SLEEP_GOLD/NORMAL,
#                           OVERNIGHT_GAP_MINUTES - điều chỉnh tại đây khi cần
#                           thay đổi hành vi pull/retry/gap-detection.
#
#   Hàm tiện ích nhỏ      - now_utc(), fmt_gap(), calc_gap_n_bars(),
#                           trading_hours_in_gap(), sleep_for()
#
#   _validate_ohlcv_df()  - Làm sạch DataFrame OHLCV trước khi ghi DB:
#                           lọc null, High<Low, duplicate timestamps, thứ tự
#                           không tăng, DST alignment (GOLD/BTCUSD), M45 anchor.
#
#   Verified market gaps  - load_verified_gaps() / save_verified_gaps():
#                           Đọc/ghi JSON cache những khoảng thị trường đóng cửa
#                           đã được xác nhận -> skip lần chạy sau, không pull lại.
#
#   pull_and_store()      - Kéo OHLCV từ TradingView API -> validate -> Staging
#                           -> Fact_OHLCV. Trả về số bar mới insert.
#   pull_with_retry()     - Wrapper gọi pull_and_store() với retry + backoff.
#
#   recompute_derived()   - Tính lại 5 TF phái sinh (M10, M20, M90, H6, H8)
#                           cho các symbol vừa nhận bar mới (GROUP BY trên Fact).
#
#   repull_full_symbol()  - Xóa Fact rows cũ và pull lại từ đầu cho 1 cặp
#                           (symbol, TF). Direct TF: xóa staging + pull TV.
#                           Derived TF: re-aggregate từ source TF.
#
#   find_hole_pairs()     - Quét Fact_OHLCV bằng SQL LEAD() để tìm lỗ hổng
#                           dữ liệu bên trong timeline (không phải thiếu ở rìa).
#                           Lọc bỏ gap qua đêm, cuối tuần, đã verified.
#
# ─────────────────────────────────────────────────────────────────────────────
# THÔNG SỐ ĐIỀU CHỈNH ĐƯỢC
# ─────────────────────────────────────────────────────────────────────────────
#   SAFETY_FACTOR          - Pull dư bao nhiêu % so với cần (mặc định 1.5 = +50%)
#   MIN_PULL_BARS          - Số bar tối thiểu dù gap nhỏ (mặc định 10)
#   RETRY_DELAYS           - Thời gian chờ giữa các lần retry: [10, 30, 60] giây
#   SLEEP_GOLD/NORMAL      - Nghỉ giữa request: GOLD=10s, còn lại=5s
#   OVERNIGHT_GAP_MINUTES  - Ngưỡng gap qua đêm bình thường theo asset type
#   HOLE_LOOKBACK_DAYS     - Quét lỗ hổng trong bao nhiêu ngày gần nhất (60)
#
#   [WARN] Thay đổi các ngưỡng này ảnh hưởng trực tiếp đến cách hệ thống phân biệt
#      "gap thật cần fill" vs "thị trường đóng cửa bình thường".
# =============================================================================

import json  # Đọc/ghi file JSON (verified_market_gaps.json)
import logging          # Hệ thống logging của Python
import logging.handlers  # RotatingFileHandler - dùng cho ws_live 24/7
import math  # math.ceil() để làm tròn lên số bar cần pull
import os  # Thao tác đường dẫn file
import re
import pandas as pd
import sys  # Thêm đường dẫn import
import time  # time.sleep() để rate-limit giữa các request TV
from datetime import datetime, timedelta, timezone  # Xử lý thời gian UTC


class ResilientRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler variant that avoids stderr floods on Windows locks."""

    def __init__(self, *args, rollover_retry_sec: int = 300, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_retry_sec = rollover_retry_sec
        self._rollover_retry_at = 0.0
        self._rollover_warned = False
        self._primary_base_filename = self.baseFilename

    def shouldRollover(self, record):
        if self.maxBytes > 0 and time.monotonic() < self._rollover_retry_at:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as exc:
            if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) != 32:
                raise
            fallback = self._switch_to_fallback_log()
            if fallback:
                self._rollover_retry_at = 0.0
            else:
                self._rollover_retry_at = time.monotonic() + self._rollover_retry_sec
                self._reopen_after_rollover_failure()
            self._write_rollover_warning(exc, fallback)
        else:
            self._rollover_retry_at = 0.0
            self._rollover_warned = False

    def _switch_to_fallback_log(self) -> str | None:
        root, ext = os.path.splitext(self._primary_base_filename)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = f"{root}.active.{os.getpid()}.{stamp}{ext or '.log'}"
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        try:
            self.baseFilename = os.path.abspath(fallback)
            self.mode = "a"
            self.stream = self._open()
            return self.baseFilename
        except Exception:
            self.baseFilename = self._primary_base_filename
            self.stream = None
            return None

    def _reopen_after_rollover_failure(self) -> None:
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if not self.delay:
            try:
                self.stream = self._open()
            except Exception:
                self.stream = None

    def _write_rollover_warning(self, exc: OSError, fallback: str | None = None) -> None:
        if self._rollover_warned:
            return
        self._rollover_warned = True
        if fallback:
            action = f"Switched current log file to {fallback}."
        else:
            action = f"Retrying in {self._rollover_retry_sec}s."
        msg = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [WARNING] "
            f"Log rollover delayed for {self._primary_base_filename}: {exc}. "
            f"{action}\n"
        )
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(msg)
            self.flush()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
# ---------------------------------------------------------------------------
_DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # data_provider/ directory
_PROJ = os.path.dirname(_DATA)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# ---------------------------------------------------------------------------
# Import cấu hình từ config.py và các hàm DB từ db_connector.py
# ---------------------------------------------------------------------------
from config import (
    COMPUTED_TIMEFRAMES,  # Tất cả TF (direct + derived)
    DERIVED_TFS,  # Các TF phái sinh (tính từ TF gốc): {"M10","M20","M90","H6","H8"}
    DIRECT_TFS,  # Các TF kéo trực tiếp từ TV: {"M5","M15","M30","M45","H1","H2","H3","H4","D1","W"}
    HISTORICAL_PROVIDER,
    TV_WS_HISTORY_ENDPOINT,
    TV_WS_HISTORY_FALLBACK_ENDPOINTS,
    TV_WS_HISTORY_REQUEST_MORE_BARS,
    TV_WS_HISTORY_REQUEST_MORE_ROUNDS,
    TV_WS_HISTORY_TIMEOUT_SEC,
    TV_WS_REPLAY_ADVANCE_FACTOR,
    TV_WS_REPLAY_ENABLED,
    TV_WS_REPLAY_ENDPOINT,
    TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR,
    TV_WS_REPLAY_START_DATE,
    TV_WS_REPLAY_STEP_BARS,
    TV_WS_REPLAY_TFS,
    TV_WS_REPLAY_TIMEOUT_SEC,
    TV_WS_REPLAY_WINDOW_BARS,
    LOG_LEVEL,  # Mức log: "INFO", "DEBUG", "WARNING"...
    N_BARS_D1,
    N_BARS_H1,
    N_BARS_H2,
    N_BARS_H3,
    N_BARS_H4,
    N_BARS_H6,
    N_BARS_H8,
    N_BARS_M5,
    N_BARS_M10,
    N_BARS_M15,
    N_BARS_M20,
    N_BARS_M30,
    N_BARS_M45,
    N_BARS_M90,
    # Số bar tối đa cần pull cho mỗi TF (dùng khi cần FULL LOAD hoặc thiếu data)
    N_BARS_W,
    ASSET_TYPE_MAP,  # Map tv_symbol -> asset_type
    SYMBOL_OVERNIGHT_MINS,  # Per-symbol overnight gap threshold (phút)
    FIXED_H_ALIGNMENT,  # DST alignment cố định: GOLD h%N==1, BTCUSD h%N==0
    SYMBOLS,  # Danh sách symbol: [{symbol_id, tv_symbol, tv_exchange, asset_type}, ...]
    STAGING_INSERT_CHUNK_ROWS,
    TF_MINUTES,  # Map tf_code -> số phút: "H1" -> 60, "M15" -> 15
    TF_STAGING,  # Map tf_code -> tên bảng staging: "H1" -> "Staging_H1"
    WEEKEND_CLOSED,  # Set các asset_type nghỉ cuối tuần: {"Indice","Metal","FOREX"}
)
from modules.db_connector import (
    aggregate_from_fact,  # Tính TF phái sinh bằng GROUP BY trên Fact_OHLCV
    clean_staging_transitions,  # Xóa transition bar do DST shift sau insert staging
    DatabaseWriteError,  # Lỗi ghi DB bắt buộc caller phải retry/abort
    delete_fact_bars,  # Xóa rows Fact_OHLCV trước khi repull
    delete_staging_bars,  # Xóa rows staging trước khi repull
    get_candle_count,  # Đếm số bar trong Fact_OHLCV (kiểm tra source TF có data chưa)
    get_internal_gaps,  # Chạy SQL LEAD() tìm khoảng gap trong Fact_OHLCV
    insert_staging_batch,  # Ghi DataFrame OHLCV vào bảng Staging (MERGE, chống duplicate)
    run_etl_direct,  # Gọi usp_LoadDirect: chuyển Staging -> Fact_OHLCV
)
from data_provider.common.locks import acquire, release
from data_provider.tv.coord import sleep_between_historical_requests, wait_for_historical_slot
from data_provider.tv import ws_history as _tv_ws_history
from data_provider.tv import ws_replay as _tv_ws_replay
from data_provider.common import logfmt as _logfmt
from data_provider.paths import VERIFIED_MARKET_GAPS

# ---------------------------------------------------------------------------
# Tạo logger - ghi log ra cả console lẫn file
# ---------------------------------------------------------------------------

_CONSOLE_SANITIZE_MAP = {
    "âœ“": "[OK]",
    "[OK]": "[OK]",
    "âœ-": "[ERR]",
    "✗": "[ERR]",
    "â†»": "[RETRY]",
    "↻": "[RETRY]",
    "â†’": "->",
    "->": "->",
    "â€”": "-",
    "-": "-",
    "â‰ˆ": "~",
    "≈": "~",
    "â€¢": "-",
    "•": "-",
    "â-‹": "[SKIP]",
    "[SKIP]": "[SKIP]",
    "ðŸ”": "[CHECK]",
    "🔍": "[CHECK]",
    "ðŸ”§": "[FIX]",
    "🔧": "[FIX]",
    "âŒ": "[FAIL]",
    "[FAIL]": "[FAIL]",
    "âš ï¸": "[WARN]",
    "[WARN]": "[WARN]",
}


class ConsoleSanitizingFormatter(logging.Formatter):
    """
    Console-only formatter:
    - keeps logs ASCII-friendly on Windows terminals
    - normalizes mojibake / emoji markers into short status tags
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for src, dst in _CONSOLE_SANITIZE_MAP.items():
            text = text.replace(src, dst)
        if record.name == "checker":
            text = self._normalize_checker_console(text)
        return text

    @staticmethod
    def _status_line(tag: str, label: str, rate: str = "", miss: str = "",
                     ohlc: str = "", extra: str = "", vol: str = "") -> str:
        parts = [f"{tag:<7} {label:<12}"]
        if rate:
            parts.append(f"rate={rate:>5}")
        if miss:
            parts.append(f"miss={int(miss):>4}")
        if ohlc:
            parts.append(f"ohlc={int(ohlc):>4}")
        if extra:
            parts.append(f"extra={int(extra):>4}")
        if vol:
            parts.append(f"vol={int(vol):>4}")
        return " | ".join(parts)

    def _normalize_checker_console(self, text: str) -> str:
        parts = text.split(" | ", 2)
        if len(parts) != 3:
            return text
        prefix = " | ".join(parts[:2])
        message = parts[2].strip()

        patterns = [
            (
                r"^\[DRY\]\s+(\S+):\s+([\d.]+%) mismatch \(miss=(\d+) wrong=(\d+) extra=(\d+)\)$",
                lambda m: self._status_line("ISSUE", m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)),
            ),
            (
                r"^\[ERR\]\s+(\S+):\s+([\d.]+%) mismatch \(miss=(\d+) wrong=(\d+) extra=(\d+)\)\s+- repairing\.\.\.$",
                lambda m: self._status_line("REPAIR", m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)),
            ),
            (
                r"^\[OK\]\s+REPAIRED\s+(\S+)\s+\(verified clean\)$",
                lambda m: self._status_line("FIXED", m.group(1)),
            ),
            (
                r"^\[OK\]\s+RESOLVED\s+(\S+)\s+\(scan rate now ([\d.]+%)\)$",
                lambda m: self._status_line("FIXED", m.group(1), m.group(2)),
            ),
            (
                r"^clean on retry\s+(\S+)\s+\(scan rate now ([\d.]+%)\)$",
                lambda m: self._status_line("CLEAN", m.group(1), m.group(2)),
            ),
            (
                r"^\[ERR\]\s+PERSISTENT\s+(\S+):\s+([\d.]+%) mismatch$",
                lambda m: self._status_line("FAILED", m.group(1), m.group(2)),
            ),
            (
                r"^\[RETRY\]\s+(\S+):\s+([\d.]+%) still failing after repair\s+- will retry$",
                lambda m: f"{'RETRY':<7} {m.group(1):<12} | verify_rate={m.group(2):>5}",
            ),
            (
                r"^Focused repair\s+(\S+)\s+(\S+)\s+\(([^)]+)\): window=(.+) \| pull=(\d+) bars$",
                lambda m: f"{'FOCUS':<7} {m.group(1)}/{m.group(2):<8} | reason={m.group(3)} | window={m.group(4)} | pull={m.group(5)}",
            ),
            (
                r"^Focused re-pull recovered on attempt (\d+)/(\d+)\s+-\s+(\S+)\s+(\S+)\s+\(([^)]+)\)$",
                lambda m: f"{'RECOVER':<7} {m.group(3)}/{m.group(4):<8} | attempt={m.group(1)}/{m.group(2)} | reason={m.group(5)}",
            ),
            (
                r"^Focused re-pull FAILED for\s+(\S+)\s+(\S+)\s+\(([^)]+)\)\s+- Fact untouched$",
                lambda m: f"{'FAILED':<7} {m.group(1)}/{m.group(2):<8} | reason={m.group(3)} | fact=untouched",
            ),
            (
                r"^Deleted\s+(\d+) Fact bars in focused window\s+-\s+(\S+)\s+(\S+)\s+\(([^)]+)\)$",
                lambda m: f"{'DELETE':<7} {m.group(2)}/{m.group(3):<8} | bars={m.group(1)} | reason={m.group(4)}",
            ),
            (
                r"^ETL: \+(\d+) bars inserted into Fact\s+-\s+(\S+)\s+(\S+)\s+\(([^)]+)\)$",
                lambda m: f"{'ETL':<7} {m.group(2)}/{m.group(3):<8} | inserted={m.group(1)} | reason={m.group(4)}",
            ),
            (
                r"^Escalating\s+(\S+)\s+to SAFE FULL REPULL:\s+(.+)$",
                lambda m: f"{'ESCAL.':<7} {m.group(1):<12} | {m.group(2)}",
            ),
            (
                r"^SAFE FULL REPULL OK\s+(\S+): deleted=(\--\d+) inserted=(\--\d+)$",
                lambda m: f"{'FULL OK':<7} {m.group(1):<12} | deleted={m.group(2)} | inserted={m.group(3)}",
            ),
            (
                r"^SAFE FULL REPULL FAILED\s+(\S+): deleted=(\--\d+) inserted=(\--\d+)$",
                lambda m: f"{'FULL NG':<7} {m.group(1):<12} | deleted={m.group(2)} | inserted={m.group(3)}",
            ),
        ]

        for pattern, builder in patterns:
            match = re.match(pattern, message)
            if match:
                return f"{prefix} | {builder(match)}"
        return text


def setup_logger(name: str, log_file: str, rotating: bool = False) -> logging.Logger:
    """
    Tạo (hoặc lấy lại) một logger có tên `name`.
    Logger sẽ ghi ra 2 nơi đồng thời:
      - Console (stdout) - để theo dõi realtime khi chạy
      - File log - để xem lại sau

    Tham số:
      rotating=False (mặc định) - FileHandler thường, phù hợp script chạy thủ công.
      rotating=True             - RotatingFileHandler (10 MB × 5 files), phù hợp
                                  tiến trình 24/7 như ws_live để tránh log phình vô hạn.

    Nếu logger đã được cấu hình rồi (gọi lần 2) -> trả về ngay, không tạo lại.
    """
    logger = logging.getLogger(name)
    if logger.handlers:          # Đã cấu hình rồi -> trả về luôn
        return logger
    # Đảm bảo stdout hỗ trợ UTF-8 (tránh UnicodeEncodeError trên Windows CP1252)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    log_dir = os.path.dirname(os.path.abspath(log_file))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    console_fmt = ConsoleSanitizingFormatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)   # Handler ghi ra console
    sh.setFormatter(console_fmt)
    if rotating:
        # Tối đa 6 files × 10 MB = 60 MB - đủ giữ ~10-14 ngày log ws_live
        fh = ResilientRotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
    else:
        fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(file_fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# Hằng số cấu hình
# ---------------------------------------------------------------------------

# Số bar tối đa cho mỗi TF - dùng khi cần FULL LOAD (lần đầu hoặc thiếu toàn bộ)
# Ví dụ: H1 = 5000 bar ≈ 208 ngày dữ liệu 1 giờ
FULL_N_BARS = {
    "W":   N_BARS_W,   "D1":  N_BARS_D1,
    "H8":  N_BARS_H8,  "H6":  N_BARS_H6,
    "H4":  N_BARS_H4,  "H3":  N_BARS_H3,  "H2":  N_BARS_H2,
    "H1":  N_BARS_H1,  "M90": N_BARS_M90,
    "M45": N_BARS_M45, "M30": N_BARS_M30,
    "M20": N_BARS_M20, "M15": N_BARS_M15,
    "M10": N_BARS_M10, "M5":  N_BARS_M5,
}

# Hệ số an toàn: pull thêm 50% so với cần thiết để đảm bảo đủ data
def _ws_endpoint_order() -> list[str]:
    """Return primary + fallback TradingView WS endpoints without duplicates."""
    ordered: list[str] = []
    for endpoint in [TV_WS_HISTORY_ENDPOINT, *TV_WS_HISTORY_FALLBACK_ENDPOINTS]:
        endpoint = (endpoint or "").strip().lower()
        if endpoint and endpoint not in ordered:
            ordered.append(endpoint)
    return ordered or ["data"]


def _is_deep_history_load(tf_code: str, n_bars: int) -> bool:
    """True for full/reset/MISS-sized loads, false for small daily fills."""
    full_target = FULL_N_BARS.get(tf_code, 0)
    if full_target <= 0:
        return False
    return n_bars >= int(full_target * 0.8)


def _should_request_more_history(tf_code: str, n_bars: int) -> bool:
    """Use request_more_data for deep loads, not for tiny daily stale fills."""
    if TV_WS_HISTORY_REQUEST_MORE_ROUNDS <= 0 or TV_WS_HISTORY_REQUEST_MORE_BARS <= 0:
        return False
    return _is_deep_history_load(tf_code, n_bars)


def _should_replay_history(tf_code: str, n_bars: int) -> bool:
    """Replay is the max-depth phase for full/reset/MISS style deep loads."""
    if not TV_WS_REPLAY_ENABLED:
        return False
    if tf_code.upper() not in TV_WS_REPLAY_TFS:
        return False
    return _is_deep_history_load(tf_code, n_bars)


def _combine_history_frames(replay_df, series_df):
    frames = [df for df in (replay_df, series_df) if df is not None and not df.empty]
    if not frames:
        return None
    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined


def _fmt_log_ts(value) -> str:
    """Compact UTC timestamp for readable one-line console logs."""
    if value is None:
        return "-"
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        return ts.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


SAFETY_FACTOR  = 1.5

# Số bar tối thiểu - dù gap nhỏ vẫn pull ít nhất 10 bar
MIN_PULL_BARS  = 10

# Return codes từ pull_and_store()
RESULT_ERROR    = -1   # TV exception / lỗi nghiêm trọng
RESULT_TV_EMPTY = -2   # TV trả về rỗng (không có data, không phải lỗi kỹ thuật)

# Thời gian chờ (giây) giữa mỗi lần retry - tăng dần để tránh rate-limit
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
SLEEP_GOLD   = 10   # giây - cho Gold
SLEEP_NORMAL = 5    # giây - cho tất cả symbol khác

# Quét lỗ hổng trong bao nhiêu ngày gần nhất (mặc định 60 ngày)
HOLE_LOOKBACK_DAYS = 60

# Ngưỡng gap qua đêm BÌNH THƯỜNG theo loại asset (phút)
# Gap nhỏ hơn ngưỡng này = thị trường đóng cửa hàng ngày, KHÔNG phải lỗ hổng
# Gap lớn hơn ngưỡng này = có khả năng thiếu dữ liệu thực sự
OVERNIGHT_GAP_MINUTES = {
    "Indice": 1080,  # ~18 giờ - thị trường chứng khoán chỉ mở ~6h/ngày
    "Metal":  180,   # ~3 giờ  - vàng nghỉ 1 khoảng ngắn giữa phiên
    "FOREX":  150,   # ~2.5 giờ - forex gần như 24h nhưng có gap nhỏ
    "Crypto": 0,     # 0 phút  - crypto giao dịch 24/7, mọi gap đều bất thường
}

# Đường dẫn file JSON lưu market gap đã xác nhận
# (gap do thị trường đóng cửa, KHÔNG phải thiếu data -> skip lần sau)
# Runtime cache is kept out of source directories.
_VERIFIED_GAPS_FILE = str(VERIFIED_MARKET_GAPS)

# Source TF cho từng derived TF - dùng để kiểm tra source có data trước khi aggregate
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


def normalize_tv_hist_df_to_utc(df):
    """
    Chuẩn hóa index lịch sử từ tvDatafeed về naive UTC.

    tvDatafeed thường trả DatetimeIndex naive theo múi giờ local của máy chạy.
    Warehouse của SEN05 lưu BarTime theo UTC, nên cần chuẩn hóa ngay ở đây để:
      - không đánh nhầm bar hiện tại thành "future"
      - so sánh checker đúng với Fact_OHLCV
      - ghi DB nhất quán khi chạy ở máy local hay VPS

    Returns (df, normalized):
      normalized=True khi index đã được đổi sang UTC naive.
    """
    if df is None or df.empty:
        return df, False

    idx = getattr(df, "index", None)
    if idx is None:
        return df, False

    try:
        tz_attr = getattr(idx, "tz", None)
        if tz_attr is not None:
            normalized_idx = idx.tz_convert(timezone.utc).tz_localize(None)
        else:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
            normalized_idx = idx.tz_localize(local_tz).tz_convert(timezone.utc).tz_localize(None)
    except Exception:
        return df, False

    if normalized_idx.equals(idx):
        return df, False

    df = df.copy()
    df.index = normalized_idx
    return df, True


def fmt_gap(hours: float) -> str:
    """
    Format số giờ gap thành chuỗi dễ đọc:
      0.5 giờ  -> "30m"
      3.5 giờ  -> "3.5h"
      72 giờ   -> "3.0d"
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

    Ví dụ: gap từ thứ 6 -> thứ 2 = 72h tổng, nhưng chỉ ~24h trading
    (trừ 48h cuối tuần).

    Dùng để đánh giá: gap này có thực sự lớn không, hay chỉ do cuối tuần-
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
            weekend_h += 1.0   # Giờ này là cuối tuần -> đếm vào weekend
        t      += timedelta(hours=1)
        walked += 1.0
    return max(0.0, total_hours - weekend_h)  # Tổng - cuối tuần = trading hours


def sleep_for(tv_symbol: str) -> None:
    """
    Nghỉ giữa các request TradingView để tránh bị rate-limit.
    GOLD nghỉ 10 giây (API nặng hơn), các mã khác nghỉ 5 giây.
    """
    sleep_between_historical_requests(tv_symbol)


def _build_tvdatafeed_interval(tf_code: str):
    """Legacy tvDatafeed interval map. Custom 10/20/90/6H/8H TFs are unsupported."""
    try:
        from tvDatafeed import Interval
    except Exception:
        return None
    return {
        "W": Interval.in_weekly,
        "D1": Interval.in_daily,
        "H4": Interval.in_4_hour,
        "H3": Interval.in_3_hour,
        "H2": Interval.in_2_hour,
        "H1": Interval.in_1_hour,
        "M45": Interval.in_45_minute,
        "M30": Interval.in_30_minute,
        "M15": Interval.in_15_minute,
        "M5": Interval.in_5_minute,
    }.get(tf_code)


def _acquire_short_write_lock(
    task_name: str,
    logger: logging.Logger,
    *,
    action: str,
    duration_min: int = 15,
    wait_timeout_sec: float = 15 * 60.0,
    poll_sec: float = 5.0,
) -> bool:
    """
    Acquire a short-lived maintenance lock for a write chunk.

    Used by cooperative historical jobs so ws_live is deferred only while a
    concrete DB write is happening, not for the whole historical session.
    """
    start = time.monotonic()
    last_log = -poll_sec
    while True:
        if acquire(task_name, duration_min=duration_min):
            return True

        waited = time.monotonic() - start
        if waited >= wait_timeout_sec:
            logger.error(
                "  [LOCK TIMEOUT] %s could not acquire %s after %.0fs",
                action, task_name, waited,
            )
            return False

        if waited - last_log >= 60.0:
            logger.info(
                "  [LOCK WAIT] %s waiting %.0fs for %s...",
                action, waited, task_name,
            )
            last_log = waited
        time.sleep(poll_sec)


def _validate_ohlcv_df(
    df,
    tv_symbol: str,
    tf_code: str,
    logger: logging.Logger,
    *,
    normalize_timestamps: bool = True,
):
    """
    Kiểm tra tính hợp lệ của DataFrame OHLCV trước khi ghi vào DB.

    Các kiểm tra (theo thứ tự):
      1. Null trong Open/High/Low/Close -> loại bỏ row đó
      2. High < Low (giá đảo ngược) -> loại bỏ row đó
      3. Timestamp trùng lặp -> giữ lại row đầu tiên
      4. Timestamp không tăng dần -> sắp xếp lại
      5. DST alignment cho GOLD/BTCUSD H2/H3/H4 - các symbol này có alignment cố
         định quanh năm; bars lệch giờ do DST sẽ bị loại (xem FIXED_H_ALIGNMENT)
      6. M45 alignment - phát hiện dominant anchor. Nếu thấy anchor shift dạng
         2 đoạn liên tục (ví dụ trước/ sau DST) thì giữ cả hai; chỉ loại các
         lệch anchor rải rác như glitch.

    Trả về (cleaned_df, had_issues):
      - cleaned_df  : DataFrame sau khi làm sạch (có thể empty nếu tất cả đều lỗi)
      - had_issues  : True nếu có bất kỳ vấn đề nào được phát hiện
    """
    if df is None or df.empty:
        return df, False

    if normalize_timestamps:
        df, normalized = normalize_tv_hist_df_to_utc(df)
        if normalized:
            logger.debug("  VALIDATE %s %s: normalized tvDatafeed timestamps to UTC", tv_symbol, tf_code)

    original_len = len(df)
    had_issues   = False

    # 1. Kiểm tra null trong các cột OHLC bắt buộc
    ohlc_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if ohlc_cols:
        null_mask = df[ohlc_cols].isnull().any(axis=1)
        if null_mask.any():
            logger.warning("  VALIDATE %s %s: %d rows with null OHLC -> dropped",
                           tv_symbol, tf_code, int(null_mask.sum()))
            df         = df[~null_mask]
            had_issues = True

    if df.empty:
        return df, had_issues

    # 2. Kiểm tra High >= Low (giá trị hợp lệ)
    if "high" in df.columns and "low" in df.columns:
        invalid_hl = df["high"] < df["low"]
        if invalid_hl.any():
            logger.warning("  VALIDATE %s %s: %d rows with High < Low -> dropped",
                           tv_symbol, tf_code, int(invalid_hl.sum()))
            df         = df[~invalid_hl]
            had_issues = True

    if df.empty:
        return df, had_issues

    # 3. Kiểm tra timestamp trùng lặp
    if df.index.duplicated().any():
        n_dupes = int(df.index.duplicated().sum())
        logger.warning("  VALIDATE %s %s: %d duplicate timestamps -> kept first",
                       tv_symbol, tf_code, n_dupes)
        df         = df[~df.index.duplicated(keep="first")]
        had_issues = True

    # 4. Kiểm tra timestamp tăng dần (monotonic)
    if not df.index.is_monotonic_increasing:
        logger.warning("  VALIDATE %s %s: timestamps not monotonic -> sorted",
                       tv_symbol, tf_code)
        df         = df.sort_index()
        had_issues = True

    # 4b. Chặn future bars để không làm kẹt watermark khi API trả sai thời gian.
    now_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
    future_mask = df.index > now_cutoff
    if future_mask.any():
        n_future = int(future_mask.sum())
        logger.warning(
            "  VALIDATE %s %s: %d future bar(s) beyond %s -> dropped",
            tv_symbol, tf_code, n_future, now_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        )
        df = df[~future_mask]
        had_issues = True

    if df.empty:
        return df, had_issues

    # 5. Kiểm tra DST alignment cho GOLD và BTCUSD (H2/H3/H4)
    # Các symbol này có alignment cố định quanh năm - lọc bars lệch giờ trước khi insert.
    if tf_code in FIXED_H_ALIGNMENT.get(tv_symbol, {}):
        tf_hours    = int(tf_code[1:])  # H4->4, H3->3, H2->2
        expected    = FIXED_H_ALIGNMENT[tv_symbol][tf_code]
        wrong_align = df.index.hour % tf_hours != expected
        if wrong_align.any():
            n_bad = int(wrong_align.sum())
            logger.warning(
                "  VALIDATE %s %s: %d bars alignment sai (h%%%d != %d) -> dropped",
                tv_symbol, tf_code, n_bad, tf_hours, expected,
            )
            df         = df[~wrong_align]
            had_issues = True

    # 6. M45 alignment check.
    # Trường hợp bình thường: chỉ có 1 anchor remainder % 45.
    # Trường hợp DST/session shift thật: TV có thể trả 2 anchor hợp lệ theo 2 đoạn liên tiếp
    # (ví dụ toàn bộ đoạn cũ = 30, đoạn mới = 15). Khi đó giữ cả hai để checker/pipeline
    # không tự tạo missing bars. Chỉ drop các lệch anchor rải rác như glitch.
    if tf_code == "M45":
        remainders = (df.index.hour * 60 + df.index.minute) % 45
        counts = remainders.value_counts()
        asset_type = ASSET_TYPE_MAP.get(tv_symbol)
        if asset_type == "Indice":
            logger.info(
                "  VALIDATE %s M45: session-based index anchors %s -> keep raw anchors",
                tv_symbol, ",".join(str(int(v)) for v in counts.index.tolist()),
            )
        elif len(counts) > 1:
            rem_values = [int(v) for v in remainders.tolist()]
            run_remainders = []
            for rem in rem_values:
                if not run_remainders or rem != run_remainders[-1]:
                    run_remainders.append(rem)
            if len(counts) == 2 and len(run_remainders) <= 4:
                logger.warning(
                    "  VALIDATE %s M45: detected contiguous anchor shift %s -> keeping both anchors",
                    tv_symbol, " -> ".join(str(int(v)) for v in run_remainders),
                )
            else:
                anchor = int(counts.idxmax())
                wrong_align = remainders != anchor
                if wrong_align.any():
                    n_bad = int(wrong_align.sum())
                    logger.warning(
                        "  VALIDATE %s M45: %d bars alignment sai (anchor=%d, not consistent) -> dropped",
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
# thay vì chỉ lưu pair - tránh skip nhầm hole thật mới phát sinh sau gap cũ.
# ---------------------------------------------------------------------------

def load_verified_gaps() -> dict:
    """
    Đọc các gap window đã xác nhận là market gap từ verified_market_gaps.json.

    Trả về:
      dict: (symbol_id, tf_code) -> list[(gap_start, gap_end)]
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
        # Hết hạn sau 30 ngày -> quét lại từ đầu
        if (now_utc() - saved).days > 30:
            return {}
        # Format mới: "windows" key
        if "windows" not in data:
            # File format cũ ("pairs") - treat as expired để force re-verify
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
      windows - set of (symbol_id, tf_code, gap_start: datetime, gap_end: datetime)
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
# Đây là hàm quan trọng nhất - được gọi cho MỖI hole cần fill.
# Flow: TradingView API -> DataFrame -> Staging table -> Fact_OHLCV
# ---------------------------------------------------------------------------

def pull_and_store(tv, sym: dict, tf_code: str,
                   n_bars: int, interval,
                   logger: logging.Logger,
                   skip_etl: bool = False,
                   write_lock_name: str | None = None) -> int:
    """
    Kéo n_bars nến OHLCV từ TradingView cho 1 cặp (symbol, timeframe),
    sau đó ghi vào database qua 2 bước: Staging -> Fact.

    Tham số:
      tv        - kết nối TradingView (tvDatafeed instance)
      sym       - dict thông tin symbol: {symbol_id, tv_symbol, tv_exchange, asset_type}
      tf_code   - mã timeframe: "H1", "M15", "M30"...
      n_bars    - số bar cần kéo (đã nhân hệ số an toàn)
      interval  - interval object mà TV API cần (ví dụ: Interval.in_1_hour)
      logger    - logger để ghi log
      skip_etl  - nếu True: chỉ pull vào Staging, KHÔNG chạy ETL sang Fact.
                  Dùng trong _repair_pair() để đảm bảo data an toàn trong staging
                  trước khi xóa bars sai trong Fact.

    Trả về (skip_etl=False, mặc định):
      ≥ 1  - số bar MỚI được insert vào Fact_OHLCV (thành công, có data mới)
        0  - pull thành công nhưng 0 bar mới (DB đã có đủ = market gap)
       -1  - thất bại do exception (TV lỗi kỹ thuật, timeout...)
       -2  - TV trả về rỗng (không có data cho khoảng này)

    Trả về (skip_etl=True):
      ≥ 0  - số bar đã stage vào Staging (thành công; 0 = không có bar mới)
       -1  - thất bại do exception
       -2  - TV trả về rỗng
    """
    symbol_id   = sym["symbol_id"]
    tv_symbol   = sym["tv_symbol"]
    tv_exchange = sym["tv_exchange"]
    staging     = TF_STAGING[tf_code]  # Tên bảng staging: "Staging_H1", "Staging_M15"...

    # ----- BƯỚC A: Kéo dữ liệu từ TradingView API -----
    # Pull thêm 5 bar dự phòng (n_bars + 5) vì bar cuối sẽ bị bỏ
    wait_for_historical_slot("historical", logger)
    if HISTORICAL_PROVIDER == "websocket":
        try:
            request_more_rounds = (
                TV_WS_HISTORY_REQUEST_MORE_ROUNDS
                if _should_request_more_history(tf_code, n_bars)
                else 0
            )
            df = None
            ws_result = None
            for endpoint in _ws_endpoint_order():
                ws_result = _tv_ws_history.fetch_history(
                    symbol=tv_symbol,
                    exchange=tv_exchange,
                    tf_code=tf_code,
                    n_bars=n_bars + 5,
                    logger=logger,
                    timeout_sec=TV_WS_HISTORY_TIMEOUT_SEC,
                    endpoint=endpoint,
                    request_more_rounds=request_more_rounds,
                    request_more_bars=TV_WS_HISTORY_REQUEST_MORE_BARS,
                )
                df = ws_result.df
                if df is not None and not df.empty:
                    break
                _logfmt.log(
                    logger,
                    "WS_WARN",
                    symbol=tv_symbol,
                    tf=tf_code,
                    action="history_empty",
                    amount=f"endpoint {ws_result.endpoint}",
                    status=f"{_logfmt.tv_status(ws_result.status)} {ws_result.error or ''}".strip(),
                    level=logging.WARNING,
                )

            if ws_result is not None:
                if df is not None and not df.empty:
                    first_bar = df.index.min()
                    last_bar = df.index.max()
                    _logfmt.log(
                        logger,
                        "WS",
                        symbol=tv_symbol,
                        tf=tf_code,
                        action=f"history via {ws_result.endpoint}",
                        amount=f"got {_logfmt.num(ws_result.returned)} / asked {_logfmt.num(ws_result.requested)}",
                        range_=_logfmt.window(first_bar, last_bar),
                        status=_logfmt.tv_status(ws_result.status),
                    )
                if not (
                    ws_result.status.startswith("completed")
                    or ws_result.status.startswith("partial_timeout")
                ):
                    _logfmt.log(
                        logger,
                        "WS_WARN",
                        symbol=tv_symbol,
                        tf=tf_code,
                        action=f"history via {ws_result.endpoint}",
                        status=f"{_logfmt.tv_status(ws_result.status)} {ws_result.error or ''}".strip(),
                        level=logging.WARNING,
                    )

            if df is not None and not df.empty and _should_replay_history(tf_code, n_bars):
                series_first = df.index.min()
                _logfmt.log(
                    logger,
                    "REPLAY",
                    symbol=tv_symbol,
                    tf=tf_code,
                    action="older_history",
                    amount=f"from {TV_WS_REPLAY_START_DATE}",
                    range_=f"before {_fmt_log_ts(series_first)}",
                    status=f"endpoint {TV_WS_REPLAY_ENDPOINT}",
                )
                replay_result = _tv_ws_replay.crawl_replay_history(
                    symbol=tv_symbol,
                    exchange=tv_exchange,
                    tf_code=tf_code,
                    start_utc=TV_WS_REPLAY_START_DATE,
                    end_before_utc=series_first,
                    endpoint=TV_WS_REPLAY_ENDPOINT,
                    window_bars=TV_WS_REPLAY_WINDOW_BARS,
                    step_bars=TV_WS_REPLAY_STEP_BARS,
                    max_windows=TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR,
                    advance_factor=TV_WS_REPLAY_ADVANCE_FACTOR,
                    timeout_sec=TV_WS_REPLAY_TIMEOUT_SEC,
                    logger=logger,
                )
                if replay_result.df is not None and not replay_result.df.empty:
                    before_rows = len(df)
                    df = _combine_history_frames(replay_result.df, df)
                    _logfmt.log(
                        logger,
                        "REPLAY",
                        symbol=tv_symbol,
                        tf=tf_code,
                        action="merged_history",
                        amount=f"older {_logfmt.num(replay_result.returned)} + recent {_logfmt.num(before_rows)}",
                        range_=_logfmt.window(df.index.min(), df.index.max()),
                        status=f"total {_logfmt.num(len(df))}; windows {replay_result.windows}",
                    )
                else:
                    _logfmt.log(
                        logger,
                        "REPLAY",
                        symbol=tv_symbol,
                        tf=tf_code,
                        action="older_history",
                        amount=f"windows {replay_result.windows}",
                        status=f"no older bars; {_logfmt.tv_status(replay_result.status)}",
                    )
        except Exception as e:
            _logfmt.log(logger, "ERROR", symbol=tv_symbol, tf=tf_code, action="ws_pull", status=str(e), level=logging.ERROR)
            return RESULT_ERROR
    else:
        try:
            tvdf_interval = interval
            if isinstance(interval, str):
                tvdf_interval = _build_tvdatafeed_interval(tf_code)
            if tvdf_interval is None:
                _logfmt.log(logger, "ERROR", symbol=tv_symbol, tf=tf_code, action="tvdatafeed_tf", status="unsupported", level=logging.ERROR)
                return RESULT_TV_EMPTY
            df = tv.get_hist(
                symbol   = tv_symbol,
                exchange = tv_exchange,
                interval = tvdf_interval,
                n_bars   = n_bars + 5,
            )
        except Exception as e:
            _logfmt.log(logger, "ERROR", symbol=tv_symbol, tf=tf_code, action="tv_pull", status=str(e), level=logging.ERROR)
            return RESULT_ERROR

    # Nếu TV trả về rỗng -> TV không có data cho khoảng thời gian này
    # (khác với lỗi kỹ thuật - đây có thể là market gap thật)
    if df is None or df.empty:
        _logfmt.log(logger, "WARN", symbol=tv_symbol, tf=tf_code, action="tv_returned", status="empty", level=logging.WARNING)
        return RESULT_TV_EMPTY

    # Cảnh báo nếu TV trả về < 50% số bar yêu cầu (có thể TV bị giới hạn)
    returned_bars = len(df)
    if returned_bars < n_bars * 0.5:
        try:
            from data_provider.tv.auth import get_auth_mode as _get_auth_mode
            _mode = _get_auth_mode()
        except Exception:
            _mode = "unknown"
        _auth_hint = " auth=guest bar_limit_hint=500" if _mode == "guest" else ""
        _logfmt.log(
            logger,
            "WARN",
            symbol=tv_symbol,
            tf=tf_code,
            action="short_history",
            amount=f"got {_logfmt.num(returned_bars)} / wanted {_logfmt.num(n_bars)}",
            status=f"{returned_bars / n_bars * 100:.0f}% returned{_auth_hint}",
            level=logging.WARNING,
        )

    # ----- BỎ BAR CUỐI CÙNG -----
    # Bar cuối rất có thể ĐANG MỞ (chưa đóng xong) -> dữ liệu OHLCV chưa chính xác.
    # Ví dụ: nến H1 lúc 14:30 mới chạy được nửa -> O/H/L/C chưa phải giá trị cuối.
    # -> Bỏ đi để chỉ giữ các bar đã đóng hoàn toàn.
    df = df.iloc[:-1]
    if df.empty:
        _logfmt.log(logger, "WARN", symbol=tv_symbol, tf=tf_code, action="closed_bars", status="none; only open bar returned", level=logging.WARNING)
        return RESULT_TV_EMPTY

    # ----- BƯỚC A2: Kiểm tra chất lượng dữ liệu trước khi ghi -----
    df, _ = _validate_ohlcv_df(df, tv_symbol, tf_code, logger)
    if df.empty:
        _logfmt.log(logger, "WARN", symbol=tv_symbol, tf=tf_code, action="validation", status="all rows removed", level=logging.WARNING)
        return RESULT_TV_EMPTY

    # ----- BƯỚC B: Ghi vào Staging (MERGE - chống duplicate) -----
    # insert_staging_batch() tạo temp table -> bulk insert -> MERGE vào staging
    # Nếu row đã tồn tại (cùng SymbolID + BarTime) -> bỏ qua, chỉ insert row mới
    try:
        if write_lock_name:
            wait_for_historical_slot("historical-db", logger)
            if not _acquire_short_write_lock(
                write_lock_name,
                logger,
                action=f"write {tv_symbol} {tf_code}",
            ):
                return RESULT_ERROR
        if STAGING_INSERT_CHUNK_ROWS > 0 and len(df) > STAGING_INSERT_CHUNK_ROWS:
            staged = 0
            for start in range(0, len(df), STAGING_INSERT_CHUNK_ROWS):
                chunk = df.iloc[start:start + STAGING_INSERT_CHUNK_ROWS]
                staged += insert_staging_batch(chunk, symbol_id, staging)
            _logfmt.log(
                logger,
                "STAGE",
                symbol=tv_symbol,
                tf=tf_code,
                action="write_chunks",
                amount=f"rows {_logfmt.num(len(df))}",
                range_=f"chunk {_logfmt.num(STAGING_INSERT_CHUNK_ROWS)}",
                status=f"affected {_logfmt.num(staged)}",
            )
        else:
            staged = insert_staging_batch(df, symbol_id, staging)
    except DatabaseWriteError as e:
        if write_lock_name:
            release(write_lock_name)
        _logfmt.log(logger, "ERROR", symbol=tv_symbol, tf=tf_code, action="stage_write", status=str(e), level=logging.ERROR)
        return RESULT_ERROR

    # ----- BƯỚC B2: Dọn transition bar DST / anchor drift khỏi staging -----
    # M45/H2/H3/H4: Capital.com dịch chuyển UTC offset qua DST -> bar anchor drift.
    # Xoá ngay sau insert để Fact không nhận bar nhiễm.
    if tf_code in ('M45', 'H2', 'H3', 'H4') and ASSET_TYPE_MAP.get(tv_symbol) != "Indice":
        from config import TF_MINUTES
        n_cleaned = clean_staging_transitions(symbol_id, staging, TF_MINUTES[tf_code])
        if n_cleaned > 0:
            _logfmt.log(logger, "CLEAN", symbol=tv_symbol, tf=tf_code, action="anchor_transition", amount=f"removed {_logfmt.num(n_cleaned)}")

    # ----- BƯỚC C: Chuyển Staging -> Fact_OHLCV (stored procedure) -----
    # skip_etl=True: caller (VD: _repair_pair) sẽ tự gọi ETL sau khi đã xóa
    # bars sai khỏi Fact. Đây là cơ chế đảm bảo không mất data: staging được
    # điền trước, chỉ sau đó mới xóa Fact và chạy ETL.
    if skip_etl:
        _logfmt.log(logger, "DB", symbol=tv_symbol, tf=tf_code, action="stage_only", amount=f"staged {_logfmt.num(staged)}", status="fact unchanged")
        if write_lock_name:
            release(write_lock_name)
        return staged

    # run_etl_direct() gọi DWH.usp_LoadDirect -> chuyển row từ staging sang Fact
    # Cũng dùng NOT EXISTS chống duplicate. Trả về số row mới insert vào Fact.
    try:
        etl_inserted = run_etl_direct(symbol_id, tf_code, staging)
    except Exception as e:
        _logfmt.log(logger, "ERROR", symbol=tv_symbol, tf=tf_code, action="fact_load", status=f"{e}; cleaning staging", level=logging.ERROR)
        try:
            delete_staging_bars(symbol_id, staging)
            if write_lock_name:
                release(write_lock_name)
        except Exception as e2:
            if write_lock_name:
                release(write_lock_name)
            _logfmt.log(logger, "WARN", symbol=tv_symbol, tf=tf_code, action="stage_cleanup", status=str(e2), level=logging.WARNING)
        return RESULT_ERROR

    # ----- Ghi log kết quả -----
    if etl_inserted > 0:
        # CÓ bar mới -> ghi [OK]
        _logfmt.log(logger, "DB", symbol=tv_symbol, tf=tf_code, action="fact_loaded", amount=f"inserted {_logfmt.num(etl_inserted)}", range_=f"staged {_logfmt.num(staged)}", status="Fact_OHLCV")
    elif staged > 0:
        # Staging có row mới nhưng Fact đã có đủ -> [SKIP] (market gap)
        _logfmt.log(logger, "DB", symbol=tv_symbol, tf=tf_code, action="fact_loaded", amount="inserted 0", range_=f"staged {_logfmt.num(staged)}", status="already in Fact")
    else:
        # Cả staging lẫn Fact đều không có gì mới -> đã up to date
        _logfmt.log(logger, "DB", symbol=tv_symbol, tf=tf_code, action="fact_loaded", amount="inserted 0", range_="staged 0", status="already up to date")
    if write_lock_name:
        release(write_lock_name)
    return etl_inserted


def pull_with_retry(tv, sym: dict, tf_code: str, n_bars: int, interval,
                    logger: logging.Logger, max_retries: int = 3) -> int:
    """
    Gọi pull_and_store() tối đa (1 + max_retries) lần.
    Retry khi kết quả là RESULT_TV_EMPTY hoặc RESULT_ERROR (< 0).
    Backoff tăng dần theo RETRY_DELAYS: 10s -> 30s -> 60s.
    Trả về kết quả của lần thử cuối cùng.
    """
    result = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)

    for attempt in range(1, max_retries + 1):
        if result >= 0:
            break
        delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
        logger.warning(
            "  [Retry %d/%d] %s %s - waiting %ds...",
            attempt, max_retries, sym["tv_symbol"], tf_code, delay,
        )
        time.sleep(delay)
        result = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)

    if result < 0:
        logger.error(
            "  [FINAL FAIL] %s %s - failed after %d attempt(s).",
            sym["tv_symbol"], tf_code, max_retries + 1,
        )
    return result


# ---------------------------------------------------------------------------
# Tính lại timeframe phái sinh từ dữ liệu đã có trong Fact_OHLCV
# ---------------------------------------------------------------------------
# 5 TF này KHÔNG kéo trực tiếp từ TradingView mà tính từ TF gốc:
#   M5  -> gộp thành M10 (2 nến M5 = 1 nến M10)
#   M5  -> gộp thành M20 (4 nến M5 = 1 nến M20)
#   M30 -> gộp thành M90 (3 nến M30 = 1 nến M90)
#   H3  -> gộp thành H6  (2 nến H3 = 1 nến H6)
#   H4  -> gộp thành H8  (2 nến H4 = 1 nến H8)
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
            continue  # Symbol này không có bar mới -> bỏ qua
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
                # để tạo nến TF lớn hơn từ TF nhỏ hơn (ví dụ M5 -> M10)
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
      3. Pull FULL_N_BARS từ TradingView -> Staging -> Fact

    DERIVED TFs (M10, M20, M90, H6, H8):
      1. Xóa Fact_OHLCV (không có staging cho derived TFs)
      2. Re-aggregate từ source TF đang có trong Fact_OHLCV

    Trả về (n_deleted, n_inserted).
      n_inserted < 0 nghĩa là repull thất bại.
    """
    symbol_id = sym["symbol_id"]
    tv_symbol = sym["tv_symbol"]

    # ----- Bước 1a: DERIVED TF -> re-aggregate từ source -----
    if tf_code in DERIVED_TFS:
        n_deleted = delete_fact_bars(symbol_id, tf_code)
        logger.info("  repull %s %s: deleted %d Fact rows",
                    tv_symbol, tf_code, n_deleted)
        try:
            n_inserted = aggregate_from_fact(symbol_id, tf_code)
            logger.info("  repull %s %s: re-aggregated %d bars",
                        tv_symbol, tf_code, n_inserted)
            return n_deleted, n_inserted
        except Exception as e:
            logger.error("  repull %s %s re-aggregate FAIL: %s",
                         tv_symbol, tf_code, e)
            return n_deleted, RESULT_ERROR

    # ----- Bước 1b: DIRECT TF -> stage mới trước, chỉ xóa Fact khi đã có data thay thế -----
    staging = TF_STAGING.get(tf_code)
    if staging:
        delete_staging_bars(symbol_id, staging)

    n_bars  = FULL_N_BARS.get(tf_code, 5000)
    staged  = pull_and_store(tv, sym, tf_code, n_bars, interval, logger, skip_etl=True)
    if staged < 0:
        logger.error("  repull %s %s: staging replacement failed - keeping existing Fact data",
                     tv_symbol, tf_code)
        return 0, RESULT_ERROR

    n_deleted = delete_fact_bars(symbol_id, tf_code)
    logger.info("  repull %s %s: deleted %d Fact rows after staging replacement",
                tv_symbol, tf_code, n_deleted)
    try:
        n_inserted = run_etl_direct(symbol_id, tf_code, staging)
    except Exception as e:
        logger.error("  repull %s %s ETL FAIL after safe staging: %s",
                     tv_symbol, tf_code, e)
        return n_deleted, RESULT_ERROR
    return n_deleted, n_inserted


# ---------------------------------------------------------------------------
# Tìm lỗ hổng dữ liệu trong Fact_OHLCV (dùng bởi gap_fill)
# ---------------------------------------------------------------------------
#
# FLOW:
#   1. Chạy SQL LEAD() trên Fact_OHLCV -> tìm mọi gap > 10 phút (raw gaps)
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
      stale          - danh sách pair đang chờ pull (từ backfill pipeline).
                       Nếu hole trùng pair đã có trong stale -> nâng n_bars.
      logger         - logger để ghi log
      verified_gaps  - dict (symbol_id, tf_code) -> list[(gap_start, gap_end)]
                       đã xác nhận là market gap -> bỏ qua gap window đó

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

    # Index bảng stale để tra nhanh: (sym_id, tf_code) -> vị trí trong list
    stale_index = {(x["sym"]["symbol_id"], x["tf_code"]): i
                   for i, x in enumerate(stale)}
    # Map symbol_id -> dict symbol đầy đủ (để tra thông tin asset_type, tv_symbol...)
    sym_map = {s["symbol_id"]: s for s in SYMBOLS}

    new_holes       = []   # Kết quả: danh sách hole mới cần trả về
    n_raw           = sum(len(v) for v in raw.values())  # Tổng raw gaps
    n_excluded      = 0    # Đếm gap bị loại (non-trading)
    n_upgraded      = 0    # Đếm pair trong stale được nâng n_bars
    n_new           = 0    # Đếm hole mới
    n_skip_verified = 0    # Đếm pair được skip do đã verified

    # ----- BƯỚC 2: Duyệt từng cặp (symbol_id, tf_code) có raw gap -----
    for (sym_id, tf_code), gaps in raw.items():
        tf_mins = TF_MINUTES.get(tf_code)  # Số phút của TF: H1 -> 60, M15 -> 15
        sym     = sym_map.get(sym_id)       # Thông tin symbol
        if tf_mins is None or sym is None:
            continue  # TF hoặc symbol không hợp lệ -> bỏ qua

        # Lấy danh sách gap window đã verified cho pair này (nếu có)
        verified_windows = (verified_gaps or {}).get((sym_id, tf_code), [])

        # ----- BƯỚC 3: Tính ngưỡng (threshold) để phân biệt gap thật vs giả -----
        asset_type = sym["asset_type"]  # "Indice", "Metal", "FOREX", "Crypto"

        # Ngưỡng cơ bản: gap phải > 3× khoảng cách TF mới coi là bất thường
        # Ví dụ H1 = 60 phút -> threshold = 180 phút (3 giờ)
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

        # ----- BƯỚC 4: Lọc từng gap - giữ lại chỉ hole thật -----
        real_gaps = []
        for gap_start, gap_end, gap_raw_min in gaps:
            # Bỏ qua nếu gap window này đã được xác nhận là market gap trước đó
            # (kiểm tra per-window thay vì skip cả pair - tránh bỏ sót hole thật mới)
            if any(vs <= gap_start and gap_end <= ve
                   for vs, ve in verified_windows):
                n_skip_verified += 1
                continue

            # Nếu asset nghỉ cuối tuần -> trừ giờ Sat/Sun khỏi gap
            # (vì gap dài 60h nhưng 48h là cuối tuần -> thực chỉ 12h)
            if asset_type in WEEKEND_CLOSED:
                trading_min = trading_hours_in_gap(gap_start, gap_end) * 60
            else:
                # Crypto: 24/7 -> gap thô = gap thực
                trading_min = float(gap_raw_min)

            # So sánh với ngưỡng: nếu vượt -> hole thật, nếu không -> loại
            if trading_min > threshold:
                real_gaps.append((gap_start, gap_end, trading_min))
            else:
                n_excluded += 1  # Gap bình thường (qua đêm/cuối tuần) -> loại

        # Nếu tất cả gap của cặp này đều bị loại -> không cần xử lý
        if not real_gaps:
            continue

        # ----- BƯỚC 5: Tính số bar cần pull cho hole này -----
        # Lấy thời điểm sớm nhất của gap -> tính khoảng cách đến hiện tại
        earliest_start = min(g[0] for g in real_gaps)
        hole_hours     = (now - earliest_start).total_seconds() / 3600
        # Tính số bar cần pull (có hệ số an toàn ×1.5, tối thiểu 10)
        n_bars_needed  = calc_gap_n_bars(hole_hours, tf_code, asset_type)

        # ----- BƯỚC 6: Thêm vào kết quả hoặc nâng cấp stale -----
        key = (sym_id, tf_code)
        if key in stale_index:
            # Cặp này đã có trong stale (từ backfill pipeline)
            # -> Nâng n_bars nếu hole cần pull nhiều hơn
            idx = stale_index[key]
            if stale[idx]["n_bars"] < n_bars_needed:
                stale[idx]["n_bars"]  = n_bars_needed
                stale[idx]["reason"] += "+HOLE"
                n_upgraded += 1
        else:
            # Cặp mới -> thêm vào danh sách hole cần xử lý
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
