"""
Xuất tín hiệu chiến lược ra file CSV từ terminal.

Mô tả dễ hiểu:
    File này dành cho nhu cầu xuất dữ liệu signal mà không cần mở dashboard.
    Người dùng truyền vào strategy, symbol, timeframe và khoảng thời gian
    cần lấy (--from/--to). Hệ thống sẽ tự:
    1. Lấy dữ liệu nến OHLCV từ SQL Server theo đúng khoảng thời gian đó
       (bỏ trống cả --from lẫn --to = lấy toàn bộ lịch sử có trong DP6, từ
       bar đầu tiên tới bar gần nhất).
    2. Chạy strategy tương ứng.
    3. Lọc lại những dòng thật sự có tín hiệu BUY/SELL.
    4. Ghi kết quả ra file CSV.

File này KHÔNG làm các việc sau:
    - Không hiển thị chart.
    - Không ghi ngược vào SQL Server.
    - Không publish Redis.
    - Không đặt lệnh.

    CSV hiện tại là bản tối giản, chỉ giữ các hàng có signal. Filename dùng
    hậu tố mode ngắn ở cuối:
        - nt: signal gốc của strategy, không lọc trend.
        - tf_<type>_<tf>: signal đã lọc theo trend reference.

    Schema CSV:
    - MA Cross: bartime, atr, signal
    - Combo: bartime, atr, entry, signal

    Cột "signal" trong CSV dùng đúng mã ProtoOATradeSide của cTrader
    (BUY=1, SELL=2) -- từ 2026-09-19, KHÔNG còn là quy ước nội bộ 1/-1 của
    OG (quy ước đó chỉ tồn tại trong DataFrame trước khi ra CSV, xem
    signal_rows()). VM-BO20 đọc thẳng cột này cho ctrader-cli.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from strategy_lab.src.configuration import (
    DEFAULT_SYMBOL,
    WARMUP_BARS,
    get_strategy,
    run_strategy,
    run_strategy_with_trend_reference,
    trim_to_requested_range,
)
from strategy_lab.src.db_connector import get_symbol, load_range, load_range_with_warmup

CSV_COLUMNS = ("bartime", "atr", "signal")
COMBO_CSV_COLUMNS = ("bartime", "atr", "entry", "signal")
TREND_MODE_NO_TREND = "no_trend"
TREND_MODE_FILTER = "trend_filter"
TREND_MODES = (TREND_MODE_NO_TREND, TREND_MODE_FILTER)


def csv_columns(strategy: str) -> tuple[str, ...]:
    """Return the minimal CSV schema for one strategy."""
    key = str(strategy).strip().lower()
    if key == "combo":
        return COMBO_CSV_COLUMNS
    return CSV_COLUMNS


def signal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lọc DataFrame, chỉ giữ lại những dòng có tín hiệu BUY hoặc SELL.

    signal = 1  nghĩa là BUY.
    signal = -1 nghĩa là SELL.
    signal = 0  nghĩa là không có tín hiệu, nên bị bỏ khỏi CSV.
    """
    if df.empty or "signal" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["signal"].fillna(0).astype(int).ne(0)].copy()


