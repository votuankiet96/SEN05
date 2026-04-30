"""Bộ adapter dữ liệu thị trường dùng chung cho research, scanner và backtest.

1. Mục đích tổng quan
---------------------
File này là "cửa vào dữ liệu" của phần `core_python/shared`. Các strategy,
scanner, chart dashboard và backtest engine không nên tự viết SQL hoặc gọi thẳng
vào tầng dữ liệu cũ `modules.*`. Thay vào đó, chúng gọi các hàm trong file này
để nhận dữ liệu theo đúng schema mà từng mục đích sử dụng cần.

Bài toán file này giải quyết:
- Gom toàn bộ logic lấy dữ liệu OHLCV về một nơi dễ kiểm soát.
- Chuẩn hóa output cho từng use case: scanner, chart, backtest.
- Chặn sớm dữ liệu bẩn trước khi đưa vào execution/backtest.
- Giảm rủi ro khi thay đổi database hoặc `modules.data_loader`, vì các phần còn
  lại chỉ phụ thuộc vào contract ổn định tại đây.

2. Diễn giải từng nhóm hàm
--------------------------
Các hàm trong file được chia thành 2 nhóm chính:
- Nhóm "data quality gate": `DataQualityError`, `validate_backtest_data()`.
- Nhóm "loader": `load_scan_ohlcv()`, `load_chart_candles()`,
  `load_backtest_ohlcv()`, `load_backtest_ohlcv_full()`.

3. Luồng hoạt động tổng quát
----------------------------
Scanner/chart:
1. Code bên ngoài gọi `load_scan_ohlcv()` hoặc `load_chart_candles()`.
2. Hàm trong file này chuyển tiếp sang `modules.data_loader`.
3. Dữ liệu được trả về theo đúng schema dành cho scanner hoặc chart.

Backtest:
1. Backtest runner gọi `load_backtest_ohlcv()`.
2. Hàm mở kết nối SQL bằng `get_connection()`.
3. Query dữ liệu từ `DWH.Fact_OHLCV`, lọc theo `symbol_id` và `tf`.
4. Ép kiểu các cột giá/volume sang numeric, sort theo thời gian tăng dần.
5. Nếu bật `validate=True`, dữ liệu đi qua `validate_backtest_data()`.
6. Nếu dữ liệu đạt chuẩn, trả `DataFrame` cho execution engine.
7. Nếu dữ liệu rỗng hoặc không đủ chất lượng, ném `DataQualityError`.

4. Điểm kỹ thuật đáng chú ý
---------------------------
File này dùng kiểu thiết kế adapter/facade: che giấu chi tiết nguồn dữ liệu thật
ở phía sau một API ổn định. Nhờ vậy strategy không bị phụ thuộc trực tiếp vào
SQL schema hoặc module loader cũ.

Mỗi loader có contract riêng:
- `load_scan_ohlcv()` trả dữ liệu lowercase, phù hợp scanner/research.
- `load_chart_candles()` trả dữ liệu dạng chart contract, phù hợp dashboard.
- `load_backtest_ohlcv()` trả dữ liệu có `DatetimeIndex`, lowercase OHLCV, phù
  hợp backtest/execution.

Lưu ý thay đổi:
Mọi thay đổi schema tại file này có thể ảnh hưởng dây chuyền đến signal
generation, scanner, backtest, walk-forward test và portfolio aggregation.
"""

from __future__ import annotations

import logging

import pandas as pd

from core_python.shared.sessions import detect_time_gaps, normalize_datetime_index_utc
from modules.data_loader import (
    load_candles as _load_chart_candles,
    load_ohlcv as _load_scan_ohlcv,
    load_symbols,
    load_timeframes,
)
from modules.db_connector import get_connection

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA QUALITY GATE
# ─────────────────────────────────────────────────────────────────────────────

