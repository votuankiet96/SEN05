# =============================================================================
# data_provider/04_checker.py  —  Kiểm tra & Tự sửa chất lượng dữ liệu
# =============================================================================
#
# FILE NÀY LÀM GÌ?
#   Đây là "thanh tra dữ liệu" — chạy mỗi 3 ngày, nó so sánh dữ liệu trong
#   database của bạn với dữ liệu thực từ TradingView để phát hiện xem có nến
#   nào bị sai số liệu (giá mở/đóng/cao/thấp không khớp) hay bị thiếu không.
#
#   Quan trọng: Script KHÔNG tự động sửa. Khi phát hiện vấn đề, nó sẽ gửi
#   tin Telegram mô tả chi tiết và hỏi bạn có muốn sửa không.
#   Bạn gõ /confirm_TOKEN để sửa, hoặc /skip_TOKEN để bỏ qua.
#
# TẠI SAO CẦN SCRIPT NÀY?
#   Dữ liệu trong DB có thể bị lệch so với TradingView do nhiều lý do:
#     - Kết nối mạng bị gián đoạn giữa chừng → nến bị thiếu
#     - TradingView thỉnh thoảng điều chỉnh lại giá lịch sử (restatement)
#     - Pipeline ghi đúng nhưng có lỗi làm tròn số
#   Nếu dữ liệu sai, chiến lược giao dịch sẽ tính toán sai signal → nguy hiểm.
#
# QUY TRÌNH 3 GIAI ĐOẠN (chỉ khi chạy thường, không có --dry-run):
#
#   Giai đoạn 1 — SCAN (quét, không sửa):
#     Với mỗi cặp (symbol, timeframe):
#       - Kéo N nến mới nhất từ TradingView (nguồn dữ liệu gốc)
#       - Truy vấn đúng N nến đó trong database
#       - So sánh từng số liệu OHLCV: nếu chênh > 0.01% → đánh dấu sai
#     Kết quả: danh sách cặp có vấn đề (rate > 2%)
#
#   Giai đoạn 2 — CONFIRM (hỏi user):
#     Gửi Telegram mô tả chi tiết → đợi user gõ /confirm_TOKEN hoặc /skip_TOKEN
#     Timeout 4 giờ, mặc định bỏ qua nếu không có phản hồi
#
#   Giai đoạn 3 — REPAIR (sửa nếu user đồng ý):
#     - Acquire lock 'checker_repair' (ngăn WS chạy ETL đồng thời)
#     - Xóa nến sai → kéo lại từ TradingView → kiểm tra sau khi sửa
#     - Release lock → WS tự động resume ETL đã hoãn
#     - Gửi báo cáo kết quả lên Telegram
#
# CÁCH CHẠY THỦ CÔNG:
#   python 04_checker.py                    # chạy đầy đủ (hỏi trước khi sửa)
#   python 04_checker.py --dry-run          # chỉ scan + báo cáo, không hỏi, không sửa
#   python 04_checker.py --sym XAUUSD       # chỉ kiểm tra 1 symbol
#   python 04_checker.py --tf H4            # chỉ kiểm tra 1 khung thời gian
#   python 04_checker.py --threshold 0.05   # nâng ngưỡng sai lên 5% (mặc định 2%)
#
# LỊCH CHẠY TỰ ĐỘNG:
#   Task Scheduler (Windows) tự động gọi file này mỗi 3 ngày lúc 03:00 UTC
#
# KẾT QUẢ GỬI LÊN TELEGRAM:
#   - Nếu sạch: "✅ X pairs — tất cả dữ liệu khớp"
#   - Nếu có vấn đề: gửi câu hỏi /confirm_TOKEN để sửa
#   - Sau khi sửa: "🔧 X pairs đã sửa xong / Y pairs lỗi persistent"
# =============================================================================

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
_DATA = Path(__file__).resolve().parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import SYMBOLS, TF_STAGING, TF_MINUTES
from _helpers import (
    setup_logger,
    recompute_derived,
    pull_and_store,
    now_utc,
    sleep_for,
)
from _tv_auth import get_valid_tv_connection, refresh_mid_run
from _tg import tg_send, tg_flush
from _task_lock import acquire, release, cleanup_expired, request_confirm
from modules.db_connector import get_connection, delete_ohlcv_bars, delete_staging_bars, run_etl_direct

# =============================================================================
# HẰNG SỐ CẤU HÌNH
# =============================================================================

# Số nến kiểm tra mỗi lần cho từng TF (mỗi symbol).
# M5/M15 lớn hơn vì muốn bao phủ ~10 ngày giao dịch (để đảm bảo
# khi chạy mỗi 3 ngày vẫn có vùng chồng lấp với lần chạy trước).
CHECKER_N_BARS: dict[str, int] = {
    "M5":  2000,   # ~7 ngày nến 5 phút
    "M15": 2000,   # ~21 ngày nến 15 phút
    "M30": 1500,   # ~31 ngày nến 30 phút
    "M45": 1000,   # ~31 ngày nến 45 phút
    "H1":  1000,   # ~42 ngày nến 1 giờ
    "H2":  1000,   # ~83 ngày nến 2 giờ
    "H3":  1000,   # ~125 ngày nến 3 giờ
    "H4":  1000,   # ~167 ngày nến 4 giờ
}

# Số nến kiểm tra lại SAU KHI SỬA (để xác nhận sửa thành công).
# Nhỏ hơn CHECKER_N_BARS để nhanh hơn — chỉ cần kiểm tra ngắn gọn.
VERIFY_N_BARS = 200

# Ngưỡng tỷ lệ sai cho phép — nếu vượt ngưỡng này → hỏi user có sửa không.
# 0.02 = 2%: tức nếu > 2% số nến bị sai/thiếu → báo cáo vấn đề.
DEFAULT_THRESHOLD = 0.02

# Sai số tương đối cho phép khi so sánh giá OHLCV:
# abs(giá_TV - giá_DB) / giá_TV phải < 0.01% thì mới coi là "khớp".
# Cần thiết vì số thực trong máy tính không bao giờ hoàn toàn bằng nhau.
OHLCV_REL_TOL = 1e-4   # 0.01%

# Số vòng sửa tối đa trước khi kết luận cặp đó "lỗi cố định".
# Vòng 0: quét + sửa lần 1.
# Vòng 1: quét lại + sửa lần 2.
# Vòng 2: quét lần cuối, nếu vẫn sai → đánh dấu PERSISTENT_FAILURE.
MAX_REPAIR_ROUNDS = 3

# Danh sách khung thời gian cần kiểm tra (từ H4 xuống M5).
# Không kiểm tra W và D1 vì chúng ít thay đổi và TradingView
# rất hiếm khi điều chỉnh lại nến tuần/ngày.
TFS_TO_CHECK = ["H4", "H3", "H2", "H1", "M45", "M30", "M15", "M5"]

# Thời gian nghỉ giữa mỗi lần gọi API TradingView (giây).
# Tránh bị TradingView phát hiện là bot và giới hạn tốc độ (rate-limit).
TV_SLEEP_BETWEEN_CALLS = 0.5

# Thời gian nghỉ dài khi TradingView trả về rỗng 3 lần liên tiếp.
# Khả năng cao là đang bị rate-limit → nghỉ 60 giây để TV "nguội xuống".
TV_THROTTLE_SLEEP = 60

