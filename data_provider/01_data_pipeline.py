"""
Orchestrator cho luong nap du lieu lich su vao kho du lieu.

File nay phu trach:
- che do `auto`: tu quyet dinh full load hay gap fill dua tren tinh trang Fact_OHLCV
- che do `full`: tai lai lich su cho toan bo symbol / timeframe duoc chon
- che do `gap`: bo sung phan du lieu con thieu ke tu lan chay truoc
- bo loc theo `--symbols`, `--timeframes`, `--asset-type`
- che do an toan `--dry-run` va `--reset`

Nguyen tac van hanh:
- data luon vao staging truoc, sau do moi ETL vao Fact_OHLCV
- voi reset, script uu tien cach "nap du lieu thay the an toan" thay vi xoa rong truoc
- pipeline nhuong quyen cho ws_live/checker thong qua task lock va TV coordination lock
- guest mode van co the chay, nhung du lieu history co nguy co thieu nen can doc canh bao
"""

# =============================================================================
# data_provider/01_data_pipeline.py  —  Data Pipeline (Full Load + Daily Backfill)
# Version : 2.0
# =============================================================================
#
# FILE NÀY LÀ GÌ?
#   "Đường ống dữ liệu" — đảm bảo database luôn có đủ nến OHLCV để
#   các chiến lược giao dịch tự động hoạt động. Kéo dữ liệu từ
#   TradingView, lưu qua Staging → Fact_OHLCV, rồi tính lại TF phái sinh.
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC CHẾ ĐỘ CHẠY (--mode)
# ─────────────────────────────────────────────────────────────────────────────
#
#  auto  (mặc định) — tự phát hiện: Fact_OHLCV trống → full, có data → gap
#  full             — kéo toàn bộ lịch sử (chỉ dùng lần đầu, mất 2–4 giờ)
#                     37 symbol × 10 TF trực tiếp + 5 TF phái sinh
#  gap              — chỉ bù phần bị thiếu kể từ lần chạy trước (~3–8 phút)
#                     Đây là chế độ chạy hàng ngày thông thường
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC TÙY CHỌN BỘ LỌC (kết hợp được với nhau)
# ─────────────────────────────────────────────────────────────────────────────
#
#  --symbols US30,GOLD,EURUSD
#      Chỉ chạy cho các symbol chỉ định (phân cách bằng dấu phẩy)
#
#  --timeframes M45,H1,H4
#      Chỉ pull/reset các timeframe chỉ định (phân cách bằng dấu phẩy)
#
#  --asset-type Indice
#  --asset-type FOREX,Metal
#      Chỉ chạy cho symbols thuộc asset type chỉ định
#      Các giá trị hợp lệ: Indice, FOREX, Metal, Crypto
#
# ─────────────────────────────────────────────────────────────────────────────
# CÁC TÙY CHỌN ĐẶC BIỆT
# ─────────────────────────────────────────────────────────────────────────────
#
#  --dry-run
#      Chỉ hiển thị kế hoạch (cặp nào cần kéo, bao nhiêu bar) — KHÔNG ghi DB.
#      Dùng để kiểm tra trước khi chạy thật.
#
#  --reset
#      Xóa data cũ trong Fact_OHLCV TRƯỚC khi pull lại.
#      ⚠️ BẮT BUỘC kết hợp ít nhất 1 bộ lọc (--symbols / --timeframes / --asset-type)
#         để tránh xóa nhầm toàn bộ database.
#      Script sẽ hỏi xác nhận (y/N) trước khi xóa.
#
# ─────────────────────────────────────────────────────────────────────────────
# VÍ DỤ SỬ DỤNG
# ─────────────────────────────────────────────────────────────────────────────
#
#  python 01_data_pipeline.py
#      → Tự phát hiện chế độ và chạy toàn bộ 37 symbol
#
#  python 01_data_pipeline.py --mode gap
#      → Ép chạy backfill hàng ngày (không tự detect)
#
#  python 01_data_pipeline.py --mode full
#      → Ép chạy full load (dùng khi cần rebuild từ đầu)
#
#  python 01_data_pipeline.py --dry-run
#      → Xem kế hoạch, không ghi gì vào DB
#
#  python 01_data_pipeline.py --symbols GOLD,BTCUSD
#      → Chỉ backfill cho GOLD và BTCUSD
#
#  python 01_data_pipeline.py --timeframes M45
#      → Chỉ pull timeframe M45 cho tất cả symbol
#
#  python 01_data_pipeline.py --symbols EURUSD,GBPUSD --timeframes M45 --reset
#      → Xóa M45 của EURUSD + GBPUSD rồi pull lại từ đầu
#      (đây là lệnh dùng để sửa dữ liệu M45 bị hỏng cho một cặp cụ thể)
#
#  python 01_data_pipeline.py --asset-type Indice --timeframes M45 --reset
#      → Xóa và pull lại M45 cho toàn bộ Indices (US30, DE40, J225, v.v.)
#
# ─────────────────────────────────────────────────────────────────────────────
# LỊCH CHẠY TỰ ĐỘNG
# ─────────────────────────────────────────────────────────────────────────────
#   Task Scheduler (Windows) tự động gọi file này mỗi ngày lúc 22:22 UTC
#   (chạy ở chế độ auto, không có tham số đặc biệt)
#
# ─────────────────────────────────────────────────────────────────────────────
# MÃ KẾT QUẢ (Exit codes)
# ─────────────────────────────────────────────────────────────────────────────
#   0 = thành công hoàn toàn
#   1 = lỗi nghiêm trọng (không kết nối được DB hoặc TradingView)
#   2 = thành công một phần (có một số cặp kéo bị thất bại)
#
# ─────────────────────────────────────────────────────────────────────────────
# LƯU Ý VẬN HÀNH
# ─────────────────────────────────────────────────────────────────────────────
#   - Không sửa các vòng lặp bên trong — script điều phối toàn bộ luồng dữ liệu
#   - Chạy 01_data_pipeline.py TRƯỚC, sau đó mới chạy 02_ws_live.py realtime
#   - Sau --reset: pipeline tự fill lại data; staging cũ tự xóa sau 7 ngày
# =============================================================================


# --- NHẬP CÁC THƯ VIỆN CẦN THIẾT ---

