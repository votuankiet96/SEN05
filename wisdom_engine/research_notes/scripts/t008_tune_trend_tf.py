#!/usr/bin/env python3
"""T008 -- chọn khung trend cho NHIỀU tín hiệu nhất theo từng (chiến lược, symbol, khung vào lệnh).

Với mỗi chiến lược (combo, ma_cross, macd_break), mỗi symbol vận hành và mỗi khung vào lệnh mà worker chạy, thử MỌI khung trend được phép
(nằm trong `strategies.<tên>.trend_timeframes` của config VÀ lớn hơn hoặc bằng khung vào lệnh, quy tắc 2026-10-16) bằng đúng đường chạy của
hệ thống (`run_strategy_with_trend_reference`: chống trùng rồi trend, nến trend đóng trước nến tín hiệu mở), đếm tín hiệu từ --from tới nay và so
với không lọc trend (chỉ chống trùng, cột DB0). Thước đo là SỐ LƯỢNG tín hiệu còn lại, KHÔNG phải chất lượng (hệ thống chưa đo lãi/lỗi).

Luật chọn: khung có số tín hiệu lớn nhất; hoà đúng số thì chọn khung lớn hơn. (Các bảng tạo TRƯỚC 2026-10-16 dùng luật cũ: hoà thì ưu tiên
`default_trend_tf` nếu nằm trong nhóm hoà; khoá đó đã bị xoá khỏi config.) Ô chênh dưới --near-tie (mặc định 5%) với khung xếp nhì được in riêng để người dùng cân nhắc sửa tay.

Script CHỈ ĐỌC (SQL SELECT), KHÔNG sửa config. Kết quả:
  <out-dir>/t008_trend_tf_options.csv      mọi tổ hợp (strategy, symbol, entry, trend, signals, buy, sell, baseline, retention)
  <out-dir>/t008_trend_tf_by_symbol.yaml   đoạn `trend_tf_by_symbol` cho từng chiến lược, để dán vào we_config.yaml / we_config.example.yaml

Chạy (từ thư mục cha của repo, vd ~/Desktop):
  wisdom_engine/.venv/bin/python -B wisdom_engine/research_notes/scripts/t008_tune_trend_tf.py --from 2024-01-01 --out-dir /tmp/t008
Bảng chỉ đúng với luật hiện tại (cách B, quy tắc ghép nến trend, KNN); đổi luật thì phải chạy lại.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from wisdom_engine.we_core.src import configuration as C
from wisdom_engine.we_core.src.db_connector import load_range_with_warmup
from wisdom_engine.we_core.src.indicator import timeframe_minutes
from wisdom_engine.we_core.src.we_signal.export_cli import signal_frame

# Mỗi chiến lược: (khung vào lệnh worker chạy, khung trend cho phép theo config, khung trend mặc định)
PLAN = {
    "combo": (C.COMBO_RECOMMENDED_TIMEFRAMES, C.COMBO_TREND_TIMEFRAMES),
    "ma_cross": (C.MA_CROSS_SUPPORTED_TIMEFRAMES, C.MA_CROSS_TREND_TIMEFRAMES),
    "macd_break": (C.MACD_BREAK_SUPPORTED_TIMEFRAMES, C.MACD_BREAK_TREND_TIMEFRAMES),
}

# KNN chỉ phụ thuộc nến khung trend nên tính một lần mỗi (symbol, khung trend) -- khoá bằng NHÃN, không bằng id() (id bị tái sử dụng giữa các symbol).
_orig_build_trend_frame = C.build_trend_frame
_trend_cache: dict = {}


def _cached_build_trend_frame(trend_bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    key = (trend_bars.attrs["label"], params["TREND_TF"], tuple(params["TREND_METHODS"]))
    if key not in _trend_cache:
        _trend_cache[key] = _orig_build_trend_frame(trend_bars, params)
    return _trend_cache[key].copy()


C.build_trend_frame = _cached_build_trend_frame


def choose(counts: dict[str, int]) -> str:
    """Khung có số tín hiệu lớn nhất; hoà đúng số thì chọn khung lớn hơn."""
    top = max(counts.values())
    tied = [tf for tf, n in counts.items() if n == top]
    return max(tied, key=timeframe_minutes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chọn khung trend cho nhiều tín hiệu nhất theo (chiến lược, symbol, khung vào lệnh).")
    parser.add_argument("--from", dest="date_from", default="2024-01-01", help="Chỉ đếm tín hiệu từ ngày này (YYYY-MM-DD).")
    parser.add_argument("--strategies", default="combo,ma_cross,macd_break")
    parser.add_argument("--symbols", default="", help="Chỉ chạy các symbol này, cách nhau bằng dấu phẩy (mặc định: mọi symbol vận hành).")
    parser.add_argument("--near-tie", type=float, default=0.05, help="Báo ô có chênh giữa khung nhất và nhì nhỏ hơn tỷ lệ này.")
    parser.add_argument("--out-dir", default=".", help="Thư mục ghi CSV + YAML.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or sorted(C.COMBO_SYMBOL_X)
    records: list[dict] = []
    started = time.time()
    for strategy in [s.strip() for s in args.strategies.split(",") if s.strip()]:
        entry_tfs, trend_tfs = PLAN[strategy]
        for symbol in symbols:
            bars: dict[str, pd.DataFrame] = {}
            for tf in sorted(set(entry_tfs) | set(trend_tfs), key=timeframe_minutes):
                frame = load_range_with_warmup(symbol, tf, C.SIGNAL_START_DATE, None, C.WARMUP_BARS)
                frame.attrs["label"] = (symbol, tf)
                bars[tf] = frame
            for entry_tf in entry_tfs:
                base = signal_frame(
                    C.trim_to_requested_range(C.run_strategy(strategy, symbol=symbol, tf=entry_tf, bars=bars[entry_tf]), args.date_from),
                    strategy=strategy,
                )
                records.append({"strategy": strategy, "symbol": symbol, "entry": entry_tf, "trend": "none", "signals": len(base),
                                "buy": int((base["signal"] == 1).sum()), "sell": int((base["signal"] == 2).sum())})
                base_times = set(base["bartime"])
                for trend_tf in trend_tfs:
                    if timeframe_minutes(trend_tf) < timeframe_minutes(entry_tf):
                        continue
                    out = C.run_strategy_with_trend_reference(
                        strategy, symbol=symbol, entry_tf=entry_tf, entry_bars=bars[entry_tf], trend_tf=trend_tf, trend_bars=bars[trend_tf]
                    )
                    frame_out = signal_frame(C.trim_to_requested_range(out, args.date_from), strategy=strategy)
                    assert set(frame_out["bartime"]) <= base_times, (strategy, symbol, entry_tf, trend_tf, "tín hiệu lọc trend phải ⊆ không lọc")
                    records.append({"strategy": strategy, "symbol": symbol, "entry": entry_tf, "trend": trend_tf, "signals": len(frame_out),
                                    "buy": int((frame_out["signal"] == 1).sum()), "sell": int((frame_out["signal"] == 2).sum())})
            print(f"{strategy} {symbol} xong ({time.time() - started:.0f}s)", flush=True)

    result = pd.DataFrame(records)
    baseline = result[result["trend"] == "none"].set_index(["strategy", "symbol", "entry"])["signals"]
    result["baseline"] = [int(baseline[(r.strategy, r.symbol, r.entry)]) for r in result.itertuples()]
    result["retention"] = result["signals"] / result["baseline"].where(result["baseline"] > 0)
    result.to_csv(out_dir / "t008_trend_tf_options.csv", index=False)

    yaml_lines: list[str] = []
    near: list[str] = []
    for strategy in result["strategy"].unique():
        entry_tfs, _ = PLAN[strategy]
        yaml_lines += [f"# {strategy} (đoạn dán dưới strategies.{strategy})", "trend_tf_by_symbol:"]
        for symbol in symbols:
            picks: dict[str, str] = {}
            for entry_tf in entry_tfs:
                sub = result[(result.strategy == strategy) & (result.symbol == symbol) & (result.entry == entry_tf) & (result.trend != "none")]
                counts = dict(zip(sub["trend"], sub["signals"], strict=True))
                picks[entry_tf] = choose(counts)
                if len(counts) > 1:
                    ordered = sorted(counts.values(), reverse=True)
                    if ordered[0] and (ordered[0] - ordered[1]) / ordered[0] < args.near_tie:
                        near.append(f"{strategy} {symbol} vào {entry_tf}: " + ", ".join(f"{tf} {n}" for tf, n in sorted(counts.items(), key=lambda kv: -kv[1])) + f" -> chọn {picks[entry_tf]}")
            yaml_lines.append(f"  {symbol}: {{" + ", ".join(f"{tf}: {trend}" for tf, trend in picks.items()) + "}")
        yaml_lines.append("")
    (out_dir / "t008_trend_tf_by_symbol.yaml").write_text("\n".join(yaml_lines), encoding="utf-8")

    print(f"\nĐã ghi {out_dir / 't008_trend_tf_options.csv'} và {out_dir / 't008_trend_tf_by_symbol.yaml'}")
    print(f"{len(near)} ô gần như hoà (chênh < {args.near_tie:.0%} giữa khung nhất và nhì):")
    for line in near:
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
