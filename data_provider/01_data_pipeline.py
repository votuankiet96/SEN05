# =============================================================================
# data_provider/01_data_pipeline.py  —  Data Pipeline (Full Load + Daily Backfill)
# Version : 2.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Dùng script này để nạp dữ liệu lịch sử ban đầu và backfill hàng ngày.
#
# Các chế độ chạy:
# - full    : nạp toàn bộ lịch sử (chỉ dùng lần đầu cài đặt hệ thống)
# - gap     : bù phần còn thiếu so với hiện tại (chạy hàng ngày)
# - dry-run : chỉ kiểm tra kế hoạch, không ghi gì vào DB
#
# Thao tác vận hành an toàn:
# - Chọn chế độ chạy qua dòng lệnh (CLI)
# - Lên lịch tự động qua Windows Task Scheduler
#
# Hạn chế: không nên sửa các vòng lặp bên trong vì script này điều phối
# toàn bộ luồng dữ liệu từ symbol/timeframe cho đến tính lại derived TF.
#
#
# FILE NÀY LÀ GÌ?
#   Đây là "đường ống dữ liệu" (data pipeline) — nhiệm vụ duy nhất của nó là
#   đảm bảo cơ sở dữ liệu (database) luôn có đủ dữ liệu nến (OHLCV bars) để
#   các chiến lược giao dịch tự động có thể hoạt động.
#
# HAI CHẾ ĐỘ HOẠT ĐỘNG:
#
#   ┌─ FULL LOAD (Tải lần đầu)
#   │   Khi nào chạy: Database hoàn toàn trống (chưa có dữ liệu nào)
#   │   Làm gì:       Kéo toàn bộ lịch sử giá từ TradingView
#   │                 37 cặp tiền × 10 khung thời gian trực tiếp
#   │                 + tính thêm 5 khung thời gian phái sinh (M10, M20, M90, H6, H8)
#   │   Thời gian:    ~2–4 giờ (chỉ phải chạy đúng 1 lần duy nhất)
#   │
#   └─ BACKFILL (Bù dữ liệu thiếu — chạy hàng ngày)
#       Khi nào chạy: Database đã có dữ liệu từ trước
#       Làm gì:       Chỉ kéo phần bị thiếu kể từ lần chạy trước đến thời điểm hiện tại
#       Thời gian:    ~3–8 phút
#
# CÁCH CHẠY THỦ CÔNG (gõ vào terminal):
#   python 01_data_pipeline.py                # tự động phát hiện chế độ
#   python 01_data_pipeline.py --mode full    # ép chạy full load
#   python 01_data_pipeline.py --mode gap     # ép chạy backfill
#   python 01_data_pipeline.py --dry-run      # chỉ kiểm tra, không kéo data
#
# LỊCH CHẠY TỰ ĐỘNG:
#   Task Scheduler (Windows) tự động gọi file này mỗi ngày lúc 22:22 UTC
#
# MÃ KẾT QUẢ (Exit codes):
#   0 = thành công hoàn toàn
#   1 = lỗi nghiêm trọng (không kết nối được DB hoặc TradingView)
#   2 = thành công một phần (có một số cặp kéo bị thất bại)
# =============================================================================


# --- NHẬP CÁC THƯ VIỆN CẦN THIẾT ---

import argparse  # Thư viện đọc tham số dòng lệnh (--mode, --dry-run, v.v.)
import os  # Thư viện làm việc với hệ thống file/thư mục (đường dẫn, tồn tại không, v.v.)
import sys  # Thư viện tương tác với Python runtime (thoát chương trình, thêm đường dẫn, v.v.)
import time  # Thư viện đo thời gian (tính xem query mất bao lâu)
from datetime import datetime  # Dùng để ghi lại thời điểm bắt đầu và tính tổng thời gian chạy

# --- CẤU HÌNH ĐƯỜNG DẪN PROJECT ---
# Python cần biết vị trí các file config/module khác của project nằm ở đâu.
# Mặc định nó chỉ tìm trong thư mục hiện tại, nên ta phải tự thêm các đường dẫn vào.

# Lấy đường dẫn tuyệt đối đến thư mục gốc của project (2 cấp lên từ file này)
# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)


# --- NHẬP CÁC MODULE NỘI BỘ CỦA PROJECT ---

