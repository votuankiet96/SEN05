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

from config import SYMBOLS, TF_STAGING
from _helpers import (
    setup_logger,
    recompute_derived,
    pull_and_store,
    now_utc,
)
from _tv_auth import get_valid_tv_connection, refresh_mid_run
from _tg import tg_send, tg_flush
from _task_lock import acquire, release, cleanup_expired, request_confirm
from modules.db_connector import get_connection, delete_ohlcv_bars, delete_staging_bars

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

    Ví dụ:
      TV = 1900.1234, DB = 1900.1235 → chênh 0.0001/1900 = 0.000005% → KHỚP
      TV = 1900.0000, DB = 1902.0000 → chênh 2/1900 = 0.1% → KHÔNG KHỚP

    Trả về True nếu tất cả 4 giá trị đều khớp, False nếu bất kỳ cái nào sai.
    """
    for a, b in zip(tv_vals, db_vals):
        if abs(a - b) > max(OHLCV_REL_TOL, abs(a) * OHLCV_REL_TOL):
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
            SELECT f.BarTime, f.Open, f.High, f.Low, f.Close
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
    Repair one (symbol, TF) pair:
      1. Delete bars with wrong OHLCV values from Fact (so re-pull can insert correct ones)
      2. Clear staging for this symbol (avoid stale merge collisions)
      3. Re-pull N bars from TV via pull_and_store()

    The pull_and_store MERGE will:
      - Insert bars missing from DB (they weren't there before)
      - Insert bars we just deleted (mismatched OHLCV, now fresh from TV)
      - Skip bars that were already correct (untouched)

    Returns True if the re-pull succeeded (result >= 0).
    """
    n_bars   = CHECKER_N_BARS[tf_code]
    interval = interval_map[tf_code]
    staging  = TF_STAGING.get(tf_code)

    # Step 1: Delete mismatched bars so MERGE can re-insert them with correct values
    if scan["mismatched"]:
        bar_times = list(scan["mismatched"].keys())
        deleted = delete_ohlcv_bars(sym["symbol_id"], tf_code, bar_times)
        logger.info("  Deleted %d mismatched bars — %s %s",
                    deleted, sym["tv_symbol"], tf_code)

    # Step 2: Clear staging for this symbol/TF to avoid stale rows
    if staging:
        delete_staging_bars(sym["symbol_id"], staging)

    # Step 3: Re-pull from TV (inserts missing + re-deleted mismatched bars)
    result = pull_and_store(tv, sym, tf_code, n_bars, interval, logger)
    if result < 0:
        logger.error("  Re-pull FAILED for %s %s (result=%d)",
                     sym["tv_symbol"], tf_code, result)
        return False
    return True


def _verify_pair(tv, sym: dict, tf_code: str,
                 interval_map: dict, logger: logging.Logger) -> tuple[bool, float]:
    """
    Quick post-repair verification using VERIFY_N_BARS (faster than full scan).
    Returns (is_clean, mismatch_rate).
    """
    scan = _scan_pair(tv, sym, tf_code, VERIFY_N_BARS, interval_map, logger)
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

        for idx, (sym, tf_code) in enumerate(pending, 1):
            label = f"{sym['tv_symbol']}/{tf_code}"

            if idx % 50 == 1:
                logger.info("[%d/%d] %s ...", idx, len(pending), label)

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

                # Auth: try mid-run refresh
                if auth_consecutive_fail >= 3 and not dry_run:
                    logger.warning("3+ auth-related failures — attempting mid-run refresh...")
                    if refresh_mid_run(tv, logger):
                        auth_consecutive_fail = 0

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
                # Vẫn đếm là "ok" cho mục đích thống kê (không có repair xảy ra)
                ok_pairs += 1

            time.sleep(TV_SLEEP_BETWEEN_CALLS)

        pending = still_failing

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
    args = parser.parse_args()

    # ── Setup logger ─────────────────────────────────────────────────────────
    log_dir = _DATA / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = setup_logger("checker", str(log_dir / "checker.log"))

    logger.info(
        "==== 06_checker.py START | dry=%s | sym=%s | tf=%s | threshold=%.1f%% ====",
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

    tfs = list(TFS_TO_CHECK)
    if args.tf:
        tf = args.tf.upper()
        if tf not in TFS_TO_CHECK:
            logger.error("TF '%s' not in TFS_TO_CHECK (%s).", tf, TFS_TO_CHECK)
            sys.exit(1)
        tfs = [tf]

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

    tg_flush()
    sys.exit(exit_code)


def _log_report(report: str, logger: logging.Logger) -> None:
    """Log report ra file (strip HTML tags cho dễ đọc)."""
    clean = (report
             .replace("<b>", "").replace("</b>", "")
             .replace("<i>", "").replace("</i>", ""))
    logger.info("\n%s", clean)


if __name__ == "__main__":
    main()
