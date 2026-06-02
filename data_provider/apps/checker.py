"""
Bo checker / repair cho chat luong du lieu OHLCV.

File nay so sanh du lieu trong DB voi TradingView de tim:
- nen bi thieu
- nen du thua
- OHLC sai so
- volume lech bat thuong
- continuity / interval gap issue o direct TF va computed TF

Che do van hanh hien tai:
- `--dry-run`: chi quet va bao cao, khong sua
- mac dinh: uu tien auto-repair theo rollout an toan, co lock va co verify sau sua
- `--manual-confirm`: deprecated; chi gui thong bao Discord roi van auto-repair

Nguong mac dinh hien tai la `0.001 = 0.1%`.
Tuy nhien cac "core issue" nhu missing / wrong OHLC / extra bars co the bo qua nguong nay
va duoc dua vao danh sach sua ngay, vi do la loi cau truc du lieu chinh.
"""

# =============================================================================
# data_provider/apps/checker.py  â€"  Kiá»ƒm tra & Tá»± sá»­a cháº¥t lÆ°á»£ng dá»¯ liá»‡u
# =============================================================================
#
# FILE NÃ€Y LÃ€M GÃŒ-
#   ÄÃ¢y lÃ  "thanh tra dá»¯ liá»‡u" â€" cháº¡y má»—i 3 ngÃ y, nÃ³ so sÃ¡nh dá»¯ liá»‡u trong
#   database cá»§a báº¡n vá»›i dá»¯ liá»‡u thá»±c tá»« TradingView Ä‘á»ƒ phÃ¡t hiá»‡n xem cÃ³ náº¿n
#   nÃ o bá»‹ sai sá»‘ liá»‡u (giÃ¡ má»Ÿ/Ä‘Ã³ng/cao/tháº¥p khÃ´ng khá»›p) hay bá»‹ thiáº¿u khÃ´ng.
#
#   Máº·c Ä‘á»‹nh hiá»‡n nay script CÃ" THá»‚ tá»± sá»­a theo cháº¿ Ä‘á»™ auto-repair an toÃ n.
#   Chá»‰ khi cháº¡y vá»›i --manual-confirm thÃ¬ Discord má»›i gá»­i thÃ´ng bÃ¡o chi tiáº¿t.
#   Discord lÃ  one-way (khÃ´ng nháº­n lá»‡nh ngÆ°á»£c) â€" xÃ¡c nháº­n tá»± Ä‘á»™ng sau timeout.
#
# Táº I SAO Cáº¦N SCRIPT NÃ€Y-
#   Dá»¯ liá»‡u trong DB cÃ³ thá»ƒ bá»‹ lá»‡ch so vá»›i TradingView do nhiá»u lÃ½ do:
#     - Káº¿t ná»‘i máº¡ng bá»‹ giÃ¡n Ä‘oáº¡n giá»¯a chá»«ng â†’ náº¿n bá»‹ thiáº¿u
#     - TradingView thá»‰nh thoáº£ng Ä‘iá»u chá»‰nh láº¡i giÃ¡ lá»‹ch sá»­ (restatement)
#     - Pipeline ghi Ä‘Ãºng nhÆ°ng cÃ³ lá»—i lÃ m trÃ²n sá»‘
#   Náº¿u dá»¯ liá»‡u sai, chiáº¿n lÆ°á»£c giao dá»‹ch sáº½ tÃ­nh toÃ¡n sai signal â†’ nguy hiá»ƒm.
#
# QUY TRÃŒNH 3 GIAI ÄOáº N (chá»‰ khi cháº¡y thÆ°á»ng, khÃ´ng cÃ³ --dry-run):
#
#   Giai Ä‘oáº¡n 1 â€" SCAN (quÃ©t, khÃ´ng sá»­a):
#     Vá»›i má»—i cáº·p (symbol, timeframe):
#       - KÃ©o N náº¿n má»›i nháº¥t tá»« TradingView (nguá»"n dá»¯ liá»‡u gá»‘c)
#       - Truy váº¥n Ä‘Ãºng N náº¿n Ä‘Ã³ trong database
#       - So sÃ¡nh tá»«ng sá»‘ liá»‡u OHLCV: náº¿u chÃªnh > 0.01% â†’ Ä‘Ã¡nh dáº¥u sai
#     Káº¿t quáº£: danh sÃ¡ch cáº·p cÃ³ váº¥n Ä‘á» theo core issue hoáº·c vÆ°á»£t ngÆ°á»¡ng mismatch hiá»‡n hÃ nh
#
#   Giai Ä‘oáº¡n 2 â€" AUTO-CONFIRM:
#     Discord webhook lÃ  one-way, nÃªn checker chá»‰ gá»­i notice rá»“i tiáº¿p tá»¥c auto-repair.
#
#   Giai Ä‘oáº¡n 3 â€" REPAIR (sá»­a náº¿u user Ä‘á»"ng Ã½):
#     - Acquire lock 'checker_repair' (ngÄƒn WS cháº¡y ETL Ä‘á»"ng thá»i)
#     - XÃ³a náº¿n sai â†’ kÃ©o láº¡i tá»« TradingView â†’ kiá»ƒm tra sau khi sá»­a
#     - Release lock â†’ WS tá»± Ä‘á»™ng resume ETL Ä‘Ã£ hoÃ£n
#     - Gá»­i bÃ¡o cÃ¡o káº¿t quáº£ lÃªn Discord
#
# CÃCH CHáº Y THá»¦ CÃ"NG:
#   python checker.py                    # cháº¡y scan + auto-repair an toÃ n (máº·c Ä‘á»‹nh)
#   python checker.py --dry-run          # chá»‰ scan + bÃ¡o cÃ¡o, khÃ´ng há»i, khÃ´ng sá»­a
#   python checker.py --manual-confirm   # deprecated; gá»­i notice rá»“i váº«n auto-repair
#   python checker.py --sym GOLD         # chá»‰ kiá»ƒm tra 1 symbol
#   python checker.py --tf H4            # chá»‰ kiá»ƒm tra 1 khung thá»i gian
#   python checker.py --threshold 0.05   # nÃ¢ng ngÆ°á»¡ng sai lÃªn 5% (máº·c Ä‘á»‹nh 0.1%)
#
# Lá»ŠCH CHáº Y Tá»° Äá»˜NG:
#   Task Scheduler (Windows) tá»± Ä‘á»™ng gá»i file nÃ y má»—i 3 ngÃ y lÃºc 03:00 UTC
#
# Káº¾T QUáº¢ Gá»¬I LÃŠN TELEGRAM:
#   - Náº¿u sáº¡ch: "âœ… X pairs â€" táº¥t cáº£ dá»¯ liá»‡u khá»›p"
#   - Náº¿u cÃ³ váº¥n Ä‘á»: gá»­i notice vÃ  auto-repair theo lock/verify
#   - Sau khi sá»­a: "ðŸ"§ X pairs Ä‘Ã£ sá»­a xong / Y pairs lá»—i persistent"
# =============================================================================

import argparse
import atexit
import logging
import math
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
_DATA = Path(__file__).resolve().parents[1]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from config import COMPUTED_TIMEFRAMES, SYMBOL_OVERNIGHT_MINS, SYMBOLS, TF_STAGING, TF_MINUTES
from data_provider.common.helpers import (
    FULL_N_BARS,
    normalize_tv_hist_df_to_utc,
    setup_logger,
    recompute_derived,
    pull_and_store,
    now_utc,
    sleep_for,
)
from data_provider.tv.auth import get_valid_tv_connection, refresh_mid_run
from data_provider.tv import ws_history as _tv_ws_history
from data_provider.tv.coord import (
    HistoricalJobHandoffRequested,
    acquire_historical_job,
    release_historical_job,
    wait_for_historical_slot,
)
from data_provider.common.notifications import tg_send, tg_flush
from data_provider.common.locks import acquire, release, cleanup_expired, renew, is_locked
from data_provider.paths import LOG_DIR
from modules.db_connector import (
    aggregate_from_fact,
    delete_fact_bars,
    delete_ohlcv_bars,
    delete_staging_bars,
    get_connection,
    run_etl_direct,
    test_connection,
)

# =============================================================================
# Háº°NG Sá» Cáº¤U HÃŒNH
# =============================================================================

# Sá»‘ náº¿n kiá»ƒm tra má»—i láº§n cho tá»«ng TF (má»—i symbol).
# M5/M15 lá»›n hÆ¡n vÃ¬ muá»‘n bao phá»§ ~10 ngÃ y giao dá»‹ch (Ä‘á»ƒ Ä‘áº£m báº£o
# khi cháº¡y má»—i 3 ngÃ y váº«n cÃ³ vÃ¹ng chá»"ng láº¥p vá»›i láº§n cháº¡y trÆ°á»›c).
CHECKER_N_BARS: dict[str, int] = {
    "M5":  2000,
    "M10": 2000,
    "M15": 2000,
    "M20": 2000,
    "M30": 1500,
    "M45": 1000,
    "H1":  1000,
    "M90": 1000,
    "H2":  1000,
    "H3":  1000,
    "H4":  1000,
    "H6":  1000,
    "H8":  1000,
    "D1":   400,
    "W":    200,
}

# Sá»‘ náº¿n kiá»ƒm tra láº¡i SAU KHI Sá»¬A (Ä‘á»ƒ xÃ¡c nháº­n sá»­a thÃ nh cÃ´ng).
# Nhá» hÆ¡n CHECKER_N_BARS Ä‘á»ƒ nhanh hÆ¡n â€" chá»‰ cáº§n kiá»ƒm tra ngáº¯n gá»n.
VERIFY_N_BARS = 200

# Khi mismatch lon theo kieu missing/extra timeline, focused repair thuong khong
# du de lam sach du lieu cu. Luc do safe full repull cho dung pair/TF se on dinh hon.
SYSTEMIC_RESET_RATE = 0.20
SYSTEMIC_RESET_MISSING_FLOOR = 50
SYSTEMIC_RESET_EXTRA_FLOOR = 20

# NgÆ°á»¡ng tá»· lá»‡ sai cho phÃ©p cho nhÃ³m mismatch tá»•ng há»£p.
# 0.001 = 0.1%: tá»©c náº¿u > 0.1% sá»‘ náº¿n trong cá»­a sá»• kiá»ƒm tra bá»‹ mismatch thÃ¬ cáº·p Ä‘Ã³ bá»‹ gáº¯n cá».
DEFAULT_THRESHOLD = 0.001
# Aggregate threshold only: core issues (missing / OHLC wrong / extra) bypass it.

# Sai sá»‘ tÆ°Æ¡ng Ä‘á»‘i cho phÃ©p khi so sÃ¡nh giÃ¡ OHLCV:
# abs(giÃ¡_TV - giÃ¡_DB) / giÃ¡_TV pháº£i < 0.01% thÃ¬ má»›i coi lÃ  "khá»›p".
# Cáº§n thiáº¿t vÃ¬ sá»‘ thá»±c trong mÃ¡y tÃ­nh khÃ´ng bao giá» hoÃ n toÃ n báº±ng nhau.
OHLCV_REL_TOL = 1e-4   # 0.01%
VOLUME_REL_TOL = 5e-3  # 0.5%
VOLUME_ABS_TOL = 25.0  # TV volume co the dao dong nho giua 2 lan pull

# Sá»‘ vÃ²ng sá»­a tá»‘i Ä‘a trÆ°á»›c khi káº¿t luáº­n cáº·p Ä‘Ã³ "lá»—i cá»‘ Ä‘á»‹nh".
# VÃ²ng 0: quÃ©t + sá»­a láº§n 1.
# VÃ²ng 1: quÃ©t láº¡i + sá»­a láº§n 2.
# VÃ²ng 2: quÃ©t láº§n cuá»‘i, náº¿u váº«n sai â†’ Ä‘Ã¡nh dáº¥u PERSISTENT_FAILURE.
MAX_REPAIR_ROUNDS = 3

# Danh sÃ¡ch khung thá»i gian cáº§n kiá»ƒm tra (tá»« H4 xuá»‘ng M5).
# KhÃ´ng kiá»ƒm tra W vÃ  D1 vÃ¬ chÃºng Ã­t thay Ä‘á»•i vÃ  TradingView
# ráº¥t hiáº¿m khi Ä‘iá»u chá»‰nh láº¡i náº¿n tuáº§n/ngÃ y.
TFS_TO_CHECK = ["W", "D1", "H8", "H6", "H4", "H3", "H2", "H1",
                "M90", "M45", "M30", "M20", "M15", "M10", "M5"]

# Thá»i gian nghá»‰ giá»¯a má»—i láº§n gá»i API TradingView (giÃ¢y).
# TrÃ¡nh bá»‹ TradingView phÃ¡t hiá»‡n lÃ  bot vÃ  giá»›i háº¡n tá»‘c Ä‘á»™ (rate-limit).
TV_SLEEP_BETWEEN_CALLS = 0.5

# Thá»i gian nghá»‰ dÃ i khi TradingView tráº£ vá» rá»—ng 3 láº§n liÃªn tiáº¿p.
# Kháº£ nÄƒng cao lÃ  Ä‘ang bá»‹ rate-limit â†’ nghá»‰ 60 giÃ¢y Ä‘á»ƒ TV "nguá»™i xuá»‘ng".
TV_THROTTLE_SLEEP = 60

# DST precheck chá»‰ cáº§n má»™t máº«u nhá» nhÆ°ng pháº£i Ä‘á»§ rá»™ng Ä‘á»ƒ phÃ¡t hiá»‡n drift hÃ ng loáº¡t.
# Giá»¯ má»¥c tiÃªu quanh 18 pairs giÃºp checker khá»Ÿi Ä‘á»™ng nhanh hÆ¡n nhiá»u mÃ  váº«n Ä‘áº¡t
# ngÆ°á»¡ng tá»‘i thiá»ƒu 15 pairs Ä‘á»ƒ heuristic cÃ²n Ã½ nghÄ©a.
DST_PRECHECK_TARGET_PAIRS = 18

# Circuit breaker: náº¿u auth fail liÃªn tiáº¿p vÆ°á»£t ngÆ°á»¡ng nÃ y â†’ ngá»«ng checker.
# Má»¥c Ä‘Ã­ch: trÃ¡nh tiáº¿p tá»¥c gá»i TV khi IP Ä‘Ã£ bá»‹ block (HTTP 403), lÃ m tá»‡ hÆ¡n.
# 5 láº§n â‰ˆ ~40 giÃ¢y (sau throttle) â†’ Ä‘á»§ Ä‘á»ƒ phÃ¢n biá»‡t lá»—i táº¡m thá»i vs. block tháº­t.
MAX_AUTH_CONSECUTIVE_FAIL = 5