def signal_frame(enriched: pd.DataFrame, *, strategy: str) -> pd.DataFrame:
    """
    Dựng DataFrame đúng schema CSV (bartime/atr/entry/signal theo strategy),
    CHƯA chuyển thành chuỗi CSV -- dùng chung cho to_csv() (ghi file) và
    og_signal/redis_io/publisher.py (ghi Redis DB2 "past_signal") để 2 đường
    xuất không bao giờ lệch cột/giá trị.

    CSV/Redis chỉ lấy các dòng có BUY/SELL signal:
        - MA Cross: bartime, atr, signal
        - Combo: bartime, atr, entry, signal

    Combo dùng entry_price đã được add_combo_levels() tính từ high/low và X
    theo symbol.
    """
    rows = signal_rows(enriched)
    # "signal" là mã ProtoOATradeSide của cTrader (BUY=1, SELL=2), không phải
    # quy ước nội bộ 1/-1 -- ctrader-cli/ctrader cli simulation đọc thẳng cột
    # này. signal_rows() đã loại hết dòng signal==0 ở trên, nên .replace() chỉ
    # còn gặp 1/-1. KHÔNG làm tròn -- đây là mã phân loại, không phải số đo.
    signal = pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int)
    # atr/entry làm tròn 2 chữ số thập phân (chốt 2026-09-23) -- atr tính qua
    # Wilder smoothing đệ quy (_wilder_smooth) nên vốn có rất nhiều chữ số lẻ
    # không mang thêm thông tin thật (đã thấy thực tế 15 chữ số lẻ trên CSV/
    # Redis DB2), làm tròn ở ĐÚNG 1 chỗ này vì signal_frame() là nguồn duy
    # nhất cho cả to_csv() lẫn publisher.py -- 2 đường xuất tự động đồng bộ,
    # không cần sửa gì thêm ở nơi khác. .round(2) giữ nguyên NaN (không biến
    # NaN thành 0), không đổi hành vi to_csv()/publish_past_signals() đã có
    # với NaN.
    data = {
        # Có giây (chốt 2026-09-24) -- khớp quy ước "YYYY-MM-DD HH:MM:SS" mà
        # order_gateway/DP dùng xuyên suốt DB0/DB1 (xem
        # order_gateway/src/signal_publisher.py + live.py:_stamp() phía DP).
        # Trước đó thiếu giây (BarTime luôn đúng :00 nên KHÔNG mất thông tin
        # dữ liệu -- đã kiểm toàn bộ 17.471.791 nến trong SQL, 0 nến có giây
        # khác 0), nhưng khác quy ước hệ thống và ctrader-cli/VM-BO20 (đọc cả
        # CSV lẫn Redis DB2) rất có thể parse chuỗi theo khuôn có %S, gặp
        # chuỗi thiếu giây sẽ lỗi. Đây là dữ liệu XUẤT RA NGOÀI repo (CSV +
        # Redis DB2) nên đổi cần báo VM-BO20 trước (xem CLAUDE.md mục 7).
        "bartime": pd.to_datetime(rows["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S"),
        "atr": pd.to_numeric(rows.get("atr"), errors="coerce").round(2),
        "signal": signal.replace({-1: 2}),
    }
    key = str(strategy).strip().lower()
    if key == "combo":
        data["entry"] = pd.to_numeric(rows["entry_price"], errors="coerce").round(2)

    return pd.DataFrame(data, columns=list(csv_columns(strategy)))


def to_csv(enriched: pd.DataFrame, *, strategy: str) -> str:
    """
    Tạo nội dung CSV từ DataFrame đã chạy strategy.

    Dashboard dùng lại chính hàm này để hai đường export không lệch schema
    hay giá pending. Xem signal_frame() cho ý nghĩa từng cột.
    """
    return signal_frame(enriched, strategy=strategy).to_csv(index=False)


def parse_overrides(param_list: list[str]) -> dict[str, str]:
    """
    Chuyển danh sách tham số dạng NAME=VALUE thành dict override.

    Ví dụ command line:
        --param MA_PERIOD=25 --param FAST_MA=13

    Sẽ thành:
        {"MA_PERIOD": "25", "FAST_MA": "13"}

    Việc kiểm tra giá trị đúng/sai không làm ở đây. Mỗi strategy sẽ tự
    validate trong configuration.normalize_*_params().
    """
    overrides: dict[str, str] = {}
    for item in param_list:
        if "=" not in item:
            raise ValueError(f"Invalid --param '{item}', expected NAME=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --param '{item}', empty name.")
        overrides[key] = value.strip()
    return overrides


def default_timeframe(strategy_key: str) -> str:
    """
    Chọn timeframe mặc định khi user không truyền --tf.

    Mỗi strategy có thể có default_timeframe riêng trong config.yaml.
    Ví dụ hiện tại:
        Combo    -> H4
        MA Cross -> M30
    """
    spec = get_strategy(strategy_key)
    if spec.default_timeframe:
        return str(spec.default_timeframe).upper()
    if spec.recommended_timeframes:
        return str(spec.recommended_timeframes[0]).upper()
    raise ValueError(f"Strategy '{strategy_key}' has no default timeframe; pass --tf explicitly.")


def resolve_trend_mode(trend_mode: str = "", *, use_trend: bool | None = None) -> str:
    """
    Chuẩn hóa mode export trend.

    `--trend` cũ vẫn là shortcut cho `--trend-mode trend_filter`; đường mới nên
    dùng `--trend-mode` để tên gọi khớp với filename.
    """
    raw = str(trend_mode or "").strip().lower().replace("-", "_")
    aliases = {
        "": TREND_MODE_FILTER if use_trend else TREND_MODE_NO_TREND,
        "none": TREND_MODE_NO_TREND,
        "off": TREND_MODE_NO_TREND,
        "false": TREND_MODE_NO_TREND,
        "0": TREND_MODE_NO_TREND,
        TREND_MODE_NO_TREND: TREND_MODE_NO_TREND,
        "trend": TREND_MODE_FILTER,
        "filter": TREND_MODE_FILTER,
        "true": TREND_MODE_FILTER,
        "1": TREND_MODE_FILTER,
        "on": TREND_MODE_FILTER,
        TREND_MODE_FILTER: TREND_MODE_FILTER,
    }
    if raw not in aliases:
        raise ValueError(f"trend mode must be one of: {', '.join(TREND_MODES)}")
    mode = aliases[raw]
    if use_trend and mode == TREND_MODE_NO_TREND:
        raise ValueError("--trend conflicts with --trend-mode no_trend")
    return mode


def resolve_trend_reference(
    strategy_key: str,
    *,
    symbol: str,
    entry_tf: str,
    overrides: dict[str, str],
    trend_type: str = "",
    trend_tf: str = "",
) -> tuple[str, str]:
    """
    Resolve trend type/timeframe từ CLI override hoặc default strategy config.
    """
    spec = get_strategy(strategy_key)
    trend_overrides = {**overrides, "ENTRY_TF": entry_tf}
    if trend_type:
        trend_overrides["TREND_TYPE"] = str(trend_type).strip().lower()
    if trend_tf:
        trend_overrides["TREND_TF"] = str(trend_tf).strip().upper()

    params = spec.normalize_params(trend_overrides, symbol, entry_tf)
    resolved_type = str(params.get("TREND_TYPE") or "").strip().lower()
    resolved_tf = str(params.get("TREND_TF") or "").strip().upper()
    if not resolved_type or not resolved_tf:
        raise ValueError(f"Strategy '{strategy_key}' does not support trend reference export.")
    return resolved_type, resolved_tf


def export_mode_suffix(mode: str, *, trend_type: str = "", trend_tf: str = "") -> str:
    """
    Phần tên file biểu diễn mode export.
    """
    if mode == TREND_MODE_NO_TREND:
        return "nt"
    return f"tf_{trend_type.lower()}_{trend_tf.upper()}"


def resolve_symbol_tf(strategy: str, symbol: str, tf: str) -> tuple[str, str]:
    """
    Chuẩn hóa và kiểm tra cặp symbol/timeframe trước khi chạy export.

    Việc làm:
        - Chuyển symbol về chữ hoa.
        - Kiểm tra symbol có tồn tại trong DP6 hay không.
        - Nếu tf rỗng, dùng timeframe mặc định của strategy.

    Raises:
        KeyError: Nếu symbol không tồn tại.
        ValueError: Nếu thiếu timeframe mà strategy không có default.
    """
    symbol = str(symbol).strip().upper()
    get_symbol(symbol)  # Báo lỗi sớm nếu symbol không tồn tại trên DP6.
    tf = str(tf).strip().upper() or default_timeframe(strategy)
    return symbol, tf


def _range_label(date_from: str, date_to: str) -> str:
    """Nhãn khoảng thời gian dùng để đặt tên file CSV.

    Phản ánh đúng input --from/--to người dùng gõ (chỉ bỏ dấu '-' cho gọn),
    KHÔNG suy ra từ dữ liệu load được thật. Đã thử cách suy ra từ dữ liệu
    thật trước đó — gây khó nhớ/khó thao tác vì mỗi symbol có lịch sử dài
    ngắn khác nhau trong DP6, nên trường hợp phổ biến nhất (không truyền
    --from/--to = toàn bộ lịch sử) sẽ ra ngày khác nhau ở mỗi file thay vì
    một nhãn cố định. Ngày chỉ xuất hiện trong tên file khi người dùng chủ
    động chọn khoảng cụ thể.
    """
    start = date_from.replace("-", "") if date_from else ""
    end = date_to.replace("-", "") if date_to else ""
    if not start and not end:
        return "full_history"
    if start and end:
        return f"{start}_{end}"
    if start:
        return f"from_{start}"
    return f"to_{end}"


def _load_entry_range(symbol: str, tf: str, date_from: str, date_to: str) -> pd.DataFrame:
    """Load OHLCV cho khoảng [date_from, date_to] -- kèm warmup trước
    date_from (WARMUP_BARS bar) nếu có, để chỉ báo kịp "ấm" trước khi vào
    đúng khoảng yêu cầu. Không --from (full history export) thì không có
    gì đứng trước bar đầu tiên, giữ nguyên load_range() thường.
    """
    if date_from:
        return load_range_with_warmup(symbol, tf, date_from, date_to, WARMUP_BARS)
    return load_range(symbol, tf, date_from, date_to)


def export_signals(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    date_from: str,
    date_to: str,
    overrides: dict[str, str],
    output_dir: str,
    output_override: str = "",
    trend_mode: str = "",
    use_trend: bool | None = None,
    trend_type: str = "",
    trend_tf: str = "",
) -> Path:
    """
    Chạy đầy đủ một lượt export CSV.

    Luồng xử lý:
        1. Validate symbol/timeframe.
        2. Load OHLCV theo khoảng thời gian [date_from, date_to] từ SQL
           Server — để trống cả 2 mốc = toàn bộ lịch sử có trong DP6. Có
           `date_from` thì tải kèm WARMUP_BARS bar trước mốc đó để chỉ báo
           kịp "ấm" (xem `_load_entry_range`), rồi cắt bỏ đoạn warmup khỏi
           kết quả sau khi chạy strategy (`trim_to_requested_range`, dùng
           chung với redis_io/worker.py -- sống trong configuration.py).
        3. Chạy strategy theo export mode:
           - no_trend: run_strategy().
           - trend_filter: run_strategy_with_trend_reference().
        4. Đặt tên file có mode suffix ở cuối theo input --from/--to (trừ khi
           output_override chỉ định sẵn 1 đường dẫn chính xác).
        5. Ghi các dòng có signal ra file CSV.
    """
    spec = get_strategy(strategy)
    strategy_key = spec.key
    symbol, tf = resolve_symbol_tf(strategy, symbol, tf)
    mode = resolve_trend_mode(trend_mode, use_trend=use_trend)

    if mode == TREND_MODE_FILTER:
        resolved_trend_type, resolved_trend_tf = resolve_trend_reference(
            strategy_key,
            symbol=symbol,
            entry_tf=tf,
            overrides=overrides,
            trend_type=trend_type,
            trend_tf=trend_tf,
        )
        entry_raw = _load_entry_range(symbol, tf, date_from, date_to)
        trend_raw = _load_entry_range(symbol, resolved_trend_tf, date_from, date_to)
        enriched = run_strategy_with_trend_reference(
            strategy_key,
            symbol=symbol,
            entry_tf=tf,
            entry_bars=entry_raw,
            trend_tf=resolved_trend_tf,
            trend_bars=trend_raw,
            overrides={**overrides, "TREND_TYPE": resolved_trend_type},
        )
    else:
        resolved_trend_type = ""
        resolved_trend_tf = ""
        raw = _load_entry_range(symbol, tf, date_from, date_to)
        enriched = run_strategy(strategy_key, symbol=symbol, tf=tf, bars=raw, overrides=overrides)
    enriched = trim_to_requested_range(enriched, date_from)

    if output_override:
        output = Path(output_override)
    else:
        label = _range_label(date_from, date_to)
        mode_suffix = export_mode_suffix(
            mode,
            trend_type=resolved_trend_type,
            trend_tf=resolved_trend_tf,
        )
        filename = f"{strategy_key}_{symbol}_{tf}_{label}_{mode_suffix}.csv"
        # Bản trend_filter nằm riêng ở <output_dir>/trend_filter/ để không lẫn
        # với bản no_trend mặc định ngay tại <output_dir> — theo yêu cầu giữ
        # thư mục export gọn, không phải do khác schema CSV.
        base_dir = Path(output_dir)
        if mode == TREND_MODE_FILTER:
            base_dir = base_dir / "trend_filter"
        output = base_dir / filename

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(to_csv(enriched, strategy=strategy_key), encoding="utf-8")
    return output


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export strategy_lab strategy signals to CSV.")
    parser.add_argument("--strategy", default="combo", help="Strategy key: combo or ma_cross.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol code, e.g. US30.")
    parser.add_argument("--tf", default="", help="Timeframe code; omitted uses the strategy default.")
    parser.add_argument(
        "--from",
        dest="date_from",
        default="",
        help="Start date, e.g. 2020-01-01. Omitted = earliest bar available in DP6.",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        default="",
        help="End date. Omitted = latest bar available.",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Strategy parameter override as NAME=VALUE. Repeatable.",
    )
    parser.add_argument("--output-dir", default="runtime/exports", help="Directory for the generated CSV file.")
    parser.add_argument("--output", default="", help="Exact output file path. Overrides --output-dir.")
    parser.add_argument(
        "--trend-mode",
        default="",
        choices=TREND_MODES,
        help="Export mode: no_trend (default) or trend_filter.",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Shortcut for --trend-mode trend_filter.",
    )
    parser.add_argument("--trend-type", default="", help="Trend type; omitted uses strategy default.")
    parser.add_argument("--trend-tf", default="", help="Trend-reference timeframe; omitted uses strategy default.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        args = _parse_args(argv)
        symbol, tf = resolve_symbol_tf(args.strategy, args.symbol, args.tf)
        overrides = parse_overrides(args.param)
        output = export_signals(
            strategy=args.strategy,
            symbol=symbol,
            tf=tf,
            date_from=args.date_from,
            date_to=args.date_to,
            overrides=overrides,
            output_dir=args.output_dir,
            output_override=args.output,
            trend_mode=args.trend_mode,
            use_trend=args.trend,
            trend_type=args.trend_type,
            trend_tf=args.trend_tf,
        )
    except Exception as exc:  # noqa: BLE001 -- Biên CLI: gom mọi lỗi thành 1 dòng dễ đọc.
        message = str(exc).strip('"')
        print(f"ERROR: {message}")
        return 1
    print(f"CSV exported: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
