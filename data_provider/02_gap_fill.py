# =============================================================================
# data_provider/02_gap_fill.py  —  Internal Hole (Gap) Fill
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Dùng script này để vá các lỗ hổng dữ liệu ở giữa timeline,
# không chỉ phần mới nhất bị thiếu.
#
# Khuyến nghị sử dụng:
# - Chạy định kỳ mỗi tuần hoặc khi nghi ngờ dữ liệu bị lỗi
# - Dùng --dry-run trước nếu vừa thay đổi các ngưỡng
# - Dùng --price-check tùy chọn để phát hiện nến có giá bất thường (spike)
#
# Các thông số ảnh hưởng lớn (lấy từ _helpers.py và config.py):
# - lookback_days (số ngày quét ngược)
# - overnight thresholds (ngưỡng gap qua đêm)
# - fill threshold / spike threshold
#
# Thay đổi các giá trị này ảnh hưởng đến mức độ "tích cực" trong việc sửa dữ liệu.

#
# MỤC ĐÍCH:
#   Script này kiểm tra dữ liệu nến (OHLCV) trong database xem có bị thiếu
#   (lỗ hổng / hole) ở giữa timeline hay không. Nếu phát hiện thiếu, nó sẽ
#   tự động kéo dữ liệu từ TradingView về để lấp đầy lỗ hổng.
#
# TẠI SAO CẦN TÁCH RIÊNG KHỎI 01_data_pipeline.py?
#   - 01_data_pipeline.py (backfill) chỉ kéo bar MỚI từ bar cuối → hiện tại
#     → chạy nhanh, dùng hàng ngày.
#   - Script này (gap_fill) quét TOÀN BỘ dữ liệu cũ tìm lỗ hổng ở giữa
#     → chạy chậm hơn, thường dùng hàng tuần hoặc khi cần kiểm tra.
#
# CÁCH CHẠY:
#   python 02_gap_fill.py                # chạy bình thường
#   python 02_gap_fill.py --dry-run      # chỉ báo cáo, không sửa
#   python 02_gap_fill.py --lookback 30  # quét 30 ngày gần nhất
#
# FLOW TỔNG QUAN:
#   1. Kết nối DB + TradingView
#   2. Đọc danh sách "market gap đã xác nhận" từ file JSON (để skip)
#   3. Quét Fact_OHLCV bằng SQL (LEAD) tìm khoảng cách bất thường giữa 2 bar
#   4. Lọc bỏ gap bình thường (qua đêm, cuối tuần) → chỉ giữ hole thực sự
#   5. Với mỗi hole: kéo dữ liệu từ TradingView → ghi vào Staging → Fact
#   6. Nếu pull về mà DB đã có đủ → xác nhận đó là market gap → lưu lại
#   7. Tính lại các TF phái sinh (M10, M20, M90, H6, H8) cho symbol đã cập nhật
# =============================================================================

import argparse  # Xử lý tham số dòng lệnh (--dry-run, --lookback)
import os  # Thao tác đường dẫn file/thư mục
import sys  # Thêm đường dẫn import, exit code
import time  # (được import nhưng không dùng trực tiếp ở file này)
from datetime import datetime  # Đo thời gian chạy script

# ---------------------------------------------------------------------------
# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
# ---------------------------------------------------------------------------
# _PROJ = thư mục gốc dự án (cha của data_provider/)
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# ---------------------------------------------------------------------------
# Import các module cần thiết
# ---------------------------------------------------------------------------
from _helpers import (
    FILL_THRESHOLD,  # Ngưỡng bar mới tối thiểu để coi là "fill thật" (5)
    HOLE_LOOKBACK_DAYS,  # Mặc định quét bao nhiêu ngày (60 ngày)
    OVERNIGHT_GAP_MINUTES,  # Ngưỡng gap qua đêm bình thường theo loại asset
    RESULT_ERROR,  # Return code: TV lỗi kỹ thuật (-1)
    RESULT_TV_EMPTY,  # Return code: TV trả về rỗng (-2)
    SPIKE_ATR_THRESHOLD,  # ATR multiplier mặc định cho price check (3.0)
    find_hole_pairs,  # Quét DB tìm lỗ hổng → trả danh sách (symbol, TF) cần fill
    load_verified_gaps,  # Đọc file JSON chứa market gap đã xác nhận
    now_utc,  # Lấy thời gian UTC hiện tại
    pull_and_store,  # Kéo OHLCV từ TV → ghi Staging → chuyển Fact
    recompute_derived,  # Tính lại TF phái sinh (M10, M20, M90, H6, H8)
    repull_full_symbol,  # Xóa + pull lại toàn bộ 1 cặp (symbol, TF)
    save_verified_gaps,  # Ghi market gap đã xác nhận vào file JSON
    setup_logger,  # Tạo logger ghi ra console + file
    sleep_for,  # Nghỉ giữa các request TV (rate-limit: 5s hoặc 10s cho GOLD)
)
from _tg import tg_alert, tg_flush  # Gửi thông báo kết quả gap fill lên Telegram
from _tv_auth import get_valid_tv_connection, refresh_mid_run  # Auth module dùng chung