# Circuit breaker: nếu auth fail liên tiếp vượt ngưỡng này → ngừng checker.
# Mục đích: tránh tiếp tục gọi TV khi IP đã bị block (HTTP 403), làm tệ hơn.
# 5 lần ≈ ~40 giây (sau throttle) → đủ để phân biệt lỗi tạm thời vs. block thật.
MAX_AUTH_CONSECUTIVE_FAIL = 5


# ─── Helper: build Interval map (lazy import to avoid circular import) ───────

def _build_interval_map() -> dict:
    """Build tf_code → tvDatafeed.Interval mapping. Called once in main()."""
    from tvDatafeed import Interval
    return {
        "H4":  Interval.in_4_hour,
        "H3":  Interval.in_3_hour,
        "H2":  Interval.in_2_hour,
        "H1":  Interval.in_1_hour,
        "M45": Interval.in_45_minute,
        "M30": Interval.in_30_minute,
        "M15": Interval.in_15_minute,
        "M5":  Interval.in_5_minute,
    }


# =============================================================================
# SO SÁNH DỮ LIỆU OHLCV — Trái tim của quá trình kiểm tra
# =============================================================================

def _ohlcv_match(tv_vals: tuple, db_vals: tuple) -> bool:
    """
    Kiểm tra 4 giá trị OHLCV (Open, High, Low, Close) có khớp nhau không.

    Không so sánh bằng nhau tuyệt đối vì số thực trong máy tính có thể
    có sai số nhỏ do làm tròn. Thay vào đó dùng sai số tương đối:
      |giá_TV - giá_DB| / giá_TV < 0.01%  →  coi là khớp

    Dùng floor 1e-6 để tránh false-positive khi giá TV = 0 (tránh divide-by-zero).
    Không dùng max(OHLCV_REL_TOL, abs(a)*OHLCV_REL_TOL) vì với giá rất nhỏ
    (VD: EURUSD nhỏ, crypto) tolerance tuyệt đối 1e-4 sẽ che khuất sai số 100%.

    Ví dụ:
      TV = 1900.1234, DB = 1900.1235 → chênh 0.0001/1900 ≈ 0% → KHỚP
      TV = 1900.0000, DB = 1902.0000 → chênh 2/1900 = 0.1% → KHÔNG KHỚP
      TV = 0.00010,   DB = 0.00020   → chênh 100% → KHÔNG KHỚP (trường hợp cũ bỏ sót)

    Trả về True nếu tất cả 4 giá trị đều khớp, False nếu bất kỳ cái nào sai.
    """
    for a, b in zip(tv_vals, db_vals):
        tol = max(abs(a) * OHLCV_REL_TOL, 1e-6)
        if abs(a - b) > tol:
            return False
    return True


def _compare(tv_dict: dict, db_dict: dict) -> tuple[set, dict, float]:
    """
    So sánh dữ liệu TradingView với dữ liệu trong DB cho một cặp (symbol, TF).

    Tham số:
      tv_dict — dữ liệu từ TradingView: {timestamp: (O, H, L, C)}
      db_dict — dữ liệu từ DB:          {timestamp: (O, H, L, C)}

    Trả về 3 giá trị:
      missing       — set timestamp có trong TV nhưng KHÔNG có trong DB (thiếu nến)
      mismatched    — dict {timestamp: (giá_TV, giá_DB)} nến có nhưng giá sai
      mismatch_rate — tỷ lệ = (thiếu + sai) / tổng_nến_TV (0.0 = hoàn hảo, 1.0 = sai hết)

    Ví dụ kết quả:
      missing = {datetime(2024,1,15,8,0), ...}  # 2 nến thiếu
      mismatched = {datetime(2024,1,14,16,0): ((1900.1, ...), (1890.2, ...))}  # 1 nến sai giá
      rate = 3/1000 = 0.003 = 0.3%  → dưới ngưỡng 2% → không cần sửa
    """
    if not tv_dict:
        return set(), {}, 0.0

    tv_keys = set(tv_dict)
    db_keys = set(db_dict)
    # Tìm nến có trong TV nhưng không có trong DB (thiếu)
    missing = tv_keys - db_keys
    # Tìm nến có ở cả 2 nơi nhưng giá trị không khớp (sai)
    mismatched = {
        dt: (tv_dict[dt], db_dict[dt])
        for dt in tv_keys & db_keys
        if not _ohlcv_match(tv_dict[dt], db_dict[dt])
    }
    # Tỷ lệ sai = (số thiếu + số sai) / tổng nến TradingView
    rate = (len(missing) + len(mismatched)) / len(tv_dict)
    return missing, mismatched, rate


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _query_db_bars(symbol_id: int, tf_code: str,
                   from_dt: datetime, to_dt: datetime) -> dict:
    """
    Query Fact_OHLCV for bars within [from_dt, to_dt].
    Returns dict {datetime: (open, high, low, close)}.
    Returns {} on error.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.BarTime, f.[Open], f.High, f.Low, f.[Close]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              AND  f.BarTime  BETWEEN ? AND ?
            ORDER  BY f.BarTime
        """, (symbol_id, tf_code, from_dt, to_dt))
        return {row[0]: (float(row[1]), float(row[2]),
                         float(row[3]), float(row[4]))
                for row in cursor.fetchall()}
    except Exception as e:
        logging.getLogger("checker").error(
            "_query_db_bars FAILED sym_id=%s tf=%s: %s", symbol_id, tf_code, e
        )
        return {}
    finally:
        conn.close()


# ─── TV helpers ──────────────────────────────────────────────────────────────

def _pull_tv_bars(tv, sym: dict, tf_code: str, n_bars: int,
                  interval_map: dict,
                  logger: logging.Logger) -> dict | None:
    """
    Pull n_bars from TradingView for (symbol, TF).
    Drops last bar (may be open/unclosed) and normalizes datetimes to naive UTC.

    Returns dict {datetime: (O, H, L, C)}.
    Returns None on TV error or empty response.
    """
    interval = interval_map[tf_code]
    try:
        df = tv.get_hist(
            symbol   = sym["tv_symbol"],
            exchange = sym["tv_exchange"],
            interval = interval,
            n_bars   = n_bars + 5,  # extra buffer; last bar discarded
        )
    except Exception as e:
        logger.error("TV FAIL %s %s: %s", sym["tv_symbol"], tf_code, e)
        return None

    if df is None or df.empty:
        return None

    # Discard last bar (potentially still open)
    df = df.iloc[:-1]
    if df.empty:
        return None

    result: dict[datetime, tuple] = {}
    for dt_idx, row in df.iterrows():
        # Normalize: pandas Timestamp → naive Python datetime (UTC)
        dt = dt_idx.to_pydatetime()
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        result[dt] = (float(row["open"]), float(row["high"]),
                      float(row["low"]),  float(row["close"]))
    return result if result else None


# ─── Scan & Repair ───────────────────────────────────────────────────────────

