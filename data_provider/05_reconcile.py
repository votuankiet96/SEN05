# =============================================================================
# data_provider/05_reconcile.py  —  Expected vs Actual bar count reconcile
# Phiên bản : 1.0
# =============================================================================
# HƯỚNG DẪN:
#   Script đọc DB và tính xem mỗi cặp (symbol, TF) đáng lẽ phải có bao nhiêu
#   bar trong khoảng lookback, so với số bar thực tế đang có trong Fact_OHLCV.
#
# Script CHỈ ĐỌC — không ghi gì vào DB, không kéo dữ liệu.
# Dùng để kiểm tra chất lượng dữ liệu theo lịch định kỳ (cron) hoặc thủ công.
#
# Cách chạy:
#   python data_provider/05_reconcile.py                    # mặc định lookback 24h
#   python data_provider/05_reconcile.py --lookback 48      # lookback 48 giờ
#   python data_provider/05_reconcile.py --tf H1,M5         # chỉ kiểm tra 2 TF
#   python data_provider/05_reconcile.py --symbol BTCUSD    # chỉ 1 symbol
#
# Exit code:
#   0 = tất cả OK
#   1 = có WARNING hoặc ERROR (dùng để cron phát hiện)
# =============================================================================

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows console may default to cp1252 — force utf-8 for Unicode icons
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import SYMBOLS, TF_MINUTES, WEEKEND_CLOSED, SYMBOL_OVERNIGHT_MINS
from modules.db_connector import get_connection, test_connection
from _tg import tg_alert, tg_flush  # noqa: E402 — Gửi digest chất lượng dữ liệu lên Telegram

# Thứ tự hiển thị TF (từ nhỏ đến lớn)
_TF_ORDER = ["M5", "M10", "M15", "M20", "M30", "M45", "M90", "H1", "H2", "H3", "H4", "H6", "H8", "D1", "W"]


# =============================================================================
# TÍNH EXPECTED BAR COUNT
# =============================================================================

def _trading_minutes(sym: dict, tf_code: str, lookback_hours: float) -> float:
    """
    Tính số phút giao dịch thực sự trong khoảng lookback_hours.

    Bỏ:
      - Thứ 7 và Chủ nhật (với WEEKEND_CLOSED assets)
      - Thời gian qua đêm (overnight gap theo SYMBOL_OVERNIGHT_MINS)

    Trả về số phút trading (float).
    """
    asset_type = sym["asset_type"]
    tv_symbol  = sym["tv_symbol"]
    now        = datetime.now(timezone.utc)
    start      = now - timedelta(hours=lookback_hours)

    # Crypto: 24/7 → không cần bỏ gì
    if asset_type not in WEEKEND_CLOSED:
        return lookback_hours * 60

    total_min    = lookback_hours * 60
    weekend_mins = 0.0

    # Đếm số phút cuối tuần trong khoảng [start, now]
    cur = start
    while cur < now:
        # Sat = 5, Sun = 6
        if cur.weekday() in (5, 6):
            # Tính phút cuối tuần trong giờ hiện tại
            next_hour = min(cur + timedelta(hours=1), now)
            weekend_mins += (next_hour - cur).total_seconds() / 60
        cur += timedelta(hours=1)

    # Bỏ overnight gap: N phiên overnight trong lookback
    overnight_mins_per_day = SYMBOL_OVERNIGHT_MINS.get(
        tv_symbol, 0  # Nếu không có per-symbol config → 0 (assume 24h)
    )
    trading_days = (lookback_hours - weekend_mins / 60) / 24
    overnight_total = overnight_mins_per_day * trading_days

    return max(0.0, total_min - weekend_mins - overnight_total)


def _expected_bars(sym: dict, tf_code: str, lookback_hours: float) -> int:
    """Tính số bar kỳ vọng cho 1 cặp (symbol, TF) trong lookback_hours."""
    tf_min = TF_MINUTES.get(tf_code)
    if tf_min is None or tf_min == 0:
        return 0
    trading_min = _trading_minutes(sym, tf_code, lookback_hours)
    return max(0, int(trading_min / tf_min))


# =============================================================================
# TRUY VẤN ACTUAL BARS TỪ DB
# =============================================================================

def _get_actual_counts(lookback_hours: float, tf_codes: list, sym_ids: list) -> dict:
    """
    Truy vấn số bar thực tế trong Fact_OHLCV cho các cặp (sym_id, tf_code).

    Trả về: {(symbol_id, tf_code): (actual_count, latest_bar_time)}
    """
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    conn   = get_connection()
    cursor = conn.cursor()

    tf_placeholders  = ",".join("?" * len(tf_codes))
    sym_placeholders = ",".join("?" * len(sym_ids))

    cursor.execute(f"""
        SELECT f.SymbolID, tf.Code, COUNT(*) as cnt, MAX(f.BarTime) as latest
        FROM DWH.Fact_OHLCV f
        JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
        WHERE tf.Code IN ({tf_placeholders})
          AND f.SymbolID IN ({sym_placeholders})
          AND f.BarTime >= ?
        GROUP BY f.SymbolID, tf.Code
    """, (*tf_codes, *sym_ids, since))

    result = {}
    for sym_id, tf_code, cnt, latest in cursor.fetchall():
        result[(sym_id, tf_code)] = (cnt, latest)

    conn.close()
    return result


