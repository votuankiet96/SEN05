# =============================================================================
# config.py  —  Central configuration for the AutoTrading pipeline
# Version   : V4
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH (dành cho người vận hành, không cần biết code)
# 1) Đây là bảng điều khiển trung tâm của toàn bộ hệ thống.
# 2) Phần lớn thay đổi hàng ngày nên thực hiện ở đây, không phải trong code logic.
# 3) Vùng AN TOÀN có thể chỉnh:
#    - Thông tin kết nối SQL Server
#    - Credentials TradingView / Telegram (qua file .env)
#    - Các giá trị N_BARS_* (độ sâu lịch sử tải về)
#    - Danh sách SYMBOLS (bật/tắt từng tài sản)
# 4) Vùng NHẠY CẢM (chỉnh cẩn thận):
#    - get_historical_timeframes()
#    - COMPUTED_TIMEFRAMES
#    Mapping sai ở đây sẽ làm hỏng dữ liệu các timeframe phái sinh.
# 5) Sau mỗi thay đổi: chạy dry-run một lần trước, sau đó mới chạy thật.

# THIS IS THE ONLY FILE YOU NEED TO EDIT.
# All other scripts import from here — change settings here once,
# and every script picks up the change automatically.
#
# Credentials are loaded from .env (python-dotenv) or OS environment variables.
# Never hard-code passwords here.  See .env for the values to set.
#
# Pipeline design:
#   Historical load (run once):
#       Pull 10 TFs directly from TradingView → SEN.TF_*
#       → usp_LoadDirect → Fact_OHLCV (1:1, no aggregation)
#       Then compute 5 derived TFs via usp_AggregateFromStaging:
#         M10 (from M5), M20 (from M5), M90 (from M30),
#         H6  (from H3), H8  (from H4)
#
#   Live scheduler (run forever):
#       Every 1 minute: pull latest N bars for each of 10 direct TFs
#       → insert staging → usp_LoadDirect → Fact_OHLCV
#       Then re-aggregate the 5 derived TFs for symbols that had new bars.
#
# Scripts:
#   data_provider/01_data_pipeline.py      — historical load + daily gap fill
#   data_provider/02_gap_fill.py           — internal hole scanner & filler
#   data_provider/03_ws_live.py            — WebSocket live updater (V5 Batch mode)
#   data_provider/04_chart.py             — interactive Dash chart
#   strategies/combo/05_signal_dashboard.py — Combo signal scanner (Streamlit)
# =============================================================================

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # python-dotenv not installed — fall through to OS env vars

# -----------------------------------------------------------------------------
# 1. SQL SERVER CONNECTION
#    Windows Authentication (recommended for local machine):
#      Leave SQL_UID and SQL_PWD as empty strings ''.
#    SQL Server Authentication:
#      Fill in SQL_UID and SQL_PWD.
# -----------------------------------------------------------------------------
SQL_SERVER   = "localhost"              # or '.' or your machine name
SQL_DATABASE = "SEN05_AutoTrading"
SQL_DRIVER   = "ODBC Driver 17 for SQL Server"
SQL_UID      = os.environ.get("SQL_UID", "")   # empty = Windows Auth
SQL_PWD      = os.environ.get("SQL_PWD", "")   # empty = Windows Auth
SQL_ENCRYPT  = os.environ.get("SQL_ENCRYPT", "yes")
SQL_TRUST_SERVER_CERT = os.environ.get("SQL_TRUST_SERVER_CERT", "yes")


# -----------------------------------------------------------------------------
# 2. TRADINGVIEW LOGIN
#    Preferred method: TV_AUTH_TOKEN (works with Google/social login accounts).
#    How to get it: Log in to TradingView in any browser → F12 → Application
#    → Cookies → tradingview.com → copy the value of the "auth_token" cookie.
#    Paste it into .env as TV_AUTH_TOKEN=<value>.
#
#    Fallback: TV_USERNAME + TV_PASSWORD (only works for native TV accounts,
#    NOT for Google/Facebook/Apple login). Leave blank if using auth_token.
#
#    Guest mode (all three empty): lower bar limits, higher timeout rate.
#    Set all three in .env — never hard-code here.
# -----------------------------------------------------------------------------
TV_AUTH_TOKEN = os.environ.get("TV_AUTH_TOKEN", "")  # preferred: cookie token
TV_COOKIE     = os.environ.get("TV_COOKIE", "")      # full browser cookie string
TV_USERNAME   = os.environ.get("TV_USERNAME", "")    # fallback: native TV email
TV_PASSWORD   = os.environ.get("TV_PASSWORD", "")    # fallback: native TV password