from config import (
    DERIVED_TFS,  # Các timeframe phái sinh (M10, M20, M90, H6, H8)
    DIRECT_TFS,  # Các timeframe lấy trực tiếp từ TV (M5, M15, M30, H1, H2...)
    SYMBOLS,  # Danh sách tất cả symbol cần xử lý (HK50, GOLD, EURUSD...)
    TF_MINUTES,  # Map tf_code → số phút: "H1" → 60, "M15" → 15
    get_tf_interval_map,  # Map tf_code → interval object mà TV API cần
)
from modules.db_connector import find_price_spikes, test_connection

# File log riêng cho gap_fill (lưu vào data_provider/logs/)
LOG_FILE_GAP = os.path.join(_PROJ, "data_provider", "logs", "gap_fill.log")
logger = setup_logger("gap_fill", LOG_FILE_GAP)


# ---------------------------------------------------------------------------
# Hàm chính: chạy toàn bộ quy trình gap fill
# ---------------------------------------------------------------------------

def run_gap_fill(tv, lookback_days: int, dry_run: bool = False) -> int:
    """
    Quét dữ liệu trong DB tìm lỗ hổng, kéo dữ liệu từ TradingView lấp đầy.
    Trả về số cặp (symbol, TF) bị lỗi khi xử lý.
    """

    # --- Ghi log tiêu đề ---
    logger.info("=" * 65)
    logger.info("  GAP FILL  —  scanning for internal holes (last %d days)...",
                lookback_days)
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 1: Đọc danh sách "market gap đã xác nhận" từ file JSON
    # -----------------------------------------------------------------------
    # Một số lỗ hổng trong DB không phải do thiếu dữ liệu mà do thị trường
    # thực sự đóng cửa (ví dụ: HK50 H3 nghỉ cuối tuần). Lần chạy trước
    # đã pull TV và xác nhận không có bar nào → lưu vào verified_market_gaps.json.
    # Lần này đọc ra để SKIP, tránh pull TV lãng phí.
    verified_gaps = load_verified_gaps()
    if verified_gaps:
        logger.info("Loaded %d previously verified market-gap pairs.",
                    len(verified_gaps))

    # -----------------------------------------------------------------------
    # BƯỚC 2: Quét DB tìm lỗ hổng (holes) bên trong timeline
    # -----------------------------------------------------------------------
    # Hàm find_hole_pairs() sẽ:
    #   a) Chạy SQL dùng LEAD() trên Fact_OHLCV tìm khoảng cách bất thường
    #      giữa 2 bar liên tiếp (raw gaps)
    #   b) Lọc bỏ gap bình thường (qua đêm, cuối tuần, < 3× khoảng cách TF)
    #   c) Bỏ qua các cặp đã nằm trong verified_gaps
    #   d) Tính số bar cần pull cho mỗi hole (có hệ số an toàn ×1.5)
    #   e) Trả về danh sách [{sym, tf_code, last_bar, n_bars, ...}]
    stale = []  # Không có danh sách stale (chỉ dùng trong backfill pipeline)
    hole_items = find_hole_pairs(stale, logger, verified_gaps=verified_gaps,
                                 lookback_days=lookback_days)

    # Nếu không tìm thấy lỗ hổng nào → dữ liệu sạch, kết thúc sớm
    if not hole_items:
        logger.info("✓ No internal holes detected — data is clean.")
        return 0

    # Ghi log danh sách hole tìm được
    logger.info("HOLES (%d pairs — gaps mid-timeline):", len(hole_items))
    for x in hole_items:
        logger.info("  %-12s %-5s  since=%s  pull=%d bars",
                    x["sym"]["tv_symbol"], x["tf_code"],
                    x["last_bar"].strftime("%Y-%m-%d %H:%M"),
                    x["n_bars"])

    logger.info("Total: %d pair(s) to fill.", len(hole_items))

    # Nếu chạy ở chế độ dry-run → chỉ báo cáo, KHÔNG kéo dữ liệu, KHÔNG ghi DB
    if dry_run:
        logger.info("[DRY RUN] No changes made.")
        return 0

    # -----------------------------------------------------------------------
    # BƯỚC 3: Lần lượt kéo dữ liệu từ TradingView cho từng hole
    # -----------------------------------------------------------------------
    TF_INTERVAL      = get_tf_interval_map()  # Map: "H1" → Interval.in_1_hour, v.v.
    ok = fail        = 0                      # Đếm số thành công / thất bại
    updated_sym_ids: set = set()              # Tập symbol đã nhận bar mới (cần recompute)
    newly_verified:  set = set()              # Tập (sym_id, tf, gap_start, gap_end) xác nhận là market gap
    prev_sym         = None                   # Theo dõi symbol trước để in tiêu đề nhóm
    consecutive_fail = 0                      # Đếm failures liên tiếp để phát hiện auth expired

    for i, item in enumerate(hole_items, 1):
        sym     = item["sym"]       # Thông tin symbol (id, tên TV, sàn, loại asset...)
        tf_code = item["tf_code"]   # Mã timeframe: "H1", "M15", "M30"...
        n_bars  = item["n_bars"]    # Số bar cần kéo từ TV (đã nhân hệ số an toàn)

        # In tiêu đề nhóm khi chuyển sang symbol mới (dễ đọc log)
        if sym["tv_symbol"] != prev_sym:
            logger.info("─── %s ───", sym["tv_symbol"])
            prev_sym = sym["tv_symbol"]

        # In dòng trạng thái cho cặp đang xử lý (flush=True để hiện ngay)
        # Ví dụ: [001/131] HK50         H1     since=2026-02-17 03:00  pull= 1,135
        print(f"  [{i:03d}/{len(hole_items)}] "
              f"{sym['tv_symbol']:<12} {tf_code:<5}  "
              f"since={item['last_bar'].strftime('%Y-%m-%d %H:%M')}  "
              f"pull={n_bars:>6,}",
              end="  ", flush=True)

        # -------------------------------------------------------------------
        # Gọi pull_and_store() — Đây là bước chính:
        #   a) Kéo n_bars từ TradingView API
        #   b) Bỏ bar cuối (đang mở, chưa đóng xong)
        #   c) MERGE vào bảng Staging (bỏ qua duplicate)
        #   d) Gọi stored procedure usp_LoadDirect → chuyển Staging → Fact_OHLCV
        #   e) Trả về: ≥0 = số bar mới, -1 = lỗi kỹ thuật, -2 = TV trả rỗng
        # -------------------------------------------------------------------
        result = pull_and_store(tv, sym, tf_code, n_bars, TF_INTERVAL[tf_code], logger)

        # Retry once on TV empty — transient connection drops return empty df,
        # so one retry distinguishes "connection hiccup" from genuine market gap.
        if result == RESULT_TV_EMPTY:
            logger.info("  Retrying %s %s after TV empty (connection drop?)...",
                        sym["tv_symbol"], tf_code)
            time.sleep(10)
            result = pull_and_store(tv, sym, tf_code, n_bars, TF_INTERVAL[tf_code], logger)

        if result >= 0:
            # Pull thành công (không lỗi)
            ok += 1
            consecutive_fail = 0
            if result > 0:
                # CÓ bar mới được thêm vào Fact → ghi nhận symbol cần recompute TF phái sinh
                updated_sym_ids.add(sym["symbol_id"])

            if result < FILL_THRESHOLD:
                # Ít hơn FILL_THRESHOLD (5) bar mới → gap KHÔNG thực sự được fill.
                # Các bar mới (nếu có) chỉ là bar trading gần đây, KHÔNG phải bar
                # nằm trong khoảng hole lịch sử.
                # → Xác nhận đây là MARKET GAP (thị trường đóng cửa / nghỉ lễ)
                # → Lưu gap windows cụ thể để lần chạy sau chỉ skip đúng khoảng đó.
                for gs, ge in item.get("gap_windows", []):
                    newly_verified.add((sym["symbol_id"], tf_code, gs, ge))
                if result == 0:
                    print("○ market gap")
                else:
                    print(f"○ market gap (+{result} recent)")
            else:
                print("✓")

        elif result == RESULT_TV_EMPTY:
            # TV trả rỗng — không có data cho khoảng này.
            # Nếu gap đã cũ (> 7 ngày) → rất có thể là market gap thật
            # (TV cũng không có bar cho khoảng thị trường đóng cửa).
            # Nếu gap mới (≤ 7 ngày) → có thể TV lỗi tạm → đếm là fail, thử lại.
            gap_age_days = (now_utc() - item["last_bar"]).days
            if gap_age_days > 7:
                ok += 1
                for gs, ge in item.get("gap_windows", []):
                    newly_verified.add((sym["symbol_id"], tf_code, gs, ge))
                print("○ market gap (TV no data)")
            else:
                fail += 1
                print("✗ (TV empty)")

        else:
            # Lỗi kỹ thuật thật (exception, timeout...) → thử lại lần sau
            fail += 1
            consecutive_fail += 1
            print("✗")

            # 3+ failures liên tiếp → nghi ngờ token hết hạn → thử refresh
            if consecutive_fail == 3:
                logger.warning("[AUTH] %d consecutive failures — trying mid-run token refresh...", consecutive_fail)
                if refresh_mid_run(tv, logger):
                    consecutive_fail = 0  # reset nếu refresh thành công

        # Nghỉ giữa các request để tránh bị TradingView rate-limit
        # GOLD nghỉ 10s (API nặng hơn), các mã khác nghỉ 5s
        sleep_for(sym["tv_symbol"])

    logger.info("Pull done: %d OK, %d failed.", ok, fail)

    # -----------------------------------------------------------------------
    # BƯỚC 4: Lưu market gap mới xác nhận vào file JSON
    # -----------------------------------------------------------------------
    # Gộp gap windows mới + cũ → ghi đè verified_market_gaps.json
    # Lần chạy sau, find_hole_pairs() sẽ chỉ skip đúng gap window đã verified,
    # không skip cả pair (tránh bỏ sót hole thật mới phát sinh).
    if newly_verified:
        # Chuyển verified_gaps dict cũ về set windows để merge
        all_windows: set = set()
        for (sid, tfc), wlist in (verified_gaps or {}).items():
            for gs, ge in wlist:
                all_windows.add((sid, tfc, gs, ge))
        all_windows |= newly_verified
        save_verified_gaps(all_windows, logger)
        logger.info("%d gap windows confirmed as market gaps (will skip next run).",
                    len(newly_verified))

    # -----------------------------------------------------------------------
    # BƯỚC 5: Tính lại các timeframe phái sinh cho symbol đã cập nhật
    # -----------------------------------------------------------------------
    # Các TF phái sinh (M10, M20, M90, H6, H8) không kéo trực tiếp từ TV
    # mà được TÍNH từ dữ liệu TF gốc đã có trong Fact_OHLCV:
    #   M5  → gộp thành M10, M20
    #   M30 → gộp thành M90
    #   H3  → gộp thành H6
    #   H4  → gộp thành H8
    # Chỉ tính lại cho symbol nào VỪA nhận bar mới (tiết kiệm thời gian)
    if updated_sym_ids:
        logger.info("Recomputing derived TFs...")
        recompute_derived(updated_sym_ids, logger)

    return fail