def _scan_pair(tv, sym: dict, tf_code: str, n_bars: int,
               interval_map: dict, logger: logging.Logger) -> dict | None:
    """
    Scan one (symbol, TF) pair.
    Returns scan result dict or None on TV failure.

    Result dict keys: missing, mismatched, rate, from_dt, to_dt
    """
    tv_bars = _pull_tv_bars(tv, sym, tf_code, n_bars, interval_map, logger)
    if tv_bars is None:
        return None

    from_dt = min(tv_bars)
    to_dt   = max(tv_bars)
    db_bars = _query_db_bars(sym["symbol_id"], tf_code, from_dt, to_dt)

    missing, mismatched, rate = _compare(tv_bars, db_bars)
    return {
        "missing":    missing,
        "mismatched": mismatched,
        "rate":       rate,
        "from_dt":    from_dt,
        "to_dt":      to_dt,
    }


def _repair_pair(tv, sym: dict, tf_code: str, scan: dict,
                 interval_map: dict, logger: logging.Logger) -> bool:
    """
    Repair one (symbol, TF) pair với thứ tự an toàn — tránh mất bar vĩnh viễn.

    Thứ tự mới (safe):
      1. Xóa staging cũ (tạm thời, an toàn — không ảnh hưởng Fact)
      2. Kéo data mới từ TV vào Staging TRƯỚC (skip_etl=True — chưa đụng Fact)
         → Nếu bước này thất bại: Fact vẫn nguyên vẹn, không mất bar nào
      3. CHỈ sau khi staging thành công: xóa bars sai khỏi Fact
         → Nếu bước này thất bại: staging vẫn có data đúng, có thể retry ETL
      4. Chạy ETL staging → Fact để insert bars mới (missing + đã xóa ở bước 3)

    Thứ tự cũ (nguy hiểm):
      Xóa Fact trước → kéo TV sau → nếu TV fail: mất bar vĩnh viễn.

    Returns True nếu toàn bộ quá trình thành công.
    """
    n_bars   = CHECKER_N_BARS[tf_code]
    interval = interval_map[tf_code]
    staging  = TF_STAGING.get(tf_code)
    sym_id   = sym["symbol_id"]
    label    = sym["tv_symbol"]

    # Bước 1: Xóa staging cũ để tránh stale rows gây MERGE collision
    if staging:
        delete_staging_bars(sym_id, staging)

    # Bước 2: Kéo TV → Staging ONLY (chưa ETL sang Fact)
    # Nếu TV thất bại ở đây → Fact hoàn toàn nguyên vẹn
    staged = pull_and_store(tv, sym, tf_code, n_bars, interval, logger, skip_etl=True)
    if staged < 0:
        logger.error(
            "  Re-pull FAILED for %s %s (staged=%d) — Fact untouched, no data loss",
            label, tf_code, staged,
        )
        return False

    # Bước 3: Staging đã có data đúng → giờ mới an toàn để xóa bars sai khỏi Fact
    if scan["mismatched"]:
        bar_times = list(scan["mismatched"].keys())
        deleted = delete_ohlcv_bars(sym_id, tf_code, bar_times)
        logger.info("  Deleted %d mismatched bars from Fact — %s %s", deleted, label, tf_code)

    # Bước 4: ETL staging → Fact (insert missing bars + bars vừa xóa ở bước 3)
    try:
        etl_inserted = run_etl_direct(sym_id, tf_code, staging)
        logger.info("  ETL: +%d bars inserted into Fact — %s %s", etl_inserted, label, tf_code)
    except Exception as e:
        logger.error(
            "  ETL FAILED after staging — %s %s: %s — data still in staging, can retry ETL",
            label, tf_code, e,
        )
        return False
    return True


def _verify_pair(tv, sym: dict, tf_code: str,
                 interval_map: dict, logger: logging.Logger,
                 n_bars: int | None = None) -> tuple[bool, float]:
    """
    Post-repair verification. Mặc định dùng CHECKER_N_BARS[tf_code] (cùng depth với scan)
    để không bỏ sót lỗi cũ nằm ngoài VERIFY_N_BARS=200.
    Truyền n_bars=VERIFY_N_BARS để dùng quick scan nếu cần tốc độ.
    Returns (is_clean, mismatch_rate).
    """
    bars = n_bars if n_bars is not None else CHECKER_N_BARS.get(tf_code, VERIFY_N_BARS)
    scan = _scan_pair(tv, sym, tf_code, bars, interval_map, logger)
    if scan is None:
        return False, 1.0
    return scan["rate"] == 0.0, scan["rate"]


# ─── Main orchestrator ───────────────────────────────────────────────────────