import argparse  # Thư viện đọc tham số dòng lệnh (--mode, --dry-run, v.v.)
import atexit
import json
import logging
import os  # Thư viện làm việc với hệ thống file/thư mục (đường dẫn, tồn tại không, v.v.)
import sys  # Thư viện tương tác với Python runtime (thoát chương trình, thêm đường dẫn, v.v.)
import time  # Thư viện đo thời gian (tính xem query mất bao lâu)
import threading
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
    _acquire_short_write_lock,
    FULL_N_BARS,  # Hằng số: số bar cần kéo cho từng TF khi chạy full load
    calc_gap_n_bars,  # Hàm tính số bar cần kéo dựa trên khoảng thời gian bị thiếu
    fmt_gap,  # Hàm định dạng khoảng thời gian thiếu thành chuỗi dễ đọc (ví dụ: "2d 3h")
    now_utc,  # Hàm trả về thời điểm hiện tại theo múi giờ UTC (không có timezone info)
    pull_with_retry,  # Kéo dữ liệu từ TradingView, tự retry tối đa 3 lần khi fail
    repull_full_symbol,  # Safe reset: stage trước rồi mới thay thế dữ liệu cũ theo từng cặp
    recompute_derived,  # Hàm tính lại tất cả TF phái sinh cho các symbol vừa được cập nhật
    setup_logger,  # Tạo logger để ghi log ra file và console
    sleep_for,  # Hàm tạm dừng giữa các request để tránh bị TradingView rate-limit
    trading_hours_in_gap,  # Tính giờ trading thực (trừ Sat/Sun) trong một khoảng thời gian
)
from _discord import tg_alert, tg_flush, QUICK_COMMANDS_HINT  # Gửi thông báo kết quả pipeline lên Discord
from _tv_auth import get_valid_tv_connection, refresh_mid_run  # Auth module dùng chung
from _task_lock import acquire, cleanup_expired, release, renew, is_locked
from _tv_coord import (
    HistoricalJobHandoffRequested,
    acquire_historical_job,
    release_historical_job,
    wait_for_historical_slot,
)
import _logfmt