# Discord webhook để nhận thông báo pipeline (để trống nếu không dùng)
# Lấy URL từ: Server Settings → Integrations → Webhooks → Copy Webhook URL
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


# -----------------------------------------------------------------------------
# 3. BAR COUNTS — HISTORICAL BULK LOAD  (data_provider/01_data_pipeline.py)
#    10 TFs pulled directly from TradingView. 5 computed TFs (M10/M20/M90/H6/H8)
#    dùng lại staging của source TF → không cần bar count riêng.
#    TV Premium intraday limit: ~20,000 bars per request.
#    Approximate history:
#      M5   ≈  69 days    M15  ≈ 208 days   M30  ≈ 416 days
#      M45  ≈ 625 days    H1   ≈ 2.3 yrs    H2   ≈ 4.5 yrs
#      H3   ≈ 6.8 yrs     H4   ≈ 6.8 yrs    D1   ≈ 14 yrs
#      W    ≈ 19 yrs
# -----------------------------------------------------------------------------
N_BARS_W    =  1000   # Weekly  — ~19 years
N_BARS_D1   =  5000   # Daily   — ~14 years
N_BARS_H4   = 10000   # 4-hour  — ~6.8 years  (source for H8)
N_BARS_H3   = 10000   # 3-hour  — ~6.8 years  (source for H6)
N_BARS_H2   = 10000   # 2-hour  — ~4.5 years
N_BARS_H1   = 20000   # 1-hour  — ~2.3 years
N_BARS_M45  = 10000   # 45-min  — ~1.4 years
N_BARS_M30  = 20000   # 30-min  — ~416 days   (source for M90)
N_BARS_M15  = 20000   # 15-min  — ~208 days
N_BARS_M5   = 20000   # 5-min   — ~69 days    (source for M10, M20)


# -----------------------------------------------------------------------------
# 4. BAR COUNTS — LIVE UPDATE  (data_provider/02_ws_live.py)
#    Pull chỉ N bar gần nhất per TF per minute.
#    5 bar là đủ — bar mới xuất hiện tối đa 1 lần mỗi TF period.
# -----------------------------------------------------------------------------
N_BARS_LIVE = 5