# =============================================================================
# PRICE CHECK — Kiểm tra price continuity (phát hiện spike candles)
# =============================================================================

def run_price_check(tv, lookback_days: int, threshold_atr: float,
                    do_repull: bool, dry_run: bool) -> int:
    """
    Quét Fact_OHLCV tìm price discontinuities: các cặp bar liên tiếp
    (gap_minutes == TF_minutes ± 1) mà |close[i] - open[i+1]| > threshold × ATR14.

    Công thức: nếu dữ liệu liên tục, close của bar trước phải xấp xỉ open
    của bar tiếp theo. Sai lệch lớn → dấu hiệu bar bị sai giá hoặc thiếu bar.

    Trả về số cặp (symbol, TF) bị ảnh hưởng.
    """
    from collections import defaultdict

    logger.info("=" * 65)
    logger.info(
        "  PRICE CHECK  —  spike detection (threshold=%.1f×ATR14, last %d days)",
        threshold_atr, lookback_days,
    )
    logger.info("=" * 65)

    # ----- Bước 1: Query DB — tìm mọi jump > threshold×ATR -----
    all_tf_codes = sorted(DIRECT_TFS | DERIVED_TFS)
    spikes = find_price_spikes(all_tf_codes, lookback_days, threshold_atr)

    if not spikes:
        logger.info("✓ No price discontinuities detected — data is clean.")
        return 0

    # ----- Bước 2: Lọc chỉ giữ truly consecutive bars -----
    # Spike chỉ có ý nghĩa khi 2 bar thực sự liền kề (gap_minutes == TF_minutes ± 1).
    # Bỏ qua: overnight gaps, cross-session jumps (price movement là bình thường).
    sym_map        = {s["symbol_id"]: s for s in SYMBOLS}
    session_spikes = []
    n_cross        = 0

    for spike in spikes:
        sym = sym_map.get(spike["symbol_id"])
        if not sym:
            continue
        tf_mins  = TF_MINUTES.get(spike["tf_code"], 0)
        gap_mins = spike["gap_minutes"]
        # Chỉ giữ các cặp bar hoàn toàn liên tiếp: gap == TF_minutes ± 1 phút
        if abs(gap_mins - tf_mins) > 1:
            n_cross += 1
            continue
        session_spikes.append({**spike, "sym": sym})

    if n_cross:
        logger.info("  (excluded %d cross-session jumps — overnight/session gaps are normal)",
                    n_cross)

    if not session_spikes:
        logger.info("✓ No within-session discontinuities found "
                    "(%d total jumps were all across non-consecutive bars).", len(spikes))
        return 0

    # ----- Bước 3: Group by (symbol, TF) và in báo cáo -----
    by_pair: defaultdict = defaultdict(list)
    for spike in session_spikes:
        key = (spike["symbol_id"], spike["tf_code"])
        by_pair[key].append(spike)

    logger.info(
        "PRICE DISCONTINUITIES: %d spike(s) in %d (symbol, TF) pair(s):",
        len(session_spikes), len(by_pair),
    )
    logger.info("")

    for (sym_id, tf_code), items in sorted(by_pair.items(),
                                            key=lambda kv: -len(kv[1])):
        sym      = sym_map[sym_id]
        worst    = max(items, key=lambda x: x["jump_atr"])
        tag      = "  ⚠ DERIVED" if tf_code in DERIVED_TFS else ""
        logger.info("  %-12s %-5s  %d spike(s)  worst=%.1f×ATR  at %s%s",
                    sym["tv_symbol"], tf_code, len(items),
                    worst["jump_atr"],
                    worst["bar_time"].strftime("%Y-%m-%d %H:%M"),
                    tag)
        # Top 3 chi tiết
        for item in sorted(items, key=lambda x: -x["jump_atr"])[:3]:
            logger.info(
                "      %s  close=%.5g → open=%.5g  jump=%.5g (%.1f×ATR)",
                item["bar_time"].strftime("%Y-%m-%d %H:%M"),
                item["close"], item["next_open"],
                item["price_jump"], item["jump_atr"],
            )

    logger.info("")
    logger.info("Total: %d affected (symbol, TF) pair(s).", len(by_pair))

    if dry_run:
        logger.info("[DRY RUN] No repull performed.")
        return len(by_pair)

    if not do_repull:
        logger.info("Run with --price-check --repull to automatically re-fetch affected pairs.")
        return len(by_pair)

    # ----- Bước 4: Re-pull affected pairs -----
    # DIRECT TFs trước → DERIVED TFs re-aggregate từ source mới (thứ tự quan trọng!)
    sorted_pairs = sorted(
        by_pair.items(),
        key=lambda kv: (0 if kv[0][1] in DIRECT_TFS else 1, kv[0]),
    )

    TF_INTERVAL  = get_tf_interval_map()
    ok = fail    = 0
    updated_syms: set = set()
    direct_syms:  set = set()

    logger.info("Re-fetching %d pair(s) (direct TFs first, derived after)...",
                len(sorted_pairs))

    for i, ((sym_id, tf_code), _) in enumerate(sorted_pairs, 1):
        sym      = sym_map[sym_id]
        interval = TF_INTERVAL.get(tf_code)   # None cho derived TFs

        print(f"  [{i:03d}/{len(sorted_pairs)}] "
              f"{sym['tv_symbol']:<12} {tf_code:<5}",
              end="  ", flush=True)

        n_del, n_ins = repull_full_symbol(tv, sym, tf_code, interval, logger)

        if n_del >= 0:
            ok += 1
            updated_syms.add(sym_id)
            if tf_code in DIRECT_TFS:
                direct_syms.add(sym_id)
            print(f"✓  del={n_del}  ins={n_ins}")
        else:
            fail += 1
            print(f"✗  del={n_del}  ins={n_ins}")

        # Rate-limit: chỉ cần sleep cho direct TFs (TV requests), không cần cho derived
        if tf_code in DIRECT_TFS:
            sleep_for(sym["tv_symbol"])

    # Tính lại TF phái sinh cho các symbol vừa repull direct TF
    # (ngoài những cặp derived đã xử lý trong vòng lặp trên)
    if direct_syms:
        logger.info("Recomputing derived TFs for %d symbol(s) with repulled direct TFs...",
                    len(direct_syms))
        recompute_derived(direct_syms, logger)

    logger.info("Repull done: %d OK, %d failed.", ok, fail)
    return fail