# â"€â"€â"€ Helper: build Interval map (lazy import to avoid circular import) â"€â"€â"€â"€â"€â"€â"€

def _build_interval_map() -> dict:
    """Build tf_code -> TradingView WebSocket interval mapping."""
    return _tv_ws_history.get_ws_interval_map()


# =============================================================================
# SO SÃNH Dá»® LIá»†U OHLCV â€" TrÃ¡i tim cá»§a quÃ¡ trÃ¬nh kiá»ƒm tra
# =============================================================================

def _configure_console_encoding() -> None:
    """Best-effort UTF-8 console output to avoid Windows argparse crashes."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _mismatch_flags(tv_vals: tuple, db_vals: tuple) -> tuple[bool, bool]:
    """
    Return (ohlc_bad, volume_bad) using the same tolerances as the checker.
    """
    ohlc_bad = False
    for idx, (a, b) in enumerate(zip(tv_vals[:4], db_vals[:4])):
        tol = max(abs(a) * OHLCV_REL_TOL, 1e-6)
        if abs(a - b) > tol:
            ohlc_bad = True
            break

    vol_a = tv_vals[4] if len(tv_vals) > 4 else 0.0
    vol_b = db_vals[4] if len(db_vals) > 4 else 0.0
    vol_tol = max(abs(vol_a) * VOLUME_REL_TOL, VOLUME_ABS_TOL)
    volume_bad = abs(vol_a - vol_b) > vol_tol
    return ohlc_bad, volume_bad


def _ohlcv_match(tv_vals: tuple, db_vals: tuple) -> bool:
    """
    Kiá»ƒm tra tuple OHLCV cÃ³ khá»›p nhau khÃ´ng.

    KhÃ´ng so sÃ¡nh báº±ng nhau tuyá»‡t Ä‘á»‘i vÃ¬ sá»‘ thá»±c trong mÃ¡y tÃ­nh cÃ³ thá»ƒ
    cÃ³ sai sá»‘ nhá» do lÃ m trÃ²n. Thay vÃ o Ä‘Ã³ dÃ¹ng sai sá»‘ tÆ°Æ¡ng Ä‘á»‘i:
      |giÃ¡_TV - giÃ¡_DB| / giÃ¡_TV < 0.01%  â†’  coi lÃ  khá»›p

    DÃ¹ng floor 1e-6 Ä‘á»ƒ trÃ¡nh false-positive khi giÃ¡ TV = 0 (trÃ¡nh divide-by-zero).
    KhÃ´ng dÃ¹ng max(OHLCV_REL_TOL, abs(a)*OHLCV_REL_TOL) vÃ¬ vá»›i giÃ¡ ráº¥t nhá»
    (VD: EURUSD nhá», crypto) tolerance tuyá»‡t Ä‘á»‘i 1e-4 sáº½ che khuáº¥t sai sá»‘ 100%.

    VÃ­ dá»¥:
      TV = 1900.1234, DB = 1900.1235 â†’ chÃªnh 0.0001/1900 â‰ˆ 0% â†’ KHá»šP
      TV = 1900.0000, DB = 1902.0000 â†’ chÃªnh 2/1900 = 0.1% â†’ KHÃ"NG KHá»šP
      TV = 0.00010,   DB = 0.00020   â†’ chÃªnh 100% â†’ KHÃ"NG KHá»šP (trÆ°á»ng há»£p cÅ© bá» sÃ³t)

    Tráº£ vá» True náº¿u táº¥t cáº£ 4 giÃ¡ trá»‹ Ä‘á»u khá»›p, False náº¿u báº¥t ká»³ cÃ¡i nÃ o sai.
    """
    ohlc_bad, volume_bad = _mismatch_flags(tv_vals, db_vals)
    return not (ohlc_bad or volume_bad)


def _compare(tv_dict: dict, db_dict: dict) -> tuple[set, dict, dict, set, float]:
    """
    So sÃ¡nh dá»¯ liá»‡u TradingView vá»›i dá»¯ liá»‡u trong DB cho má»™t cáº·p (symbol, TF).

    Tham sá»‘:
      tv_dict â€" dá»¯ liá»‡u tá»« TradingView: {timestamp: (O, H, L, C, V)}
      db_dict â€" dá»¯ liá»‡u tá»« DB:          {timestamp: (O, H, L, C, V)}

    Tráº£ vá» 3 giÃ¡ trá»‹:
      missing       â€" set timestamp cÃ³ trong TV nhÆ°ng KHÃ"NG cÃ³ trong DB (thiáº¿u náº¿n)
      mismatched    â€" dict {timestamp: (giÃ¡_TV, giÃ¡_DB)} náº¿n cÃ³ nhÆ°ng giÃ¡ sai
      mismatch_rate â€" tá»· lá»‡ = (thiáº¿u + sai) / tá»•ng_náº¿n_TV (0.0 = hoÃ n háº£o, 1.0 = sai háº¿t)

    VÃ­ dá»¥ káº¿t quáº£:
      missing = {datetime(2024,1,15,8,0), ...}  # 2 náº¿n thiáº¿u
      mismatched = {datetime(2024,1,14,16,0): ((1900.1, ...), (1890.2, ...))}  # 1 náº¿n sai giÃ¡
      rate = 3/1000 = 0.003 = 0.3%  â†’ cao hÆ¡n ngÆ°á»¡ng máº·c Ä‘á»‹nh 0.1% â†’ cáº§n xem xÃ©t sá»­a
    """
    if not tv_dict:
        return set(), {}, {}, set(), 0.0

    tv_keys = set(tv_dict)
    db_keys = set(db_dict)
    # TÃ¬m náº¿n cÃ³ trong TV nhÆ°ng khÃ´ng cÃ³ trong DB (thiáº¿u)
    missing = tv_keys - db_keys
    # TÃ¬m náº¿n cÃ³ trong DB nhÆ°ng khÃ´ng cÃ³ trong TV (thá»«a / sai timeline)
    extra = db_keys - tv_keys
    # TÃ¬m náº¿n cÃ³ á»Ÿ cáº£ 2 nÆ¡i nhÆ°ng giÃ¡ trá»‹ khÃ´ng khá»›p (sai)
    mismatched = {}
    volume_only = {}
    for dt in tv_keys & db_keys:
        ohlc_bad, volume_bad = _mismatch_flags(tv_dict[dt], db_dict[dt])
        if ohlc_bad:
            mismatched[dt] = (tv_dict[dt], db_dict[dt])
        elif volume_bad:
            volume_only[dt] = (tv_dict[dt], db_dict[dt])
    # Tá»· lá»‡ sai = (thiáº¿u + sai + thá»«a) / max(TV, DB)
    denom = max(len(tv_dict), len(db_dict), 1)
    rate = (len(missing) + len(mismatched) + len(extra)) / denom
    return missing, mismatched, volume_only, extra, rate


# â"€â"€â"€ DB helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _query_db_bars(symbol_id: int, tf_code: str,
                   from_dt: datetime, to_dt: datetime) -> dict:
    """
    Query Fact_OHLCV for bars within [from_dt, to_dt].
    Returns dict {datetime: (open, high, low, close, volume)}.
    Returns {} on error.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.BarTime, f.[Open], f.High, f.Low, f.[Close], f.Volume
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              AND  f.BarTime  BETWEEN ? AND ?
            ORDER  BY f.BarTime
        """, (symbol_id, tf_code, from_dt, to_dt))
        return {row[0]: (float(row[1]), float(row[2]),
                         float(row[3]), float(row[4]),
                         float(row[5]) if row[5] is not None else 0.0)
                for row in cursor.fetchall()}
    except Exception as e:
        logging.getLogger("checker").error(
            "_query_db_bars FAILED sym_id=%s tf=%s: %s", symbol_id, tf_code, e
        )
        return {}
    finally:
        conn.close()


# â"€â"€â"€ TV helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _pull_tv_bars(tv, sym: dict, tf_code: str, n_bars: int,
                  interval_map: dict,
                  logger: logging.Logger) -> dict | None:
    """
    Pull n_bars from TradingView for (symbol, TF).
    Drops last bar (may be open/unclosed) and normalizes datetimes to naive UTC.

    Returns dict {datetime: (O, H, L, C, V)}.
    Returns None on TV error or empty response.
    """
    if tf_code not in interval_map:
        logger.error("Unsupported TradingView WebSocket timeframe for checker: %s", tf_code)
        return None

    wait_for_historical_slot("checker", logger)
    try:
        ws_result = _tv_ws_history.fetch_history(
            symbol=sym["tv_symbol"],
            exchange=sym["tv_exchange"],
            tf_code=tf_code,
            n_bars=n_bars + 5,
            logger=logger,
        )
        df = ws_result.df
        if ws_result.status not in ("completed", "partial_timeout"):
            logger.warning(
                "TradingView WS pull status for %s %s: %s %s",
                sym["tv_symbol"], tf_code, ws_result.status, ws_result.error,
            )
    except Exception as e:
        logger.error("TradingView WS pull failed for %s %s: %s", sym["tv_symbol"], tf_code, e)
        return None

    if df is None or df.empty:
        return None

    df, normalized = normalize_tv_hist_df_to_utc(df)
    if normalized:
        logger.debug(
            "  TV scan %s %s: normalized historical timestamps to UTC",
            sym["tv_symbol"], tf_code,
        )

    df = df.iloc[:-1]
    if df.empty:
        return None

    result: dict[datetime, tuple] = {}
    future_cutoff = now_utc() + timedelta(minutes=1)
    dropped_future = 0
    for dt_idx, row in df.iterrows():
        dt = dt_idx.to_pydatetime()
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        if dt > future_cutoff:
            dropped_future += 1
            continue
        result[dt] = (float(row["open"]), float(row["high"]),
                      float(row["low"]),  float(row["close"]),
                      float(row.get("volume", 0.0) or 0.0))
    if dropped_future:
        logger.warning(
            "  Dropped %d future bars before close - %s %s (cutoff: %s)",
            dropped_future, sym["tv_symbol"], tf_code,
            future_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        )
    return result if result else None


# â"€â"€â"€ Scan & Repair â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _scan_pair(tv, sym: dict, tf_code: str, n_bars: int,
               interval_map: dict, logger: logging.Logger) -> dict | None:
    """
    Scan one (symbol, TF) pair.
    Returns scan result dict or None on TV failure.

    Result dict keys: missing, mismatched, volume_only, extra, rate, from_dt, to_dt
    """
    tv_bars = _pull_tv_bars(tv, sym, tf_code, n_bars, interval_map, logger)
    if tv_bars is None:
        return None

    from_dt = min(tv_bars)
    to_dt   = max(tv_bars)
    db_bars = _query_db_bars(sym["symbol_id"], tf_code, from_dt, to_dt)

    missing, mismatched, volume_only, extra, rate = _compare(tv_bars, db_bars)
    return {
        "tv_bars":    tv_bars,
        "db_bars":    db_bars,
        "missing":    missing,
        "mismatched": mismatched,
        "volume_only": volume_only,
        "extra":      extra,
        "rate":       rate,
        "from_dt":    from_dt,
        "to_dt":      to_dt,
    }


def _filter_unstable_volume_mismatches(tv, sym: dict, tf_code: str, n_bars: int,
                                       interval_map: dict, mismatched: dict,
                                       logger: logging.Logger) -> dict:
    """
    TradingView historical volume for some non-crypto assets can drift between pulls.
    When a mismatch is volume-only, require a second TV pull to confirm it before
    treating it as a real DB issue. This avoids repair churn on unstable vendor data.
    """
    volume_only = {
        dt: pair for dt, pair in mismatched.items()
        if (lambda flags: (not flags[0]) and flags[1])(_mismatch_flags(pair[0], pair[1]))
    }
    if not volume_only:
        return mismatched

    second_tv = _pull_tv_bars(tv, sym, tf_code, n_bars, interval_map, logger)
    if not second_tv:
        logger.warning(
            "  %s/%s: second fetch failed - skip %d volume-only bars",
            sym["tv_symbol"], tf_code, len(volume_only),
        )
        return {dt: pair for dt, pair in mismatched.items() if dt not in volume_only}

    filtered = dict(mismatched)
    unstable = 0
    confirmed = 0
    for dt, (tv_vals, db_vals) in volume_only.items():
        second_vals = second_tv.get(dt)
        if second_vals is None:
            filtered.pop(dt, None)
            unstable += 1
            continue

        src_ohlc_bad, src_volume_bad = _mismatch_flags(tv_vals, second_vals)
        second_ohlc_bad, second_volume_bad = _mismatch_flags(second_vals, db_vals)
        if src_ohlc_bad or src_volume_bad:
            filtered.pop(dt, None)
            unstable += 1
            continue
        if second_ohlc_bad or second_volume_bad:
            filtered[dt] = (second_vals, db_vals)
            confirmed += 1
        else:
            filtered.pop(dt, None)
            unstable += 1

    if unstable:
        logger.info(
            "  %s/%s: filtered %d unstable volume-only bars; confirmed %d bars are really wrong",
            sym["tv_symbol"], tf_code, unstable, confirmed,
        )
    return filtered


def _issue_window(tf_code: str, issue_times: list[datetime]) -> tuple[datetime, datetime]:
    """
    Táº¡o cá»­a sá»• repair háº¹p quanh cÃ¡c Ä‘iá»ƒm lá»—i.
    Má»Ÿ rá»™ng thÃªm 2 bar má»—i phÃ­a Ä‘á»ƒ trÃ¡nh bá» sÃ³t bar lÃ¢n cáº­n khi TV restate.
    """
    tf_minutes = TF_MINUTES[tf_code]
    pad = timedelta(minutes=tf_minutes * 2)
    start_dt = min(issue_times) - pad
    end_dt = max(issue_times) + pad
    return start_dt, end_dt


def _choose_repair_strategy(sym: dict, tf_code: str, scan: dict) -> tuple[str, str]:
    """
    Focused repair cho loi cuc bo; safe full repull cho loi lech timeline/anchor.
    """
    tv_count = max(len(scan.get("tv_bars", {})), 1)
    n_miss = len(scan["missing"])
    n_bad = len(scan["mismatched"])
    n_extra = len(scan.get("extra", set()))
    rate = scan["rate"]

    if n_extra >= max(200, int(tv_count * 0.30)):
        return (
            "full_repull",
            f"systemic DB-only timeline contamination (miss={n_miss}, wrong={n_bad}, extra={n_extra}, rate={rate:.1%})",
        )

    if rate >= SYSTEMIC_RESET_RATE and n_miss >= max(SYSTEMIC_RESET_MISSING_FLOOR, int(tv_count * 0.15)):
        if n_extra >= max(SYSTEMIC_RESET_EXTRA_FLOOR, int(tv_count * 0.05)) or n_bad <= max(20, int(tv_count * 0.02)):
            return (
                "full_repull",
                f"systemic timeline drift (miss={n_miss}, wrong={n_bad}, extra={n_extra}, rate={rate:.1%})",
            )

    if tf_code in {"W", "D1"} and (
        n_miss >= max(20, int(tv_count * 0.20))
        or n_extra >= max(10, int(tv_count * 0.10))
    ):
        return (
            "full_repull",
            f"higher-TF drift (miss={n_miss}, wrong={n_bad}, extra={n_extra}, rate={rate:.1%})",
        )

    return "focused", "localized mismatch"


def _estimate_repair_n_bars(tf_code: str, start_dt: datetime) -> int:
    """
    Æ¯á»›c tÃ­nh sá»‘ bars cáº§n pull tá»« TradingView Ä‘á»ƒ bao phá»§ cá»­a sá»• lá»—i.
    TV API hiá»‡n chá»‰ há»— trá»£ get_hist(n_bars), nÃªn ta kÃ©o Ä‘á»§ tá»« Ä‘iá»ƒm lá»—i sá»›m nháº¥t tá»›i hiá»‡n táº¡i.
    """
    tf_minutes = max(TF_MINUTES[tf_code], 1)
    gap_minutes = max(0.0, (now_utc() - start_dt).total_seconds() / 60)
    raw_needed = math.ceil(gap_minutes / tf_minutes) + 10
    max_bars = max(CHECKER_N_BARS.get(tf_code, VERIFY_N_BARS), 600)
    return min(max(raw_needed, 20), max_bars)


def _query_bar_times(symbol_id: int, tf_code: str,
                     from_dt: datetime, to_dt: datetime) -> list[datetime]:
    """Láº¥y danh sÃ¡ch BarTime trong Fact cho má»™t cá»­a sá»• repair cá»¥ thá»ƒ."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.BarTime
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              AND  f.BarTime BETWEEN ? AND ?
            ORDER BY f.BarTime
        """, (symbol_id, tf_code, from_dt, to_dt))
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logging.getLogger(__name__).error(
            "_query_bar_times FAILED sym_id=%s tf=%s from=%s to=%s: %s",
            symbol_id, tf_code, from_dt, to_dt, e,
        )
        return []
    finally:
        if conn is not None:
            conn.close()


def _query_staging_bar_times(symbol_id: int, staging_table: str,
                             bar_times: list[datetime]) -> set[datetime]:
    """Return staged BarTime values for a controlled staging table and symbol."""
    unique_times = sorted(set(bar_times))
    if not unique_times:
        return set()

    found: set[datetime] = set()
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        for i in range(0, len(unique_times), 500):
            chunk = unique_times[i:i + 500]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"""
                SELECT BarTime
                FROM {staging_table}
                WHERE SymbolID = ?
                  AND BarTime IN ({placeholders})
                """,
                [symbol_id, *chunk],
            )
            found.update(row[0] for row in cursor.fetchall())
    except Exception as e:
        logging.getLogger(__name__).error(
            "_query_staging_bar_times FAILED sym_id=%s staging=%s: %s",
            symbol_id, staging_table, e,
        )
        return set()
    finally:
        if conn is not None:
            conn.close()
    return found


def _repair_direct_window(tv, sym: dict, tf_code: str, issue_times: list[datetime],
                          interval_map: dict, logger: logging.Logger,
                          reason: str,
                          delete_times: list[datetime] | None = None,
                          max_pull_bars: int | None = None,
                          expected_times: list[datetime] | None = None) -> bool:
    """
    Auto-repair direct TF báº±ng focused repull:
      1. Stage láº¡i Ä‘Ãºng cá»­a sá»• chá»©a Ä‘iá»ƒm lá»—i.
      2. XÃ³a bars hiá»‡n cÃ³ trong cá»­a sá»• Ä‘Ã³ khá»i Fact.
      3. ETL tá»« staging vÃ o Fact.

    max_pull_bars caps checker repair inside the already checked window. When it
    is set, this function must not fall back to FULL_N_BARS or trigger Replay.
    """
    if not issue_times:
        logger.warning("  No issue time found to repair for %s %s (%s)", sym["tv_symbol"], tf_code, reason)
        return True

    interval = interval_map[tf_code]
    staging  = TF_STAGING.get(tf_code)
    sym_id   = sym["symbol_id"]
    label    = sym["tv_symbol"]
    start_dt, end_dt = _issue_window(tf_code, issue_times)
    n_bars = _estimate_repair_n_bars(tf_code, start_dt)
    if max_pull_bars is not None:
        max_pull_bars = max(1, int(max_pull_bars))
        n_bars = min(n_bars, max_pull_bars)

    pull_attempts = [n_bars]
    if max_pull_bars is not None:
        widened = min(max_pull_bars, max(n_bars * 2, n_bars + 50))
        if widened > n_bars and widened not in pull_attempts:
            pull_attempts.append(widened)
    else:
        full_pull_n_bars = FULL_N_BARS.get(tf_code)
        if full_pull_n_bars and full_pull_n_bars > n_bars:
            widened = min(full_pull_n_bars, max(n_bars * 2, n_bars + 50))
            if widened not in pull_attempts:
                pull_attempts.append(widened)
            if full_pull_n_bars not in pull_attempts:
                pull_attempts.append(full_pull_n_bars)

    pull_logger = _make_pull_logger(logger)
    staged = -1
    actual_pull_bars = pull_attempts[0]
    for attempt_idx, attempt_n_bars in enumerate(pull_attempts, 1):
        if staging:
            delete_staging_bars(sym_id, staging)

        staged = pull_and_store(
            tv, sym, tf_code, attempt_n_bars, interval, pull_logger,
            skip_etl=True,
            allow_replay=False,
        )
        if staged >= 0:
            actual_pull_bars = attempt_n_bars
            break

        if attempt_idx < len(pull_attempts):
            logger.warning(
                "           Pull attempt %d/%d failed (%d bars) -- expanding to %d bars",
                attempt_idx, len(pull_attempts), attempt_n_bars, pull_attempts[attempt_idx],
            )
            time.sleep(TV_SLEEP_BETWEEN_CALLS)

    window_str = f"{start_dt:%H:%M}->{end_dt:%H:%M}"
    logger.info(
        "           Fix: window %s  |  pull %d bars  |  staged=%d",
        window_str, actual_pull_bars, max(staged, 0),
    )

    if staged < 0:
        logger.error("           Result: pull FAILED -- database unchanged")
        return False

    expected_replacements = sorted(set(expected_times or []))
    if expected_replacements and staging:
        staged_times = _query_staging_bar_times(sym_id, staging, expected_replacements)
        missing_replacements = [dt for dt in expected_replacements if dt not in staged_times]
        if missing_replacements:
            logger.error(
                "           Result: ABORTED -- Repair stopped before deleting database rows; "
                "replacement data incomplete (%d bars missing in staging; first: %s). "
                "Existing database data was kept. Run pipeline reload if this persists.",
                len(missing_replacements),
                missing_replacements[0].strftime("%Y-%m-%d %H:%M"),
            )
            return False

    targeted_delete_times = sorted(set(delete_times or []))
    deleted = delete_ohlcv_bars(sym_id, tf_code, targeted_delete_times) if targeted_delete_times else 0

    _MAX_ETL_RETRIES = 3
    etl_inserted = -1
    for etl_attempt in range(1, _MAX_ETL_RETRIES + 1):
        try:
            etl_inserted = run_etl_direct(sym_id, tf_code, staging)
            break
        except Exception as e:
            if etl_attempt == _MAX_ETL_RETRIES:
                logger.critical(
                    "           Result: ETL FAILED after %d attempts -- gap trong DB remains in Fact! %s %s: %s",
                    _MAX_ETL_RETRIES, label, tf_code, e,
                )
                try:
                    from data_provider.common.notifications import tg_send
                    tg_send(
                        f"ETL FAILED {label}/{tf_code} after {_MAX_ETL_RETRIES} attempts "
                        f"({reason}) - gap trong DB remains!\n<code>{e}</code>"
                    )
                except Exception:
                    pass
                return False
            logger.warning(
                "           ETL attempt %d/%d failed -- retrying: %s",
                etl_attempt, _MAX_ETL_RETRIES, e,
            )
            time.sleep(2)

    logger.info(
        "           Result: deleted=%-3d  inserted=+%-3d",
        deleted, max(etl_inserted, 0),
    )
    return True


def _repair_pair(tv, sym: dict, tf_code: str, scan: dict,
                 interval_map: dict, logger: logging.Logger,
                 repair_round: int = 0) -> bool:
    """
    Repair one (symbol, TF) pair vá»›i thá»© tá»± an toÃ n â€" trÃ¡nh máº¥t bar vÄ©nh viá»…n.

    Thá»© tá»± má»›i (safe):
      1. XÃ³a staging cÅ© (táº¡m thá»i, an toÃ n â€" khÃ´ng áº£nh hÆ°á»Ÿng Fact)
      2. KÃ©o data má»›i tá»« TV vÃ o Staging TRÆ¯á»šC (skip_etl=True â€" chÆ°a Ä‘á»¥ng Fact)
         â†’ Náº¿u bÆ°á»›c nÃ y tháº¥t báº¡i: Fact váº«n nguyÃªn váº¹n, khÃ´ng máº¥t bar nÃ o
      3. CHá»ˆ sau khi staging thÃ nh cÃ´ng: xÃ³a bars sai khá»i Fact
         â†’ Náº¿u bÆ°á»›c nÃ y tháº¥t báº¡i: staging váº«n cÃ³ data Ä‘Ãºng, cÃ³ thá»ƒ retry ETL
      4. Cháº¡y ETL staging â†’ Fact Ä‘á»ƒ insert bars má»›i (missing + Ä‘Ã£ xÃ³a á»Ÿ bÆ°á»›c 3)

    Thá»© tá»± cÅ© (nguy hiá»ƒm):
      XÃ³a Fact trÆ°á»›c â†’ kÃ©o TV sau â†’ náº¿u TV fail: máº¥t bar vÄ©nh viá»…n.

    Returns True náº¿u toÃ n bá»™ quÃ¡ trÃ¬nh thÃ nh cÃ´ng.
    """
    strategy, why = _choose_repair_strategy(sym, tf_code, scan)
    checker_n_bars = CHECKER_N_BARS.get(tf_code, VERIFY_N_BARS)

    if strategy == "full_repull":
        logger.warning(
            "  [CHECKED WINDOW REPAIR] %s/%s: %s. Checker will repair only the latest %d bars it checked. It will not reload older history or run Replay. If verification still fails, run pipeline full/scoped reload for this pair.",
            sym["tv_symbol"], tf_code, why, checker_n_bars,
        )
        checked_window_times = sorted(
            set(scan.get("tv_bars", {}).keys()) | set(scan.get("db_bars", {}).keys())
        )
        return _repair_direct_window(
            tv, sym, tf_code, checked_window_times, interval_map, logger,
            reason=f"checked-window repair ({why})",
            delete_times=list(scan.get("db_bars", {}).keys()),
            max_pull_bars=checker_n_bars,
            expected_times=list(scan.get("tv_bars", {}).keys()),
        )

    if repair_round >= 1:
        logger.warning(
            "  Retry %d for %s/%s: previous repair did not verify clean. Checker will retry inside the checked window only; deep history Replay is not used.",
            repair_round, sym["tv_symbol"], tf_code,
        )

    issue_times = sorted(
        set(scan["missing"]) | set(scan["mismatched"].keys()) | set(scan.get("extra", set()))
    )
    return _repair_direct_window(
        tv, sym, tf_code, issue_times, interval_map, logger,
        reason="missing/mismatch",
        delete_times=list(set(scan["mismatched"].keys()) | set(scan.get("extra", set()))),
        max_pull_bars=checker_n_bars,
        expected_times=list(set(scan["missing"]) | set(scan["mismatched"].keys())),
    )


def _verify_pair(tv, sym: dict, tf_code: str,
                 interval_map: dict, logger: logging.Logger,
                 n_bars: int | None = None) -> tuple[bool, float]:
    """
    Post-repair verification. Máº·c Ä‘á»‹nh dÃ¹ng CHECKER_N_BARS[tf_code] (cÃ¹ng depth vá»›i scan)
    Ä‘á»ƒ khÃ´ng bá» sÃ³t lá»—i cÅ© náº±m ngoÃ i VERIFY_N_BARS=200.
    Truyá»n n_bars=VERIFY_N_BARS Ä‘á»ƒ dÃ¹ng quick scan náº¿u cáº§n tá»‘c Ä‘á»™.
    Returns (is_clean, mismatch_rate).
    """
    bars = n_bars if n_bars is not None else CHECKER_N_BARS.get(tf_code, VERIFY_N_BARS)
    scan = _scan_pair(tv, sym, tf_code, bars, interval_map, logger)
    if scan is None:
        return False, 1.0
    return scan["rate"] == 0.0, scan["rate"]


# â"€â"€â"€ Main orchestrator â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_BOX_W = 68


def _fmt_box(lines: list[str]) -> list[str]:
    w = _BOX_W
    out = ["+" + "=" * (w - 2) + "+"]
    for ln in lines:
        body = "  " + ln
        out.append("|" + body.ljust(w - 2)[:w - 2] + "|")
    out.append("+" + "=" * (w - 2) + "+")
    return out


def _fmt_section(title: str) -> list[str]:
    w = _BOX_W
    bar = "-" * (w - 2)
    body = "  " + title
    return ["+" + bar + "+", "|" + body.ljust(w - 2)[:w - 2] + "|", "+" + bar + "+"]


def _log_box(logger: logging.Logger, lines: list[str]) -> None:
    logger.info("")
    for ln in _fmt_box(lines):
        logger.info("%s", ln)


def _log_report_block(
    logger: logging.Logger,
    title: str,
    lines: list[str],
    level: int = logging.INFO,
) -> None:
    """Write a compact operator-facing report block to checker.log."""
    logger.log(level, "")
    for ln in _fmt_section(title):
        logger.log(level, "%s", ln)
    for line in lines:
        logger.log(level, "  %s", line)


def _fmt_item_list(items: list[str], *, limit: int = 8) -> str:
    if not items:
        return "-"
    shown = items[:limit]
    text = "; ".join(shown)
    if len(items) > limit:
        text += f"; ... +{len(items) - limit} more"
    return text


def _fmt_issue_item(issue: dict) -> str:
    label = f"{issue.get('sym', '?')}/{issue.get('tf', '?')}"
    reason = issue.get("reason", "-")
    return f"{label}: {reason}"


def _make_pull_logger(logger: logging.Logger) -> logging.Logger:
    """Return a logger that suppresses INFO from pull_and_store (WS/DB detail lines)."""
    base_name = getattr(logger, "name", "checker")
    if not isinstance(base_name, str) or not base_name:
        base_name = "checker"
    quiet = logging.getLogger(base_name + "._pull")
    quiet.setLevel(logging.WARNING)
    return quiet


def _log_section(logger: logging.Logger, title: str) -> None:
    logger.info("")
    for ln in _fmt_section(title):
        logger.info("%s", ln)


def _fmt_pair_status(label: str, rate: float, n_miss: int, n_bad: int, n_extra: int,
                     n_vol: int = 0) -> str:
    text = (
        f"{label:<12} | rate={rate * 100:5.1f}%"
        f" | miss={n_miss:4d}"
        f" | ohlc={n_bad:4d}"
        f" | extra={n_extra:4d}"
    )
    if n_vol:
        text += f" | vol={n_vol:4d}"
    return text


def _configure_checker_library_logs() -> None:
    """
    Keep noisy third-party logs out of the console. The checker already emits
    its own summarized TV/repair messages.
    """
    for name in ("tvDatafeed", "tvDatafeed.main", "urllib3", "websocket"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = False
        lib_logger.setLevel(logging.CRITICAL)


def run_checker(tv, symbols: list, tfs: list, interval_map: dict,
                dry_run: bool, threshold: float,
                logger: logging.Logger,
                pending_pairs: list[tuple[dict, str]] | None = None) -> dict:
    """
    Full checker run.

    Phase 1+2: Scan all (symbol, TF) pairs. Repair failing ones. Retry up to
               MAX_REPAIR_ROUNDS times. Pairs that remain broken become
               PERSISTENT_FAILURE.
    Phase 3:   Recompute derived TFs (M10/M20/M90/H6/H8) for repaired symbols.
    Phase 4:   Return stats dict for report.
    """
    total_pairs       = len(pending_pairs) if pending_pairs is not None else len(symbols) * len(tfs)
    ok_pairs          = 0     # Clean on first scan
    repaired_pairs    = 0     # Fixed after repair
    persistent_fails: list[dict] = []
    volume_advisory_pairs = 0
    volume_advisory_bars = 0
    dry_issues:       list[dict] = []  # Populated in dry_run mode: pairs cÃ³ core issue hoáº·c rate > threshold

    repaired_sym_ids: set[int] = set()

    # Counters for rate-limit / auth protection
    tv_consecutive_fail = 0
    auth_consecutive_fail = 0

    # Thu tháº­p scan rate round 0 Ä‘á»ƒ phÃ¡t hiá»‡n DST transition
    round0_rates: dict[tuple, float] = {}   # {(tv_symbol, tf_code): rate}

    _log_report_block(
        logger,
        "CHECKER RUN CONTEXT",
        [
            f"Mode     : {'DRY RUN - scan only' if dry_run else 'LIVE REPAIR - scan, repair, verify'}",
            f"Scope    : {total_pairs} pair(s)",
            f"Threshold: {threshold * 100:.1f}%",
            f"Rounds   : up to {MAX_REPAIR_ROUNDS}",
        ],
    )

    # â"€â"€ Repair loop â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Start with all pairs; each round carries forward only the still-failing ones.
    pending: list[tuple[dict, str]] = list(pending_pairs) if pending_pairs is not None else [
        (sym, tf) for sym in symbols for tf in tfs
    ]

    for repair_round in range(MAX_REPAIR_ROUNDS):
        if not pending:
            break

        is_final_round = (repair_round == MAX_REPAIR_ROUNDS - 1)
        round_name = "INITIAL SCAN" if repair_round == 0 else f"RETRY {repair_round}"
        round_pending = len(pending)

        _log_section(logger, f"SCAN  Round {repair_round + 1}/{MAX_REPAIR_ROUNDS}  |  {len(pending)} pairs pending")

        still_failing: list[tuple[dict, str]] = []
        prev_symbol: str | None = None  # theo dÃµi symbol trÆ°á»›c Ä‘á»ƒ thÃªm sleep khi Ä‘á»•i symbol
        round_clean = 0
        round_issues = 0
        round_repaired = 0
        round_resolved = 0
        round_retry = 0
        round_failed = 0
        round_tv_empty = 0
        round_issue_labels: list[str] = []
        round_retry_labels: list[str] = []
        round_failed_labels: list[str] = []

        for idx, (sym, tf_code) in enumerate(pending, 1):
            label = f"{sym['tv_symbol']}/{tf_code}"

            if idx % 50 == 1:
                logger.info("  [%03d/%03d] %s ...", idx, len(pending), label)

            # Nghá»‰ giá»¯a cÃ¡c symbol (khÃ´ng chá»‰ giá»¯a TF) Ä‘á»ƒ giáº£m rate vá»›i TradingView.
            # 0.5s giá»¯a cÃ¡c TF lÃ  Ä‘á»§, nhÆ°ng khi chuyá»ƒn sang symbol má»›i thÃ¬ dÃ¹ng
            # sleep_for() (5s hoáº·c 10s cho GOLD) Ä‘á»ƒ trÃ¡nh bá»‹ phÃ¡t hiá»‡n lÃ  bot.
            if sym["tv_symbol"] != prev_symbol:
                if prev_symbol is not None:
                    sleep_for(sym["tv_symbol"])
                prev_symbol = sym["tv_symbol"]

            # â"€â"€ Pull & scan â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            n_bars = CHECKER_N_BARS[tf_code]
            scan   = _scan_pair(tv, sym, tf_code, n_bars, interval_map, logger)

            if scan is None:
                # TV returned nothing (error or empty)
                round_tv_empty += 1
                tv_consecutive_fail   += 1
                auth_consecutive_fail += 1

                # Throttle: too many consecutive TV failures
                if tv_consecutive_fail >= 3:
                    logger.warning(
                        "TradingView failed 3+ consecutive times - sleeping %ds to avoid rate limiting.",
                        TV_THROTTLE_SLEEP,
                    )
                    time.sleep(TV_THROTTLE_SLEEP)
                    tv_consecutive_fail = 0

                # Auth: try mid-run refresh sau 3 láº§n fail
                if auth_consecutive_fail >= 3 and not dry_run:
                    logger.warning("3+ consecutive auth failures - trying mid-run token refresh...")
                    if refresh_mid_run(tv, logger):
                        auth_consecutive_fail = 0

                # Circuit breaker: dá»«ng háº³n náº¿u auth fail liÃªn tiáº¿p quÃ¡ nhiá»u
                # Kháº£ nÄƒng IP bá»‹ block (403) â†’ tiáº¿p tá»¥c request sáº½ lÃ m tÃ¬nh tráº¡ng tá»‡ hÆ¡n
                if auth_consecutive_fail >= MAX_AUTH_CONSECUTIVE_FAIL:
                    logger.error(
                        "[CIRCUIT BREAKER] %d consecutive failures - "
                        "TradingView may be blocking this IP. Stopping checker.",
                        auth_consecutive_fail,
                    )
                    tg_send(
                        f"<b>[Checker]</b> {auth_consecutive_fail} consecutive TV failures "
                        "may indicate an IP block. Stopped automatically.\n"
                        "Wait 30 minutes or change internet connection before rerunning."
                    )
                    return {
                        "total": total_pairs,
                        "ok": ok_pairs, "repaired": repaired_pairs,
                        "failed": len(persistent_fails),
                        "failures": persistent_fails,
                        "dry_run": dry_run,
                        "volume_advisory_pairs": volume_advisory_pairs,
                        "volume_advisory_bars": volume_advisory_bars,
                        "dry_issues": dry_issues,
                        "aborted": "circuit_breaker",
                    }

                if is_final_round:
                    fail_item = {
                        "sym": sym["tv_symbol"],
                        "tf": tf_code,
                        "reason": "TV empty/error (could not verify)",
                    }
                    persistent_fails.append(fail_item)
                    round_failed += 1
                    round_failed_labels.append(_fmt_issue_item(fail_item))
                else:
                    still_failing.append((sym, tf_code))
                    round_retry += 1
                    round_retry_labels.append(f"{label}: TV empty/error")
                time.sleep(TV_SLEEP_BETWEEN_CALLS)
                continue

            # Reset consecutive-fail counters on successful TV pull
            tv_consecutive_fail   = 0
            auth_consecutive_fail = 0

            rate   = scan["rate"]
            n_miss = len(scan["missing"])
            n_bad  = len(scan["mismatched"])
            n_vol  = len(scan.get("volume_only", {}))
            n_extra = len(scan.get("extra", set()))

            if repair_round == 0 and n_vol > 0:
                volume_advisory_pairs += 1
                volume_advisory_bars += n_vol

            # Ghi láº¡i rate round 0 Ä‘á»ƒ dÃ¹ng cho DST detection sau khi loop káº¿t thÃºc
            if repair_round == 0:
                round0_rates[(sym["tv_symbol"], tf_code)] = rate

            # Missing bars / OHLC mismatch / DB-only extra bars are ground-truth
            # issues, so repair them immediately even when the overall mismatch
            # rate stays below the usual threshold. Volume-only remains advisory.
            force_repair_for_core_issue = (n_miss > 0) or (n_bad > 0) or (n_extra > 0)

            # â"€â"€ Decision â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            if rate <= threshold and not force_repair_for_core_issue:
                # Pair is clean
                round_clean += 1
                if repair_round == 0:
                    ok_pairs += 1
                    if rate <= 0 and n_vol > 0:
                        logger.debug(
                            "ADVISORY %s",
                            _fmt_pair_status(label, rate, n_miss, n_bad, n_extra, n_vol),
                        )
                    if rate > 0:
                        logger.debug("  ~ %s: %.2f%% below threshold (ok)", label, rate * 100)
                else:
                    # Was failing in a previous round, now clean
                    if dry_run:
                        ok_pairs += 1
                        logger.info("CLEAN   %s", _fmt_pair_status(label, rate, n_miss, n_bad, n_extra, n_vol))
                        continue
                    repaired_pairs += 1
                    round_resolved += 1
                    repaired_sym_ids.add(sym["symbol_id"])
                    logger.info("  [DONE ]  %s  >> RESOLVED (rate now %.2f%%)", label, rate * 100)

            elif is_final_round:
                # Last round: no more repair attempts
                round_issues += 1
                fail_item = {
                    "sym": sym["tv_symbol"],
                    "tf": tf_code,
                    "reason": f"{rate:.1%} mismatch ({n_miss} missing, {n_bad} OHLC wrong, {n_extra} extra)",
                    "missing": n_miss,
                    "ohlc": n_bad,
                    "extra": n_extra,
                    "rate": rate,
                }
                persistent_fails.append(fail_item)
                round_failed += 1
                round_failed_labels.append(_fmt_issue_item(fail_item))
                logger.error("  [FAIL ]  %s  %.1f%% mismatch -- could not be repaired after %d rounds", label, rate * 100, MAX_REPAIR_ROUNDS)

            elif not dry_run:
                # Attempt repair
                round_issues += 1
                round_issue_labels.append(
                    f"{label}: miss={n_miss}, ohlc={n_bad}, extra={n_extra}, rate={rate * 100:.2f}%"
                )
                logger.warning(
                    "  [ISSUE]  %-12s  miss=%-3d  ohlc=%-3d  extra=%-3d  rate=%.2f%%",
                    label, n_miss, n_bad, n_extra, rate * 100,
                )
                repair_ok = _repair_pair(
                    tv, sym, tf_code, scan, interval_map, logger,
                    repair_round=repair_round,
                )

                if repair_ok:
                    # Quick verify after repair
                    time.sleep(TV_SLEEP_BETWEEN_CALLS)
                    is_clean, verify_rate = _verify_pair(
                        tv, sym, tf_code, interval_map, logger
                    )
                    if is_clean:
                        repaired_pairs += 1
                        round_repaired += 1
                        repaired_sym_ids.add(sym["symbol_id"])
                        logger.info("  [DONE ]  %s  >> REPAIRED", label)
                    else:
                        logger.warning("  [RETRY]  %s  %.1f%% mismatch remains -- will retry", label, verify_rate * 100)
                        still_failing.append((sym, tf_code))
                        round_retry += 1
                        round_retry_labels.append(f"{label}: verify rate {verify_rate * 100:.2f}%")
                else:
                    still_failing.append((sym, tf_code))
                    round_retry += 1
                    round_retry_labels.append(f"{label}: repair attempt failed")

            else:
                # dry_run â€" ghi nháº­n váº¥n Ä‘á» nhÆ°ng khÃ´ng sá»­a
                round_issues += 1
                round_issue_labels.append(
                    f"{label}: miss={n_miss}, ohlc={n_bad}, extra={n_extra}, rate={rate * 100:.2f}%"
                )
                logger.warning(
                    "  [DRY  ]  %-12s  miss=%-3d  ohlc=%-3d  extra=%-3d  rate=%.2f%%  (not repaired)",
                    label, n_miss, n_bad, n_extra, rate * 100,
                )
                # Thu tháº­p vÃ o dry_issues Ä‘á»ƒ main() cÃ³ thá»ƒ há»i user sau
                dry_issues.append({
                    "sym": sym["tv_symbol"],
                    "tf":  tf_code,
                    "reason": f"{rate:.1%} mismatch ({n_miss} missing, {n_bad} OHLC wrong, {n_extra} extra)",
                    "missing": n_miss,
                    "ohlc": n_bad,
                    "extra": n_extra,
                    "rate": rate,
                })
                # KhÃ´ng tÄƒng ok_pairs â€" pair nÃ y cÃ³ váº¥n Ä‘á», khÃ´ng pháº£i "ok"

            time.sleep(TV_SLEEP_BETWEEN_CALLS)

        if round_failed:
            analysis = "Final verification still has unresolved pairs; review Failed list before rerunning."
        elif round_retry:
            analysis = "Some pairs need another pass; next round will scan only those pending pairs."
        elif dry_run and round_issues:
            analysis = "Dry-run found repair candidates; no database rows were changed."
        elif round_repaired or round_resolved:
            analysis = "Repair pass completed and verified clean for repaired/resolved pairs."
        else:
            analysis = "Round is clean; no repair action needed."

        report_level = (
            logging.ERROR if round_failed
            else logging.WARNING if round_retry or (dry_run and round_issues)
            else logging.INFO
        )
        _log_report_block(
            logger,
            f"CHECKER ROUND REPORT {repair_round + 1}/{MAX_REPAIR_ROUNDS}",
            [
                f"Round    : {round_name}",
                f"Mode     : {'DRY RUN - scan only' if dry_run else 'LIVE REPAIR - scan, repair, verify'}",
                f"Scanned  : {round_pending} pair(s)",
                f"Clean    : {round_clean} pair(s)",
                f"Issues   : {round_issues} pair(s) | {_fmt_item_list(round_issue_labels)}",
                f"Repaired : {round_repaired} pair(s) | resolved without DB write={round_resolved}",
                f"Retry    : {round_retry} pair(s) | next round={len(still_failing)} | {_fmt_item_list(round_retry_labels)}",
                f"Failed   : {round_failed} pair(s) | {_fmt_item_list(round_failed_labels)}",
                f"TV empty : {round_tv_empty} pair(s)",
                f"Analysis : {analysis}",
            ],
            level=report_level,
        )

        pending = still_failing

        # â"€â"€ DST detection sau round 0 â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        # Náº¿u >30% cáº·p H2/H3/H4 Ä‘á»u bÃ¡o rate > 50% â†’ kháº£ nÄƒng DST transition,
        # khÃ´ng pháº£i lá»—i data tháº­t â†’ bá» qua repair cho cÃ¡c TF Ä‘Ã³ hÃ´m nay.
        if repair_round == 0 and round0_rates:
            h_tfs = {"H2", "H3", "H4"}
            h_rates = [r for (_, tf), r in round0_rates.items() if tf in h_tfs]
            # Heuristic nay chi co y nghia khi quet mot tap pair du lon.
            # Neu chi chay 1 symbol/1 nhom hep, no de bao dong gia va bo qua
            # repair that su can lam.
            if len(h_rates) >= 15:
                high_count = sum(1 for r in h_rates if r > 0.5)
                dst_ratio  = high_count / len(h_rates)
                if dst_ratio > 0.3:
                    logger.warning(
                        "[DST DETECT] %.0f%% of H2/H3/H4 pairs have rate > 50%% - "
                        "possible DST transition. Skipping H2/H3/H4 repair today; rerun after 24h.",
                        dst_ratio * 100,
                    )
                    tg_send(
                        f"<b>[Checker]</b> Possible <b>DST transition</b> "
                        f"({dst_ratio:.0%} H2/H3/H4 pairs report rate >50%).\n"
                        "Skipped H2/H3/H4 repair today. Rerun after 24h."
                    )
                    # Lá»c cÃ¡c cáº·p H2/H3/H4 ra khá»i pending Ä‘á»ƒ khÃ´ng repair nháº§m
                    pending = [(s, tf) for s, tf in pending if tf not in h_tfs]

    # Any leftover pairs (shouldn't happen, but safety net)
    for sym, tf_code in pending:
        persistent_fails.append(
            {"sym": sym["tv_symbol"], "tf": tf_code,
             "reason": f"unresolved after {MAX_REPAIR_ROUNDS} rounds"}
        )

    # â"€â"€ Phase 3: Recompute derived TFs â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if repaired_sym_ids and COMPUTED_TIMEFRAMES and not dry_run:
        logger.info(
            "Recomputing derived TFs for %d repaired pairs...",
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
        "volume_advisory_pairs": volume_advisory_pairs,
        "volume_advisory_bars": volume_advisory_bars,
        "dry_issues": dry_issues,  # populated khi dry_run=True, rá»—ng khi dry_run=False
    }


# â"€â"€â"€ Problem description builder â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _build_problem_desc(issues: list[dict], threshold: float) -> str:
    """
    Build a concise repair notice from the dry_issues list.

    """
    n = len(issues)
    miss_count  = sum(1 for f in issues if "missing" in f["reason"])
    wrong_count = sum(1 for f in issues if "wrong"   in f["reason"])
    extra_count = sum(1 for f in issues if "extra"   in f["reason"])

    lines = [f"  - {n} pairs need repair (missing / OHLC wrong / extra, or mismatch > {threshold:.0%})"]

    if wrong_count:
        lines.append(f"  - {wrong_count} pairs: bars with wrong OHLCV values")

    if miss_count:
        lines.append(f"  - {miss_count} pairs: missing bars")

    if extra_count:
        lines.append(f"  - {extra_count} pairs: extra bars / wrong timeline")

    return "\n".join(lines)


# â"€â"€â"€ Discord notification / Report â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _build_report(stats: dict, start_time: datetime, auth_mode: str) -> str:
    elapsed = max(1, int((now_utc() - start_time).total_seconds() / 60))
    total   = stats["total"]
    lines   = [
        f"<b>[Checker] Completed {now_utc():%d/%m/%Y %H:%M}</b>",
        "",
        f"OK:              {stats['ok']}/{total} pairs",
        f"Repaired:        {stats['repaired']}/{total} pairs",
        f"Persistent fail: {stats['failed']}/{total} pairs",
        "",
        f"Elapsed: {elapsed} minutes | Auth: {auth_mode}",
    ]
    if stats["dry_run"]:
        lines.insert(1, "<i>(DRY-RUN - scan only, no DB writes)</i>")
    if stats.get("volume_advisory_pairs", 0):
        lines.append(
            f"Volume advisory: {stats['volume_advisory_pairs']} pairs / "
            f"{stats['volume_advisory_bars']} bars (not used for repair)"
        )
    if stats["failures"]:
        lines.append("")
        lines.append("Failed pairs:")
        for f in stats["failures"][:10]:
            lines.append(f"  - {f['sym']} / {f['tf']}: {f['reason']}")
        if len(stats["failures"]) > 10:
            lines.append(f"  ... and {len(stats['failures']) - 10} other pairs")
    return "\n".join(lines)


# â"€â"€â"€ Entry point â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _build_problem_desc_clean(issues: list[dict], threshold: float) -> str:
    """
    ASCII-safe summary for the confirmation prompt.
    """
    n = len(issues)
    miss_count = sum(1 for f in issues if "missing" in f["reason"])
    wrong_count = sum(1 for f in issues if "wrong" in f["reason"])
    extra_count = sum(1 for f in issues if "extra" in f["reason"])

    lines = [f"  - {n} pairs need repair (missing / OHLC wrong / extra, or mismatch > {threshold:.0%})"]
    if wrong_count:
        lines.append(f"  - {wrong_count} pairs: bars with wrong OHLCV values")
    if miss_count:
        lines.append(f"  - {miss_count} pairs: missing bars")
    if extra_count:
        lines.append(f"  - {extra_count} pairs: extra bars / wrong timeline")
    return "\n".join(lines)


def _build_report_clean(stats: dict, start_time: datetime, auth_mode: str) -> str:
    """Build the final report body (used for both log and Discord)."""
    elapsed = max(1, int((now_utc() - start_time).total_seconds() / 60))
    total   = stats["total"]
    ok      = stats["ok"]
    repaired = stats["repaired"]
    failed   = stats["failed"]

    dry_tag  = " (DRY-RUN)" if stats["dry_run"] else ""
    pct      = lambda n: f"{n / total * 100:.1f}%" if total else "0.0%"

    abort_reason = stats.get("aborted")
    skipped_tfs  = stats.get("skipped_tfs", [])
    vol_pairs    = stats.get("volume_advisory_pairs", 0)
    vol_bars     = stats.get("volume_advisory_bars", 0)

    if abort_reason:
        reason_map = {
            "circuit_breaker": "PARTIAL -- stopped after repeated TV/auth failures",
            "repair_lock":     "PARTIAL -- could not acquire repair lock",
        }
        status = reason_map.get(abort_reason, f"PARTIAL -- {abort_reason}")
    elif failed > 0:
        status = f"ATTENTION -- {failed} pair(s) could not be repaired"
    else:
        status = "ALL CLEAR"

    box_lines = [
        f"REPORT{dry_tag}  |  {now_utc():%Y-%m-%d %H:%M}  |  Elapsed: {elapsed} min",
        "",
        f"Total scanned  :  {total} pairs",
        f"Clean          :  {ok:4d} pairs  ({pct(ok)})",
        f"Repaired       :  {repaired:4d} pairs  ({pct(repaired)})",
        f"Failed         :  {failed:4d} pairs  ({pct(failed)})",
    ]

    dry_issues = stats.get("dry_issues", [])
    if stats["dry_run"] and dry_issues:
        miss_c  = sum(1 for f in dry_issues if "missing" in f.get("reason", ""))
        ohlc_c  = sum(1 for f in dry_issues if "wrong"   in f.get("reason", ""))
        extra_c = sum(1 for f in dry_issues if "extra"   in f.get("reason", ""))
        box_lines += [
            "",
            "Issue breakdown (dry-run, not repaired):",
            f"  Missing bars    :  {miss_c} pairs",
            f"  OHLCV mismatch  :  {ohlc_c} pairs",
            f"  Extra bars      :  {extra_c} pairs",
        ]
    elif stats["failures"]:
        miss_c  = sum(1 for f in stats["failures"] if "missing" in f.get("reason", ""))
        ohlc_c  = sum(1 for f in stats["failures"] if "wrong"   in f.get("reason", ""))
        extra_c = sum(1 for f in stats["failures"] if "extra"   in f.get("reason", ""))
        box_lines += [
            "",
            "Issue breakdown:",
            f"  Missing bars    :  {miss_c} pairs",
            f"  OHLCV mismatch  :  {ohlc_c} pairs",
            f"  Extra bars      :  {extra_c} pairs",
        ]

    if vol_pairs:
        box_lines.append(f"Volume advisory :  {vol_pairs} pairs / {vol_bars} bars  (info only)")

    if skipped_tfs:
        box_lines.append(f"Skipped TFs     :  {', '.join(skipped_tfs)}  (DST safeguard -- retry after 24h)")

    if stats["failures"]:
        box_lines.append("")
        box_lines.append("Persistent failures:")
        for f in stats["failures"][:8]:
            box_lines.append(f"  [FAIL]  {f['sym']}/{f['tf']}  {f['reason']}")
        if len(stats["failures"]) > 8:
            box_lines.append(f"  ... and {len(stats['failures']) - 8} more")

    box_lines += ["", f"Auth: {auth_mode}   |   Status: {status}"]

    # Plain-text version (used in log file)
    plain = "\n".join(box_lines)

    # Discord version wraps key fields in bold
    discord = plain.replace("ALL CLEAR", "<b>ALL CLEAR</b>").replace("ATTENTION", "<b>ATTENTION</b>")
    discord = f"<b>[Checker]</b>\n" + discord

    # Store both versions; callers use plain via _log_report and discord via tg_send
    # We return the Discord version for tg_send; _log_report strips HTML.
    return discord


def _empty_checker_stats(total_pairs: int, *, dry_run: bool = False) -> dict:
    """Create a stats dict compatible with _build_report_clean()."""
    return {
        "total": total_pairs,
        "ok": 0,
        "repaired": 0,
        "failed": 0,
        "failures": [],
        "dry_run": dry_run,
        "volume_advisory_pairs": 0,
        "volume_advisory_bars": 0,
        "dry_issues": [],
        "skipped_tfs": [],
    }


def _merge_checker_stats(dest: dict, src: dict, *, include_advisory: bool = True) -> None:
    """Merge compatible checker stats into dest."""
    dest["ok"] += int(src.get("ok", 0) or 0)
    dest["repaired"] += int(src.get("repaired", 0) or 0)
    dest["failed"] += int(src.get("failed", 0) or 0)
    dest["failures"].extend(list(src.get("failures", [])))
    if include_advisory:
        dest["volume_advisory_pairs"] += int(src.get("volume_advisory_pairs", 0) or 0)
        dest["volume_advisory_bars"] += int(src.get("volume_advisory_bars", 0) or 0)
    if src.get("aborted"):
        dest["aborted"] = src["aborted"]


def _detect_dst_transition_risk(
    tv,
    symbols: list[dict],
    tfs: list[str],
    interval_map: dict,
    logger: logging.Logger,
) -> set[str]:
    """
    Run a lightweight global precheck for H2/H3/H4 before auto-mode cuon chieu.

    The existing DST heuristic only works when enough pairs are scanned together.
    Auto mode now repairs symbol-by-symbol, so we preserve safety with a short
    preflight over the hourly TFs only.
    """
    h_tfs = [tf for tf in tfs if tf in {"H2", "H3", "H4"}]
    if not h_tfs or len(symbols) * len(h_tfs) < 15:
        return set()

    # Prefer a diversified sample across asset types so the DST heuristic stays
    # representative even when SYMBOLS is grouped by market.
    grouped_symbols: dict[str, list[dict]] = {}
    asset_order: list[str] = []
    for sym in symbols:
        asset_type = str(sym.get("asset_type") or "unknown")
        if asset_type not in grouped_symbols:
            grouped_symbols[asset_type] = []
            asset_order.append(asset_type)
        grouped_symbols[asset_type].append(sym)

    sample_count = min(
        len(symbols),
        max(5, math.ceil(DST_PRECHECK_TARGET_PAIRS / len(h_tfs))),
    )
    sample_symbols: list[dict] = []
    while len(sample_symbols) < sample_count:
        appended = False
        for asset_type in asset_order:
            bucket = grouped_symbols.get(asset_type) or []
            if not bucket:
                continue
            sample_symbols.append(bucket.pop(0))
            appended = True
            if len(sample_symbols) >= sample_count:
                break
        if not appended:
            break

    sampled = len(sample_symbols) < len(symbols)
    sampled_for_ws_live = sampled and is_locked("ws_live_runtime")
    total_pairs = len(sample_symbols) * len(h_tfs)
    sampled_assets = ",".join(
        dict.fromkeys(str(sym.get("asset_type") or "unknown") for sym in sample_symbols)
    )

    _log_section(
        logger,
        "AUTO MODE PRECHECK | DST guard | "
        f"pairs={total_pairs}"
        + (" | sampled_for_ws_live" if sampled_for_ws_live else "")
        + (" | sampled" if sampled and not sampled_for_ws_live else "")
        + f" | assets={sampled_assets}",
    )

    rates: list[float] = []
    pair_idx = 0
    sampled_labels: list[str] = []
    for sym_idx, sym in enumerate(sample_symbols, 1):
        if sym_idx > 1:
            sleep_for(sym["tv_symbol"])
        for tf_code in h_tfs:
            pair_idx += 1
            sampled_labels.append(f"{sym['tv_symbol']}/{tf_code}")
            scan = _scan_pair(tv, sym, tf_code, CHECKER_N_BARS[tf_code], interval_map, logger)
            if scan is not None:
                rates.append(scan["rate"])
            time.sleep(TV_SLEEP_BETWEEN_CALLS)
    logger.info("  Sampled: %s", "  ".join(sampled_labels))

    if len(rates) < 15:
        return set()

    high_count = sum(1 for rate in rates if rate > 0.5)
    dst_ratio = high_count / len(rates)
    if dst_ratio <= 0.3:
        return set()

    logger.warning(
        "[DST DETECT] %.0f%% of H2/H3/H4 pairs have mismatch >50%% -- possible DST shift."
        " H2/H3/H4 repair skipped today. Re-run after 24h.",
        dst_ratio * 100,
    )
    tg_send(
        f"[WARN] <b>[Checker]</b> Possible <b>DST time shift</b> detected: "
        f"{dst_ratio:.0%} of H2/H3/H4 pairs have mismatch >50%.\n"
        "Auto-repair for H2/H3/H4 is skipped today to avoid wrong fixes. "
        "Run checker again after 24h."
    )
def _run_auto_mode_symbol_rollout(
    tv,
    symbols: list[dict],
    tfs: list[str],
    interval_map: dict,
    threshold: float,
    logger: logging.Logger,
) -> dict:
    """
    Auto mode orchestration:
      - scan tá»«ng symbol khi chÆ°a giá»¯ repair lock
      - náº¿u symbol cÃ³ váº¥n Ä‘á» thÃ¬ má»›i láº¥y lock
      - rescan + repair + verify toÃ n bá»™ TF cá»§a symbol Ä‘Ã³ dÆ°á»›i lock
    """
    blocked_tfs = _detect_dst_transition_risk(tv, symbols, tfs, interval_map, logger)
    active_tfs = [tf for tf in tfs if tf not in blocked_tfs]

    overall = _empty_checker_stats(len(symbols) * len(active_tfs), dry_run=False)
    if blocked_tfs:
        overall["skipped_tfs"] = sorted(blocked_tfs)
    if not active_tfs:
        return overall

    for sym_idx, sym in enumerate(symbols, 1):
        if sym_idx > 1:
            sleep_for(sym["tv_symbol"])

        symbol_label = sym["tv_symbol"]
        _log_report_block(
            logger,
            f"CHECKER SYMBOL SCAN: {symbol_label}",
            [
                f"Progress : {sym_idx}/{len(symbols)}",
                f"Scope    : {len(active_tfs)} TF(s)",
                "Mode     : pre-scan without repair lock",
                "Action   : scan first; acquire repair locks only if issues are found",
            ],
        )
        logger.info("  [%03d/%03d] %s", sym_idx, len(symbols), sym["tv_symbol"])
        scan_stats = run_checker(
            tv,
            [sym],
            active_tfs,
            interval_map,
            dry_run=True,
            threshold=threshold,
            logger=logger,
        )
        overall["volume_advisory_pairs"] += int(scan_stats.get("volume_advisory_pairs", 0) or 0)
        overall["volume_advisory_bars"] += int(scan_stats.get("volume_advisory_bars", 0) or 0)

        if scan_stats.get("aborted"):
            overall["aborted"] = scan_stats["aborted"]
            return overall

        issues = scan_stats.get("dry_issues", []) or scan_stats.get("failures", [])
        if not issues:
            overall["ok"] += int(scan_stats.get("ok", 0) or 0)
            _log_report_block(
                logger,
                f"CHECKER SYMBOL SUMMARY: {symbol_label}",
                [
                    "Status   : CLEAN",
                    f"Clean    : {scan_stats.get('ok', 0)}/{len(active_tfs)} TF(s)",
                    "Action   : move to next symbol",
                ],
            )
            continue

        _log_report_block(
            logger,
            f"CHECKER SYMBOL SUMMARY: {symbol_label}",
            [
                "Status   : ATTENTION",
                f"Issues   : {len(issues)} TF(s)",
                f"Top      : {_fmt_item_list([_fmt_issue_item(issue) for issue in issues], limit=8)}",
                "Action   : acquire checker_repair + warehouse_maintenance locks, then repair",
            ],
            level=logging.WARNING,
        )
        logger.info("  [ISSUES] %s: %d pair(s) need repair -- acquiring lock", sym["tv_symbol"], len(issues))
        heartbeat_stop = _acquire_repair_locks(logger)
        if heartbeat_stop is None:
            _log_report_block(
                logger,
                f"CHECKER SYMBOL REPAIR REPORT: {symbol_label}",
                [
                    "Status   : ABORTED",
                    "Reason   : could not acquire checker repair locks",
                    "Action   : no database rows were changed for this symbol",
                ],
                level=logging.ERROR,
            )
            overall["aborted"] = "repair_lock"
            return overall

        try:
            time.sleep(TV_SLEEP_BETWEEN_CALLS)
            repair_stats = run_checker(
                tv,
                [sym],
                active_tfs,
                interval_map,
                dry_run=False,
                threshold=threshold,
                logger=logger,
            )
        finally:
            _release_repair_locks(heartbeat_stop, logger)

        repair_failures = repair_stats.get("failures", [])
        if repair_stats.get("aborted"):
            repair_status = f"ABORTED - {repair_stats.get('aborted')}"
            repair_level = logging.ERROR
        elif repair_failures:
            repair_status = "ATTENTION"
            repair_level = logging.ERROR
        else:
            repair_status = "OK"
            repair_level = logging.INFO
        _log_report_block(
            logger,
            f"CHECKER SYMBOL REPAIR REPORT: {symbol_label}",
            [
                f"Status   : {repair_status}",
                f"Clean    : {repair_stats.get('ok', 0)} TF(s)",
                f"Repaired : {repair_stats.get('repaired', 0)} TF(s)",
                f"Failed   : {repair_stats.get('failed', 0)} TF(s)",
                f"Failures : {_fmt_item_list([_fmt_issue_item(item) for item in repair_failures], limit=8)}",
                "Analysis : repair locks were released; ws_live can resume deferred ETL work",
            ],
            level=repair_level,
        )

        _merge_checker_stats(overall, repair_stats, include_advisory=False)
        if overall.get("aborted"):
            return overall

    return overall


def main() -> None:
    _configure_console_encoding()

    parser = argparse.ArgumentParser(
        description="SEN05 - Ground-truth data integrity checker"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan only - no DB writes, no repairs",
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
        help="Check time gaps between bars in the database only. Does not use TradingView.",
    )
    parser.add_argument(
        "--tf-check-full", action="store_true",
        help="Use with --tf-check to show short and long gap details.",
    )
    parser.add_argument(
        "--rebuild-computed", action="store_true",
        help="Delete and rebuild computed fallback timeframes when ENABLE_COMPUTED_TIMEFRAMES=1. Use --dry-run to preview.",
    )
    parser.add_argument(
        "--manual-confirm", action="store_true",
        help="Send a Discord notice before repair. Discord is one-way, so the checker will still auto-repair unless --dry-run is used.",
    )
    args = parser.parse_args()

    # â"€â"€ Setup logger â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    log_dir = LOG_DIR
    log_dir.mkdir(exist_ok=True)
    logger = setup_logger("checker", str(log_dir / "checker.log"), rotating=True)
    _configure_checker_library_logs()

    if not test_connection():
        logger.error("CRITICAL: Could not connect to the database. Checker did not start.")
        sys.exit(1)

    start_time = now_utc()

    # â"€â"€ Filter symbols / TFs â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    symbols = list(SYMBOLS)
    if args.sym:
        needle  = args.sym.upper()
        symbols = [s for s in SYMBOLS if s["tv_symbol"] == needle]
        if not symbols:
            logger.error("Symbol '%s' is not in the watch list.", args.sym)
            sys.exit(1)

    # --tf-check / --rebuild-computed operate across all 15 TFs, not just TFS_TO_CHECK
    db_only_mode = args.tf_check or args.rebuild_computed

    tfs = list(TFS_TO_CHECK)
    if args.tf:
        tf = args.tf.upper()
        if not db_only_mode and tf not in TFS_TO_CHECK:
            logger.error("TF '%s' is not in the checker TF list (%s).", args.tf, ",".join(TFS_TO_CHECK))
            sys.exit(1)
        tfs = [tf]

    # â"€â"€ C-O continuity check (DB-only, runs before TradingView connection) â"€â"€â"€â"€â"€
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

    # â"€â"€ Interval gap check (DB-only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    tf_gap_issues: list[dict] = []
    if args.tf_check:
        tf_report, has_issues, tf_gap_issues = check_interval_gaps(
            sym_filter=[args.sym] if args.sym else None,
            tf_filter=args.tf.upper() if args.tf else None,
            full=args.tf_check_full,
        )
        logger.info("\n%s", tf_report)
        tg_send(f"<b>[Checker] TF Gap Check</b>\n<pre>{tf_report}</pre>")
        tg_flush()
        if args.dry_run or not has_issues:
            sys.exit(1 if has_issues else 0)

    # â"€â"€ Rebuild computed TFs (DB-only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if args.rebuild_computed:
        cleaned = cleanup_expired()
        if cleaned:
            logger.info("Cleaned up %d expired locks before rebuilding computed TFs.", cleaned)

        heartbeat_stop: threading.Event | None = None
        try:
            if not args.dry_run:
                heartbeat_stop = _acquire_repair_locks(logger, duration_min=120)
                if heartbeat_stop is None:
                    sys.exit(1)
                logger.info("Maintenance lock acquired for computed TF rebuild.")

            rc_report = rebuild_computed_tfs(
                dry_run=args.dry_run,
                sym_filter=[args.sym] if args.sym else None,
                tf_filter=args.tf.upper() if args.tf else None,
                logger=logger,
            )
            logger.info("\n%s", rc_report)
            print(rc_report)

            if not args.dry_run:
                tick_report = _format_derived_tickcount_summary()
                continuity_report = _format_derived_continuity_summary()
                logger.info("\n%s", tick_report)
                logger.info("\n%s", continuity_report)
                print("")
                print(tick_report)
                print("")
                print(continuity_report)

            tg_send(f"<b>[Checker] Rebuild Computed TFs</b>\n<pre>{rc_report}</pre>")
            tg_flush()
            sys.exit(0)
        finally:
            if not args.dry_run:
                _release_repair_locks(heartbeat_stop, logger)

    # â"€â"€ Build Interval map (deferred import) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    interval_map = _build_interval_map()

    # â"€â"€ Dá»n dáº¹p lock cÅ© tá»« cÃ¡c láº§n cháº¡y bá»‹ crash trÆ°á»›c Ä‘Ã³ â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    cleaned = cleanup_expired()
    if cleaned:
        logger.info("Cleaned up %d expired locks.", cleaned)

    tv_job_stop: threading.Event | None = None

    def _exit(code: int) -> None:
        nonlocal tv_job_stop
        if tv_job_stop is not None:
            release_historical_job(tv_job_stop, "checker", logger)
            tv_job_stop = None
        tg_flush()
        raise SystemExit(code)

    def _yield_to_newer_historical_run(exc: HistoricalJobHandoffRequested) -> None:
        logger.info("[HANDOFF] Checker gave the history slot to a newer run: %s", exc)
        tg_send(
            "<b>[Checker]</b> Gave the TradingView history slot to a newer run.\n"
            "<i>This run stopped at a safe checkpoint to avoid a conflict.</i>"
        )
        _exit(0)

    tv_job_stop = acquire_historical_job("checker", logger, duration_min=180)
    if tv_job_stop is None:
        tg_send(
            "[WARN] <b>[Checker]</b> Could not get the TradingView history slot.\n"
            "Another heavy job (pipeline/checker) is still running or did not release the lock."
        )
        _exit(1)
    atexit.register(release_historical_job, tv_job_stop, "checker", logger)

    # â"€â"€ Auth + connection â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    dry_label = " (DRY-RUN)" if args.dry_run else ""
    tg_send(
        f"[START] <b>Checker started</b>{dry_label}\n"
        f"Scope: {len(symbols)} pairs x {len(tfs)} timeframes\n"
        f"Mismatch limit: {args.threshold * 100:.1f}%  |  "
        f"Mode: {'Scan and report' if args.dry_run else 'Scan and auto-repair'}"
    )
    tv, auth_mode = get_valid_tv_connection(logger)
    mode_label = "SCAN ONLY (dry-run)" if args.dry_run else "LIVE REPAIR"
    _log_box(logger, [
        f"CHECKER  |  {start_time:%Y-%m-%d %H:%M}  |  {len(symbols)} symbols x {len(tfs)} TFs",
        f"Mode: {mode_label}  |  Threshold: {args.threshold * 100:.1f}%  |  Auth: {auth_mode}",
        f"Scope: sym={args.sym or 'ALL'}  tf={args.tf or 'ALL'}",
    ])

    # â"€â"€ Guard: khÃ´ng cháº¡y náº¿u Ä‘ang á»Ÿ guest mode â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Guest mode chá»‰ tráº£ vá» ~500 bars thay vÃ¬ 1000-2000 â†’ checker sáº½ tháº¥y hÃ ng trÄƒm
    # bar "missing" khÃ´ng cÃ³ tháº­t â†’ kÃ­ch hoáº¡t sá»­a nháº§m â†’ PERSISTENT_FAILURE áº£o.
    if auth_mode == "guest":
        msg = (
            "[WARN] <b>[Checker]</b> Running in <b>GUEST MODE</b>. The bar limit may be lower "
            "(around 500 bars). Checker may report many false missing bars "
            "and may repair data that is not actually wrong.\n\n"
            "Update <code>TV_AUTH_TOKEN</code> or <code>TV_COOKIE</code> "
            "in <code>.env</code>, then run checker again."
        )
        logger.error("[CHECKER] Guest mode detected - stopping to avoid wrong data repair.")
        tg_send(msg)
        tg_flush()
        _exit(2)

    # â"€â"€ Cháº¿ Ä‘á»™ dry-run: scan vÃ  bÃ¡o cÃ¡o, khÃ´ng há»i xÃ¡c nháº­n â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if args.dry_run:
        try:
            stats = run_checker(
                tv, symbols, tfs, interval_map,
                dry_run=True, threshold=args.threshold, logger=logger,
            )
        except HistoricalJobHandoffRequested as exc:
            _yield_to_newer_historical_run(exc)
        report = _build_report_clean(stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.info(
            "DONE (dry-run) | clean=%d  issues=%d",
            stats["ok"], len(stats["dry_issues"]),
        )
        _exit(0)


    # â"€â"€ Phase 1: Scan (dry_run=True) â€" tÃ¬m váº¥n Ä‘á» mÃ  khÃ´ng sá»­a â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if args.tf_check and tf_gap_issues:
        logger.info("Found %d pairs with interval gaps in DB.", len(tf_gap_issues))
        if args.manual_confirm:
            logger.warning(
                "--manual-confirm no longer works because Discord webhook is one-way. "
                "Continuing with auto-repair for interval gaps."
            )
            tg_send(
                "[WARN] <b>[Checker]</b> <code>--manual-confirm</code> no longer works "
                "because Discord webhook is one-way.\n"
                f"Found {len(tf_gap_issues)} pairs with interval gaps. "
                "Continuing with <b>auto-repair</b>."
            )
            tg_flush()
        else:
            tg_send(
                f"<b>[Checker]</b> Found {len(tf_gap_issues)} pairs with interval gaps. "
                "Auto re-pulling and rebuilding the broken data parts."
            )

        heartbeat_stop = _acquire_repair_locks(logger)
        if heartbeat_stop is None:
            _exit(1)

        exit_code = 1
        try:
            try:
                gap_result = auto_repair_interval_gaps(
                    tv,
                    symbols,
                    tf_gap_issues,
                    interval_map,
                    logger,
                    tf_filter=args.tf.upper() if args.tf else None,
                )
            except HistoricalJobHandoffRequested as exc:
                _yield_to_newer_historical_run(exc)
            failure_lines = [
                f"{row['sym']}/{row['tf']}: {row['reason']}"
                for row in gap_result["failures"][:10]
            ]
            summary_lines = [
                f"Repaired: {gap_result['repaired']}",
                f"Failed: {gap_result['failed']}",
                f"Issues still found after verify: {'Yes' if gap_result['verify_has_issues'] else 'No'}",
            ]
            if failure_lines:
                summary_lines.append("Failed pairs:")
                summary_lines.extend(failure_lines)
            tg_send("\n".join(summary_lines) + "\n\n<pre>" + gap_result["verify_report"] + "</pre>")
            logger.info(
                "==== DONE | tf_gap_repaired=%d failed=%d still_has_issues=%s ====",
                gap_result["repaired"], gap_result["failed"], gap_result["verify_has_issues"],
            )
            exit_code = 1 if gap_result["failed"] > 0 or gap_result["verify_has_issues"] else 0
        finally:
            _release_repair_locks(heartbeat_stop, logger)

        _exit(exit_code)

    if not args.manual_confirm:
        _log_section(logger, "AUTO MODE | Symbol Rollout Scan + Repair")
        tg_send(
            "<b>[Checker]</b> Scanning and auto-repairing one symbol at a time.\n"
            "<i>Each bad symbol gets its own lock, then scan, repair, verify, "
            "and only then move to the next symbol.</i>"
        )
        try:
            auto_stats = _run_auto_mode_symbol_rollout(
                tv, symbols, tfs, interval_map,
                threshold=args.threshold, logger=logger,
            )
        except HistoricalJobHandoffRequested as exc:
            _yield_to_newer_historical_run(exc)
        report = _build_report_clean(auto_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.info(
            "DONE | ok=%d  repaired=%d  failed=%d  aborted=%s",
            auto_stats["ok"], auto_stats["repaired"], auto_stats["failed"],
            auto_stats.get("aborted", "no"),
        )
        exit_code = 1 if auto_stats["failed"] > 0 else 0
        if auto_stats.get("aborted") in {"circuit_breaker", "repair_lock"}:
            exit_code = 1
        _exit(exit_code)

    # --manual-confirm khÃ´ng cÃ²n hoáº¡t Ä‘á»™ng sau khi migrate sang Discord (one-way webhook).
    # Tá»± Ä‘á»™ng fall-through sang auto-repair vÃ  cáº£nh bÃ¡o rÃµ Ä‘á»ƒ user biáº¿t.
    logger.warning(
        "--manual-confirm no longer works because Discord webhook is one-way. "
        "Switching to auto-repair."
    )
    try:
        tg_send(
            "[WARN] <b>[Checker]</b> <code>--manual-confirm</code> no longer works "
            "because Discord webhook is one-way and cannot receive replies.\n"
            "Switched to <b>auto-repair</b> automatically (scan + repair)."
        )
        tg_flush()
    except Exception:
        pass

    _log_section(logger, "PHASE 1 | Scan")
    tg_send(
        "[SCAN] <b>[Checker] Phase 1/3 - Scanning data</b>\n"
        "Checking each pair against TradingView to find data issues..."
    )
    try:
        scan_stats = run_checker(
            tv, symbols, tfs, interval_map,
            dry_run=True, threshold=args.threshold, logger=logger,
        )
    except HistoricalJobHandoffRequested as exc:
        _yield_to_newer_historical_run(exc)

    issues = scan_stats.get("dry_issues", []) or scan_stats.get("failures", [])
    logger.info("Phase 1 finished: found %d issues.", len(issues))

    # â"€â"€ KhÃ´ng cÃ³ váº¥n Ä‘á» gÃ¬: bÃ¡o cÃ¡o vÃ  thoÃ¡t â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if not issues:
        report = _build_report_clean(scan_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.info("DONE | all data is clean")
        _exit(0)
        logger.info("DONE | all data is clean")
    # â"€â"€ Phase 2: Auto-confirm (manual-confirm khÃ´ng cÃ²n há»— trá»£) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    _log_section(logger, f"PHASE 2 | Auto-confirm | issues={len(issues)}")
    logger.info("Auto-confirm: repairing %d pairs.", len(issues))

    logger.info("Auto-confirm: repairing %d pairs.", len(issues))
    pending_pairs = _issues_to_pending_pairs(issues, symbols)
    if not pending_pairs:
        report = _build_report_clean(scan_stats, start_time, auth_mode)
        _log_report(report, logger)
        tg_send(report)
        tg_flush()
        logger.error("Phase 3 cancelled: could not map issues to pairs that need repair.")
        _exit(1)

    _log_section(logger, "PHASE 3 | Repair")
    if not acquire("checker_repair", duration_min=90):
        msg = "[WARN] <b>[Checker]</b> Lock is busy. Another process is repairing data. Skipping."
        logger.warning("Could not get checker_repair lock.")
        tg_send(msg)
        tg_flush()
        _exit(1)
    if not acquire("warehouse_maintenance", duration_min=90):
        release("checker_repair")
        msg = "[WARN] <b>[Checker]</b> Warehouse is busy. Pipeline or another maintenance job is running. Skipping."
        logger.warning("Could not get warehouse_maintenance lock.")
        tg_send(msg)
        tg_flush()
        _exit(1)

    exit_code = 1  # default: failure â€" sáº½ ghi Ä‘Ã¨ sau khi repair hoÃ n táº¥t
    heartbeat_stop = threading.Event()

    def _lock_heartbeat() -> None:
        while not heartbeat_stop.wait(900):
            renew("checker_repair", duration_min=90)
            renew("warehouse_maintenance", duration_min=90)

    threading.Thread(target=_lock_heartbeat, name="checker-lock-heartbeat", daemon=True).start()
    try:
        tg_send(
            f"[FIX] <b>[Checker] Phase 3/3 - Repairing {len(issues)} bad pairs</b>\n"
            "<i>WS Live pauses writes to the main data table to avoid a conflict. "
            "It will continue automatically after repair finishes.</i>"
        )
        try:
            repair_stats = run_checker(
                tv, symbols, tfs, interval_map,
                dry_run=False, threshold=args.threshold, logger=logger,
                pending_pairs=pending_pairs,
            )
        except HistoricalJobHandoffRequested as exc:
            _yield_to_newer_historical_run(exc)
        report = _build_report_clean(repair_stats, start_time, auth_mode)
        _log_report(report, logger)
        logger.info(
            "==== DONE | repaired=%d failed=%d ====",
            repair_stats["repaired"], repair_stats["failed"],
        )
        exit_code = 1 if repair_stats["failed"] > 0 else 0
    finally:
        heartbeat_stop.set()
        release("checker_repair")
        release("warehouse_maintenance")
        logger.info("Released locks.")
        tg_flush()  # always flush regardless of success or exception

    _exit(exit_code)


# =============================================================================
# C[T0] = O[T1] CONTINUITY CHECK  (DB-only, no TradingView needed)
# =============================================================================

# D1, W excluded â€" overnight/weekend gaps are expected and do not violate C=O
_CO_TF_MINUTES: dict[str, int] = {
    "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
    "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480,
}

# Static CASE expression â€" safe, no user input
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
          AND f.BarTime >= DATEADD(DAY, --, GETUTCDATE())
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
          AND f.BarTime >= DATEADD(DAY, --, GETUTCDATE())
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
    Kiá»ƒm tra C[T0] = O[T1] cho cÃ¡c bar liÃªn tiáº¿p trong cÃ¹ng session.

    Chá»‰ kiá»ƒm tra TF M5..H8 (bá» qua D1/W vÃ¬ overnight gap lÃ  bÃ¬nh thÆ°á»ng).
    "LiÃªn tiáº¿p" = DATEDIFF(MINUTE) Ä‘Ãºng báº±ng TF period.
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

    sym_phs = ",".join("-" * len(sym_ids))
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
    """Tráº£ vá» chuá»—i bÃ¡o cÃ¡o C-O continuity check (dÃ¹ng cáº£ cho log vÃ  Discord)."""
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
        lines.append(f"Result: CLEAN - all {total:,} within-session C=O (diff < 0.5%)")
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
    """Log report ra file (strip HTML tags cho dá»… Ä‘á»c)."""
    import re
    clean = re.sub(r"<[^>]+>", "", report)
    _log_box(logger, clean.splitlines())


def _acquire_repair_locks(
    logger: logging.Logger,
    duration_min: int = 90,
) -> threading.Event | None:
    """Acquire checker locks and start heartbeat. Returns stop event or None on failure."""
    if not acquire("checker_repair", duration_min=duration_min):
        msg = "[WARN] <b>[Checker]</b> Lock is busy. Another process is repairing data. Skipping."
        logger.warning("Could not get checker_repair lock.")
        tg_send(msg)
        tg_flush()
        return None
    if not acquire("warehouse_maintenance", duration_min=duration_min):
        release("checker_repair")
        msg = "[WARN] <b>[Checker]</b> Warehouse is busy. Pipeline or another maintenance job is running. Skipping."
        logger.warning("Could not get warehouse_maintenance lock.")
        tg_send(msg)
        tg_flush()
        return None

    heartbeat_stop = threading.Event()

    def _lock_heartbeat() -> None:
        while not heartbeat_stop.wait(900):
            renew("checker_repair", duration_min=duration_min)
            renew("warehouse_maintenance", duration_min=duration_min)

    threading.Thread(target=_lock_heartbeat, name="checker-lock-heartbeat", daemon=True).start()
    return heartbeat_stop


def _release_repair_locks(heartbeat_stop: threading.Event | None, logger: logging.Logger) -> None:
    """Stop heartbeat and release locks acquired for repair."""
    if heartbeat_stop is not None:
        heartbeat_stop.set()
    release("checker_repair")
    release("warehouse_maintenance")
    logger.info("Released locks.")
    tg_flush()


def _issues_to_pending_pairs(issues: list[dict], symbols: list[dict]) -> list[tuple[dict, str]]:
    """Map issue rows back to concrete (symbol_dict, tf_code) pairs for targeted rescans."""
    if not issues:
        return []

    symbol_map = {sym["tv_symbol"]: sym for sym in symbols}
    pending: list[tuple[dict, str]] = []
    seen: set[tuple[str, str]] = set()

    for issue in issues:
        tv_symbol = str(issue.get("sym", "")).strip()
        tf_code = str(issue.get("tf", "")).strip().upper()
        sym = symbol_map.get(tv_symbol)
        key = (tv_symbol, tf_code)
        if sym is None or not tf_code or key in seen:
            continue
        seen.add(key)
        pending.append((sym, tf_code))
    return pending


def _format_derived_tickcount_summary() -> str:
    """Return a compact summary of derived timeframe TickCount health."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            WITH exp AS (
                SELECT 'M10' AS Code, 2 AS ExpectedCount UNION ALL
                SELECT 'M20', 4 UNION ALL
                SELECT 'M90', 3 UNION ALL
                SELECT 'H6', 2 UNION ALL
                SELECT 'H8', 2
            )
            SELECT
                tf.Code,
                COUNT(*) AS TotalRows,
                SUM(
                    CASE WHEN ISNULL(f.TickCount, -1) <> exp.ExpectedCount THEN 1 ELSE 0 END
                ) AS BadTickRows
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            JOIN exp ON exp.Code = tf.Code
            GROUP BY tf.Code
            ORDER BY tf.Code;
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    lines = ["=== Derived TickCount Summary ==="]
    for code, total_rows, bad_rows in rows:
        lines.append(f"{code}: total={int(total_rows):,} bad_tick_rows={int(bad_rows):,}")
    return "\n".join(lines)


def _format_derived_continuity_summary() -> str:
    """Return a compact summary of derived timeframe continuity health."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            WITH tf AS (
                SELECT TimeframeID, Code, Minutes
                FROM DWH.Dim_Timeframe
                WHERE Code IN ('M10', 'M20', 'M90', 'H6', 'H8')
            ),
            seq AS (
                SELECT
                    tf.Code,
                    tf.Minutes,
                    f.SymbolID,
                    f.BarTime,
                    f.[Close],
                    LEAD(f.[Open]) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
                    ) AS NextOpen,
                    LEAD(f.BarTime) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime
                    ) AS NextBarTime
                FROM DWH.Fact_OHLCV f
                JOIN tf ON tf.TimeframeID = f.TimeframeID
            )
            SELECT
                Code,
                COUNT(*) AS CheckedTransitions,
                SUM(CASE
                        WHEN ABS(NextOpen - [Close]) / NULLIF([Close], 0) * 100 > 0.5
                        THEN 1 ELSE 0
                    END) AS FlaggedTransitions
            FROM seq
            WHERE NextOpen IS NOT NULL
              AND DATEDIFF(MINUTE, BarTime, NextBarTime) = Minutes
            GROUP BY Code
            ORDER BY Code;
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    lines = ["=== Derived Continuity Summary ==="]
    for code, checked, flagged in rows:
        lines.append(f"{code}: checked={int(checked):,} flagged={int(flagged):,}")
    return "\n".join(lines)


# =============================================================================
# INTERVAL GAP CHECK  (DB-only, no TradingView)
# =============================================================================

_NORMAL_GAP_MAX = {
    1440:  5760,   # D1: up to 4 ngay (weekend + DST)
    10080: 12240,  # W:  up to 8.5 ngay
}

_DERIVED_EXPECTED_TICKS = {
    "M10": 2,
    "M20": 4,
    "M90": 3,
    "H6": 2,
    "H8": 2,
}


def check_interval_gaps(
    sym_filter: list | None = None,
    tf_filter: str | None = None,
    full: bool = False,
) -> tuple[str, bool, list[dict]]:
    """
    Kiem tra interval gaps giua cac bar lien tiep trong Fact_OHLCV.

    Returns (report_str, has_issues, issue_pairs).
    has_issues = True neu bat ky cap (sym, tf) nao co > 1% gap sai.
    """
    from collections import Counter

    conn   = get_connection()
    cursor = conn.cursor()

    if sym_filter:
        needle_set = {s.upper() for s in sym_filter}
        config_symbol_ids = {
            sym["symbol_id"]
            for sym in SYMBOLS
            if sym["tv_symbol"].upper() in needle_set
        }
        cursor.execute("SELECT SymbolID, Symbol FROM DWH.Dim_Symbol ORDER BY Symbol")
        symbols = [
            (r[0], r[1])
            for r in cursor.fetchall()
            if r[1].upper() in needle_set or r[0] in config_symbol_ids
        ]
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
    symbol_meta = {sym["symbol_id"]: sym for sym in SYMBOLS}

    for sym_id, sym_code in symbols:
        for tf_id, tf_code, tf_min in all_tfs:
            sym_meta = symbol_meta.get(sym_id, {})
            asset_type = sym_meta.get("asset_type", "")

            if tf_code in {"D1", "W"}:
                results[(sym_code, tf_code)] = {
                    'status': 'skipped',
                    'tf_min': tf_min,
                    'symbol_id': sym_id,
                    'reason': 'session_based',
                }
                continue

            if asset_type in {"Indice", "FOREX", "Metal"} and tf_code in TF_STAGING:
                results[(sym_code, tf_code)] = {
                    'status': 'skipped',
                    'tf_min': tf_min,
                    'symbol_id': sym_id,
                    'reason': 'tv_validated_direct_tf',
                }
                continue

            if COMPUTED_TIMEFRAMES and tf_code in _DERIVED_EXPECTED_TICKS:
                expected_ticks = _DERIVED_EXPECTED_TICKS[tf_code]
                cursor.execute("""
                    SELECT TOP 500 BarTime, TickCount
                    FROM DWH.Fact_OHLCV
                    WHERE SymbolID = ? AND TimeframeID = ?
                    ORDER BY BarTime DESC
                """, (sym_id, tf_id))
                rows = sorted([(r[0], r[1]) for r in cursor.fetchall()], key=lambda r: r[0])

                if len(rows) < 2:
                    results[(sym_code, tf_code)] = {
                        'status': 'empty',
                        'tf_min': tf_min,
                        'symbol_id': sym_id,
                    }
                    continue

                issue_rows = [
                    {
                        "prev_bar": rows[idx - 1][0] if idx > 0 else bar_time,
                        "next_bar": bar_time,
                        "gap": int(tick_count or 0),
                    }
                    for idx, (bar_time, tick_count) in enumerate(rows)
                    if int(tick_count or 0) != expected_ticks
                ]
                wrong = len(issue_rows)
                pct = wrong / len(rows) * 100
                results[(sym_code, tf_code)] = {
                    'status': 'issues' if wrong > 0 else 'ok',
                    'tf_min': tf_min,
                    'total': len(rows),
                    'wrong': wrong,
                    'short_gaps': wrong,
                    'long_gaps': 0,
                    'pct': pct,
                    'top': [(f"TickCount!={expected_ticks}", wrong)] if wrong else [],
                    'issue_rows': issue_rows,
                    'symbol_id': sym_id,
                }
                continue

            cursor.execute("""
                SELECT TOP 500 BarTime
                FROM DWH.Fact_OHLCV
                WHERE SymbolID = ? AND TimeframeID = ?
                ORDER BY BarTime DESC
            """, (sym_id, tf_id))
            rows = sorted([r[0] for r in cursor.fetchall()])

            if len(rows) < 2:
                results[(sym_code, tf_code)] = {'status': 'empty', 'tf_min': tf_min, 'symbol_id': sym_id}
                continue

            gaps = [int((rows[i] - rows[i-1]).total_seconds() / 60) for i in range(1, len(rows))]
            max_gap = _NORMAL_GAP_MAX.get(tf_min, tf_min * 3)
            overnight = sym_meta.get("tv_symbol")
            overnight_mins = 0
            if asset_type in {"Indice", "FOREX", "Metal"} and overnight:
                overnight_mins = SYMBOL_OVERNIGHT_MINS.get(overnight, 0)
            if overnight_mins > 0:
                max_gap = max(max_gap, overnight_mins + tf_min)
            working    = [g for g in gaps if g <= max_gap]

            if not working:
                results[(sym_code, tf_code)] = {'status': 'ok', 'tf_min': tf_min,
                                                'total': 0, 'wrong': 0, 'pct': 0.0,
                                                'symbol_id': sym_id}
                continue

            expected_overnight_min = None
            expected_overnight_max = None
            if overnight_mins > 0:
                expected_overnight_min = max(tf_min + 1, overnight_mins - max(120, tf_min))
                expected_overnight_max = overnight_mins + tf_min

            issue_rows = []
            for i in range(1, len(rows)):
                gap_val = gaps[i - 1]
                if gap_val > max_gap or gap_val == tf_min:
                    continue

                if expected_overnight_min is not None and gap_val > tf_min:
                    if expected_overnight_min <= gap_val <= expected_overnight_max:
                        continue

                issue_rows.append({
                    "prev_bar": rows[i - 1],
                    "next_bar": rows[i],
                    "gap": gap_val,
                })

            wrong  = [r["gap"] for r in issue_rows]
            short  = [g for g in wrong if g < tf_min]
            long_  = [g for g in wrong if g > tf_min]
            pct    = len(wrong) / len(working) * 100
            top5   = Counter(wrong).most_common(5)

            results[(sym_code, tf_code)] = {
                'status':     'issues' if wrong else 'ok',
                'tf_min':     tf_min,
                'total':      len(working),
                'wrong':      len(wrong),
                'short_gaps': len(short),
                'long_gaps':  len(long_),
                'pct':        pct,
                'top':        top5,
                'issue_rows': issue_rows,
                'symbol_id':  sym_id,
            }

    conn.close()

    # â"€â"€ Format report â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
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
        skip_n  = sum(1 for _, r in tf_res if r['status'] == 'skipped')
        total_n = len(tf_res)

        lines.append(
            f"\nTF: {tf_code:<6} | OK: {ok_n} | Issues: {issue_n} | Empty: {empty_n} | Skipped: {skip_n} / Total: {total_n}"
        )

        if skip_n == total_n:
            reasons = {r.get('reason') for _, r in tf_res}
            if reasons == {"tv_validated_direct_tf"}:
                lines.append("  [SKIP] Direct TF with sessions - default checker vs TradingView already covers gaps/mismatch for this TF.")
            else:
                lines.append("  [SKIP] Session-based TF - default checker vs TradingView will cover these bars.")
            continue

        if issue_n == 0:
            lines.append(f"  [CLEAN] All {ok_n} symbols OK")
            continue

        has_issues = True
        if full:
            lines.append(f"  {'Symbol':<12} {'Bad/Total':>10} {'%Bad':>7} {'Short':>7} {'Long':>7}  Top bad gaps")
            for sc, r in sorted(tf_res, key=lambda x: -x[1].get('pct', 0)):
                if r['status'] != 'issues':
                    continue
                top_str = ', '.join([
                    f"{g}*{c}" if isinstance(g, str) else f"{g}min*{c}"
                    for g, c in r['top']
                ])
                flag = " ***SHORT***" if r['short_gaps'] > 0 else ""
                lines.append(
                    f"  {sc:<12} {r['wrong']:>5}/{r['total']:<5} {r['pct']:>6.1f}%"
                    f" {r['short_gaps']:>7}  {r['long_gaps']:>6}   {top_str}{flag}"
                )
        else:
            lines.append(f"  {'Symbol':<12} {'Bad/Total':>10} {'%Bad':>7}  Top bad gaps")
            for sc, r in sorted(tf_res, key=lambda x: -x[1].get('pct', 0)):
                if r['status'] != 'issues':
                    continue
                top_str = ', '.join([
                    f"{g}*{c}" if isinstance(g, str) else f"{g}min*{c}"
                    for g, c in r['top'][:3]
                ])
                lines.append(
                    f"  {sc:<12} {r['wrong']:>5}/{r['total']:<5} {r['pct']:>6.1f}%   {top_str}"
                )

    total_ok     = sum(1 for r in results.values() if r['status'] == 'ok')
    total_issues = sum(1 for r in results.values() if r['status'] == 'issues')
    lines.append(f"\nSummary: OK={total_ok} | Issues={total_issues} / {len(results)} pairs")

    issue_pairs = [
        {"sym": sc, "tf": tf_code, **results[(sc, tf_code)]}
        for sc, tf_code in results
        if results[(sc, tf_code)].get("status") == "issues"
    ]
    return "\n".join(lines), has_issues, issue_pairs


def _repair_interval_gap_pair(tv, sym: dict, tf_code: str, gap_issue: dict,
                              interval_map: dict, logger: logging.Logger) -> bool:
    """
    Auto-repair cho interval gaps.
    - Direct TF: focused repull quanh cÃ¡c Ä‘oáº¡n gap sai.
    - Computed TF: xÃ³a vÃ  rebuild toÃ n bá»™ TF phÃ¡i sinh cá»§a symbol Ä‘Ã³.
    """
    if tf_code in TF_STAGING:
        issue_times = sorted({
            row["prev_bar"]
            for row in gap_issue.get("issue_rows", [])
        } | {
            row["next_bar"]
            for row in gap_issue.get("issue_rows", [])
        })
        return _repair_direct_window(
            tv, sym, tf_code, issue_times, interval_map, logger,
            reason="interval-gap",
        )

    logger.info("  Rebuilding computed TF because of interval gaps - %s %s", sym["tv_symbol"], tf_code)
    deleted = delete_fact_bars(sym["symbol_id"], tf_code)
    inserted = aggregate_from_fact(sym["symbol_id"], tf_code)
    logger.info(
        "  Computed TF rebuild finished - %s %s | deleted=%d inserted=%d",
        sym["tv_symbol"], tf_code, deleted, inserted,
    )
    return inserted >= 0


def auto_repair_interval_gaps(
    tv,
    symbols: list[dict],
    gap_issues: list[dict],
    interval_map: dict,
    logger: logging.Logger,
    tf_filter: str | None = None,
) -> dict:
    """Repair all interval-gap issues and verify láº¡i báº±ng check_interval_gaps."""
    symbol_map = {sym["symbol_id"]: sym for sym in symbols}
    repaired = failed = 0
    repaired_sym_ids: set[int] = set()
    failures: list[dict] = []

    for issue in gap_issues:
        sym = symbol_map.get(issue.get("symbol_id"))
        if sym is None:
            failures.append({"sym": issue["sym"], "tf": issue["tf"], "reason": "symbol_not_found"})
            failed += 1
            continue

        ok = _repair_interval_gap_pair(tv, sym, issue["tf"], issue, interval_map, logger)
        if ok:
            repaired += 1
            repaired_sym_ids.add(sym["symbol_id"])
        else:
            failed += 1
            failures.append({"sym": issue["sym"], "tf": issue["tf"], "reason": "repair_failed"})

        time.sleep(TV_SLEEP_BETWEEN_CALLS)

    if repaired_sym_ids and COMPUTED_TIMEFRAMES:
        logger.info(
            "Rebuilding computed TFs after interval-gap repair for %d pairs...",
            len(repaired_sym_ids),
        )
        recompute_derived(repaired_sym_ids, logger)

    verify_report, verify_has_issues, _ = check_interval_gaps(
        sym_filter=[sym["tv_symbol"] for sym in symbol_map.values()] if symbol_map else None,
        tf_filter=tf_filter,
        full=False,
    )
    logger.info("\n%s", verify_report)
    return {
        "repaired": repaired,
        "failed": failed,
        "failures": failures,
        "verify_has_issues": verify_has_issues,
        "verify_report": verify_report,
    }


# =============================================================================
# REBUILD COMPUTED TFS  (DB-only, no TradingView)
# =============================================================================

_COMPUTED_TFS = [tf_code for tf_code, _source_table in COMPUTED_TIMEFRAMES]


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

    if not _COMPUTED_TFS:
        return (
            "[Rebuild] Computed timeframe fallback is disabled "
            "(ENABLE_COMPUTED_TIMEFRAMES=0). All 15 timeframes are configured as direct TradingView pulls."
        )

    target_tfs = _COMPUTED_TFS
    if tf_filter:
        if tf_filter not in _COMPUTED_TFS:
            return f"[Rebuild] TF '{tf_filter}' is not a computed TF. Supported: {_COMPUTED_TFS}"
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
        f"SELECT Code, TimeframeID FROM DWH.Dim_Timeframe WHERE Code IN ({','.join('-' * len(target_tfs))})",
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
                        logger.warning("Error deleting data before computed TF rebuild %s %s: %s", sym_name, tf_code, exc)
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
        lines.append("Run again with --rebuild-computed (without --dry-run) to apply changes.")

    return "\n".join(lines)


if __name__ == "__main__":
    import traceback as _tb_mod
    try:
        main()
    except KeyboardInterrupt:
        tg_send("[WARN] <b>[Checker] Stopped manually (Ctrl+C)</b>")
        tg_flush()
        sys.exit(130)
    except Exception as _exc:
        _tb = _tb_mod.format_exc()
        tg_send(
            f"[ERROR] <b>[Checker] Crashed unexpectedly</b>\n"
            f"{type(_exc).__name__}: {_exc}\n"
            f"<pre>{_tb[-800:]}</pre>"
        )
        tg_flush()
        sys.exit(1)
