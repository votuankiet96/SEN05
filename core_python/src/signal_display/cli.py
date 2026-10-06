"""
CLI hiển thị điều kiện signal BUY/SELL của 1 chiến lược lên chart HTML.

Mô tả:
    Pipeline tối giản: load OHLCV -> chạy chiến lược -> build candles+markers
    -> ghi ra 1 file HTML tự chứa. File HTML mở trực tiếp bằng trình duyệt
    (file://), không cần server.

    File này KHÔNG phụ thuộc export_cli.py — tự import thẳng từ
    core_python.src.db_connector và core_python.src.configuration. Cả hai CLI
    dùng chung indicator/strategies nhưng không import lẫn nhau.

Chạy:
    python -m core_python.src.signal_display.cli --strategy combo --symbol US30 --tf H1
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from core_python.src.configuration import (
    DEFAULT_SYMBOL,
    N_BARS,
    get_strategy,
    run_strategy,
)
from core_python.src.db_connector import get_symbol, load
from core_python.src.signal_display.payload import candlestick_points, signal_markers
from core_python.src.signal_display.renderer import render_chart_html

BARS_MIN, BARS_MAX = 50, 20000


def default_timeframe(strategy_key: str) -> str:
    """
    Chọn timeframe mặc định khi user không truyền --tf.

    Giá trị lấy từ StrategySpec trong configuration.py, không hardcode ở đây.
    """
    spec = get_strategy(strategy_key)
    if spec.default_timeframe:
        return str(spec.default_timeframe).upper()
    if spec.recommended_timeframes:
        return str(spec.recommended_timeframes[0]).upper()
    raise ValueError(f"Strategy '{strategy_key}' has no default timeframe; pass --tf explicitly.")


def resolve_symbol_tf(strategy: str, symbol: str, tf: str) -> tuple[str, str]:
    """
    Chuẩn hóa và kiểm tra cặp symbol/timeframe trước khi render chart.

    Hàm này được server.py dùng lại để dashboard sống và CLI chart tĩnh có
    cùng quy tắc validate.

    Raises:
        KeyError: Nếu symbol không tồn tại trên DP6.
        ValueError: Nếu thiếu timeframe mà strategy không có default.
    """
    symbol = str(symbol).strip().upper()
    get_symbol(symbol)  # Báo lỗi sớm nếu symbol không tồn tại trên DP6.
    tf = str(tf).strip().upper() or default_timeframe(strategy)
    return symbol, tf


def clamp_bars(value: int) -> int:
    """
    Giới hạn số lượng bar trong khoảng an toàn.

    Mục đích là tránh user nhập quá ít dữ liệu khiến indicator chưa đủ
    warmup, hoặc quá nhiều dữ liệu làm dashboard/SQL nặng không cần thiết.
    """
    return max(BARS_MIN, min(int(value), BARS_MAX))


def render_signal_chart(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    output: Path,
) -> Path:
    """
    Chạy đầy đủ một lượt tạo chart HTML tĩnh.

    Luồng xử lý:
        1. Validate symbol/timeframe.
        2. Load N bar mới nhất từ SQL Server.
        3. Chạy strategy.
        4. Chuyển kết quả thành candles + BUY/SELL markers.
        5. Ghi ra file HTML tự chứa.
    """
    symbol, tf = resolve_symbol_tf(strategy, symbol, tf)
    bars = clamp_bars(bars)

    raw = load(symbol, tf, bars)
    enriched = run_strategy(strategy, symbol=symbol, tf=tf, bars=raw)
    spec = get_strategy(strategy)

    html = render_chart_html(
        candlestick_points(enriched),
        signal_markers(enriched),
        symbol=symbol,
        tf=tf,
        strategy_label=spec.label,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def _output_path(args: argparse.Namespace, *, strategy: str, symbol: str, tf: str) -> Path:
    if args.output:
        return Path(args.output)
    filename = f"{strategy}_{symbol}_{tf}_latest_{args.bars}bars_signals.html"
    return Path(args.output_dir) / filename


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render core_python BUY/SELL signals to an HTML chart.")
    parser.add_argument("--strategy", default="combo", help="Strategy key: combo, ma_cross, breakout_atr or sma_trend.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol code, e.g. US30.")
    parser.add_argument("--tf", default="", help="Timeframe code; omitted uses the strategy default.")
    parser.add_argument("--bars", type=int, default=N_BARS, help="Latest bars to load.")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[2] / "runtime" / "charts"), help="Directory for the generated HTML file.")
    parser.add_argument("--output", default="", help="Exact output file path. Overrides --output-dir.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        args = _parse_args(argv)
        symbol, tf = resolve_symbol_tf(args.strategy, args.symbol, args.tf)
        output = _output_path(args, strategy=args.strategy, symbol=symbol, tf=tf)
        render_signal_chart(
            strategy=args.strategy,
            symbol=symbol,
            tf=tf,
            bars=args.bars,
            output=output,
        )
    except Exception as exc:  # noqa: BLE001 -- Biên CLI: gom mọi lỗi thành 1 dòng dễ đọc.
        message = str(exc).strip('"')
        print(f"ERROR: {message}")
        return 1
    print(f"Chart written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