# =============================================================================
# MAIN REPORT
# =============================================================================

def run_reconcile(lookback_hours: float, tf_filter: list | None, sym_filter: list | None):
    """Chạy reconcile và in bảng kết quả. Trả về True nếu tất cả OK."""
    now = datetime.now(timezone.utc)

    # Lọc symbols theo --symbol arg
    symbols = SYMBOLS
    if sym_filter:
        sym_upper = {s.upper() for s in sym_filter}
        symbols = [s for s in symbols if s["tv_symbol"].upper() in sym_upper]
        if not symbols:
            print(f"[ERROR] No symbols matched filter: {sym_filter}")
            return False

    # Lọc TF theo --tf arg
    all_tfs = [tf for tf in _TF_ORDER if tf in TF_MINUTES]
    if tf_filter:
        tf_upper = {t.upper() for t in tf_filter}
        tf_codes = [t for t in all_tfs if t in tf_upper]
        if not tf_codes:
            print(f"[ERROR] No TF matched filter: {tf_filter}")
            return False
    else:
        tf_codes = all_tfs

    sym_ids = [s["symbol_id"] for s in symbols]

    print(f"\n  AUTO TRADING — Data Reconcile Report")
    print(f"  Lookback: {lookback_hours:.0f}h  |  From: {(now - timedelta(hours=lookback_hours)).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Symbols : {len(symbols)}  |  TFs: {', '.join(tf_codes)}")
    print()

    # Lấy actual counts từ DB
    actual = _get_actual_counts(lookback_hours, tf_codes, sym_ids)

    # Header
    hdr = f"  {'Symbol':<12} {'TF':<5} {'Expected':>8} {'Actual':>8} {'Missing':>8}  {'Latest bar':<19}  Status"
    sep = "  " + "-" * (len(hdr) - 2)
    print(hdr)
    print(sep)

    has_issues = False
    rows = []

    sym_map = {s["symbol_id"]: s for s in symbols}

    for sym in sorted(symbols, key=lambda s: s["tv_symbol"]):
        for tf_code in tf_codes:
            sym_id  = sym["symbol_id"]
            key     = (sym_id, tf_code)

            exp     = _expected_bars(sym, tf_code, lookback_hours)
            cnt, latest = actual.get(key, (0, None))
            missing = max(0, exp - cnt)

            if latest:
                latest_str = latest.strftime("%Y-%m-%d %H:%M")
                # Data freshness: age của bar mới nhất
                age_min = (now - latest.replace(tzinfo=timezone.utc)
                           if latest.tzinfo is None
                           else now - latest).total_seconds() / 60
                tf_min  = TF_MINUTES.get(tf_code, 60)
                stale   = age_min > tf_min * 3
            else:
                latest_str = "—"
                stale      = True

            # Status
            if exp == 0:
                status = "—"          # Market closed during entire lookback (e.g. weekend D1)
            elif missing == 0 and not stale:
                status = "OK"
            elif missing == 0 and stale:
                status = "STALE"
                has_issues = True
            elif missing <= max(2, int(exp * 0.02)):  # ≤2% missing → WARNING
                status = "WARNING"
                has_issues = True
            else:
                status = "ERROR"
                has_issues = True

            rows.append((sym["tv_symbol"], tf_code, exp, cnt, missing, latest_str, status))

    # In rows, nhóm theo symbol
    prev_sym = None
    for tv_sym, tf_code, exp, cnt, missing, latest_str, status in rows:
        if tv_sym != prev_sym:
            if prev_sym is not None:
                print()
            prev_sym = tv_sym

        status_icon = {"OK": "✓", "WARNING": "⚠", "ERROR": "✗", "STALE": "⏱", "—": " "}.get(status, "?")
        line = (f"  {tv_sym:<12} {tf_code:<5} {exp:>8,} {cnt:>8,} {missing:>8,}  "
                f"{latest_str:<19}  {status_icon} {status}")
        print(line)

    print(sep)

    # Summary
    ok_count      = sum(1 for r in rows if r[6] == "OK")
    warn_count    = sum(1 for r in rows if r[6] == "WARNING")
    error_count   = sum(1 for r in rows if r[6] == "ERROR")
    stale_count   = sum(1 for r in rows if r[6] == "STALE")
    skip_count    = sum(1 for r in rows if r[6] == "—")
    print(f"\n  Summary: {ok_count} OK  |  {warn_count} WARNING  |  {error_count} ERROR  |  {stale_count} STALE  |  {skip_count} skipped (market closed)")

    if not has_issues:
        print("  → All pairs are complete and fresh.\n")
    else:
        print("  → Run gap_fill to repair missing bars:\n"
              "    python data_provider/02_gap_fill.py --lookback 2\n")

    # Trả về (is_all_ok, summary_dict) để main() có thể gửi Telegram
    summary = {
        "ok": ok_count,
        "warning": warn_count,
        "error": error_count,
        "stale": stale_count,
        "skip": skip_count,
        "error_rows": [(r[0], r[1]) for r in rows if r[6] == "ERROR"],
        "stale_rows": [(r[0], r[1]) for r in rows if r[6] == "STALE"],
        "warn_rows":  [(r[0], r[1]) for r in rows if r[6] == "WARNING"],
    }
    return not has_issues, summary