def run_checker(tv, symbols: list, tfs: list, interval_map: dict,
                dry_run: bool, threshold: float,
                logger: logging.Logger) -> dict:
    """
    Full checker run.

    Phase 1+2: Scan all (symbol, TF) pairs. Repair failing ones. Retry up to
               MAX_REPAIR_ROUNDS times. Pairs that remain broken become
               PERSISTENT_FAILURE.
    Phase 3:   Recompute derived TFs (M10/M20/M90/H6/H8) for repaired symbols.
    Phase 4:   Return stats dict for report.
    """
    total_pairs       = len(symbols) * len(tfs)
    ok_pairs          = 0     # Clean on first scan
    repaired_pairs    = 0     # Fixed after repair
    persistent_fails: list[dict] = []
    dry_issues:       list[dict] = []  # Populated in dry_run mode: pairs có rate > threshold

    repaired_sym_ids: set[int] = set()

    # Counters for rate-limit / auth protection
    tv_consecutive_fail = 0
    auth_consecutive_fail = 0

    # Thu thập scan rate round 0 để phát hiện DST transition
    round0_rates: dict[tuple, float] = {}   # {(tv_symbol, tf_code): rate}

    logger.info(
        "=== CHECKER START | %d pairs | dry=%s | threshold=%.1f%% ===",
        total_pairs, dry_run, threshold * 100,
    )

    # ── Repair loop ──────────────────────────────────────────────────────────
    # Start with all pairs; each round carries forward only the still-failing ones.
    pending: list[tuple[dict, str]] = [
        (sym, tf) for sym in symbols for tf in tfs
    ]

    for repair_round in range(MAX_REPAIR_ROUNDS):
        if not pending:
            break

        is_final_round = (repair_round == MAX_REPAIR_ROUNDS - 1)
        round_name = "INITIAL SCAN" if repair_round == 0 else f"RETRY {repair_round}"
        logger.info("--- %s | %d pairs pending ---", round_name, len(pending))

        still_failing: list[tuple[dict, str]] = []
        prev_symbol: str | None = None  # theo dõi symbol trước để thêm sleep khi đổi symbol

        for idx, (sym, tf_code) in enumerate(pending, 1):
            label = f"{sym['tv_symbol']}/{tf_code}"

            if idx % 50 == 1:
                logger.info("[%d/%d] %s ...", idx, len(pending), label)

            # Nghỉ giữa các symbol (không chỉ giữa TF) để giảm rate với TradingView.
            # 0.5s giữa các TF là đủ, nhưng khi chuyển sang symbol mới thì dùng
            # sleep_for() (5s hoặc 10s cho GOLD) để tránh bị phát hiện là bot.
            if sym["tv_symbol"] != prev_symbol:
                if prev_symbol is not None:
                    sleep_for(sym["tv_symbol"])
                prev_symbol = sym["tv_symbol"]

            # ── Pull & scan ──────────────────────────────────────────────────
            n_bars = CHECKER_N_BARS[tf_code]
            scan   = _scan_pair(tv, sym, tf_code, n_bars, interval_map, logger)

            if scan is None:
                # TV returned nothing (error or empty)
                tv_consecutive_fail   += 1
                auth_consecutive_fail += 1

                # Throttle: too many consecutive TV failures
                if tv_consecutive_fail >= 3:
                    logger.warning(
                        "3+ consecutive TV failures — sleeping %ds (throttle/rate-limit)",
                        TV_THROTTLE_SLEEP,
                    )
                    time.sleep(TV_THROTTLE_SLEEP)
                    tv_consecutive_fail = 0

                # Auth: try mid-run refresh sau 3 lần fail
                if auth_consecutive_fail >= 3 and not dry_run:
                    logger.warning("3+ auth-related failures — attempting mid-run refresh...")
                    if refresh_mid_run(tv, logger):
                        auth_consecutive_fail = 0

                # Circuit breaker: dừng hẳn nếu auth fail liên tiếp quá nhiều
                # Khả năng IP bị block (403) → tiếp tục request sẽ làm tình trạng tệ hơn
                if auth_consecutive_fail >= MAX_AUTH_CONSECUTIVE_FAIL:
                    logger.error(
                        "[CIRCUIT BREAKER] %d auth/TV failures liên tiếp — "
                        "khả năng IP bị block. Dừng checker.",
                        auth_consecutive_fail,
                    )
                    tg_send(
                        f"🚨 <b>[Checker]</b> {auth_consecutive_fail} lần TV thất bại "
                        "liên tiếp — nghi IP bị block. Đã dừng tự động.\n"
                        "Chờ 30 phút hoặc đổi kết nối internet rồi chạy lại."
                    )
                    return {
                        "ok": ok_pairs, "repaired": repaired_pairs,
                        "failed": len(persistent_fails),
                        "persistent": persistent_fails,
                        "dry_issues": dry_issues,
                        "aborted": "circuit_breaker",
                    }

                if is_final_round:
                    persistent_fails.append(
                        {"sym": sym["tv_symbol"], "tf": tf_code,
                         "reason": "TV empty/error (could not verify)"}
                    )
                else:
                    still_failing.append((sym, tf_code))
                time.sleep(TV_SLEEP_BETWEEN_CALLS)
                continue

            # Reset consecutive-fail counters on successful TV pull
            tv_consecutive_fail   = 0
            auth_consecutive_fail = 0

            rate   = scan["rate"]
            n_miss = len(scan["missing"])
            n_bad  = len(scan["mismatched"])

            # Ghi lại rate round 0 để dùng cho DST detection sau khi loop kết thúc
            if repair_round == 0:
                round0_rates[(sym["tv_symbol"], tf_code)] = rate

            # ── Decision ─────────────────────────────────────────────────────
            if rate <= threshold:
                # Pair is clean
                if repair_round == 0:
                    ok_pairs += 1
                    if rate > 0:
                        logger.debug("  ≈ %s: %.2f%% below threshold (ok)", label, rate * 100)
                else:
                    # Was failing in a previous round, now clean
                    repaired_pairs += 1
                    repaired_sym_ids.add(sym["symbol_id"])
                    logger.info("  ✓ RESOLVED %s (scan rate now %.2f%%)", label, rate * 100)

            elif is_final_round:
                # Last round: no more repair attempts
                persistent_fails.append(
                    {"sym": sym["tv_symbol"], "tf": tf_code,
                     "reason": f"{rate:.1%} mismatch ({n_miss} missing, {n_bad} wrong)"}
                )
                logger.error("  ✗ PERSISTENT %s: %.1f%% mismatch", label, rate * 100)

            elif not dry_run:
                # Attempt repair
                logger.warning(
                    "  ✗ %s: %.1f%% mismatch (miss=%d wrong=%d) — repairing...",
                    label, rate * 100, n_miss, n_bad,
                )
                repair_ok = _repair_pair(tv, sym, tf_code, scan, interval_map, logger)

                if repair_ok:
                    # Quick verify after repair
                    time.sleep(TV_SLEEP_BETWEEN_CALLS)
                    is_clean, verify_rate = _verify_pair(
                        tv, sym, tf_code, interval_map, logger
                    )
                    if is_clean:
                        repaired_pairs += 1
                        repaired_sym_ids.add(sym["symbol_id"])
                        logger.info("  ✓ REPAIRED %s (verified clean)", label)
                    else:
                        logger.warning(
                            "  ↻ %s: %.1f%% still failing after repair — will retry",
                            label, verify_rate * 100,
                        )
                        still_failing.append((sym, tf_code))
                else:
                    still_failing.append((sym, tf_code))

            else:
                # dry_run — ghi nhận vấn đề nhưng không sửa
                logger.warning(
                    "  [DRY] %s: %.1f%% mismatch (miss=%d wrong=%d)",
                    label, rate * 100, n_miss, n_bad,
                )
                # Thu thập vào dry_issues để main() có thể hỏi user sau
                dry_issues.append({
                    "sym": sym["tv_symbol"],
                    "tf":  tf_code,
                    "reason": f"{rate:.1%} mismatch ({n_miss} missing, {n_bad} wrong)",
                })
                # Không tăng ok_pairs — pair này có vấn đề, không phải "ok"

            time.sleep(TV_SLEEP_BETWEEN_CALLS)

        pending = still_failing

        # ── DST detection sau round 0 ────────────────────────────────────────
        # Nếu >30% cặp H2/H3/H4 đều báo rate > 50% → khả năng DST transition,
        # không phải lỗi data thật → bỏ qua repair cho các TF đó hôm nay.
        if repair_round == 0 and round0_rates:
            h_tfs = {"H2", "H3", "H4"}
            h_rates = [r for (_, tf), r in round0_rates.items() if tf in h_tfs]
            if h_rates:
                high_count = sum(1 for r in h_rates if r > 0.5)
                dst_ratio  = high_count / len(h_rates)
                if dst_ratio > 0.3:
                    logger.warning(
                        "[DST DETECT] %.0f%% cặp H2/H3/H4 có rate > 50%% — "
                        "khả năng DST transition (tháng 3/11). "
                        "Bỏ qua repair H2/H3/H4 để tránh sửa nhầm. Chạy lại sau 24h.",
                        dst_ratio * 100,
                    )
                    tg_send(
                        f"⚠️ <b>[Checker]</b> Phát hiện khả năng <b>DST transition</b> "
                        f"({dst_ratio:.0%} cặp H2/H3/H4 báo rate >50%).\n"
                        "Đã bỏ qua repair H2/H3/H4 hôm nay. Chạy lại sau 24h."
                    )
                    # Lọc các cặp H2/H3/H4 ra khỏi pending để không repair nhầm
                    pending = [(s, tf) for s, tf in pending if tf not in h_tfs]

    # Any leftover pairs (shouldn't happen, but safety net)
    for sym, tf_code in pending:
        persistent_fails.append(
            {"sym": sym["tv_symbol"], "tf": tf_code,
             "reason": f"unresolved after {MAX_REPAIR_ROUNDS} rounds"}
        )

    # ── Phase 3: Recompute derived TFs ───────────────────────────────────────
    if repaired_sym_ids and not dry_run:
        logger.info(
            "Recomputing derived TFs for %d repaired symbol(s)...",
            len(repaired_sym_ids),
        )
        recompute_derived(repaired_sym_ids, logger)

    return {
        "total":      total_pairs,
        "ok":         ok_pairs,
        "repaired":   repaired_pairs,
        "failed":     len(persistent_fails),
        "failures":   persistent_fails,
        "dry_run":    dry_run,
        "dry_issues": dry_issues,  # populated khi dry_run=True, rỗng khi dry_run=False
    }