from config import (
    COMPUTED_TIMEFRAMES,  # Danh sách cặp (TF đích, bảng nguồn) dùng để tính các TF phái sinh
    DERIVED_TFS,  # Danh sách 5 khung TF tính toán từ dữ liệu đã có (M10, M20, M90, H6, H8)
    DIRECT_TFS,  # Danh sách 10 khung TF kéo trực tiếp từ TradingView (M1, M5, M15, ...)
    HISTORICAL_PROVIDER,
    LOG_FILE,  # Đường dẫn file log (để ghi lại mọi hoạt động của pipeline)
    SYMBOLS,  # Danh sách 37 cặp tiền cần theo dõi (tên, loại tài sản, ID, v.v.)
    TF_MINUTES,  # Bảng quy đổi: tên khung thời gian → số phút (ví dụ: "H1" → 60)
    TV_WS_HISTORY_ENDPOINT,
    TV_WS_HISTORY_FALLBACK_ENDPOINTS,
    TV_WS_HISTORY_REQUEST_MORE_BARS,
    TV_WS_HISTORY_REQUEST_MORE_ROUNDS,
    TV_WS_REPLAY_ENABLED,
    TV_WS_REPLAY_ENDPOINT,
    TV_WS_REPLAY_TFS,
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

# Bộ lọc TF — được set bởi --timeframes trong main(), đọc bởi run_full_load()
_TF_FILTER: set = set()
_FORCE_RESET_REPULL = False
RUN_SUMMARY_FILE = os.path.join(os.path.dirname(LOG_FILE), "pipeline_run_summary.jsonl")


def _fmt_int(value: int | float | None) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def _log_rule(label: str, detail: str = "") -> None:
    """Write a compact section divider that stays readable in PowerShell."""
    _logfmt.log(logger, "SECTION", action=label, amount=detail)


def _log_pair_start(
    index: int,
    total: int,
    symbol: str,
    tf_code: str,
    amount: str,
    range_: str = "-",
    status: str = "-",
) -> None:
    _logfmt.log(
        logger,
        "PAIR",
        progress=f"{index:03d}/{total:03d}",
        symbol=symbol,
        tf=tf_code,
        action="pull_start",
        amount=amount,
        range_=range_,
        status=status,
    )


def _log_pair_result(index: int, total: int, symbol: str, tf_code: str, result: int) -> None:
    if result > 0:
        amount = f"inserted {_fmt_int(result)}"
        status = "ok"
    elif result == 0:
        amount = "inserted 0"
        status = "ok/no_new_rows"
    else:
        amount = "-"
        status = "fail/tv_empty" if result == -2 else "fail/error"
    _logfmt.log(
        logger,
        "RESULT",
        progress=f"{index:03d}/{total:03d}",
        symbol=symbol,
        tf=tf_code,
        action="pair_done",
        amount=amount,
        status=status,
    )


def _log_tf_header(step_idx: int, total_tfs: int, tf_code: str, n_bars: int, pairs: int, staging: str) -> None:
    replay_hint = "on" if TV_WS_REPLAY_ENABLED and tf_code.upper() in TV_WS_REPLAY_TFS else "off"
    _logfmt.log(
        logger,
        "TF",
        progress=f"{step_idx:02d}/{total_tfs:02d}",
        tf=tf_code,
        action="full_load",
        amount=f"target {_fmt_int(n_bars)}",
        range_=f"pairs {pairs} replay {replay_hint}",
        status=staging,
    )


def _log_tf_summary(tf_code: str, ok: int, fail: int, inserted: int | None = None) -> None:
    _logfmt.log(
        logger,
        "TF_SUM",
        tf=tf_code,
        action="tf_done",
        amount=f"ok {ok} fail {fail}",
        status=f"inserted {_fmt_int(inserted)}",
    )


def _write_run_summary(mode: str, started: datetime, elapsed: float, stats: dict) -> None:
    payload = {
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "mode": mode.upper(),
        "elapsed_sec": round(elapsed, 3),
        "fail": int(stats.get("fail", 0) or 0),
        "ok": int(stats.get("ok", 0) or 0),
        "total": int(stats.get("total", 0) or 0),
        "bars_inserted": int(stats.get("bars_inserted", 0) or 0),
        "miss_count": int(stats.get("miss_count", 0) or 0),
        "fail_pairs": list(stats.get("fail_pairs", []) or []),
        "tf_summary": list(stats.get("tf_summary", []) or []),
        "dry_run": "--dry-run" in sys.argv[1:],
        "argv": sys.argv[1:],
    }
    try:
        os.makedirs(os.path.dirname(RUN_SUMMARY_FILE), exist_ok=True)
        with open(RUN_SUMMARY_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        _logfmt.log(
            logger,
            "SUMMARY",
            action="jsonl_failed",
            status=f"could not write summary file: {exc}",
            level=logging.WARNING,
        )


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
        _logfmt.log(logger, "AUTO", action="mode_detect", amount=f"db_rows {_fmt_int(total)}", status=mode.upper())
        return mode

    except Exception as e:
        # Nếu không truy vấn được DB (ví dụ: DB chưa khởi động), mặc định dùng backfill
        # Lý do: an toàn hơn — backfill chỉ kéo thêm, không ghi đè toàn bộ
        _logfmt.log(logger, "AUTO", action="db_read_failed", amount="fallback GAP", status=str(e), level=logging.ERROR)
        return "gap"


# =============================================================================
# PHẦN 2: CHẾ ĐỘ A — FULL LOAD (Tải toàn bộ lịch sử lần đầu)
# =============================================================================

def run_full_load(tv, dry_run: bool = False) -> dict | int:
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

    # Lọc theo --timeframes nếu có
    if _TF_FILTER:
        tf_configs = [tc for tc in tf_configs if tc[1] in _TF_FILTER]

    # Đếm có bao nhiêu TF cần kéo (dùng để hiển thị tiến độ: [1/11], [2/11], ...)
    total_tfs  = len(tf_configs)

    # Dictionary lưu kết quả: { "M1": (ok_count, fail_count, inserted_count), ... }
    results    = {}
    tf_summary = []

    # In tiêu đề bắt đầu full load ra log
    _log_rule(
        "FULL DATA LOAD",
        f"direct_tfs={total_tfs} pairs={len(SYMBOLS)} computed_tfs={len(COMPUTED_TIMEFRAMES)}",
    )
    logger.info(_logfmt.header())

    # --- CHẾ ĐỘ DRY RUN: chỉ in kế hoạch, không làm gì thực sự ---
    if dry_run:
        _logfmt.log(logger, "DRYRUN", action="plan_only", status="writes=no")
        planned = []
        # Liệt kê từng TF sẽ kéo: tên TF, số bar, số symbol, tên bảng staging
        for interval, tf_code, staging, n_bars in tf_configs:
            replay_hint = "yes" if TV_WS_REPLAY_ENABLED and tf_code.upper() in TV_WS_REPLAY_TFS else "no"
            _logfmt.log(
                logger,
                "PLAN",
                tf=tf_code,
                action="full_load",
                amount=f"target {_fmt_int(n_bars)}",
                range_=f"pairs {len(SYMBOLS)} replay {replay_hint}",
                status=staging,
            )
            planned.append({
                "tf": tf_code,
                "kind": "direct_plan",
                "target_bars": n_bars,
                "pairs": len(SYMBOLS),
                "replay": replay_hint,
                "staging": staging,
            })
        # Nhắc nhở sẽ còn bước tính TF phái sinh sau khi kéo xong
        if COMPUTED_TIMEFRAMES:
            _logfmt.log(
                logger,
                "PLAN",
                action="computed_tfs",
                amount="enabled",
                status=",".join(tf for tf, _ in COMPUTED_TIMEFRAMES),
            )
        else:
            _logfmt.log(logger, "PLAN", action="computed_tfs", amount="disabled", status="direct_tradingview")
        return {
            "fail": 0,
            "ok": 0,
            "total": len(SYMBOLS) * total_tfs,
            "bars_inserted": 0,
            "miss_count": 0,
            "fail_pairs": [],
            "tf_summary": planned,
        }

    # Tập hợp các symbol_id đã được cập nhật (dùng ở cuối để tính lại TF phái sinh)
    updated_sym_ids: set = set()

    # Đếm tổng số lỗi trong toàn bộ quá trình
    total_fail = 0
    total_ok = 0
    total_inserted = 0
    fail_pairs: list[str] = []

    # -----------------------------------------------------------------------
    # GIAI ĐOẠN 1: Kéo 10 TF trực tiếp từ TradingView
    # -----------------------------------------------------------------------
    for step_idx, (interval, tf_code, staging, n_bars) in enumerate(tf_configs, 1):
        # Đếm riêng số thành công/thất bại cho TF hiện tại
        ok = fail = tf_inserted = 0

        _log_tf_header(step_idx, total_tfs, tf_code, n_bars, len(SYMBOLS), staging)

        # Lặp qua tất cả symbol, kéo dữ liệu cho từng cái
        for i, sym in enumerate(SYMBOLS, 1):
            tv_symbol = sym["tv_symbol"]  # Tên symbol theo chuẩn TradingView (ví dụ: "BINANCE:BTCUSDT")

            # In tiến độ real-time ra console (không xuống dòng ngay để thêm ✓ hoặc ✗ sau)
            _log_pair_start(
                i,
                len(SYMBOLS),
                tv_symbol,
                tf_code,
                f"target {_fmt_int(n_bars)}",
                staging,
            )

            # Thực hiện kéo dữ liệu từ TradingView và lưu vào DB staging
            # Tự retry tối đa 3 lần (backoff 10s → 30s → 60s) nếu TV trả về lỗi hoặc rỗng
            if _FORCE_RESET_REPULL:
                _, inserted = repull_full_symbol(tv, sym, tf_code, interval, logger)
                result = inserted
            else:
                result = pull_with_retry(tv, sym, tf_code, n_bars, interval, logger)

            if result >= 0:
                ok += 1
                tf_inserted += result
                # Ghi nhớ symbol này đã được cập nhật (sẽ cần tính lại TF phái sinh)
                updated_sym_ids.add(sym["symbol_id"])
            else:
                fail += 1
                fail_pairs.append(f"{tv_symbol} {tf_code}")
            _log_pair_result(i, len(SYMBOLS), tv_symbol, tf_code, result)

            # Dừng một chút giữa các request để tránh bị TradingView giới hạn tốc độ
            sleep_for(tv_symbol)

        # Lưu kết quả của TF này vào dictionary để in summary ở cuối
        results[tf_code] = (ok, fail, tf_inserted)
        tf_summary.append({
            "tf": tf_code,
            "kind": "direct",
            "ok": ok,
            "fail": fail,
            "inserted": tf_inserted,
        })
        total_fail += fail
        total_ok += ok
        total_inserted += tf_inserted
        _log_tf_summary(tf_code, ok, fail, tf_inserted)

    # -----------------------------------------------------------------------
    # GIAI ĐOẠN 2: Tính 5 TF phái sinh từ dữ liệu vừa kéo về
    # (M10 = gộp 2 nến M5, M20 = gộp 2 nến M10, M90 = gộp 3 nến M30, v.v.)
    # -----------------------------------------------------------------------
    if COMPUTED_TIMEFRAMES:
        _log_rule(
            "COMPUTED TFS",
            "targets=" + ",".join(tf for tf, _ in COMPUTED_TIMEFRAMES),
        )
    else:
        _logfmt.log(logger, "TF_SUM", action="computed_tfs", amount="disabled", status="direct_tradingview")

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
                _logfmt.log(
                    logger,
                    "ERROR",
                    symbol=sym["tv_symbol"],
                    tf=target_tf,
                    action="computed_failed",
                    status=str(e),
                    level=logging.ERROR,
                )
                fail += 1

        results[target_tf] = (ok, fail, None)
        tf_summary.append({
            "tf": target_tf,
            "kind": "computed",
            "ok": ok,
            "fail": fail,
            "inserted": None,
        })
        total_fail += fail
        total_ok += ok
        _log_tf_summary(target_tf, ok, fail, None)

    # -----------------------------------------------------------------------
    # TÓM TẮT KẾT QUẢ FULL LOAD
    # -----------------------------------------------------------------------
    _log_rule("FULL LOAD SUMMARY")
    for tf, (ok, fail, inserted) in results.items():
        # Nếu không có lỗi: hiển thị ✓, ngược lại hiển thị số lỗi
        mark = "[OK]" if fail == 0 else f"[FAIL] {fail} failed"
        _logfmt.log(
            logger,
            "SUMMARY",
            tf=tf,
            action="tf_summary",
            amount=f"ok {ok} fail {fail}",
            status=f"{mark} inserted {_fmt_int(inserted)}",
        )

    total_pairs = sum((row["ok"] or 0) + (row["fail"] or 0) for row in tf_summary)
    return {
        "fail": total_fail,
        "ok": total_ok,
        "total": total_pairs,
        "bars_inserted": total_inserted,
        "miss_count": 0,
        "fail_pairs": fail_pairs,
        "tf_summary": tf_summary,
    }


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


def run_backfill(tv, dry_run: bool = False,
                 write_lock_name: str | None = None) -> dict:
    """
    Chạy backfill hàng ngày: kiểm tra xem cặp nào bị thiếu dữ liệu
    rồi kéo bổ sung từ TradingView, sau đó tính lại TF phái sinh.

    Tham số:
        tv       — đối tượng kết nối TradingView
        dry_run  — nếu True: chỉ in báo cáo, không kéo/lưu gì cả

    Trả về: số cặp bị thất bại (0 = thành công hoàn toàn)
    """
    _log_rule("GAP FILL", "checking latest bars and stale pairs")

    # -----------------------------------------------------------------------
    # BƯỚC 1: Truy vấn DB để biết nến cuối cùng của mỗi cặp là khi nào
    # -----------------------------------------------------------------------
    t0     = time.time()  # Ghi lại thời điểm bắt đầu query để đo hiệu năng
    latest = get_latest_bars()  # Trả về dict: { (symbol_id, tf_code): last_bar_datetime }
    _logfmt.log(logger, "DB", action="latest_scan", amount=f"pairs {_fmt_int(len(latest))}", status=f"{time.time() - t0:.1f}s")

    # -----------------------------------------------------------------------
    # BƯỚC 2: So sánh với NOW để tìm ra cặp nào bị thiếu/lỗi thời
    # -----------------------------------------------------------------------
    stale = find_stale_pairs(latest)

    # Nếu tất cả đều mới → không cần làm gì, thoát sớm
    if not stale:
        _logfmt.log(logger, "GAP", action="fresh_check", amount="stale 0", status="fresh")
        return {"fail": 0, "ok": 0, "total": 0, "bars_inserted": 0,
                "miss_count": 0, "fail_pairs": [], "tf_summary": []}

    # Tách riêng hai loại vấn đề để in log rõ ràng hơn
    miss_  = [x for x in stale if x["reason"] == "MISS"]   # Hoàn toàn không có dữ liệu
    stale_ = [x for x in stale if x["reason"] == "STALE"]  # Có nhưng đã lỗi thời

    # In danh sách các cặp bị MISS (cảnh báo vì đây là bất thường)
    if miss_:
        _logfmt.log(logger, "GAP", action="missing_all", amount=f"pairs {_fmt_int(len(miss_))}", level=logging.WARNING)
        for x in miss_:
            _logfmt.log(
                logger,
                "MISS",
                symbol=x["sym"]["tv_symbol"],
                tf=x["tf_code"],
                action="needs_full_pull",
                status="MISS",
                level=logging.WARNING,
            )

    # In danh sách các cặp bị STALE (bình thường, xảy ra hàng ngày)
    if stale_:
        _logfmt.log(logger, "GAP", action="stale", amount=f"pairs {_fmt_int(len(stale_))}")
        for x in stale_:
            _logfmt.log(
                logger,
                "STALE",
                symbol=x["sym"]["tv_symbol"],
                tf=x["tf_code"],
                action="needs_backfill",
                amount=f"pull {_fmt_int(x['n_bars'])}",
                range_=f"gap {fmt_gap(x['gap_hours'])}",
            )

    _logfmt.log(logger, "GAP", action="update_plan", amount=f"pairs {_fmt_int(len(stale))}")

    # Nếu đang chạy dry-run: in xong thì dừng, không kéo dữ liệu
    if dry_run:
        _logfmt.log(logger, "DRYRUN", action="plan_only", status="writes=no")
        return {"fail": 0, "ok": 0, "total": len(stale), "bars_inserted": 0,
                "miss_count": len(miss_), "fail_pairs": [], "tf_summary": []}

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
            _logfmt.log(logger, "SYMBOL", symbol=sym["tv_symbol"], action="gap_batch")
            prev_sym = sym["tv_symbol"]

        # In tiến độ real-time ra console
        _log_pair_start(
            i,
            len(stale),
            sym["tv_symbol"],
            tf_code,
            f"pull {_fmt_int(n_bars)}",
            f"gap {fmt_gap(item['gap_hours'])}",
            reason,
        )

        # Thực hiện kéo và lưu dữ liệu
        # Tự retry tối đa 3 lần (backoff 10s → 30s → 60s) nếu TV trả về lỗi hoặc rỗng
        lock_acquired = False
        if write_lock_name:
            wait_for_historical_slot("pipeline-maint", logger)
            lock_acquired = _acquire_short_write_lock(
                write_lock_name,
                logger,
                action=f"backfill {sym['tv_symbol']} {tf_code}",
            )
            if not lock_acquired:
                fail += 1
                consecutive_fail += 1
                fail_pairs.append(f"{sym['tv_symbol']} {tf_code}")
                _log_pair_result(i, len(stale), sym["tv_symbol"], tf_code, -1)
                sleep_for(sym["tv_symbol"])
                continue

        try:
            if _FORCE_RESET_REPULL:
                _, inserted = repull_full_symbol(tv, sym, tf_code, TF_INTERVAL[tf_code], logger)
                result = inserted
            else:
                result = pull_with_retry(tv, sym, tf_code, n_bars,
                                         TF_INTERVAL[tf_code], logger)
        finally:
            if lock_acquired:
                release(write_lock_name)

        if result >= 0:
            ok += 1
            consecutive_fail = 0
            bars_inserted += result  # result = số bar thực tế được thêm vào DB
            # Chỉ đánh dấu cần tính lại TF phái sinh nếu thực sự có bar mới (result > 0)
            if result > 0:
                updated_sym_ids.add(sym["symbol_id"])
        else:
            fail += 1
            consecutive_fail += 1
            fail_pairs.append(f"{sym['tv_symbol']} {tf_code}")

            # 3+ failures liên tiếp → nghi ngờ token hết hạn → thử refresh
            if consecutive_fail == 3:
                _logfmt.log(
                    logger,
                    "AUTH",
                    action="refresh_token",
                    amount=f"consecutive_fail {consecutive_fail}",
                    level=logging.WARNING,
                )
                if refresh_mid_run(tv, logger):
                    consecutive_fail = 0  # reset nếu refresh thành công

        # Nghỉ một chút trước khi gọi symbol tiếp theo (tránh rate limit)
        _log_pair_result(i, len(stale), sym["tv_symbol"], tf_code, result)
        sleep_for(sym["tv_symbol"])

    _logfmt.log(logger, "SUMMARY", action="gap_done", amount=f"ok {ok} fail {fail}", status=f"inserted {_fmt_int(bars_inserted)}")

    # -----------------------------------------------------------------------
    # BƯỚC 4: Tính lại TF phái sinh cho các symbol vừa có dữ liệu mới
    # -----------------------------------------------------------------------
    # Chỉ tính lại nếu thực sự có symbol nào được cập nhật
    # (tránh chạy ETL không cần thiết khi không có gì thay đổi)
    if updated_sym_ids and COMPUTED_TIMEFRAMES:
        _logfmt.log(logger, "ETL", action="computed_tfs", amount=f"symbols {_fmt_int(len(updated_sym_ids))}")
        if write_lock_name:
            wait_for_historical_slot("pipeline-maint", logger)
            derived_lock = _acquire_short_write_lock(
                write_lock_name,
                logger,
                action="recompute derived batch",
            )
            if not derived_lock:
                fail += 1
                fail_pairs.append("DERIVED_TFS")
            else:
                try:
                    recompute_derived(updated_sym_ids, logger)
                finally:
                    release(write_lock_name)
        else:
            recompute_derived(updated_sym_ids, logger)

    # Trả về dict thống kê thay vì chỉ fail count
    return {
        "fail":          fail,
        "ok":            ok,
        "total":         len(stale),
        "bars_inserted": bars_inserted,
        "miss_count":    len(miss_),
        "fail_pairs":    fail_pairs,
        "tf_summary":     [],
    }


# =============================================================================
# PHẦN 4: HÀM MAIN — Điểm vào của chương trình
# =============================================================================

def main() -> int:
    """
    Hàm được gọi đầu tiên khi chạy file này.
    Đọc tham số dòng lệnh → kiểm tra DB → kết nối TradingView → chạy pipeline.
    """
    import sys as _sys
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # --- ĐỌC THAM SỐ DÒNG LỆNH ---
    # argparse tự động tạo --help và xử lý các lỗi tham số không hợp lệ
    parser = argparse.ArgumentParser(
        description="AutoTrading Data Pipeline - full load and daily backfill"
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
        help="Show the plan only. Do not pull from TradingView or write to the database.",
    )

    # Tham số --symbols: chỉ chạy cho một số symbol nhất định (phân cách bởi dấu phẩy)
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Run only these symbols. Example: --symbols US30,GOLD,EURUSD",
    )

    # Tham số --reset: xóa sạch data trong Fact_OHLCV trước khi pull lại
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Reload selected data. Must be used with at least one filter: --symbols, --timeframes, or --asset-type.",
    )

    # Tham số --timeframes: chỉ chạy cho một số TF nhất định (phân cách bởi dấu phẩy)
    parser.add_argument(
        "--timeframes",
        type=str,
        default=None,
        help="Run only these timeframes. Example: --timeframes M45 or --timeframes M45,H1,H4",
    )

    # Tham số --asset-type: chỉ chạy cho symbols thuộc asset type chỉ định
    parser.add_argument(
        "--asset-type",
        type=str,
        default=None,
        help="Run only symbols from these asset types. Example: --asset-type Indice or --asset-type FOREX,Metal",
    )

    # Tham số --force-unlock: xóa stale lock trước khi chạy
    parser.add_argument(
        "--force-unlock",
        action="store_true",
        default=False,
        help="Clear an old tv_historical_job lock before starting. Use this only when a stale lock blocks the pipeline.",
    )

    # Phân tích các tham số từ dòng lệnh
    args    = parser.parse_args()

    # -----------------------------------------------------------------------
    # LỌC SYMBOLS theo --symbols / --asset-type (nếu có)
    # Phải khai báo global để thay đổi có hiệu lực trong run_full_load / run_backfill
    # -----------------------------------------------------------------------
    global SYMBOLS, COMPUTED_TIMEFRAMES, _TF_FILTER, _FORCE_RESET_REPULL  # noqa: PLW0603
    maintenance_locked = False
    maintenance_heartbeat_stop = threading.Event()
    historical_job_stop: threading.Event | None = None

    def _finish(code: int) -> int:
        nonlocal historical_job_stop
        maintenance_heartbeat_stop.set()
        if historical_job_stop is not None:
            release_historical_job(historical_job_stop, "pipeline", logger)
            historical_job_stop = None
        if maintenance_locked:
            release("warehouse_maintenance")
        return code

    def _yield_to_newer_historical_run(exc: HistoricalJobHandoffRequested) -> int:
        _logfmt.log(logger, "HANDOFF", action="historical_slot", status=str(exc))
        tg_alert(
            "INFO",
            "[INFO] <b>Data Pipeline gave up the TradingView history slot</b>\n"
            "A newer run asked to take over. "
            "This run stopped at a safe point to avoid a conflict."
        )
        tg_flush()
        return _finish(0)

    if args.asset_type:
        asset_filter = {a.strip() for a in args.asset_type.split(",")}
        SYMBOLS = [s for s in SYMBOLS if s["asset_type"] in asset_filter]
        if not SYMBOLS:
            _logfmt.log(logger, "ERROR", action="asset_filter", amount=args.asset_type, status="no_matching_symbols", level=logging.ERROR)
            sys.exit(1)
        _logfmt.log(logger, "FILTER", action="asset_type", amount=args.asset_type, status=f"symbols {_fmt_int(len(SYMBOLS))}")

    if args.symbols:
        sym_filter = {s.strip().upper() for s in args.symbols.split(",")}
        SYMBOLS = [s for s in SYMBOLS if s["tv_symbol"] in sym_filter]
        if not SYMBOLS:
            _logfmt.log(logger, "ERROR", action="symbol_filter", amount=args.symbols, status="no_matching_symbols", level=logging.ERROR)
            sys.exit(1)
        _logfmt.log(
            logger,
            "FILTER",
            action="symbols",
            amount=f"count {_fmt_int(len(SYMBOLS))}",
            status=",".join(s["tv_symbol"] for s in SYMBOLS),
        )

    # -----------------------------------------------------------------------
    # LỌC TIMEFRAMES theo --timeframes (nếu có)
    # Ảnh hưởng cả pull (run_full_load đọc _TF_FILTER) lẫn reset (xóa đúng TF)
    # -----------------------------------------------------------------------
    if args.timeframes:
        _TF_FILTER = {tf.strip().upper() for tf in args.timeframes.split(",")}
        COMPUTED_TIMEFRAMES = [ct for ct in COMPUTED_TIMEFRAMES if ct[0] in _TF_FILTER]
        _logfmt.log(logger, "FILTER", action="timeframes", status=",".join(sorted(_TF_FILTER)))

    if not args.dry_run:
        cleaned = cleanup_expired()
        if cleaned:
            _logfmt.log(logger, "LOCK", action="cleanup_expired", amount=f"locks {_fmt_int(cleaned)}")

    # -----------------------------------------------------------------------
    # XÓA DATA CŨ theo --reset (nếu có)
    # Yêu cầu ít nhất 1 bộ lọc (--symbols, --asset-type, hoặc --timeframes)
    # để tránh xóa nhầm toàn bộ DB
    # -----------------------------------------------------------------------
    if args.reset:
        has_filter = args.symbols or args.asset_type or args.timeframes
        if not has_filter:
            _logfmt.log(logger, "ERROR", action="reset_filter", status="required", level=logging.ERROR)
            sys.exit(1)

        tf_desc = ",".join(sorted(_TF_FILTER)) if _TF_FILTER else "all"
        _logfmt.log(
            logger,
            "RESET",
            action="prepare_delete",
            amount=f"symbols {_fmt_int(len(SYMBOLS))}",
            range_=f"tf {tf_desc}",
            status="confirm_required",
            level=logging.WARNING,
        )
        _logfmt.log(
            logger,
            "RESET",
            action="symbol_list",
            status=",".join(s["tv_symbol"] for s in SYMBOLS),
            level=logging.WARNING,
        )
        confirm = input("\nConfirm delete? (y/N): ").strip().lower()
        if confirm != "y":
            _logfmt.log(logger, "RESET", action="cancelled", status="by_user")
            return _finish(0)
        _FORCE_RESET_REPULL = True
        _logfmt.log(
            logger,
            "RESET",
            action="safe_reset",
            amount="enabled",
            status="delete_after_replacement_ready",
            level=logging.WARNING,
        )

    # Ghi lại thời điểm bắt đầu để tính tổng thời gian chạy ở cuối
    started = datetime.now()

    # In banner mở đầu với thông tin tổng quan
    _log_rule("HISTORICAL DATA UPDATE SYSTEM v2.0")
    _logfmt.log(
        logger,
        "CONFIG",
        action="dataset",
        amount=f"pairs {len(SYMBOLS)}",
        range_=f"direct {len(DIRECT_TFS)} computed {len(DERIVED_TFS)}",
        status=f"provider {HISTORICAL_PROVIDER}",
    )
    if HISTORICAL_PROVIDER == "websocket":
        _logfmt.log(
            logger,
            "CONFIG",
            action="tv_ws",
            amount=f"main {TV_WS_HISTORY_ENDPOINT}",
            range_=f"fallback {','.join(TV_WS_HISTORY_FALLBACK_ENDPOINTS) or '-'}",
            status=f"request_more {TV_WS_HISTORY_REQUEST_MORE_ROUNDS}x{_fmt_int(TV_WS_HISTORY_REQUEST_MORE_BARS)}",
        )
        _logfmt.log(
            logger,
            "CONFIG",
            action="replay",
            amount="enabled yes" if TV_WS_REPLAY_ENABLED else "enabled no",
            range_=f"endpoint {TV_WS_REPLAY_ENDPOINT}",
            status="tfs " + (",".join(sorted(TV_WS_REPLAY_TFS)) if TV_WS_REPLAY_TFS else "-"),
        )
    _logfmt.log(logger, "CONFIG", action="started", status=started.strftime("%Y-%m-%d %H:%M:%S"))

    # -----------------------------------------------------------------------
    # BƯỚC 0: Kiểm tra kết nối Database
    # Làm điều này đầu tiên vì nếu DB không chạy, toàn bộ pipeline vô nghĩa
    # -----------------------------------------------------------------------
    _logfmt.log(logger, "STEP", progress="1/3", action="database_connect")
    if not test_connection():
        # Không kết nối được DB → lỗi nghiêm trọng → thoát với exit code 1
        _logfmt.log(logger, "ERROR", progress="1/3", action="database_connect", status="failed_stop", level=logging.ERROR)
        tg_alert("ERROR", "[ERROR] <b>Data Pipeline FAILED</b>\nCould not connect to SQL Server.\nCheck the database service now." + QUICK_COMMANDS_HINT)
        tg_flush()
        return _finish(1)

    # Thông báo đã bắt đầu (gửi sau khi xác nhận DB OK để tránh noise khi DB lỗi)
    dry_tag = " <i>[DRY RUN]</i>" if args.dry_run else ""
    tg_alert(
        "INFO",
        f"[START] <b>Data Pipeline started</b>{dry_tag}\n"
        f"{len(SYMBOLS)} symbols x {len(DIRECT_TFS)} direct TFs + {len(DERIVED_TFS)} computed TFs\n"
        f"Started: {started.strftime('%H:%M:%S')} UTC",
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
        _logfmt.log(logger, "MODE", action="forced", status=mode.upper())

    maintenance_scope = (mode == "full") or args.reset
    if not args.dry_run and maintenance_scope and is_locked("ws_live_runtime"):
        _logfmt.log(logger, "ERROR", action="ws_live_active", amount="full/reset blocked", status="pause_ws_live", level=logging.ERROR)
        tg_alert(
            "WARNING",
            "[WARN] <b>Data Pipeline was blocked</b>\n"
            "full/reset mode cannot run while <b>ws_live</b> is online 24/7.\n"
            "Pause ws_live, then run this command again."
            + QUICK_COMMANDS_HINT
        )
        tg_flush()
        return _finish(1)

    if not args.dry_run:
        if args.force_unlock:
            release("tv_historical_job")
            _logfmt.log(logger, "LOCK", action="force_unlock", amount="tv_historical_job", status="cleared", level=logging.WARNING)
        _lock_ttl = 300 if mode == "full" else 60
        historical_job_stop = acquire_historical_job("pipeline", logger, duration_min=_lock_ttl)
        if historical_job_stop is None:
            tg_alert(
                "WARNING",
                "[WARN] <b>Data Pipeline was blocked</b>\n"
                "Could not get the TradingView history slot because another checker/pipeline job is running.\n"
                "Wait for that job to finish, then run this again."
                + QUICK_COMMANDS_HINT
            )
            tg_flush()
            return _finish(1)
        atexit.register(release_historical_job, historical_job_stop, "pipeline", logger)

    # -----------------------------------------------------------------------
    # BƯỚC 2: Kết nối TradingView
    # Bỏ qua nếu đang chạy dry-run (không cần kết nối thật nếu không kéo data)
    # -----------------------------------------------------------------------
    tv = None  # Khởi tạo biến tv = None phòng trường hợp dry-run không gán
    if not args.dry_run:
        _logfmt.log(logger, "STEP", progress="2/3", action="tradingview_login")
        try:
            # get_valid_tv_connection: thử cache → .env token → cookie refresh → headless → guest
            tv, auth_mode = get_valid_tv_connection(logger)
            _logfmt.log(logger, "AUTH", action="login_ready", amount=f"mode {auth_mode}", status="ready")

            # Cảnh báo nếu đang dùng tài khoản guest:
            # Guest bị giới hạn số bar và dễ bị timeout hơn tài khoản đăng nhập
            if auth_mode == "guest":
                _logfmt.log(logger, "AUTH", action="guest_mode", amount="action stop", status="data_completeness", level=logging.ERROR)
                tg_alert(
                    "ERROR",
                    "[ERROR] <b>Data Pipeline FAILED</b>\n"
                    "TradingView is in guest mode, so history bars may be missing.\n"
                    "Refresh TradingView auth, then run again."
                    + QUICK_COMMANDS_HINT
                )
                tg_flush()
                return _finish(1)
        except Exception as e:
            # Không kết nối được TradingView → không kéo được data → thoát
            _logfmt.log(logger, "AUTH", action="login_failed", status=str(e), level=logging.ERROR)
            tg_alert("ERROR", f"[ERROR] <b>Data Pipeline FAILED</b>\nCould not connect to TradingView.\nError: {e}" + QUICK_COMMANDS_HINT)
            return _finish(1)

        # Hold warehouse lock only once TradingView access is ready and the
        # pipeline is about to start DB writes.
        maintenance_locked = (
            acquire("warehouse_maintenance", duration_min=240)
            if maintenance_scope else False
        )
        if maintenance_scope and not maintenance_locked:
            _logfmt.log(logger, "ERROR", action="warehouse_lock", status="busy", level=logging.ERROR)
            tg_alert(
                "WARNING",
                "[WARN] <b>Data Pipeline was blocked</b>\n"
                "Could not get the maintenance lock because another checker/maintenance job is running.\n"
                "Wait for that job to finish, then run again."
                + QUICK_COMMANDS_HINT
            )
            tg_flush()
            return _finish(1)
        if maintenance_locked:
            _logfmt.log(logger, "LOCK", action="warehouse", status="acquired")
            atexit.register(release, "warehouse_maintenance")

            def _maintenance_heartbeat() -> None:
                while not maintenance_heartbeat_stop.wait(1800):
                    renewed = renew("warehouse_maintenance", duration_min=240)
                    if not renewed:
                        _logfmt.log(logger, "LOCK", action="warehouse", status="renew_failed", level=logging.WARNING)

            threading.Thread(
                target=_maintenance_heartbeat,
                name="pipeline-lock-heartbeat",
                daemon=True,
            ).start()

    # -----------------------------------------------------------------------
    # BƯỚC 3: Chạy pipeline theo chế độ đã xác định
    # -----------------------------------------------------------------------
    _logfmt.log(logger, "STEP", progress="3/3", action="run_mode", status=mode.upper())
    if mode == "full":
        # Full load: kéo toàn bộ lịch sử (mất vài giờ, chỉ chạy lần đầu)
        try:
            raw = run_full_load(tv, dry_run=args.dry_run)
        except HistoricalJobHandoffRequested as exc:
            return _yield_to_newer_historical_run(exc)
        # run_full_load trả về int (số fail) — bọc thành dict để thống nhất
        if isinstance(raw, dict):
            run_stats = raw
            fail = int(run_stats.get("fail", 0) or 0)
        else:
            fail = int(raw)
            run_stats = {
                "fail": fail,
                "ok": 0,
                "total": 0,
                "bars_inserted": 0,
                "miss_count": 0,
                "fail_pairs": [],
                "tf_summary": [],
            }
    else:
        # Backfill: chỉ bù phần thiếu (mất vài phút, chạy hàng ngày)
        try:
            run_stats = run_backfill(
                tv,
                dry_run=args.dry_run,
                write_lock_name=None if (args.dry_run or maintenance_scope) else "warehouse_maintenance",
            )
        except HistoricalJobHandoffRequested as exc:
            return _yield_to_newer_historical_run(exc)
        fail = run_stats["fail"]

    # -----------------------------------------------------------------------
    # BƯỚC 4: Dọn dẹp bảng staging
    # Xóa dữ liệu staging cũ hơn 7 ngày để tiết kiệm dung lượng DB
    # Bỏ qua nếu dry-run vì không có gì được ghi vào DB cả
    # -----------------------------------------------------------------------
    if not args.dry_run:
        _logfmt.log(logger, "CLEANUP", action="staging", amount="older_than 7d")
        # purge_staging trả về dict: { tên_bảng: số_hàng_đã_xóa }
        purged = purge_staging(days_to_keep=7)
        # Cộng tổng số hàng đã xóa từ tất cả các bảng
        total_purged = sum(purged.values())
        _logfmt.log(logger, "CLEANUP", action="staging", amount=f"deleted {_fmt_int(total_purged)}", status="ok")

    # -----------------------------------------------------------------------
    # TÓM TẮT KẾT QUẢ TOÀN BỘ
    # -----------------------------------------------------------------------
    # Tính thời gian chạy tổng cộng (tính từ lúc bắt đầu đến bây giờ)
    elapsed = (datetime.now() - started).total_seconds()

    _logfmt.log(
        logger,
        "RUN_SUM",
        action=f"mode {mode.upper()}",
        amount=f"ok {_fmt_int(run_stats.get('ok', 0))} fail {fail}",
        range_=f"inserted {_fmt_int(run_stats.get('bars_inserted', 0))}",
        status=f"elapsed {elapsed:.0f}s",
    )
    _write_run_summary(mode, started, elapsed, run_stats)
    _log_rule("DONE", f"mode={mode.upper()} failed={fail} elapsed_sec={elapsed:.0f}")

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
            what_done = "All data is already fresh. No update was needed."
        else:
            what_done = (f"Pairs updated: {ok_pairs:,}/{total_pairs:,}\n"
                         f"New bars added to DB: {bars_new:,}")
            if miss_count:
                what_done += f"\nPairs that had no data and were filled: {miss_count}"
        tg_alert(
            "INFO",
            f"[OK] <b>Data Pipeline finished</b> (mode: {mode.upper()}){dry_tag}\n"
            f"{what_done}\n"
            f"Run time: {elapsed_str}\n"
            f"-----------------\n"
            f"<b>Next:</b>\n"
            f"  - 02_ws_live.py keeps live data updated every 5 minutes\n"
            f"  - 04_checker.py checks and repairs data every 3 days",
        )
    else:
        # Liệt kê tối đa 5 cặp bị lỗi
        fail_list = "\n".join(f"  [FAIL] {p}" for p in fail_pairs[:5])
        if len(fail_pairs) > 5:
            fail_list += f"\n  ... and {len(fail_pairs) - 5} more pairs"
        tg_alert(
            "WARNING",
            f"[WARN] <b>Data Pipeline finished with errors</b> (mode: {mode.upper()})\n"
            f"Pairs: {ok_pairs:,} OK / {fail:,} failed\n"
            f"New bars added to DB: {bars_new:,}\n"
            f"-----------------\n"
            f"<b>Failed pairs:</b>\n{fail_list}\n"
            f"-----------------\n"
            f"Run time: {elapsed_str}"
            + QUICK_COMMANDS_HINT,
        )

    tg_flush()  # Đảm bảo thông báo Discord được gửi trước khi process thoát

    # Quyết định exit code dựa trên số lỗi
    if fail > 0:
        # Có lỗi một phần: in cảnh báo và chỉ đường đến file log để xem chi tiết
        _logfmt.log(logger, "SUMMARY", action="failed_pairs", amount=f"count {fail}", status="see pipeline.log", level=logging.WARNING)
        return _finish(2)  # Exit code 2: thành công một phần

    return _finish(0)  # Exit code 0: thành công hoàn toàn


# =============================================================================
# ĐIỂM KHỞI ĐỘNG
# =============================================================================
# Dòng này đảm bảo hàm main() chỉ được gọi khi file này được chạy trực tiếp
# (không bị gọi khi file này được import bởi module khác).
# sys.exit() chuyển giá trị trả về của main() thành exit code của tiến trình,
# cho phép Task Scheduler hoặc script bên ngoài biết kết quả thành công hay thất bại.
if __name__ == "__main__":
    import traceback as _tb_mod
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        tg_alert(
            "WARNING",
            "[WARN] <b>Data Pipeline stopped</b>\nStopped manually (Ctrl+C).",
        )
        tg_flush()
        sys.exit(130)
    except Exception as _exc:
        _tb = _tb_mod.format_exc()
        tg_alert(
            "ERROR",
            f"[ERROR] <b>Data Pipeline crashed</b>\n"
            f"{type(_exc).__name__}: {_exc}\n"
            f"<pre>{_tb[-800:]}</pre>",
        )
        tg_flush()
        sys.exit(1)