# -----------------------------------------------------------------------------
# 5. SYMBOL LIST — 37 symbols
#    symbol_id  : must match Id in dbo.Symbol (set up by SQL script)
#    tv_symbol  : exact ticker string used by tvdatafeed
#    tv_exchange: exact exchange string used by tvdatafeed  →  CAPITALCOM
#    asset_type : FOREX | Indice | Metal | Crypto
#
#    Data provider: Capital.com  (TradingView exchange code: CAPITALCOM)
#      Indices → Capital.com CFD ticker names (US500, US100, US30 …)
#      FOREX   → same ticker symbols, exchange changed to CAPITALCOM
#      GOLD    → symbol 'GOLD' on exchange CAPITALCOM  (NOT 'XAUUSD')
#      BTCUSD  → symbol 'BTCUSD' on exchange CAPITALCOM
# -----------------------------------------------------------------------------
SYMBOLS = [
    # Indices — Capital.com CFD tickers
    {"symbol_id":  2, "tv_symbol": "FR40",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # CAC 40
    {"symbol_id":  3, "tv_symbol": "DE40",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # DAX 40
    {"symbol_id":  4, "tv_symbol": "HK50",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # Hang Seng 50
    {"symbol_id":  5, "tv_symbol": "J225",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # Nikkei 225
    {"symbol_id":  6, "tv_symbol": "SP35",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # IBEX 35
    {"symbol_id":  7, "tv_symbol": "UK100", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # FTSE 100
    {"symbol_id":  8, "tv_symbol": "US500", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # S&P 500
    {"symbol_id":  9, "tv_symbol": "US100", "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # NASDAQ 100
    {"symbol_id": 10, "tv_symbol": "US30",  "tv_exchange": "CAPITALCOM", "asset_type": "Indice"},   # Dow Jones 30
    # FOREX — all use CAPITALCOM exchange
    {"symbol_id": 11, "tv_symbol": "AUDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 12, "tv_symbol": "AUDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 13, "tv_symbol": "AUDNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 14, "tv_symbol": "AUDCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 15, "tv_symbol": "AUDUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 16, "tv_symbol": "GBPAUD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 17, "tv_symbol": "GBPCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 18, "tv_symbol": "GBPJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 19, "tv_symbol": "GBPNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 20, "tv_symbol": "GBPCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 21, "tv_symbol": "GBPUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 22, "tv_symbol": "CADJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 23, "tv_symbol": "CADCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 24, "tv_symbol": "EURAUD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 25, "tv_symbol": "EURGBP", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 26, "tv_symbol": "EURCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 27, "tv_symbol": "EURJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 28, "tv_symbol": "EURNZD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 32, "tv_symbol": "EURCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 33, "tv_symbol": "EURUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 34, "tv_symbol": "NZDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 35, "tv_symbol": "NZDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 36, "tv_symbol": "NZDUSD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 37, "tv_symbol": "USDCAD", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 41, "tv_symbol": "USDJPY", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    {"symbol_id": 48, "tv_symbol": "USDCHF", "tv_exchange": "CAPITALCOM", "asset_type": "FOREX"},
    # Metal & Crypto
    {"symbol_id": 56, "tv_symbol": "GOLD",   "tv_exchange": "CAPITALCOM", "asset_type": "Metal"},
    {"symbol_id": 81, "tv_symbol": "BTCUSD", "tv_exchange": "CAPITALCOM", "asset_type": "Crypto"},
]


# -----------------------------------------------------------------------------
# 6. HISTORICAL TIMEFRAMES — pull trực tiếp từ TradingView
#    Thứ tự: TF lớn trước (nhiều lịch sử nhất) → TF nhỏ sau.
#    Import deferred để tránh circular import lúc load module.
# -----------------------------------------------------------------------------
def get_historical_timeframes():
    from tvDatafeed import Interval
    return [
        # (tv Interval,            TF code,  staging table,       n_bars)
        (Interval.in_weekly,      "W",    "SEN.TF_W",    N_BARS_W),
        (Interval.in_daily,       "D1",   "SEN.TF_D1",   N_BARS_D1),
        (Interval.in_4_hour,      "H4",   "SEN.TF_H4",   N_BARS_H4),
        (Interval.in_3_hour,      "H3",   "SEN.TF_H3",   N_BARS_H3),
        (Interval.in_2_hour,      "H2",   "SEN.TF_H2",   N_BARS_H2),
        (Interval.in_1_hour,      "H1",   "SEN.TF_H1",   N_BARS_H1),
        (Interval.in_45_minute,   "M45",  "SEN.TF_M45",  N_BARS_M45),
        (Interval.in_30_minute,   "M30",  "SEN.TF_M30",  N_BARS_M30),
        (Interval.in_15_minute,   "M15",  "SEN.TF_M15",  N_BARS_M15),
        (Interval.in_5_minute,    "M5",   "SEN.TF_M5",   N_BARS_M5),
    ]


# -----------------------------------------------------------------------------
# 7. COMPUTED TIMEFRAMES — tính toán từ staging (không pull từ TV)
#    tvDatafeed không hỗ trợ M10/M20/M90/H6/H8 natively.
#    Chạy SAU KHI source TF đã được load vào staging.
# -----------------------------------------------------------------------------
COMPUTED_TIMEFRAMES = [
    # (target_tf_code, source_staging_table)
    ("M10", "SEN.TF_M5"),    # 2 × M5
    ("M20", "SEN.TF_M5"),    # 4 × M5
    ("M90", "SEN.TF_M30"),   # 3 × M30
    ("H6",  "SEN.TF_H3"),    # 2 × H3
    ("H8",  "SEN.TF_H4"),    # 2 × H4
]


# -----------------------------------------------------------------------------
# 8. LIVE SCHEDULER — 10 TF pull trực tiếp  (data_provider/02_ws_live.py)
#    Cùng danh sách với historical, nhưng chỉ lấy N_BARS_LIVE bar gần nhất.
# -----------------------------------------------------------------------------
def get_live_timeframes():
    from tvDatafeed import Interval
    return [
        # (tv Interval,            TF code,  staging table,       n_bars)
        (Interval.in_weekly,      "W",    "SEN.TF_W",    N_BARS_LIVE),
        (Interval.in_daily,       "D1",   "SEN.TF_D1",   N_BARS_LIVE),
        (Interval.in_4_hour,      "H4",   "SEN.TF_H4",   N_BARS_LIVE),
        (Interval.in_3_hour,      "H3",   "SEN.TF_H3",   N_BARS_LIVE),
        (Interval.in_2_hour,      "H2",   "SEN.TF_H2",   N_BARS_LIVE),
        (Interval.in_1_hour,      "H1",   "SEN.TF_H1",   N_BARS_LIVE),
        (Interval.in_45_minute,   "M45",  "SEN.TF_M45",  N_BARS_LIVE),
        (Interval.in_30_minute,   "M30",  "SEN.TF_M30",  N_BARS_LIVE),
        (Interval.in_15_minute,   "M15",  "SEN.TF_M15",  N_BARS_LIVE),
        (Interval.in_5_minute,    "M5",   "SEN.TF_M5",   N_BARS_LIVE),
    ]


# -----------------------------------------------------------------------------
# 9. LOGGING
# -----------------------------------------------------------------------------
LOG_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_provider", "logs", "pipeline.log")
LOG_LEVEL = "INFO"     # DEBUG | INFO | WARNING | ERROR


# -----------------------------------------------------------------------------
# 10. CENTRALIZED TF CONSTANTS — single source of truth for all scripts
# -----------------------------------------------------------------------------

# 10 direct TFs (pulled from TradingView)
DIRECT_TFS  = {"W", "D1", "H4", "H3", "H2", "H1", "M45", "M30", "M15", "M5"}

# 5 derived TFs (computed from direct TF data)
DERIVED_TFS = {"M10", "M20", "M90", "H6", "H8"}

# Display order for all 15 TFs
TF_DISPLAY_ORDER = [
    "M5", "M10", "M15", "M20", "M30", "M45",
    "H1", "M90", "H2", "H3", "H4", "H6", "H8", "D1", "W",
]

# Derived TF → source direct TF
DERIVED_SOURCE = {
    "M10": "M5",  "M20": "M5",
    "M90": "M30", "H6":  "H3", "H8": "H4",
}

# Minutes per TF (for gap/freshness calculations)
TF_MINUTES = {
    "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30, "M45": 45,
    "M90": 90, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "D1": 1440, "W": 10080,
}

# Per-symbol overnight gap calibration (minutes).
# Used by gap_fill to set the correct hole-detection threshold per symbol.
# Value = maximum NORMAL daily overnight/session gap for that symbol.
# Gap_fill threshold = max(3×TF_minutes, per_symbol_overnight + TF_minutes).
#
# How calibrated (2026-03-09 to 2026-03-21 benchmark):
#   DJI/SPX/NDX/DAX/UKX/NI225/HSI : MaxGap=2945min (weekend only), no daily gap
#     → Capital.com CFDs trade ~22h/day; any gap >10min within weekdays is real
#   CAC40 : daily overnight gap ≈ 200 min  → threshold set to 240 min
#   IBEX35: daily overnight gap ≈ 725 min  → threshold set to 800 min
# FOREX/Metal/Crypto: handled by OVERNIGHT_GAP_MINUTES asset-type dict below.
SYMBOL_OVERNIGHT_MINS = {
    # Indices that trade ~22h/day on Capital.com — overnight gap effectively 0
    # (weekend gap is handled separately via trading_hours_in_gap)
    "US30":  0,
    "US100": 0,
    "US500": 0,
    "DE40":  0,
    "UK100": 0,
    "J225":  0,
    "HK50":  0,
    # European session-only indices — real overnight closure each day
    "FR40":  240,   # CAC40: overnight ~200 min, threshold 240 min
    "SP35":  800,   # IBEX35: overnight ~725 min, threshold 800 min
}

# TF code → staging table
TF_STAGING = {
    "W":   "SEN.TF_W",   "D1":  "SEN.TF_D1",
    "H4":  "SEN.TF_H4",  "H3":  "SEN.TF_H3",
    "H2":  "SEN.TF_H2",  "H1":  "SEN.TF_H1",
    "M45": "SEN.TF_M45", "M30": "SEN.TF_M30",
    "M15": "SEN.TF_M15", "M5":  "SEN.TF_M5",
}

# Computed TF dependency: source staging table → list of (computed_tf, staging_table)
COMPUTED_TF_DEPS = {}
for _target_tf, _source_table in COMPUTED_TIMEFRAMES:
    COMPUTED_TF_DEPS.setdefault(_source_table, []).append((_target_tf, _source_table))

# All 15 TF codes as SQL IN clause
ALL_TFS_SQL = (
    "('M5','M10','M15','M20','M30','M45','H1','M90',"
    "'H2','H3','H4','H6','H8','D1','W')"
)

# Cross-TF validation pairs: (small_tf, big_tf, bucket_minutes)
CROSS_TF_PAIRS = [
    ("M5",  "M15", 15),
    ("M5",  "M30", 30),
    ("M15", "M45", 45),
    ("M30", "H1",  60),
    ("H1",  "H2",  120),
    ("H1",  "H3",  180),
    ("H1",  "H4",  240),
]

# FIXED_H_ALIGNMENT — tạm thời xóa tất cả entries.
# GOLD: winter anchor UTC+5 (h%N==1), nhưng sau DST spring 2026 dịch → UTC+4 (h%N==0).
# BTCUSD: tương tự, DST spring 2026 làm lệch anchor từ 0 → 1.
# Validation cứng theo mùa gây drop toàn bộ bars khi DST thay đổi.
# clean_staging_transitions() trong _helpers.py vẫn bảo vệ tầng staging cho cả hai.
FIXED_H_ALIGNMENT: dict[str, dict[str, int]] = {}


# -----------------------------------------------------------------------------
# 11. CENTRALIZED TRADINGVIEW CONNECTION
# -----------------------------------------------------------------------------
def get_tv_connection():
    """
    Create and return a TvDatafeed instance using the best available auth.
    Priority: auth_token > username/password > guest mode.
    """
    from tvDatafeed import TvDatafeed
    if TV_AUTH_TOKEN:
        tv = TvDatafeed()
        tv.token = TV_AUTH_TOKEN
        return tv, "auth_token"
    if TV_USERNAME and TV_PASSWORD:
        tv = TvDatafeed(TV_USERNAME, TV_PASSWORD)
        return tv, "username/password"
    tv = TvDatafeed()
    return tv, "guest"


# -----------------------------------------------------------------------------
# 12. ASSET TYPE LOOKUP  — single-source dict, no need to loop SYMBOLS
# -----------------------------------------------------------------------------
ASSET_TYPE_MAP = {s["tv_symbol"]: s["asset_type"] for s in SYMBOLS}

# Weekend-closed asset types (no bars Sat/Sun)
WEEKEND_CLOSED = {"FOREX", "Metal", "Indice"}


# -----------------------------------------------------------------------------
# 13. TF → tvDatafeed Interval MAP  — centralised, no per-script redefinition
#     Import deferred to avoid circular / heavy import at module load.
# -----------------------------------------------------------------------------
def get_tf_interval_map():
    """Return dict {tf_code: tvDatafeed.Interval} for all 10 direct TFs."""
    from tvDatafeed import Interval
    return {
        "M5":  Interval.in_5_minute,
        "M15": Interval.in_15_minute,
        "M30": Interval.in_30_minute,
        "M45": Interval.in_45_minute,
        "H1":  Interval.in_1_hour,
        "H2":  Interval.in_2_hour,
        "H3":  Interval.in_3_hour,
        "H4":  Interval.in_4_hour,
        "D1":  Interval.in_daily,
        "W":   Interval.in_weekly,
    }