# =============================================================================
# FORCE FULL — Xóa và pull lại toàn bộ lịch sử cho 1 timeframe
# =============================================================================

def run_force_full(tv, tf_code: str,
                   symbol_filter: list | None,
                   asset_type_filter: str | None,
                   dry_run: bool) -> int:
    """
    Xóa toàn bộ Fact_OHLCV cho 1 timeframe và pull lại từ TradingView.

    Bộ lọc (kết hợp được với nhau):
      symbol_filter      — danh sách tv_symbol, ví dụ ["US30", "US100"]
                           None = xử lý tất cả symbol
      asset_type_filter  — "Indice" | "FOREX" | "Metal" | "Crypto"
                           None = không lọc theo asset type

    Sau khi pull source TF xong:
      Nếu TF đó là nguồn của TF phái sinh → tự động xóa TF phái sinh cũ
      và re-aggregate từ dữ liệu mới:
        M5  → M10, M20
        M30 → M90
        H3  → H6
        H4  → H8

    Ví dụ sử dụng:
      --force-full M5                          # repull M5 tất cả, tính lại M10/M20
      --force-full M5 --symbol US30,US100      # chỉ US30 và US100
      --force-full M5 --asset-type Indice      # tất cả 9 Indices
      --force-full H3                          # repull H3, tính lại H6
      --force-full M10                         # re-aggregate M10 từ M5 (không cần TV)

    Trả về số cặp bị lỗi.
    """
    from modules.db_connector import _DERIVED_TF_SPECS

    tf_code = tf_code.upper()

    # ── Lọc target symbols ──────────────────────────────────────────────────
    targets = []
    for sym in SYMBOLS:
        if symbol_filter and sym["tv_symbol"].upper() not in symbol_filter:
            continue
        if asset_type_filter and sym["asset_type"].lower() != asset_type_filter.lower():
            continue
        targets.append(sym)

    if not targets:
        logger.warning("No symbols match the given filters.")
        return 0

    # ── Validate TF ──────────────────────────────────────────────────────────
    all_tfs = DIRECT_TFS | DERIVED_TFS
    if tf_code not in all_tfs:
        logger.error("Unknown TF '%s'. Valid direct=%s  derived=%s",
                     tf_code, sorted(DIRECT_TFS), sorted(DERIVED_TFS))
        return 1

    # Các TF phái sinh nào nguồn từ tf_code này?
    # Ví dụ: tf_code='M5' → dependent_tfs=['M10', 'M20']
    dependent_tfs = sorted(dtf for dtf, (src, _) in _DERIVED_TF_SPECS.items()
                           if src == tf_code)

    # ── Log header ───────────────────────────────────────────────────────────
    logger.info("=" * 65)
    logger.info("  FORCE FULL  |  TF=%-5s  |  %d symbol(s)%s",
                tf_code, len(targets),
                f"  |  will recompute {dependent_tfs}" if dependent_tfs else "")
    if symbol_filter:
        logger.info("  Symbol filter : %s", ", ".join(sorted(symbol_filter)))
    if asset_type_filter:
        logger.info("  Type filter   : %s", asset_type_filter)
    logger.info("=" * 65)

    if dry_run:
        for sym in targets:
            extra = f"  → recompute {dependent_tfs}" if dependent_tfs else ""
            logger.info("  would repull: %-12s %s%s",
                        sym["tv_symbol"], tf_code, extra)
        logger.info("[DRY RUN] No changes made.")
        return 0

    TF_INTERVAL      = get_tf_interval_map()
    interval         = TF_INTERVAL.get(tf_code)  # None cho derived TF
    ok = fail        = 0
    updated_sym_ids: set = set()

    # ── Step 1/2: Re-pull source TF ──────────────────────────────────────────
    logger.info("── Step 1/2: Re-pulling %s for %d symbol(s)...", tf_code, len(targets))
    for i, sym in enumerate(targets, 1):
        print(f"  [{i:03d}/{len(targets)}] {sym['tv_symbol']:<12} {tf_code:<5}",
              end="  ", flush=True)
        n_del, n_ins = repull_full_symbol(tv, sym, tf_code, interval, logger)
        if n_del >= 0:
            ok += 1
            updated_sym_ids.add(sym["symbol_id"])
            print(f"✓  del={n_del:,}  ins={n_ins:,}")
        else:
            fail += 1
            print("✗")
        if tf_code in DIRECT_TFS:
            sleep_for(sym["tv_symbol"])

    logger.info("Re-pull done: %d OK, %d failed.", ok, fail)

    # ── Step 2/2: Xóa và tính lại TF phái sinh ───────────────────────────────
    # Bắt buộc xóa trước khi re-aggregate: nếu source TF đã thay đổi,
    # dữ liệu derived cũ (tính từ source cũ) sẽ sai → phải xóa sạch rồi tính lại.
    if dependent_tfs and updated_sym_ids:
        logger.info("── Step 2/2: Recomputing %s for %d symbol(s)...",
                    dependent_tfs, len(updated_sym_ids))
        d_ok = d_fail = 0
        for sym in SYMBOLS:
            if sym["symbol_id"] not in updated_sym_ids:
                continue
            for dtf in dependent_tfs:
                print(f"  {sym['tv_symbol']:<12} {dtf:<5}", end="  ", flush=True)
                # repull_full_symbol với derived TF: xóa Fact cũ → re-aggregate từ source
                n_del, n_ins = repull_full_symbol(tv, sym, dtf, None, logger)
                if n_del >= 0:
                    d_ok += 1
                    print(f"✓  del={n_del:,}  ins={n_ins:,}")
                else:
                    d_fail += 1
                    print("✗")
        logger.info("Derived recompute: %d OK, %d failed.", d_ok, d_fail)
        fail += d_fail

    return fail