# Nhập các hằng số và hàm cấu hình từ file config.py
# Nhập các hàm tiện ích dùng chung từ file _helpers.py
from _helpers import (
    FULL_N_BARS,  # Hằng số: số bar cần kéo cho từng TF khi chạy full load
    calc_gap_n_bars,  # Hàm tính số bar cần kéo dựa trên khoảng thời gian bị thiếu
    fmt_gap,  # Hàm định dạng khoảng thời gian thiếu thành chuỗi dễ đọc (ví dụ: "2d 3h")
    now_utc,  # Hàm trả về thời điểm hiện tại theo múi giờ UTC (không có timezone info)
    pull_with_retry,  # Kéo dữ liệu từ TradingView, tự retry tối đa 3 lần khi fail
    recompute_derived,  # Hàm tính lại tất cả TF phái sinh cho các symbol vừa được cập nhật
    setup_logger,  # Tạo logger để ghi log ra file và console
    sleep_for,  # Hàm tạm dừng giữa các request để tránh bị TradingView rate-limit
    trading_hours_in_gap,  # Tính giờ trading thực (trừ Sat/Sun) trong một khoảng thời gian
)
from _tg import tg_alert, tg_flush, QUICK_COMMANDS_HINT  # Gửi thông báo kết quả pipeline lên Telegram
from _tv_auth import get_valid_tv_connection, refresh_mid_run  # Auth module dùng chung

from config import (
    COMPUTED_TIMEFRAMES,  # Danh sách cặp (TF đích, bảng nguồn) dùng để tính các TF phái sinh
    DERIVED_TFS,  # Danh sách 5 khung TF tính toán từ dữ liệu đã có (M10, M20, M90, H6, H8)
    DIRECT_TFS,  # Danh sách 10 khung TF kéo trực tiếp từ TradingView (M1, M5, M15, ...)
    LOG_FILE,  # Đường dẫn file log (để ghi lại mọi hoạt động của pipeline)
    SYMBOLS,  # Danh sách 37 cặp tiền cần theo dõi (tên, loại tài sản, ID, v.v.)
    TF_MINUTES,  # Bảng quy đổi: tên khung thời gian → số phút (ví dụ: "H1" → 60)
    WEEKEND_CLOSED,  # Danh sách loại tài sản đóng cửa cuối tuần (cổ phiếu, forex, v.v.)
    get_historical_timeframes,  # Hàm trả về danh sách TF cần kéo khi chạy full load (kèm số bar cần lấy)
    get_tf_interval_map,  # Hàm trả về bảng ánh xạ TF → interval dùng khi gọi API TradingView
)

# Nhập các hàm thao tác cơ sở dữ liệu từ file db_connector.py
from modules.db_connector import (
    get_connection,  # Mở một kết nối DB mới (trả về connection object)
    get_latest_bars,  # Truy vấn DB: lấy thời điểm nến mới nhất của từng cặp (symbol, TF)
    purge_staging,  # Xóa dữ liệu cũ trong bảng staging (giữ lại 7 ngày gần nhất)
    run_etl_aggregate,  # Tính toán và lưu dữ liệu TF phái sinh (ví dụ: gộp M1 → M10)
    test_connection,  # Kiểm tra xem có kết nối được tới SQL Server không
)

# Tạo logger cho toàn bộ pipeline này (ghi log vào file LOG_FILE với tên "pipeline")
logger = setup_logger("pipeline", LOG_FILE)


# =============================================================================
# PHẦN 1: TỰ ĐỘNG PHÁT HIỆN CHẾ ĐỘ CHẠY
# =============================================================================

def detect_mode() -> str:
    """
    Tự động xác định nên chạy FULL LOAD hay BACKFILL bằng cách
    đếm số hàng hiện có trong bảng Fact_OHLCV của database.

    - Nếu bảng trống (0 hàng) → chưa có dữ liệu gì → chạy FULL LOAD
    - Nếu bảng đã có dữ liệu   → chỉ cần bù thiếu   → chạy BACKFILL (gap)

    Trả về chuỗi 'full' hoặc 'gap'.
    """
    try:
        # Mở kết nối tới database
        conn   = get_connection()
        cursor = conn.cursor()

        # Đếm tổng số hàng trong bảng dữ liệu nến chính
        cursor.execute("SELECT COUNT(*) FROM DWH.Fact_OHLCV")

        # Lấy kết quả đếm (fetchone trả về 1 hàng, [0] lấy cột đầu tiên)
        total = cursor.fetchone()[0]

        # Đóng kết nối ngay sau khi dùng xong để giải phóng tài nguyên
        conn.close()

        # Quyết định chế độ: 0 bar → full load, có bar → backfill
        mode = "full" if total == 0 else "gap"

        # Ghi log để người dùng biết tại sao chọn chế độ này
        logger.info("[AUTO-DETECT] Fact_OHLCV has %d bars → mode = %s",
                    total, mode.upper())
        return mode

    except Exception as e:
        # Nếu không truy vấn được DB (ví dụ: DB chưa khởi động), mặc định dùng backfill
        # Lý do: an toàn hơn — backfill chỉ kéo thêm, không ghi đè toàn bộ
        logger.error("[AUTO-DETECT] Failed to count bars: %s — defaulting to gap", e)
        return "gap"