# =============================================================================
# AUTO-FIX
# =============================================================================

def _run_gap_fill_fix(lookback_hours: float, sym_filter: list | None) -> None:
    """Spawn gap_fill as subprocess. Converts reconcile lookback_hours → gap_fill days."""
    import subprocess
    gap_fill = Path(__file__).resolve().parent / "02_gap_fill.py"
    days = max(1, int(lookback_hours / 24) + 1)
    cmd = [sys.executable, str(gap_fill), "--lookback", str(days)]
    if sym_filter:
        cmd += ["--symbol", ",".join(sym_filter)]
    print(f"\n  [AUTO-FIX] Running gap_fill --lookback {days}d ...\n")
    print("  " + "-" * 60)
    subprocess.run(cmd, check=False)
    print("  " + "-" * 60)


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="AutoTrading Reconcile — expected vs actual bar count"
    )
    parser.add_argument("--lookback", type=float, default=24.0,
                        help="Lookback period in hours (default: 24)")
    parser.add_argument("--tf", type=str, default=None,
                        help="Comma-separated TF filter (e.g. H1,M5)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Comma-separated symbol filter (e.g. BTCUSD,XAUUSD)")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-run gap_fill if issues found")
    parser.add_argument("--verify", action="store_true",
                        help="Re-run reconcile after fix to confirm results")
    args = parser.parse_args()

    tf_filter  = [t.strip() for t in args.tf.split(",")]     if args.tf     else None
    sym_filter = [s.strip() for s in args.symbol.split(",")] if args.symbol else None

    print("\n[Checking DB connection...]")
    if not test_connection():
        print("ABORT: Cannot connect to SQL Server.")
        sys.exit(1)

    ok, summary = run_reconcile(
        lookback_hours=args.lookback,
        tf_filter=tf_filter,
        sym_filter=sym_filter,
    )

    # ── Gửi digest Telegram ──────────────────────────────────────────────────
    _send_reconcile_tg(summary, args.lookback)

    if not ok and args.fix:
        _run_gap_fill_fix(args.lookback, sym_filter)
        if args.verify:
            print(f"\n  [VERIFY] Re-running reconcile after fix...\n")
            ok, summary = run_reconcile(
                lookback_hours=args.lookback,
                tf_filter=tf_filter,
                sym_filter=sym_filter,
            )

    tg_flush()
    sys.exit(0 if ok else 1)


def _send_reconcile_tg(summary: dict, lookback_hours: float) -> None:
    """Gửi digest chất lượng dữ liệu lên Telegram."""
    ok  = summary["ok"]
    wrn = summary["warning"]
    err = summary["error"]
    stl = summary["stale"]

    # Xác định level và icon tổng thể
    if err > 0:
        level, icon = "WARNING", "🔴"
    elif wrn > 0 or stl > 0:
        level, icon = "WARNING", "🟡"
    else:
        level, icon = "INFO", "🟢"

    lines = [
        f"{icon} <b>Báo cáo chất lượng dữ liệu</b> (lookback: {lookback_hours:.0f}h)",
        f"✅ OK: {ok}  ⚠️ WARNING: {wrn}  🚨 ERROR: {err}  💤 STALE: {stl}",
    ]

    # Liệt kê tối đa 5 cặp có lỗi/stale (tránh tin quá dài)
    problem_rows = summary["error_rows"][:5] + summary["stale_rows"][:3] + summary["warn_rows"][:2]
    if problem_rows:
        lines.append("─" * 24)
        for sym, tf in problem_rows[:7]:
            tag = "🚨" if (sym, tf) in summary["error_rows"] else ("💤" if (sym, tf) in summary["stale_rows"] else "⚠️")
            lines.append(f"  {tag} {sym} {tf}")
        if len(problem_rows) > 7:
            lines.append(f"  ... và {len(problem_rows) - 7} cặp khác")
        lines.append("→ Chạy: <code>python 02_gap_fill.py</code>")
    else:
        lines.append("→ Tất cả dữ liệu sạch và mới.")

    tg_alert(level, "\n".join(lines))


if __name__ == "__main__":
    main()