# =============================================================================
# Hàm main() — Điểm khởi chạy khi gọi: python 02_gap_fill.py
# =============================================================================

def main() -> int:
    """
    Điểm vào chính của script. Xử lý tham số dòng lệnh, kiểm tra kết nối,
    rồi gọi run_gap_fill() thực hiện toàn bộ quy trình.
    Trả về exit code: 0 = OK, 1 = lỗi kết nối, 2 = có pair bị fail.
    """

    # --- Đọc tham số từ dòng lệnh ---
    # --dry-run : chỉ quét và báo cáo, KHÔNG kéo TV, KHÔNG ghi DB
    # --lookback: quét bao nhiêu ngày gần nhất (mặc định 60 ngày)
    parser = argparse.ArgumentParser(
        description="AutoTrading Gap Fill — scan and repair internal data holes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only — do not pull from TradingView or write to DB",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=HOLE_LOOKBACK_DAYS,
        help=f"How many days to look back for holes (default: {HOLE_LOOKBACK_DAYS})",
    )
    parser.add_argument(
        "--price-check",
        action="store_true",
        help="Run price continuity check (detect spike candles via close→open jump)",
    )
    parser.add_argument(
        "--spike-threshold",
        type=float,
        default=SPIKE_ATR_THRESHOLD,
        help=f"ATR multiplier for spike detection (default: {SPIKE_ATR_THRESHOLD})",
    )
    parser.add_argument(
        "--repull",
        action="store_true",
        help="Re-fetch full history for affected (symbol, TF) pairs (requires --price-check)",
    )
    parser.add_argument(
        "--force-full",
        metavar="TF",
        help="Force full re-pull for a TF (e.g. M5, H1, M30) or ALL for every direct TF. "
             "Deletes all existing Fact rows and re-fetches from TradingView. "
             "Derived TFs are automatically deleted and re-aggregated. "
             "TF order for ALL: W → D1 → H4 → H3 → H2 → H1 → M45 → M30 → M15 → M5.",
    )
    parser.add_argument(
        "--symbol",
        metavar="SYM[,SYM...]",
        help="Comma-separated tv_symbol(s) to process with --force-full "
             "(e.g. US30,US100,DE40). Defaults to all symbols.",
    )
    parser.add_argument(
        "--asset-type",
        metavar="TYPE",
        help="Filter by asset type with --force-full: Indice | FOREX | Metal | Crypto.",
    )
    args    = parser.parse_args()
    started = datetime.now()  # Ghi nhận thời điểm bắt đầu để tính thời gian chạy

    # --- Ghi log thông tin cấu hình ---
    logger.info("=" * 65)
    logger.info("  AUTO TRADING — GAP FILL  v1.0")
    if args.force_full:
        _tf_label = "ALL TFs" if args.force_full.upper() == "ALL" else args.force_full.upper()
        _mode = f"FORCE FULL  (TF={_tf_label})"
    elif args.price_check:
        _mode = "PRICE CHECK (spike detection)"
    else:
        _mode = "GAP FILL    (internal holes)"
    logger.info("  Mode    : %s", _mode)
    logger.info("  Symbols : %d  |  Direct TFs : %d", len(SYMBOLS), len(DIRECT_TFS))
    logger.info("  Lookback: %d days", args.lookback)
    logger.info("  Started : %s", started.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 0: Kiểm tra kết nối SQL Server
    # -----------------------------------------------------------------------
    # Nếu DB chết thì không thể quét hole hay ghi dữ liệu → dừng ngay
    logger.info("[Step 0] Checking SQL Server connection...")
    if not test_connection():
        logger.error("ABORT: Cannot reach database.")
        tg_alert("ERROR", "🚨 <b>Gap Fill THẤT BẠI</b>\nKhông kết nối được SQL Server.\nKiểm tra dịch vụ database ngay.")
        tg_flush()
        return 1  # Exit code 1 = lỗi kết nối

    # Thông báo khởi động (sau khi DB OK)
    if not args.dry_run:
        if args.force_full:
            _mode_label = f"Force Full ({args.force_full.upper()})"
        elif args.price_check:
            _mode_label = "Price Check (spike detection)"
        else:
            _mode_label = f"Gap Fill ({args.lookback} ngày lookback)"
        tg_alert(
            "INFO",
            f"🔍 <b>Gap Fill KHỞI ĐỘNG</b>\n"
            f"📋 Chế độ: {_mode_label}\n"
            f"📋 {len(SYMBOLS)} symbols × {len(DIRECT_TFS)} TF trực tiếp\n"
            f"⏰ Bắt đầu: {started.strftime('%H:%M:%S')} UTC",
        )

    # -----------------------------------------------------------------------
    # BƯỚC 1: Kết nối TradingView (bỏ qua nếu dry-run)
    # -----------------------------------------------------------------------
    # TradingView là nguồn dữ liệu OHLCV. Có 2 chế độ:
    #   - auth: đăng nhập bằng tài khoản → được pull nhiều bar, ổn định
    #   - guest: không đăng nhập → bị giới hạn bar, dễ timeout
    # TV connection cần thiết cho: gap fill mặc định, hoặc price check có --repull.
    # Price check thuần (không repull) chỉ đọc DB → không cần TV.
    # TV connection cần khi: gap fill thường, price-check --repull,
    # hoặc force-full trên direct TF. Derived TF chỉ re-aggregate từ DB → không cần TV.
    if args.dry_run:
        needs_tv = False
    elif args.force_full:
        # ALL luôn cần TV (vì sẽ có ít nhất 1 direct TF trong danh sách)
        needs_tv = args.force_full.upper() == "ALL" or args.force_full.upper() in DIRECT_TFS
    elif args.price_check:
        needs_tv = args.repull
    else:
        needs_tv = True  # gap fill luôn cần TV
    tv = None
    if needs_tv:
        logger.info("[Step 1] Connecting to TradingView...")
        try:
            # get_valid_tv_connection: thử cache → .env token → cookie refresh → headless → guest
            tv, auth_mode = get_valid_tv_connection(logger)
            logger.info("  [OK] Connected (%s).", auth_mode)
            if auth_mode == "guest":
                logger.warning("  Guest mode — lower bar limits, higher timeout risk.")
        except Exception as e:
            logger.error("  TradingView connection failed: %s", e)
            return 1  # Exit code 1 = không kết nối được TV

    # -----------------------------------------------------------------------
    # BƯỚC 2: Kiểm tra price continuity hoặc gap fill
    # -----------------------------------------------------------------------
    if args.force_full:
        sym_flt = (
            [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
            if args.symbol else None
        )
        _tf_upper = args.force_full.upper()
        # ALL: loop qua tất cả direct TFs theo thứ tự lịch sử dài → ngắn
        if _tf_upper == "ALL":
            _all_order = ["W", "D1", "H4", "H3", "H2", "H1", "M45", "M30", "M15", "M5"]
            fail = 0
            for _tf in _all_order:
                fail += run_force_full(
                    tv,
                    tf_code=_tf,
                    symbol_filter=sym_flt,
                    asset_type_filter=args.asset_type,
                    dry_run=args.dry_run,
                )
        else:
            fail = run_force_full(
                tv,
                tf_code=_tf_upper,
                symbol_filter=sym_flt,
                asset_type_filter=args.asset_type,
                dry_run=args.dry_run,
            )
    elif args.price_check:
        fail = run_price_check(
            tv,
            lookback_days=args.lookback,
            threshold_atr=args.spike_threshold,
            do_repull=args.repull,
            dry_run=args.dry_run,
        )
    else:
        fail = run_gap_fill(tv, lookback_days=args.lookback, dry_run=args.dry_run)

    # -----------------------------------------------------------------------
    # KẾT THÚC: Tổng kết kết quả
    # -----------------------------------------------------------------------
    elapsed = (datetime.now() - started).total_seconds()
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    logger.info("=" * 65)
    logger.info("  DONE  |  %d failed  |  %.0fs elapsed", fail, elapsed)
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # TELEGRAM: thông báo kết quả gap fill
    # -----------------------------------------------------------------------
    if not args.dry_run:
        if args.force_full:
            mode_label = f"Force Full ({args.force_full.upper()})"
        elif args.price_check:
            mode_label = "Price Check"
        else:
            mode_label = f"Gap Fill ({args.lookback} ngày)"

        if fail == 0:
            tg_alert(
                "INFO",
                f"✅ <b>{mode_label} HOÀN TẤT</b>\n"
                f"Không có lỗi — dữ liệu đã sạch.\n"
                f"⏱ Thời gian chạy: {elapsed_str}\n"
                f"─────────────────\n"
                f"📌 <b>Tiếp theo:</b>\n"
                f"  • ws_live tiếp tục giữ realtime\n"
                f"  • reconcile kiểm tra lại hàng ngày",
            )
        else:
            tg_alert(
                "WARNING",
                f"⚠️ <b>{mode_label} HOÀN TẤT (có lỗi)</b>\n"
                f"❌ Thất bại: {fail} cặp symbol/TF\n"
                f"⏱ Thời gian chạy: {elapsed_str}\n"
                f"─────────────────\n"
                f"🔧 <b>Kế hoạch sửa:</b>\n"
                f"  • Xem log chi tiết: <code>{LOG_FILE_GAP}</code>\n"
                f"  • Chạy lại: <code>python 02_gap_fill.py</code>\n"
                f"  • Hoặc dùng lệnh /fix qua Telegram bot",
            )

    tg_flush()  # Đảm bảo tin Telegram được gửi trước khi process thoát

    if fail > 0:
        logger.warning("%d pair(s) failed. Check %s for details.",
                       fail, LOG_FILE_GAP)
        return 2  # Exit code 2 = có pair bị lỗi (nhưng script không crash)
    return 0      # Exit code 0 = hoàn tất thành công


# Khi chạy trực tiếp: python 01b_gap_fill.py
# sys.exit() truyền exit code cho hệ điều hành (0 = OK, khác 0 = có vấn đề)
if __name__ == "__main__":
    sys.exit(main())