# =============================================================================
# PHẦN 2: CHẾ ĐỘ A — FULL LOAD (Tải toàn bộ lịch sử lần đầu)
# =============================================================================

def run_full_load(tv, dry_run: bool = False) -> int:
    """
    Kéo toàn bộ dữ liệu lịch sử từ TradingView về database.
    Hàm này chỉ nên chạy đúng 1 lần khi cài đặt hệ thống lần đầu.

    Tham số:
        tv       — đối tượng kết nối TradingView (đã được tạo ở main)
        dry_run  — nếu True: chỉ in ra kế hoạch, không thực sự kéo dữ liệu

    Trả về: tổng số cặp (symbol, TF) bị thất bại (0 = thành công hoàn toàn)
    """

    # Lấy danh sách đầy đủ các TF cần kéo (kèm số bar lịch sử cho mỗi TF)
    tf_configs = get_historical_timeframes()

    # Đếm có bao nhiêu TF cần kéo (dùng để hiển thị tiến độ: [1/11], [2/11], ...)
    total_tfs  = len(tf_configs)

    # Dictionary lưu kết quả: { "M1": (ok_count, fail_count), "M5": (...), ... }
    results    = {}

    # In tiêu đề bắt đầu full load ra log
    logger.info("=" * 65)
    logger.info("  FULL LOAD  —  %d TFs × %d symbols", total_tfs, len(SYMBOLS))
    logger.info("=" * 65)

    # --- CHẾ ĐỘ DRY RUN: chỉ in kế hoạch, không làm gì thực sự ---
    if dry_run:
        logger.info("[DRY RUN] Would pull:")
        # Liệt kê từng TF sẽ kéo: tên TF, số bar, số symbol, tên bảng staging
        for interval, tf_code, staging, n_bars in tf_configs:
            logger.info("  %s: %d bars × %d symbols → %s",
                        tf_code, n_bars, len(SYMBOLS), staging)
        # Nhắc nhở sẽ còn bước tính TF phái sinh sau khi kéo xong
        logger.info("  Then compute: M10, M20, M90, H6, H8")
        return 0

    # Tập hợp các symbol_id đã được cập nhật (dùng ở cuối để tính lại TF phái sinh)
    updated_sym_ids: set = set()

    # Đếm tổng số lỗi trong toàn bộ quá trình
    total_fail = 0

    # -----------------------------------------------------------------------
    # GIAI ĐOẠN 1: Kéo 10 TF trực tiếp từ TradingView
    # -----------------------------------------------------------------------
    for step_idx, (interval, tf_code, staging, n_bars) in enumerate(tf_configs, 1):
        # Đếm riêng số thành công/thất bại cho TF hiện tại
        ok = fail = 0

        # In dòng trống và tiêu đề cho TF đang xử lý
        logger.info("")
        logger.info("[%d/%d] DIRECT TF: %s  (%d bars × %d symbols)",
                    step_idx, total_tfs + 1, tf_code, n_bars, len(SYMBOLS))

        # Lặp qua tất cả symbol, kéo dữ liệu cho từng cái
        for i, sym in enumerate(SYMBOLS, 1):
            tv_symbol = sym["tv_symbol"]  # Tên symbol theo chuẩn TradingView (ví dụ: "BINANCE:BTCUSDT")

            # In tiến độ real-time ra console (không xuống dòng ngay để thêm ✓ hoặc ✗ sau)
            print(f"  [{i:02d}/{len(SYMBOLS)}] {tv_symbol:<12} {tf_code:<5}  ",
                  end="", flush=True)

            # Thực hiện kéo dữ liệu từ TradingView và lưu vào DB staging
            # Tự retry tối đa 3 lần (backoff 10s → 30s → 60s) nếu TV trả về lỗi hoặc rỗng
            result = pull_with_retry(tv, sym, tf_code, n_bars, interval, logger)

            if result >= 0:
                ok += 1
                # Ghi nhớ symbol này đã được cập nhật (sẽ cần tính lại TF phái sinh)
                updated_sym_ids.add(sym["symbol_id"])
                print("✓")
            else:
                fail += 1
                print("✗")

            # Dừng một chút giữa các request để tránh bị TradingView giới hạn tốc độ
            sleep_for(tv_symbol)

        # Lưu kết quả của TF này vào dictionary để in summary ở cuối
        results[tf_code] = (ok, fail)
        total_fail += fail
        logger.info("  %s: %d OK, %d failed", tf_code, ok, fail)

    # -----------------------------------------------------------------------
    # GIAI ĐOẠN 2: Tính 5 TF phái sinh từ dữ liệu vừa kéo về
    # (M10 = gộp 2 nến M5, M20 = gộp 2 nến M10, M90 = gộp 3 nến M30, v.v.)
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("[%d/%d] DERIVED TFs: M10, M20, M90, H6, H8",
                total_tfs + 1, total_tfs + 1)

    # Lặp qua từng TF phái sinh cần tính (cặp: TF đích, bảng nguồn)
    for target_tf, source_table in COMPUTED_TIMEFRAMES:
        ok = fail = 0

        # Tính TF phái sinh cho từng symbol
        for sym in SYMBOLS:
            try:
                # Gọi stored procedure/ETL để tổng hợp dữ liệu từ bảng nguồn sang TF đích
                run_etl_aggregate(sym["symbol_id"], target_tf, source_table)
                ok += 1
            except Exception as e:
                # Ghi lại lỗi nhưng không dừng chương trình — tiếp tục với symbol tiếp theo
                logger.error("  Aggregate FAIL %s %s: %s",
                             sym["tv_symbol"], target_tf, e)
                fail += 1

        results[target_tf] = (ok, fail)
        total_fail += fail
        logger.info("  %s: %d OK, %d failed", target_tf, ok, fail)

    # -----------------------------------------------------------------------
    # TÓM TẮT KẾT QUẢ FULL LOAD
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("FULL LOAD summary:")
    for tf, (ok, fail) in results.items():
        # Nếu không có lỗi: hiển thị ✓, ngược lại hiển thị số lỗi
        mark = "✓" if fail == 0 else f"✗ {fail} failed"
        logger.info("  %s: %d OK  %s", tf, ok, mark)

    # Trả về tổng số lỗi (caller dùng để quyết định exit code)
    return total_fail