# ─── Problem description builder ─────────────────────────────────────────────

def _build_problem_desc(issues: list[dict], threshold: float) -> str:
    """
    Tạo mô tả vấn đề từ danh sách dry_issues để gửi vào tg_ask().
    """
    n = len(issues)
    miss_count  = sum(1 for f in issues if "missing" in f["reason"])
    wrong_count = sum(1 for f in issues if "wrong"   in f["reason"])

    lines = [f"  • {n} pairs cần sửa (mismatch > {threshold:.0%})"]
    if wrong_count:
        lines.append(f"  • {wrong_count} pairs: bars sai giá trị OHLCV")
    if miss_count:
        lines.append(f"  • {miss_count} pairs: bars còn thiếu")
    return "\n".join(lines)


# ─── Telegram report ─────────────────────────────────────────────────────────

def _build_report(stats: dict, start_time: datetime, auth_mode: str) -> str:
    elapsed = max(1, int((now_utc() - start_time).total_seconds() / 60))
    total   = stats["total"]
    lines   = [
        f"🔍 <b>[Checker] Hoàn tất {now_utc():%d/%m/%Y %H:%M}</b>",
        "",
        f"✅ OK:              {stats['ok']}/{total} pairs",
        f"🔧 Đã sửa:          {stats['repaired']}/{total} pairs",
        f"❌ Lỗi persistent:  {stats['failed']}/{total} pairs",
        "",
        f"Thời gian: {elapsed} phút | Auth: {auth_mode}",
    ]
    if stats["dry_run"]:
        lines.insert(1, "<i>(DRY-RUN — scan only, không ghi DB)</i>")
    if stats["failures"]:
        lines.append("")
        lines.append("Pairs lỗi:")
        for f in stats["failures"][:10]:
            lines.append(f"  • {f['sym']} / {f['tf']}: {f['reason']}")
        if len(stats["failures"]) > 10:
            lines.append(f"  ... và {len(stats['failures']) - 10} pair khác")
    return "\n".join(lines)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEN05 — Ground-truth data integrity checker"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan only — no DB writes, no repairs",
    )
    parser.add_argument(
        "--sym", default=None, metavar="SYMBOL",
        help="Check a single symbol only (e.g. XAUUSD)",
    )
    parser.add_argument(
        "--tf", default=None, metavar="TF",
        help=f"Check a single TF only (one of: {', '.join(TFS_TO_CHECK)})",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD, metavar="RATE",
        help=f"Mismatch threshold (default {DEFAULT_THRESHOLD * 100:.0f}%%). E.g. 0.05 = 5%%.",
    )
    parser.add_argument(
        "--co-check", action="store_true",
        help="Run C[T0]=O[T1] continuity check (DB-only, no TradingView). Can combine with --dry-run.",
    )
    parser.add_argument(
        "--co-days", type=int, default=7, metavar="DAYS",
        help="Lookback window in days for --co-check (default: 7)",
    )
    parser.add_argument(
        "--tf-check", action="store_true",
        help="Kiểm tra interval gaps giữa các bar liên tiếp (DB-only, no TradingView).",
    )
    parser.add_argument(
        "--tf-check-full", action="store_true",
        help="Dùng với --tf-check: hiển thị chi tiết short/long gap breakdown.",
    )
    parser.add_argument(
        "--rebuild-computed", action="store_true",
        help="Xoá và rebuild TF phái sinh (M10/M20/M90/H6/H8). Dùng --dry-run để xem preview.",
    )
    args = parser.parse_args()

    # ── Setup logger ─────────────────────────────────────────────────────────
    log_dir = _DATA / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = setup_logger("checker", str(log_dir / "checker.log"), rotating=True)

    logger.info(
        "==== 04_checker.py START | dry=%s | sym=%s | tf=%s | threshold=%.1f%% ====",
        args.dry_run, args.sym or "ALL", args.tf or "ALL", args.threshold * 100,
    )
    start_time = now_utc()

    # ── Filter symbols / TFs ─────────────────────────────────────────────────
    symbols = list(SYMBOLS)
    if args.sym:
        needle  = args.sym.upper()
        symbols = [s for s in SYMBOLS if s["tv_symbol"] == needle]
        if not symbols:
            logger.error("Symbol '%s' not found in SYMBOLS list.", args.sym)
            sys.exit(1)

    # --tf-check / --rebuild-computed operate across all 15 TFs, not just TFS_TO_CHECK
    db_only_mode = args.tf_check or args.rebuild_computed

    tfs = list(TFS_TO_CHECK)
    if args.tf:
        tf = args.tf.upper()
        if not db_only_mode and tf not in TFS_TO_CHECK:
            logger.error("TF '%s' not in TFS_TO_CHECK (%s).", tf, TFS_TO_CHECK)
            sys.exit(1)
        tfs = [tf]

    # ── C-O continuity check (DB-only, runs before TradingView connection) ─────
    if args.co_check:
        sym_filter = [args.sym] if args.sym else None
        co_clean, co_stats = check_co_continuity(
            lookback_days=args.co_days,
            sym_filter=sym_filter,
        )
        co_report = _format_co_report(co_stats, args.co_days)
        logger.info("\n%s", co_report)
        print("\n" + co_report + "\n")
        tg_send(f"<b>[Checker] C-O Check</b>\n<pre>{co_report}</pre>")
        tg_flush()
        if not args.dry_run:
            # standalone --co-check: exit after report
            sys.exit(0 if co_clean else 1)

    # ── Interval gap check (DB-only) ─────────────────────────────────────────
    if args.tf_check:
        tf_report, has_issues = check_interval_gaps(
            sym_filter=[args.sym] if args.sym else None,
            tf_filter=args.tf.upper() if args.tf else None,
            full=args.tf_check_full,
        )
        logger.info("\n%s", tf_report)
        tg_send(f"<b>[Checker] TF Gap Check</b>\n<pre>{tf_report}</pre>")
        tg_flush()
        sys.exit(1 if has_issues else 0)

    # ── Rebuild computed TFs (DB-only) ───────────────────────────────────────
    if args.rebuild_computed:
        rc_report = rebuild_computed_tfs(
            dry_run=args.dry_run,
            sym_filter=[args.sym] if args.sym else None,
            tf_filter=args.tf.upper() if args.tf else None,
            logger=logger,
        )
        logger.info("\n%s", rc_report)
        tg_send(f"<b>[Checker] Rebuild Computed TFs</b>\n<pre>{rc_report}</pre>")
        tg_flush()
        sys.exit(0)

    # ── Build Interval map (deferred import) ─────────────────────────────────
    interval_map = _build_interval_map()

    # ── Dọn dẹp lock cũ từ các lần chạy bị crash trước đó ───────────────────
    cleaned = cleanup_expired()
    if cleaned:
        logger.info("Cleaned up %d expired lock(s).", cleaned)

    # ── Auth + connection ─────────────────────────────────────────────────────
    dry_label = " (DRY-RUN)" if args.dry_run else ""
    tg_send(
        f"🔍 <b>[Checker]</b> Khởi động{dry_label} "
        f"| {len(symbols)} symbols × {len(tfs)} TFs"
    )
    tv, auth_mode = get_valid_tv_connection(logger)
    logger.info("Auth mode: %s", auth_mode)

    # ── Guard: không chạy nếu đang ở guest mode ──────────────────────────────
    # Guest mode chỉ trả về ~500 bars thay vì 1000-2000 → checker sẽ thấy hàng trăm
    # bar "missing" không có thật → kích hoạt sửa nhầm → PERSISTENT_FAILURE ảo.
    if auth_mode == "guest":
        msg = (
            "⚠️ <b>[Checker]</b> Đang chạy ở <b>GUEST MODE</b> — bar limit bị giảm "
            "xuống ~500 bars. Checker sẽ báo sai nhiều 'missing bars' không có thật "
            "và có thể kích hoạt sửa nhầm.\n\n"
            "Hãy cập nhật <code>TV_AUTH_TOKEN</code> hoặc <code>TV_COOKIE</code> "
            "trong file <code>.env</code> rồi chạy lại."
        )
        logger.error("[CHECKER] Guest mode detected — aborting to prevent false repairs.")
        tg_send(msg)
        tg_flush()
        sys.exit(2)

    # ── Chế độ dry-run: scan và báo cáo, không hỏi xác nhận ─────────────────
    if args.dry_run:
        stats = run_checker(
            tv, symbols, tfs, interval_map,
            dry_run=True, threshold=args.threshold, logger=logger,
        )
        report = _build_report(stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.info(
            "==== DONE (dry-run) | ok=%d issues=%d ====",
            stats["ok"], len(stats["dry_issues"]),
        )
        sys.exit(0)

    # ── Phase 1: Scan (dry_run=True) — tìm vấn đề mà không sửa ─────────────
    logger.info("Phase 1: Scanning (dry-run to detect issues)...")
    tg_send("🔍 <b>[Checker]</b> Đang scan dữ liệu (Phase 1/3)...")
    scan_stats = run_checker(
        tv, symbols, tfs, interval_map,
        dry_run=True, threshold=args.threshold, logger=logger,
    )

    issues = scan_stats.get("dry_issues", [])
    logger.info("Phase 1 done: %d issue(s) found.", len(issues))

    # ── Không có vấn đề gì: báo cáo và thoát ────────────────────────────────
    if not issues:
        report = _build_report(scan_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.info("==== DONE | all clean ====")
        sys.exit(0)

    # ── Phase 2: Hỏi user trước khi repair ───────────────────────────────────
    logger.info("Phase 2: Requesting user confirmation for %d issue(s)...", len(issues))
    choice = request_confirm(
        title="[Checker] Phát hiện vấn đề dữ liệu",
        problem_desc=_build_problem_desc(issues, args.threshold),
        options={
            "confirm": f"Sửa tất cả {len(issues)} pairs",
            "skip":    "Bỏ qua, chỉ lưu báo cáo",
        },
        timeout_min=240,
        affected_pairs=[f"{f['sym']}/{f['tf']}: {f['reason']}" for f in issues[:8]],
        task_name="checker_repair",
    )

    logger.info("User choice: %s", choice)

    # ── User chọn skip hoặc timeout: chỉ gửi scan report ────────────────────
    if choice != "confirm":
        reason = "hết giờ (4h)" if choice == "timeout" else "người dùng bỏ qua"
        report = _build_report(scan_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(f"📋 <b>[Checker]</b> Scan report ({reason}):\n\n" + report)
        tg_flush()
        logger.info("==== DONE | repair skipped (%s) ====", reason)
        sys.exit(0)

    # ── Phase 3: Acquire lock + repair ───────────────────────────────────────
    logger.info("Phase 3: Acquiring lock and running repair...")
    if not acquire("checker_repair", duration_min=90):
        msg = "⚠️ <b>[Checker]</b> Lock đang bận — process khác đang sửa dữ liệu. Bỏ qua."
        logger.warning("Could not acquire checker_repair lock.")
        tg_send(msg)
        tg_flush()
        sys.exit(1)

    exit_code = 1  # default: failure — sẽ ghi đè sau khi repair hoàn tất
    try:
        tg_send(
            f"🔧 <b>[Checker]</b> Đang sửa {len(issues)} pairs (Phase 3/3)...\n"
            "<i>WS Live đang tạm hoãn ETL để tránh xung đột. Sẽ tự resume sau khi xong.</i>"
        )
        repair_stats = run_checker(
            tv, symbols, tfs, interval_map,
            dry_run=False, threshold=args.threshold, logger=logger,
        )
        report = _build_report(repair_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        logger.info(
            "==== DONE | repaired=%d failed=%d ====",
            repair_stats["repaired"], repair_stats["failed"],
        )
        exit_code = 1 if repair_stats["failed"] > 0 else 0
    finally:
        release("checker_repair")
        logger.info("Lock released.")
        tg_flush()  # luôn flush dù try thành công hay exception

    sys.exit(exit_code)


# =============================================================================
# C[T0] = O[T1] CONTINUITY CHECK  (DB-only, no TradingView needed)
# =============================================================================

# D1, W excluded — overnight/weekend gaps are expected and do not violate C=O
_CO_TF_MINUTES: dict[str, int] = {
    "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
    "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480,
}

# Static CASE expression — safe, no user input
_CO_TF_CASE = "\n               ".join(
    f"WHEN '{code}' THEN {mins}"
    for code, mins in _CO_TF_MINUTES.items()
)

_CO_SQL = """
    WITH tf_mins AS (
        SELECT tf.TimeframeID, tf.Code,
               CASE tf.Code
                   {tf_case}
                   ELSE NULL
               END AS TFMinutes
        FROM DWH.Dim_Timeframe tf
    ),
    seq AS (
        SELECT f.SymbolID, f.TimeframeID,
               f.BarTime, f.[Close],
               LEAD(f.[Open]) OVER (
                   PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
               ) AS NextOpen,
               LEAD(f.BarTime) OVER (
                   PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
               ) AS NextBarTime,
               tfm.TFMinutes
        FROM DWH.Fact_OHLCV f
        JOIN tf_mins tfm ON tfm.TimeframeID = f.TimeframeID
        WHERE tfm.TFMinutes IS NOT NULL
          AND f.BarTime >= DATEADD(DAY, -?, GETUTCDATE())
          AND f.SymbolID IN ({sym_phs})
    )
    SELECT
        COUNT(*)  AS TotalChecked,
        SUM(CASE WHEN ABS(NextOpen - [Close]) / NULLIF([Close], 0) * 100 > 0.5
                 THEN 1 ELSE 0 END) AS Flagged
    FROM seq
    WHERE NextOpen IS NOT NULL
      AND DATEDIFF(MINUTE, BarTime, NextBarTime) = TFMinutes
"""

_CO_SQL_TOP = """
    WITH tf_mins AS (
        SELECT tf.TimeframeID, tf.Code,
               CASE tf.Code
                   {tf_case}
                   ELSE NULL
               END AS TFMinutes
        FROM DWH.Dim_Timeframe tf
    ),
    seq AS (
        SELECT s.Symbol, tfm.Code AS TFCode,
               f.BarTime, f.[Close],
               LEAD(f.[Open]) OVER (
                   PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
               ) AS NextOpen,
               LEAD(f.BarTime) OVER (
                   PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
               ) AS NextBarTime,
               tfm.TFMinutes
        FROM DWH.Fact_OHLCV f
        JOIN DWH.Dim_Symbol s ON s.SymbolID = f.SymbolID
        JOIN tf_mins tfm      ON tfm.TimeframeID = f.TimeframeID
        WHERE tfm.TFMinutes IS NOT NULL
          AND f.BarTime >= DATEADD(DAY, -?, GETUTCDATE())
          AND f.SymbolID IN ({sym_phs})
    )
    SELECT TOP 10
        Symbol, TFCode,
        CONVERT(VARCHAR(19), BarTime, 120) AS BarTime,
        [Close], NextOpen,
        ABS(NextOpen - [Close]) / NULLIF([Close], 0) * 100 AS DiffPct
    FROM seq
    WHERE NextOpen IS NOT NULL
      AND DATEDIFF(MINUTE, BarTime, NextBarTime) = TFMinutes
      AND ABS(NextOpen - [Close]) / NULLIF([Close], 0) * 100 > 0.5
    ORDER BY DiffPct DESC
"""


def check_co_continuity(
    lookback_days: int = 7,
    sym_filter: list | None = None,
) -> tuple[bool, dict]:
    """
    Kiểm tra C[T0] = O[T1] cho các bar liên tiếp trong cùng session.

    Chỉ kiểm tra TF M5..H8 (bỏ qua D1/W vì overnight gap là bình thường).
    "Liên tiếp" = DATEDIFF(MINUTE) đúng bằng TF period.
    Flag khi |O[T1] - C[T0]| / C[T0] > 0.5%.

    Returns (is_clean, stats_dict). is_clean = True khi flag% < 1%.
    """
    symbols = list(SYMBOLS)
    if sym_filter:
        sym_upper = {s.upper() for s in sym_filter}
        symbols = [s for s in symbols if s["tv_symbol"].upper() in sym_upper]

    sym_ids = [s["symbol_id"] for s in symbols]
    if not sym_ids:
        return True, {"total": 0, "flagged": 0, "pct": 0.0, "top": []}

    sym_phs = ",".join("?" * len(sym_ids))
    params  = (lookback_days, *sym_ids)

    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        _CO_SQL.format(tf_case=_CO_TF_CASE, sym_phs=sym_phs),
        params,
    )
    row     = cursor.fetchone()
    total   = int(row[0]) if row and row[0] else 0
    flagged = int(row[1]) if row and row[1] else 0
    pct     = flagged / total * 100 if total > 0 else 0.0

    top: list[dict] = []
    if flagged > 0:
        cursor.execute(
            _CO_SQL_TOP.format(tf_case=_CO_TF_CASE, sym_phs=sym_phs),
            params,
        )
        for r in cursor.fetchall():
            top.append({
                "symbol":    r[0],
                "tf":        r[1],
                "time":      r[2],
                "close":     float(r[3]),
                "next_open": float(r[4]),
                "diff_pct":  float(r[5]),
            })

    conn.close()
    return pct < 1.0, {"total": total, "flagged": flagged, "pct": pct, "top": top}


def _format_co_report(stats: dict, lookback_days: int) -> str:
    """Trả về chuỗi báo cáo C-O continuity check (dùng cả cho log và Telegram)."""
    total   = stats["total"]
    flagged = stats["flagged"]
    pct     = stats["pct"]
    top     = stats["top"]

    lines = [
        f"=== C-O Continuity Check (lookback {lookback_days}d, TF M5..H8) ===",
        f"Transitions checked: {total:,}",
    ]
    if total == 0:
        lines.append("(no data)")
        return "\n".join(lines)

    if flagged == 0:
        lines.append(f"Result: CLEAN — all {total:,} within-session C=O (diff < 0.5%)")
    else:
        level = "WARNING" if pct < 1.0 else "ERROR"
        lines.append(f"Result: [{level}] {flagged:,} / {total:,} flagged ({pct:.3f}%)")
        if top:
            lines.append(f"Top {len(top)} worst:")
            for r in top:
                lines.append(
                    f"  {r['symbol']} {r['tf']} {r['time']}  "
                    f"C={r['close']:.5f} nextO={r['next_open']:.5f}  diff={r['diff_pct']:.3f}%"
                )
    return "\n".join(lines)


def _log_report(report: str, logger: logging.Logger) -> None:
    """Log report ra file (strip HTML tags cho dễ đọc)."""
    clean = (report
             .replace("<b>", "").replace("</b>", "")
             .replace("<i>", "").replace("</i>", ""))
    logger.info("\n%s", clean)


# =============================================================================
# INTERVAL GAP CHECK  (DB-only, no TradingView)
# =============================================================================

_NORMAL_GAP_MAX = {
    1440:  5760,   # D1: up to 4 ngay (weekend + DST)
    10080: 12240,  # W:  up to 8.5 ngay
}


def check_interval_gaps(
    sym_filter: list | None = None,
    tf_filter: str | None = None,
    full: bool = False,
) -> tuple[str, bool]:
    """
    Kiem tra interval gaps giua cac bar lien tiep trong Fact_OHLCV.

    Returns (report_str, has_issues).
    has_issues = True neu bat ky cap (sym, tf) nao co > 1% gap sai.
    """
    from collections import Counter

    conn   = get_connection()
    cursor = conn.cursor()

    if sym_filter:
        needle_set = {s.upper() for s in sym_filter}
        cursor.execute("SELECT SymbolID, Symbol FROM DWH.Dim_Symbol ORDER BY Symbol")
        symbols = [(r[0], r[1]) for r in cursor.fetchall() if r[1].upper() in needle_set]
    else:
        cursor.execute("SELECT SymbolID, Symbol FROM DWH.Dim_Symbol ORDER BY Symbol")
        symbols = cursor.fetchall()

    cursor.execute("SELECT TimeframeID, Code, Minutes FROM DWH.Dim_Timeframe ORDER BY Minutes")
    all_tfs = cursor.fetchall()
    if tf_filter:
        all_tfs = [(tid, code, mins) for tid, code, mins in all_tfs if code == tf_filter]

    tf_order = ['M5', 'M10', 'M15', 'M20', 'M30', 'M45', 'H1', 'M90',
                'H2', 'H3', 'H4', 'H6', 'H8', 'D1', 'W']

    results: dict[tuple, dict] = {}

    for sym_id, sym_code in symbols:
        for tf_id, tf_code, tf_min in all_tfs:
            cursor.execute("""
                SELECT TOP 500 BarTime
                FROM DWH.Fact_OHLCV
                WHERE SymbolID = ? AND TimeframeID = ?
                ORDER BY BarTime DESC
            """, (sym_id, tf_id))
            rows = sorted([r[0] for r in cursor.fetchall()])

            if len(rows) < 2:
                results[(sym_code, tf_code)] = {'status': 'empty', 'tf_min': tf_min}
                continue

            gaps = [int((rows[i] - rows[i-1]).total_seconds() / 60) for i in range(1, len(rows))]
            max_gap    = _NORMAL_GAP_MAX.get(tf_min, tf_min * 3)
            working    = [g for g in gaps if g <= max_gap]

            if not working:
                results[(sym_code, tf_code)] = {'status': 'ok', 'tf_min': tf_min,
                                                'total': 0, 'wrong': 0, 'pct': 0.0}
                continue

            wrong  = [g for g in working if g != tf_min]
            short  = [g for g in wrong if g < tf_min]
            long_  = [g for g in wrong if g > tf_min]
            pct    = len(wrong) / len(working) * 100
            top5   = Counter(wrong).most_common(5)

            results[(sym_code, tf_code)] = {
                'status':     'issues' if wrong and pct > 1.0 else 'ok',
                'tf_min':     tf_min,
                'total':      len(working),
                'wrong':      len(wrong),
                'short_gaps': len(short),
                'long_gaps':  len(long_),
                'pct':        pct,
                'top':        top5,
            }

    conn.close()

    # ── Format report ─────────────────────────────────────────────────────────
    sym_codes = [r[1] for r in symbols]
    lines     = ["=== TF Interval Gap Check ==="]
    has_issues = False

    for tf_code in tf_order:
        tf_res = [(sc, results[(sc, tf_code)]) for sc in sym_codes if (sc, tf_code) in results]
        if not tf_res:
            continue

        ok_n    = sum(1 for _, r in tf_res if r['status'] == 'ok')
        issue_n = sum(1 for _, r in tf_res if r['status'] == 'issues')
        empty_n = sum(1 for _, r in tf_res if r['status'] == 'empty')
        total_n = len(tf_res)

        lines.append(f"\nTF: {tf_code:<6} | OK: {ok_n} | Van de: {issue_n} | Trong: {empty_n} / Tong: {total_n}")

        if issue_n == 0:
            lines.append(f"  [SACH] Tat ca {ok_n} symbols OK")
            continue

        has_issues = True
        if full:
            lines.append(f"  {'Symbol':<12} {'Sai/Tong':>10} {'%Sai':>7} {'Short':>7} {'Long':>7}  Top gaps sai")
            for sc, r in sorted(tf_res, key=lambda x: -x[1].get('pct', 0)):
                if r['status'] != 'issues':
                    continue
                top_str = ', '.join([f"{g}min*{c}" for g, c in r['top']])
                flag = " ***SHORT***" if r['short_gaps'] > 0 else ""
                lines.append(
                    f"  {sc:<12} {r['wrong']:>5}/{r['total']:<5} {r['pct']:>6.1f}%"
                    f" {r['short_gaps']:>7}  {r['long_gaps']:>6}   {top_str}{flag}"
                )
        else:
            lines.append(f"  {'Symbol':<12} {'Sai/Tong':>10} {'%Sai':>7}  Top gaps sai")
            for sc, r in sorted(tf_res, key=lambda x: -x[1].get('pct', 0)):
                if r['status'] != 'issues':
                    continue
                top_str = ', '.join([f"{g}min*{c}" for g, c in r['top'][:3]])
                lines.append(
                    f"  {sc:<12} {r['wrong']:>5}/{r['total']:<5} {r['pct']:>6.1f}%   {top_str}"
                )

    total_ok     = sum(1 for r in results.values() if r['status'] == 'ok')
    total_issues = sum(1 for r in results.values() if r['status'] == 'issues')
    lines.append(f"\nTong ket: OK={total_ok} | Van de={total_issues} / {len(results)} cap")

    return "\n".join(lines), has_issues


# =============================================================================
# REBUILD COMPUTED TFS  (DB-only, no TradingView)
# =============================================================================

_COMPUTED_TFS = ['M10', 'M20', 'M90', 'H6', 'H8']


def rebuild_computed_tfs(
    dry_run: bool = True,
    sym_filter: list | None = None,
    tf_filter: str | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """
    Xoa va rebuild TF phai sinh (M10/M20/M90/H6/H8) tu Fact_OHLCV.

    dry_run=True: chi hien thi so bars se xoa, khong thuc hien.
    Returns report string.
    """
    from modules.db_connector import aggregate_from_fact

    target_tfs = _COMPUTED_TFS
    if tf_filter:
        if tf_filter not in _COMPUTED_TFS:
            return f"[Rebuild] TF '{tf_filter}' khong phai TF phai sinh. Ho tro: {_COMPUTED_TFS}"
        target_tfs = [tf_filter]

    conn   = get_connection()
    cursor = conn.cursor()

    if sym_filter:
        needle_set = {s.upper() for s in sym_filter}
        cursor.execute("SELECT SymbolID, Symbol FROM DWH.Dim_Symbol ORDER BY Symbol")
        symbols = [(r[0], r[1]) for r in cursor.fetchall() if r[1].upper() in needle_set]
    else:
        cursor.execute("SELECT SymbolID, Symbol FROM DWH.Dim_Symbol ORDER BY Symbol")
        symbols = cursor.fetchall()

    cursor.execute(
        f"SELECT Code, TimeframeID FROM DWH.Dim_Timeframe WHERE Code IN ({','.join('?' * len(target_tfs))})",
        target_tfs,
    )
    tf_id_map = {r[0]: r[1] for r in cursor.fetchall()}
    conn.close()

    mode  = "DRY-RUN" if dry_run else "EXECUTE"
    lines = [f"=== Rebuild Computed TFs [{mode}] ==="]
    total_del = 0
    total_ins = 0

    for tf_code in target_tfs:
        tf_id = tf_id_map.get(tf_code)
        if tf_id is None:
            continue
        lines.append(f"\n--- TF={tf_code} ---")
        tf_del = tf_ins = 0

        for sym_id, sym_name in symbols:
            # Count existing bars
            conn2   = get_connection()
            cur2    = conn2.cursor()
            cur2.execute(
                "SELECT COUNT(*) FROM DWH.Fact_OHLCV WHERE SymbolID = ? AND TimeframeID = ?",
                (sym_id, tf_id),
            )
            n_exist = cur2.fetchone()[0]
            conn2.close()

            if n_exist == 0:
                continue

            if dry_run:
                lines.append(f"  {sym_name:<12}  would delete {n_exist:>5} bars")
                tf_del += n_exist
            else:
                conn3 = get_connection()
                cur3  = conn3.cursor()
                try:
                    cur3.execute(
                        "DELETE FROM DWH.Fact_OHLCV WHERE SymbolID = ? AND TimeframeID = ?",
                        (sym_id, tf_id),
                    )
                    n_del = cur3.rowcount
                    conn3.commit()
                except Exception as exc:
                    conn3.rollback()
                    if logger:
                        logger.warning("rebuild_computed_tfs DELETE fail %s %s: %s", sym_name, tf_code, exc)
                    n_del = 0
                finally:
                    conn3.close()

                n_ins = aggregate_from_fact(sym_id, tf_code)
                lines.append(f"  {sym_name:<12}  deleted {n_del:>5}  re-aggregated {n_ins:>5}")
                tf_del += n_del
                tf_ins += n_ins

        if dry_run:
            lines.append(f"  -> Total would delete: {tf_del}")
        else:
            lines.append(f"  -> Total deleted: {tf_del}  inserted: {tf_ins}")

        total_del += tf_del
        total_ins += tf_ins

    lines.append(f"\n{'Preview' if dry_run else 'Done'}:")
    lines.append(f"  Total deleted : {total_del}")
    if not dry_run:
        lines.append(f"  Total inserted: {total_ins}")
    if dry_run:
        lines.append("Chay lai voi --rebuild-computed (khong co --dry-run) de thuc hien.")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