class DataQualityError(ValueError):
    """Lỗi riêng cho trường hợp dữ liệu không đủ điều kiện để backtest an toàn.

    Làm gì
    ------
    `DataQualityError` được ném khi dữ liệu đầu vào cho backtest bị rỗng, quá ít,
    hoặc sau khi clean không còn đủ số bar tối thiểu.

    Input / output
    --------------
    Class này không nhận input riêng ngoài message lỗi khi được khởi tạo. Nó kế
    thừa từ `ValueError`, nên có thể được bắt chung như lỗi giá trị không hợp lệ,
    hoặc bắt riêng để phân biệt lỗi dữ liệu với lỗi strategy/execution.

    Tại sao cần nó
    --------------
    Backtest trên dữ liệu xấu rất nguy hiểm vì có thể tạo ra kết quả sai nhưng
    nhìn vẫn hợp lệ. Exception riêng giúp dừng sớm và chỉ rõ nguyên nhân nằm ở
    data pipeline.
    """


def validate_backtest_data(
    df: pd.DataFrame,
    *,
    symbol_id: int | None = None,
    tf: str = "H4",
    min_bars: int = 100,
    fix_inplace: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Kiểm tra và làm sạch dữ liệu OHLCV trước khi đưa vào backtest.

    Làm gì
    ------
    Hàm này đóng vai trò "cổng kiểm định chất lượng dữ liệu". Nó nhận một
    `DataFrame` OHLCV, kiểm tra các lỗi phổ biến rồi loại bỏ những dòng có thể
    làm sai kết quả backtest:

    - Timestamp bị trùng: giữ dòng đầu tiên, bỏ các dòng sau.
    - Giá trị `NaN` trong các cột giá `open`, `high`, `low`, `close`: bỏ dòng.
    - Bar phi lý như `high < low`, hoặc `close` nằm ngoài vùng `[low, high]`: bỏ
      dòng.
    - Index chưa theo thứ tự thời gian tăng dần: sort lại.
    - Số bar cuối cùng nhỏ hơn `min_bars`: dừng bằng `DataQualityError`.

    Nhận input gì
    -------------
    `df`
        `pd.DataFrame` chứa dữ liệu nến. Kỳ vọng index là thời gian
        (`DatetimeIndex`) và các cột giá dùng chữ thường: `open`, `high`, `low`,
        `close`. Nếu có `volume`, hàm vẫn giữ lại nhưng phần check NaN hiện tập
        trung vào 4 cột giá.

    `symbol_id`
        ID symbol dùng để ghi log. Tham số này không ảnh hưởng logic clean, chỉ
        giúp đọc log biết dữ liệu của symbol nào đang lỗi.

    `tf`
        Timeframe, ví dụ `"H4"`, cũng dùng trong log/error message.

    `min_bars`
        Số bar tối thiểu cần còn lại sau khi clean. Backtest với quá ít bar
        thường không có ý nghĩa thống kê, nên hàm sẽ ném lỗi nếu không đạt.

    `fix_inplace`
        Nếu `True`, trả về dữ liệu đã loại bỏ dòng lỗi. Nếu `False`, mục đích là
        lấy report nhưng trả lại `df` gốc ở cuối hàm.

    Trả output gì
    -------------
    Trả về tuple `(df_clean, report)`:

    - `df_clean`: dữ liệu đã được sort/clean nếu `fix_inplace=True`; hoặc `df`
      gốc nếu `fix_inplace=False`.
    - `report`: dict thống kê số dòng ban đầu, số dòng lỗi, số dòng cuối cùng và
      trạng thái `quality_ok`.

    Tại sao cần nó
    --------------
    Execution engine giả định dữ liệu đầu vào đã sạch. Nếu để NaN, timestamp
    trùng hoặc bar sai logic đi vào backtest, kết quả có thể sai âm thầm. Hàm này
    giúp phát hiện lỗi sớm tại biên dữ liệu.
    """
    label = f"symbol_id={symbol_id}, tf={tf}"

    # Bước 1: dữ liệu rỗng là lỗi nghiêm trọng, vì không có gì để backtest.
    if df is None or df.empty:
        raise DataQualityError(
            f"[{label}] DataFrame rỗng — không có dữ liệu để backtest."
        )

    # Bước 2: lưu số dòng ban đầu để tạo report, rồi đảm bảo dữ liệu chạy theo
    # chiều thời gian tăng dần. Backtest thường cần thứ tự quá khứ -> hiện tại.
    n_original = len(df)
    df_out     = df.sort_index() if not df.index.is_monotonic_increasing else df.copy()

    # Bước 3: loại timestamp trùng. Một cây nến không nên xuất hiện nhiều lần,
    # nếu không indicator và execution có thể tính trùng dữ liệu.
    n_dup = df_out.index.duplicated(keep="first").sum()
    if n_dup:
        _log.warning("[%s] Xóa %d hàng timestamp trùng lặp", label, n_dup)
        df_out = df_out[~df_out.index.duplicated(keep="first")]

    gap_report = detect_time_gaps(df_out.index, tf=tf)
    if gap_report["n_unexpected_time_gaps"]:
        _log.warning(
            "[%s] Phat hien %d khoang trong thoi gian bat thuong; gap dau tien: %s -> %s",
            label,
            gap_report["n_unexpected_time_gaps"],
            gap_report["first_unexpected_gap_start"],
            gap_report["first_unexpected_gap_end"],
        )

    # Bước 4: tìm các dòng có NaN trong cột giá. `errors="coerce"` ở loader có
    # thể biến dữ liệu không đọc được thành NaN, nên check này bắt được cả lỗi
    # kiểu dữ liệu từ database.
    ohlcv_cols = [c for c in ("open", "high", "low", "close") if c in df_out.columns]
    nan_mask   = df_out[ohlcv_cols].isna().any(axis=1)
    n_nan      = int(nan_mask.sum())
    if n_nan:
        _log.warning("[%s] Xóa %d hàng có NaN trong OHLCV", label, n_nan)
        if fix_inplace:
            df_out = df_out[~nan_mask]

    # Bước 5: kiểm tra logic cơ bản của một cây nến. `high` phải lớn hơn hoặc
    # bằng `low`; nếu có `close` thì `close` phải nằm trong vùng `[low, high]`.
    n_invalid = 0
    if "high" in df_out.columns and "low" in df_out.columns:
        bad_bar = df_out["high"] < df_out["low"]
        if "open" in df_out.columns:
            bad_bar |= (df_out["open"] > df_out["high"]) | (df_out["open"] < df_out["low"])
        if "close" in df_out.columns:
            bad_bar |= (df_out["close"] > df_out["high"]) | (df_out["close"] < df_out["low"])
        n_invalid = int(bad_bar.sum())
        if n_invalid:
            _log.warning("[%s] Xóa %d bar có high < low (dữ liệu hỏng)", label, n_invalid)
            if fix_inplace:
                df_out = df_out[~bad_bar]

    # Bước 6: tổng hợp report chất lượng dữ liệu sau khi clean.
    n_final   = len(df_out)
    quality_ok = n_final >= min_bars

    report = {
        "symbol_id":       symbol_id,
        "tf":              tf,
        "n_original":      n_original,
        "n_nan_dropped":   n_nan,
        "n_dup_dropped":   n_dup,
        "n_invalid_bars":  n_invalid,
        "n_final":         n_final,
        "quality_ok":      quality_ok,
        **gap_report,
    }

    # Bước 7: nếu dữ liệu còn lại quá ít thì dừng hẳn. Đây là lỗi cần sửa ở data
    # pipeline hoặc ở tham số load data, không nên để backtest chạy tiếp.
    if not quality_ok:
        raise DataQualityError(
            f"[{label}] Chỉ còn {n_final} bars sau khi clean, cần ít nhất {min_bars}. "
            f"Kiểm tra data pipeline hoặc tăng max_bars."
        )

    return (df_out if fix_inplace else df), report


def load_scan_ohlcv(
    symbol_id: int,
    n_bars: int,
    tf_code: str = "H4",
    warmup: int = 200,
    handle_missing: str = "flag",
) -> pd.DataFrame:
    """Load dữ liệu OHLCV dạng lowercase cho scanner và visual research.

    Làm gì
    ------
    Hàm này gọi lại `_load_scan_ohlcv`, tức `modules.data_loader.load_ohlcv`,
    nhưng bọc nó sau một tên hàm ổn định trong `core_python/shared/data.py`.

    Nhận input gì
    -------------
    `symbol_id`
        ID của symbol trong database/dimension table.

    `n_bars`
        Số lượng bar chính muốn lấy.

    `tf_code`
        Timeframe code, mặc định `"H4"`.

    `warmup`
        Số bar lấy thêm để phục vụ indicator cần dữ liệu khởi động.

    `handle_missing`
        Cách xử lý dữ liệu thiếu do loader gốc hỗ trợ, ví dụ `"flag"`.

    Trả output gì
    -------------
    `pd.DataFrame` dạng scanner-friendly, thường dùng tên cột lowercase và giữ
    `BarTime` như một cột thường thay vì bắt buộc làm index.

    Tại sao cần nó
    --------------
    Scanner và research cần dữ liệu nhanh, schema đơn giản, nhưng không nên phụ
    thuộc trực tiếp vào `modules.data_loader`. Adapter này tạo một điểm gọi ổn
    định để sau này có thể đổi backend dữ liệu mà ít ảnh hưởng strategy.
    """
    return _load_scan_ohlcv(
        symbol_id=symbol_id,
        n_bars=n_bars,
        tf_code=tf_code,
        warmup=warmup,
        handle_missing=handle_missing,
    )


def load_chart_candles(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """Load dữ liệu nến cho chart/dashboard.

    Làm gì
    ------
    Hàm này chuyển tiếp sang `_load_chart_candles`, tức
    `modules.data_loader.load_candles`, để lấy dữ liệu theo contract mà chart
    builder đang dùng.

    Nhận input gì
    -------------
    `symbol`
        Tên symbol dạng text, ví dụ `"EURUSD"` hoặc `"US30"`.

    `tf`
        Timeframe cần vẽ chart.

    `n_bars`
        Số lượng nến cần lấy.

    Trả output gì
    -------------
    `pd.DataFrame` có schema phù hợp dashboard/chart. Theo module docstring, dữ
    liệu chart có thể dùng tên cột kiểu Title-Case thay vì lowercase.

    Tại sao cần nó
    --------------
    Chart thường cần schema giàu hơn scanner/backtest, ví dụ tên cột hoặc format
    thời gian khác. Tách loader riêng giúp chart thay đổi mà không làm hỏng
    execution engine.
    """
    return _load_chart_candles(symbol=symbol, tf=tf, n_bars=n_bars)


def load_backtest_ohlcv(
    symbol_id: int,
    *,
    date_to: str | None = None,
    tf: str = "H4",
    max_bars: int = 60000,
    warmup: int = 200,
    validate: bool = True,
    min_bars: int = 100,
    source_timezone: str | None = None,
) -> pd.DataFrame:
    """Load lịch sử OHLCV từ SQL theo format chuẩn cho backtest.

    Làm gì
    ------
    Hàm này lấy dữ liệu nến từ bảng `DWH.Fact_OHLCV`, lọc theo `symbol_id` và
    timeframe `tf`, rồi chuẩn hóa thành `DataFrame` dùng được ngay cho execution
    engine.

    Khác với loader cho scanner/chart, loader này ưu tiên yêu cầu của backtest:
    index phải là thời gian, dữ liệu phải sort tăng dần, cột giá phải là numeric,
    và dữ liệu nên được validate trước khi trả ra.

    Nhận input gì
    -------------
    `symbol_id`
        ID của symbol cần backtest.

    `date_to`
        Nếu truyền vào, chỉ lấy các bar có `BarTime <= date_to`. Tham số này hữu
        ích khi chạy walk-forward, rolling window hoặc backtest đến một mốc thời
        gian cụ thể.

    `tf`
        Timeframe code, mặc định `"H4"`.

    `max_bars`
        Số bar chính cần lấy cho backtest.

    `warmup`
        Số bar lấy thêm để indicator có đủ dữ liệu khởi động. Tổng số bar query
        là `max_bars + warmup`.

    `validate`
        Nếu `True`, gọi `validate_backtest_data()` trước khi trả kết quả. Nếu dữ
        liệu rỗng hoặc không đủ chất lượng, hàm ném `DataQualityError`.

    `min_bars`
        Số bar tối thiểu yêu cầu sau bước validate.

    Trả output gì
    -------------
    `pd.DataFrame` với:

    - `BarTime` làm `DatetimeIndex`.
    - Cột `open`, `high`, `low`, `close`, `volume`.
    - Index sort theo chiều thời gian tăng dần.
    - Các cột giá/volume đã được ép sang numeric.

    Tại sao cần nó
    --------------
    Execution/backtest cần dữ liệu rất chặt chẽ. Nếu dùng nhầm schema của scanner
    hoặc chart, engine có thể sai silently. Loader riêng giúp backtest luôn nhận
    đúng format và luôn có bước kiểm tra chất lượng dữ liệu.
    """
    # Lấy thêm `warmup` để indicator như MA/ATR/RSI có đủ lịch sử khởi động trước
    # giai đoạn backtest chính. Nếu warmup âm, ép về 0 để tránh giảm số bar.
    total = max_bars + max(int(warmup), 0)

    conn = get_connection()
    try:
        # Nếu có `date_to`, query chỉ lấy dữ liệu đến mốc thời gian đó. Dùng
        # parameter `?` cho input động để tránh ghép trực tiếp giá trị vào SQL.
        date_clause = "AND f.BarTime <= ?" if date_to else ""
        query = f"""
            SELECT TOP ({total})
                   f.BarTime,
                   f.[Open]  AS [open],
                   f.High    AS [high],
                   f.Low     AS [low],
                   f.[Close] AS [close],
                   f.Volume  AS [volume]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              {date_clause}
            ORDER  BY f.BarTime DESC
        """
        params = (symbol_id, tf, date_to) if date_to else (symbol_id, tf)
        # `parse_dates` biến BarTime thành datetime, `index_col` đặt BarTime làm
        # index ngay khi đọc. Đây là format tự nhiên nhất cho backtest theo thời
        # gian.
        df = pd.read_sql(
            query,
            conn,
            params=params,
            parse_dates=["BarTime"],
            index_col="BarTime",
        )
    finally:
        # Luôn đóng connection kể cả khi query lỗi, tránh rò rỉ kết nối SQL.
        conn.close()

    # Không có dữ liệu là lỗi rõ ràng khi validate=True. Nếu caller chủ động tắt
    # validate, trả DataFrame rỗng để họ tự xử lý.
    if df.empty:
        if validate:
            raise DataQualityError(
                f"[symbol_id={symbol_id}, tf={tf}] Không có dữ liệu — "
                "kiểm tra SymbolID và data pipeline."
            )
        return df

    # Ép tất cả cột giá/volume sang số. Giá trị không ép được sẽ thành NaN và bị
    # `validate_backtest_data()` bắt ở bước sau.
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # SQL lấy TOP theo thứ tự mới nhất trước để lấy đúng số bar gần nhất. Sau khi
    # đọc xong phải sort tăng dần để backtest chạy từ quá khứ đến hiện tại.
    df = df.sort_index()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None and source_timezone is None:
        _log.warning(
            "[symbol_id=%s, tf=%s] BarTime is timezone-naive and source timezone is unknown; assuming UTC.",
            symbol_id,
            tf,
        )
    df.index = normalize_datetime_index_utc(df.index, source_timezone=source_timezone)

    # Cổng chất lượng dữ liệu cuối cùng trước khi trả cho execution engine.
    if validate:
        df, _ = validate_backtest_data(df, symbol_id=symbol_id, tf=tf, min_bars=min_bars)

    return df


def load_backtest_ohlcv_full(
    symbol_id: int,
    *,
    tf: str = "H4",
    max_bars: int = 80000,
) -> pd.DataFrame:
    """Load lịch sử OHLCV dài hơn cho optimization hoặc caching.

    Làm gì
    ------
    Đây là wrapper tiện lợi quanh `load_backtest_ohlcv()`. Hàm này lấy dữ liệu từ
    đầu đến hiện tại (`date_to=None`), không cộng thêm `warmup`, và dùng
    `max_bars` mặc định lớn hơn loader backtest thông thường.

    Nhận input gì
    -------------
    `symbol_id`
        ID của symbol cần lấy lịch sử.

    `tf`
        Timeframe, mặc định `"H4"`.

    `max_bars`
        Số bar tối đa muốn lấy, mặc định `80000`.

    Trả output gì
    -------------
    `pd.DataFrame` cùng schema với `load_backtest_ohlcv()`: `DatetimeIndex` và
    các cột lowercase OHLCV.

    Tại sao cần nó
    --------------
    Optimization, parameter search hoặc cache dữ liệu thường cần lịch sử dài hơn
    một lần backtest thông thường. Wrapper này giúp caller không phải lặp lại
    cùng bộ tham số mỗi lần.
    """
    return load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=None,
        tf=tf,
        max_bars=max_bars,
        warmup=0,
    )