# =============================================================================
# PHẦN 3: CHẾ ĐỘ B — BACKFILL (Bù dữ liệu thiếu hàng ngày)
# =============================================================================

def find_stale_pairs(latest: dict) -> list:
    """
    Duyệt qua tất cả cặp (symbol, TF) và tìm ra những cặp bị "cũ" (stale),
    tức là dữ liệu trong DB đã lỗi thời và cần được bù thêm.

    Một cặp bị coi là "stale" khi:
        khoảng thời gian từ nến cuối cùng đến NOW > 2 lần độ dài của TF đó
        (ví dụ: TF H1 thì gap > 2 giờ mới tính là stale)

    Tham số:
        latest — dict: { (symbol_id, tf_code): datetime_of_last_bar, ... }

    Trả về: danh sách dict, mỗi phần tử là thông tin 1 cặp cần bù,
            đã được sắp xếp theo độ ưu tiên (MISS trước, gap lớn trước).
    """

    # Lấy thời điểm hiện tại theo UTC (không có timezone info để tiện so sánh)
    now     = now_utc()

    # Danh sách kết quả: các cặp cần được cập nhật
    stale   = []

    # Duyệt qua từng symbol trong danh sách theo dõi
    for sym in SYMBOLS:
        sid        = sym["symbol_id"]
        asset_type = sym["asset_type"]  # Loại tài sản: "crypto", "forex", "stock", v.v.

        # Duyệt qua từng khung thời gian trực tiếp
        for tf_code in DIRECT_TFS:
            # Số phút của TF này (M1=1, M5=5, H1=60, H4=240, ...)
            tf_mins  = TF_MINUTES[tf_code]

            # Lấy thời điểm nến cuối cùng trong DB cho cặp (symbol, TF) này
            last_bar = latest.get((sid, tf_code))

            # --- TRƯỜNG HỢP 1: Không có dữ liệu nào trong DB ---
            if last_bar is None:
                stale.append({
                    "sym":      sym,
                    "tf_code":  tf_code,
                    "last_bar": None,
                    # Nếu không có dữ liệu, cần kéo toàn bộ lịch sử (full bars)
                    "gap_hours": FULL_N_BARS[tf_code] * tf_mins / 60,
                    "n_bars":   FULL_N_BARS[tf_code],
                    "reason":   "MISS",  # Lý do: hoàn toàn thiếu dữ liệu
                })
                continue  # Bỏ qua các kiểm tra bên dưới, sang cặp tiếp theo

            # Chuẩn hóa: nếu datetime có timezone info thì bỏ đi để tiện so sánh
            if hasattr(last_bar, "tzinfo") and last_bar.tzinfo:
                last_bar = last_bar.replace(tzinfo=None)

            # Tính khoảng thời gian giữa nến cuối cùng và bây giờ (đơn vị: phút và giờ)
            gap_min   = (now - last_bar).total_seconds() / 60
            gap_hours = gap_min / 60

            # --- TRƯỜNG HỢP 2: Dữ liệu còn mới, không cần cập nhật ---
            if asset_type in WEEKEND_CLOSED:
                # Tính giờ trading thực (bỏ Sat/Sun) để tránh false positive khi
                # pipeline chạy đầu tuần — gap Fri→Mon trông như 72h nhưng thực ra
                # chỉ có vài giờ trading (thị trường chưa mở lại đủ 2 TF periods).
                trading_h   = trading_hours_in_gap(last_bar, now)
                # W bar = 5 ngày trading × 24h = 120h (khác 7×24=168h lịch)
                threshold_h = (5 * 24.0 if tf_code == "W" else tf_mins / 60.0) * 2
                if trading_h <= threshold_h:
                    continue
            else:
                # Crypto và các asset giao dịch 24/7: dùng gap lịch thông thường
                if gap_min <= tf_mins * 2:
                    continue

            # --- TRƯỜNG HỢP 3: Dữ liệu cũ, cần bù thêm ---
            # Tính số bar cần kéo dựa trên khoảng thời gian bị thiếu
            n_bars = calc_gap_n_bars(gap_hours, tf_code, asset_type)
            stale.append({
                "sym":      sym,
                "tf_code":  tf_code,
                "last_bar": last_bar,
                "gap_hours": round(gap_hours, 1),
                "n_bars":   n_bars,
                "reason":   "STALE",  # Lý do: có dữ liệu nhưng đã lỗi thời
            })

    # Sắp xếp danh sách: MISS (thiếu hoàn toàn) lên đầu, sau đó theo gap lớn nhất
    # Lý do: ưu tiên sửa những cặp bị thiếu nhiều nhất trước
    stale.sort(key=lambda x: (x["reason"] != "MISS", -x["gap_hours"]))
    return stale


def run_backfill(tv, dry_run: bool = False) -> int:
    """
    Chạy backfill hàng ngày: kiểm tra xem cặp nào bị thiếu dữ liệu
    rồi kéo bổ sung từ TradingView, sau đó tính lại TF phái sinh.

    Tham số:
        tv       — đối tượng kết nối TradingView
        dry_run  — nếu True: chỉ in báo cáo, không kéo/lưu gì cả

    Trả về: số cặp bị thất bại (0 = thành công hoàn toàn)
    """
    logger.info("=" * 65)
    logger.info("  BACKFILL  —  querying latest bars...")
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 1: Truy vấn DB để biết nến cuối cùng của mỗi cặp là khi nào
    # -----------------------------------------------------------------------
    t0     = time.time()  # Ghi lại thời điểm bắt đầu query để đo hiệu năng
    latest = get_latest_bars()  # Trả về dict: { (symbol_id, tf_code): last_bar_datetime }
    logger.info("Query done in %.1fs — %d (symbol, TF) pairs in DB.",
                time.time() - t0, len(latest))

    # -----------------------------------------------------------------------
    # BƯỚC 2: So sánh với NOW để tìm ra cặp nào bị thiếu/lỗi thời
    # -----------------------------------------------------------------------
    stale = find_stale_pairs(latest)

    # Nếu tất cả đều mới → không cần làm gì, thoát sớm
    if not stale:
        logger.info("✓ All data is fresh — nothing to backfill.")
        return {"fail": 0, "ok": 0, "total": 0, "bars_inserted": 0,
                "miss_count": 0, "fail_pairs": []}

    # Tách riêng hai loại vấn đề để in log rõ ràng hơn
    miss_  = [x for x in stale if x["reason"] == "MISS"]   # Hoàn toàn không có dữ liệu
    stale_ = [x for x in stale if x["reason"] == "STALE"]  # Có nhưng đã lỗi thời

    # In danh sách các cặp bị MISS (cảnh báo vì đây là bất thường)
    if miss_:
        logger.warning("MISSING (%d pairs — no data at all):", len(miss_))
        for x in miss_:
            logger.warning("  %-12s %s", x["sym"]["tv_symbol"], x["tf_code"])

    # In danh sách các cặp bị STALE (bình thường, xảy ra hàng ngày)
    if stale_:
        logger.info("STALE (%d pairs):", len(stale_))
        for x in stale_:
            logger.info("  %-12s %-5s  gap=%-8s  pull=%d bars",
                        x["sym"]["tv_symbol"], x["tf_code"],
                        fmt_gap(x["gap_hours"]), x["n_bars"])

    logger.info("Total: %d pair(s) to update.", len(stale))

    # Nếu đang chạy dry-run: in xong thì dừng, không kéo dữ liệu
    if dry_run:
        logger.info("[DRY RUN] No changes made.")
        return {"fail": 0, "ok": 0, "total": len(stale), "bars_inserted": 0,
                "miss_count": len(miss_), "fail_pairs": []}

    # -----------------------------------------------------------------------
    # BƯỚC 3: Kéo dữ liệu bổ sung cho tất cả các cặp bị stale
    # -----------------------------------------------------------------------

    # Lấy bảng ánh xạ TF → interval (cần để gọi đúng API TradingView)
    TF_INTERVAL     = get_tf_interval_map()

    # Bộ đếm thành công/thất bại/bars mới
    ok = fail = bars_inserted = 0
    fail_pairs: list = []

    # Tập hợp symbol_id đã được cập nhật (cần để tính lại TF phái sinh sau)
    updated_sym_ids: set = set()

    # Biến ghi nhớ symbol trước để in dòng phân cách khi đổi symbol mới
    prev_sym        = None

    # Đếm failures liên tiếp để phát hiện auth expired giữa chừng
    consecutive_fail = 0

    # Lặp qua từng cặp cần cập nhật
    for i, item in enumerate(stale, 1):
        sym     = item["sym"]      # Thông tin symbol (tên, ID, loại tài sản)
        tf_code = item["tf_code"]  # Tên TF (M1, M5, H1, ...)
        n_bars  = item["n_bars"]   # Số bar cần kéo
        reason  = item["reason"]   # "MISS" hoặc "STALE"

        # Khi chuyển sang symbol mới, in dòng phân cách để log dễ đọc hơn
        if sym["tv_symbol"] != prev_sym:
            logger.info("─── %s ───", sym["tv_symbol"])
            prev_sym = sym["tv_symbol"]

        # In tiến độ real-time ra console
        print(f"  [{i:03d}/{len(stale)}] "
              f"{sym['tv_symbol']:<12} {tf_code:<5}  "
              f"gap={fmt_gap(item['gap_hours']):>7}  "
              f"pull={n_bars:>6,}  ({reason})",
              end="  ", flush=True)

        # Thực hiện kéo và lưu dữ liệu
        # Tự retry tối đa 3 lần (backoff 10s → 30s → 60s) nếu TV trả về lỗi hoặc rỗng
        result = pull_with_retry(tv, sym, tf_code, n_bars,
                                 TF_INTERVAL[tf_code], logger)

        if result >= 0:
            ok += 1
            consecutive_fail = 0
            bars_inserted += result  # result = số bar thực tế được thêm vào DB
            # Chỉ đánh dấu cần tính lại TF phái sinh nếu thực sự có bar mới (result > 0)
            if result > 0:
                updated_sym_ids.add(sym["symbol_id"])
            print("✓")
        else:
            fail += 1
            consecutive_fail += 1
            fail_pairs.append(f"{sym['tv_symbol']} {tf_code}")
            print("✗")

            # 3+ failures liên tiếp → nghi ngờ token hết hạn → thử refresh
            if consecutive_fail == 3:
                logger.warning("[AUTH] %d consecutive failures — trying mid-run token refresh...", consecutive_fail)
                if refresh_mid_run(tv, logger):
                    consecutive_fail = 0  # reset nếu refresh thành công

        # Nghỉ một chút trước khi gọi symbol tiếp theo (tránh rate limit)
        sleep_for(sym["tv_symbol"])

    logger.info("Pull done: %d OK, %d failed.", ok, fail)

    # -----------------------------------------------------------------------
    # BƯỚC 4: Tính lại TF phái sinh cho các symbol vừa có dữ liệu mới
    # -----------------------------------------------------------------------
    # Chỉ tính lại nếu thực sự có symbol nào được cập nhật
    # (tránh chạy ETL không cần thiết khi không có gì thay đổi)
    if updated_sym_ids:
        logger.info("Recomputing derived TFs...")
        recompute_derived(updated_sym_ids, logger)

    # Trả về dict thống kê thay vì chỉ fail count
    return {
        "fail":          fail,
        "ok":            ok,
        "total":         len(stale),
        "bars_inserted": bars_inserted,
        "miss_count":    len(miss_),
        "fail_pairs":    fail_pairs,
    }


# =============================================================================
# PHẦN 4: HÀM MAIN — Điểm vào của chương trình
# =============================================================================

def main() -> int:
    """
    Hàm được gọi đầu tiên khi chạy file này.
    Đọc tham số dòng lệnh → kiểm tra DB → kết nối TradingView → chạy pipeline.
    """

    # --- ĐỌC THAM SỐ DÒNG LỆNH ---
    # argparse tự động tạo --help và xử lý các lỗi tham số không hợp lệ
    parser = argparse.ArgumentParser(
        description="AutoTrading Data Pipeline — Full Load + Daily Backfill"
    )

    # Tham số --mode: chọn chế độ chạy
    # auto = tự phát hiện (mặc định), full = ép full load, gap = ép backfill
    parser.add_argument(
        "--mode", choices=["auto", "full", "gap"], default="auto",
        help="auto = detect automatically | full = force full | gap = force backfill",
    )

    # Tham số --dry-run: chạy thử, chỉ xem báo cáo, không thay đổi gì trong DB
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report only — do not pull from TradingView or write to DB",
    )

    # Phân tích các tham số từ dòng lệnh
    args    = parser.parse_args()

    # Ghi lại thời điểm bắt đầu để tính tổng thời gian chạy ở cuối
    started = datetime.now()

    # In banner mở đầu với thông tin tổng quan
    logger.info("=" * 65)
    logger.info("  AUTO TRADING — DATA PIPELINE  v2.0")
    logger.info("  Symbols : %d  |  Direct TFs : %d  |  Derived TFs : %d",
                len(SYMBOLS), len(DIRECT_TFS), len(DERIVED_TFS))
    logger.info("  Started : %s", started.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # BƯỚC 0: Kiểm tra kết nối Database
    # Làm điều này đầu tiên vì nếu DB không chạy, toàn bộ pipeline vô nghĩa
    # -----------------------------------------------------------------------
    logger.info("[Step 0] Checking SQL Server connection...")
    if not test_connection():
        # Không kết nối được DB → lỗi nghiêm trọng → thoát với exit code 1
        logger.error("ABORT: Cannot reach database.")
        tg_alert("ERROR", "🚨 <b>Data Pipeline THẤT BẠI</b>\nKhông kết nối được SQL Server.\nKiểm tra dịch vụ database ngay." + QUICK_COMMANDS_HINT)
        tg_flush()
        return 1

    # Thông báo đã bắt đầu (gửi sau khi xác nhận DB OK để tránh noise khi DB lỗi)
    dry_tag = " <i>[DRY RUN]</i>" if args.dry_run else ""
    tg_alert(
        "INFO",
        f"🚀 <b>Data Pipeline KHỞI ĐỘNG</b>{dry_tag}\n"
        f"📋 {len(SYMBOLS)} symbols × {len(DIRECT_TFS)} TF trực tiếp + {len(DERIVED_TFS)} TF phái sinh\n"
        f"⏰ Bắt đầu: {started.strftime('%H:%M:%S')} UTC",
    )

    # -----------------------------------------------------------------------
    # BƯỚC 1: Xác định chế độ chạy
    # -----------------------------------------------------------------------
    if args.mode == "auto":
        # Không chỉ định rõ → tự phát hiện dựa trên số bar trong DB
        mode = detect_mode()
    else:
        # Người dùng ép buộc chế độ cụ thể → dùng thẳng
        mode = args.mode
        logger.info("[MODE] Forced to: %s", mode.upper())

    # -----------------------------------------------------------------------
    # BƯỚC 2: Kết nối TradingView
    # Bỏ qua nếu đang chạy dry-run (không cần kết nối thật nếu không kéo data)
    # -----------------------------------------------------------------------
    tv = None  # Khởi tạo biến tv = None phòng trường hợp dry-run không gán
    if not args.dry_run:
        logger.info("[Step 2] Connecting to TradingView...")
        try:
            # get_valid_tv_connection: thử cache → .env token → cookie refresh → headless → guest
            tv, auth_mode = get_valid_tv_connection(logger)
            logger.info("  [OK] Connected (%s).", auth_mode)

            # Cảnh báo nếu đang dùng tài khoản guest:
            # Guest bị giới hạn số bar và dễ bị timeout hơn tài khoản đăng nhập
            if auth_mode == "guest":
                logger.warning("  Guest mode — lower bar limits, higher timeout risk.")
        except Exception as e:
            # Không kết nối được TradingView → không kéo được data → thoát
            logger.error("  TradingView connection failed: %s", e)
            tg_alert("ERROR", f"🚨 <b>Data Pipeline THẤT BẠI</b>\nKhông kết nối được TradingView.\nLỗi: {e}" + QUICK_COMMANDS_HINT)
            return 1

    # -----------------------------------------------------------------------
    # BƯỚC 3: Chạy pipeline theo chế độ đã xác định
    # -----------------------------------------------------------------------
    if mode == "full":
        # Full load: kéo toàn bộ lịch sử (mất vài giờ, chỉ chạy lần đầu)
        raw = run_full_load(tv, dry_run=args.dry_run)
        # run_full_load trả về int (số fail) — bọc thành dict để thống nhất
        fail = raw if isinstance(raw, int) else raw.get("fail", 0)
        run_stats = {"fail": fail, "ok": 0, "total": 0, "bars_inserted": 0,
                     "miss_count": 0, "fail_pairs": []}
    else:
        # Backfill: chỉ bù phần thiếu (mất vài phút, chạy hàng ngày)
        run_stats = run_backfill(tv, dry_run=args.dry_run)
        fail = run_stats["fail"]

    # -----------------------------------------------------------------------
    # BƯỚC 4: Dọn dẹp bảng staging
    # Xóa dữ liệu staging cũ hơn 7 ngày để tiết kiệm dung lượng DB
    # Bỏ qua nếu dry-run vì không có gì được ghi vào DB cả
    # -----------------------------------------------------------------------
    if not args.dry_run:
        logger.info("[Final] Purging old staging rows (keep 7 days)...")
        # purge_staging trả về dict: { tên_bảng: số_hàng_đã_xóa }
        purged = purge_staging(days_to_keep=7)
        # Cộng tổng số hàng đã xóa từ tất cả các bảng
        total_purged = sum(purged.values())
        logger.info("  Purged %d rows.", total_purged)

    # -----------------------------------------------------------------------
    # TÓM TẮT KẾT QUẢ TOÀN BỘ
    # -----------------------------------------------------------------------
    # Tính thời gian chạy tổng cộng (tính từ lúc bắt đầu đến bây giờ)
    elapsed = (datetime.now() - started).total_seconds()

    logger.info("=" * 65)
    logger.info("  DONE  |  mode=%s  |  %d failed  |  %.0fs elapsed",
                mode.upper(), fail, elapsed)
    logger.info("=" * 65)

    # -----------------------------------------------------------------------
    # TELEGRAM: thông báo kết quả pipeline
    # -----------------------------------------------------------------------
    elapsed_str  = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    total_pairs  = run_stats["total"]
    ok_pairs     = run_stats["ok"]
    bars_new     = run_stats["bars_inserted"]
    miss_count   = run_stats["miss_count"]
    fail_pairs   = run_stats["fail_pairs"]
    dry_tag      = " <i>[DRY RUN]</i>" if args.dry_run else ""

    if fail == 0:
        # Nội dung chi tiết khi thành công
        if total_pairs == 0:
            what_done = "Tất cả dữ liệu đã mới — không cần cập nhật."
        else:
            what_done = (f"📊 Pairs cập nhật: {ok_pairs:,}/{total_pairs:,}\n"
                         f"📈 Bars mới thêm vào DB: {bars_new:,}")
            if miss_count:
                what_done += f"\n🔩 Pairs thiếu hoàn toàn đã vá: {miss_count}"
        tg_alert(
            "INFO",
            f"✅ <b>Data Pipeline HOÀN TẤT</b> (chế độ: {mode.upper()}){dry_tag}\n"
            f"{what_done}\n"
            f"⏱ Thời gian chạy: {elapsed_str}\n"
            f"─────────────────\n"
            f"📌 <b>Tiếp theo:</b>\n"
            f"  • 02_ws_live.py đang giữ realtime (mỗi 5 phút)\n"
            f"  • 04_checker.py chạy mỗi 3 ngày để kiểm tra &amp; tự sửa dữ liệu",
        )
    else:
        # Liệt kê tối đa 5 cặp bị lỗi
        fail_list = "\n".join(f"  ❌ {p}" for p in fail_pairs[:5])
        if len(fail_pairs) > 5:
            fail_list += f"\n  ... và {len(fail_pairs) - 5} cặp khác"
        tg_alert(
            "WARNING",
            f"⚠️ <b>Data Pipeline HOÀN TẤT (có lỗi)</b> (chế độ: {mode.upper()})\n"
            f"📊 Pairs: {ok_pairs:,} OK / {fail:,} thất bại\n"
            f"📈 Bars mới thêm vào DB: {bars_new:,}\n"
            f"─────────────────\n"
            f"<b>Cặp bị lỗi:</b>\n{fail_list}\n"
            f"─────────────────\n"
            f"⏱ Thời gian chạy: {elapsed_str}"
            + QUICK_COMMANDS_HINT,
        )

    tg_flush()  # Đảm bảo tin Telegram được gửi trước khi process thoát

    # Quyết định exit code dựa trên số lỗi
    if fail > 0:
        # Có lỗi một phần: in cảnh báo và chỉ đường đến file log để xem chi tiết
        logger.warning("%d pair(s) failed. Check %s for details.", fail, LOG_FILE)
        return 2  # Exit code 2: thành công một phần

    return 0  # Exit code 0: thành công hoàn toàn


# =============================================================================
# ĐIỂM KHỞI ĐỘNG
# =============================================================================
# Dòng này đảm bảo hàm main() chỉ được gọi khi file này được chạy trực tiếp
# (không bị gọi khi file này được import bởi module khác).
# sys.exit() chuyển giá trị trả về của main() thành exit code của tiến trình,
# cho phép Task Scheduler hoặc script bên ngoài biết kết quả thành công hay thất bại.
if __name__ == "__main__":
    sys.exit(main())
