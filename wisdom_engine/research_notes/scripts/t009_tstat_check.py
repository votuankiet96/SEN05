#!/usr/bin/env python3
"""T009 -- đo đặc tính khối trend `tstat` trên dữ liệu thật để chọn bộ tham số mặc định (chế độ slope/return, số nến N).

Hai phần, CHỈ ĐỌC (SQL SELECT), không sửa config, không đụng Redis:
  A. Hướng của khối trên nến khung tham chiếu (H1, H2, H4) từ SIGNAL_START_DATE: tỷ lệ +1 / -1 / 0, số lần đổi hướng mỗi năm
     (đếm đổi dấu, bỏ qua các nến trung tính), độ dài trung vị của một đoạn cùng hướng, và mức ĐỒNG THUẬN với knn / zigzag
     (tỷ lệ nến cùng hướng trong số nến mà cả hai cùng khác 0). Có hàng đối chiếu knn, zigzag, breakout.
  B. Đường chạy thật (`run_strategy_with_trend_reference`, cách B: chống trùng rồi trend) tại các ô sản xuất: khung trend tra
     bảng trend_tf_by_symbol như knn/zigzag; đếm tín hiệu từ --from và tính tỷ lệ giữ lại so với không lọc trend (cột DB0).
     Có đối chiếu knn và zigzag cùng ô.
Thước đo là cỡ và hình dạng của bộ lọc, KHÔNG phải lãi/lỗi (hệ này chưa đo được; cần backtest BO20).

Chạy (từ thư mục cha của repo, vd ~/Desktop):
  wisdom_engine/.venv/bin/python -B wisdom_engine/research_notes/scripts/t009_tstat_check.py --out-dir /tmp/t009
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from wisdom_engine.we_core.src import configuration as C
from wisdom_engine.we_core.src.db_connector import load_range_with_warmup
from wisdom_engine.we_core.src.trend_methods import breakout, knn, tstat, zigzag
from wisdom_engine.we_core.src.we_signal.export_cli import signal_frame

BLOCK_TFS = ("H1", "H2", "H4")
CONFIGS = [(mode, n) for mode in ("slope", "return") for n in (50, 100, 200)]
# ô sản xuất để đo đường chạy thật: (chiến lược, khung vào lệnh)
CELLS = [("combo", "H1"), ("combo", "H4"), ("ma_cross", "M30"), ("macd_break", "H1"), ("macd_break", "H4")]


def stats(bias: np.ndarray, years: float) -> dict[str, float]:
    nonzero = bias[bias != 0]
    flips = int((np.diff(nonzero) != 0).sum()) if nonzero.size > 1 else 0
    change = np.flatnonzero(np.diff(bias) != 0) + 1
    runs = np.diff(np.r_[0, change, bias.size])
    values = bias[np.r_[0, change]]
    return {
        "up": float((bias == 1).mean()), "down": float((bias == -1).mean()), "neutral": float((bias == 0).mean()),
        "flips_per_year": flips / years, "median_run": float(np.median(runs[values != 0])) if (values != 0).any() else float("nan"),
    }


def agreement(a: np.ndarray, b: np.ndarray) -> float:
    both = (a != 0) & (b != 0)
    return float((a[both] == b[both]).mean()) if both.any() else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description="Đo đặc tính khối tstat (hướng, đồng thuận, tỷ lệ giữ tín hiệu).")
    parser.add_argument("--symbols", default="US30,GOLD,BTCUSD,EURUSD,DE40")
    parser.add_argument("--from", dest="date_from", default="2024-01-01", help="Chỉ đếm tín hiệu (phần B) từ ngày này.")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    default_params = dict(tstat.TSTAT_TREND_DEFAULT_PARAMS)
    started = time.time()
    block_rows: list[dict] = []
    pipe_rows: list[dict] = []

    for symbol in symbols:
        bars: dict[str, pd.DataFrame] = {}
        for tf in sorted({*BLOCK_TFS, *(entry for _, entry in CELLS)}, key=lambda t: (t[0], int(t[1:]))):
            bars[tf] = load_range_with_warmup(symbol, tf, C.SIGNAL_START_DATE, None, C.WARMUP_BARS)
        # --- A. hướng của khối trên nến khung tham chiếu ---
        for tf in BLOCK_TFS:
            frame = bars[tf]
            keep = (pd.to_datetime(frame["bartime"]) >= pd.Timestamp(C.SIGNAL_START_DATE)).to_numpy()
            years = (pd.to_datetime(frame["bartime"]).iloc[-1] - pd.Timestamp(C.SIGNAL_START_DATE)).days / 365.25
            ref = {
                "knn": knn.build(frame)[knn.BIAS_COLUMN].to_numpy(),
                "zigzag": zigzag.build(frame)[zigzag.BIAS_COLUMN].to_numpy(),
                "breakout": breakout.build(frame)[breakout.BIAS_COLUMN].to_numpy(),
            }
            for name, bias in ref.items():
                block_rows.append({"symbol": symbol, "tf": tf, "method": name, "agree_knn": np.nan, "agree_zigzag": np.nan,
                                   **stats(bias[keep], years)})
            for mode, n in CONFIGS:
                bias = tstat.build(frame, {"TSTAT_MODE": mode, "TSTAT_LOOKBACK": n})[tstat.BIAS_COLUMN].to_numpy()
                block_rows.append({"symbol": symbol, "tf": tf, "method": f"tstat-{mode}-{n}",
                                   "agree_knn": agreement(bias[keep], ref["knn"][keep]), "agree_zigzag": agreement(bias[keep], ref["zigzag"][keep]),
                                   **stats(bias[keep], years)})
        # --- B. đường chạy thật tại ô sản xuất ---
        for strategy, entry_tf in CELLS:
            spec = C.get_strategy(strategy)
            trend_tf = spec.normalize_params({"TREND_METHODS": "knn"}, symbol, entry_tf)["TREND_TF"]
            trend_bars = load_range_with_warmup(symbol, trend_tf, C.SIGNAL_START_DATE, None, C.WARMUP_BARS)
            raw = signal_frame(C.trim_to_requested_range(C.run_strategy(strategy, symbol=symbol, tf=entry_tf, bars=bars[entry_tf]), args.date_from), strategy=strategy)
            base = {"symbol": symbol, "strategy": strategy, "entry": entry_tf, "trend_tf": trend_tf, "raw": len(raw)}

            def run(method: str, label: str, *, entry_bars=bars[entry_tf], trend_frame=trend_bars, _strategy=strategy, _entry=entry_tf, _tf=trend_tf, _symbol=symbol, _base=base, _raw_times=frozenset(raw["bartime"])):
                out = C.run_strategy_with_trend_reference(
                    _strategy, symbol=_symbol, entry_tf=_entry, entry_bars=entry_bars, trend_tf=_tf, trend_bars=trend_frame,
                    overrides={"TREND_METHODS": method},
                )
                kept = signal_frame(C.trim_to_requested_range(out, args.date_from), strategy=_strategy)
                assert set(kept["bartime"]) <= _raw_times, (_symbol, _strategy, _entry, label, "tín hiệu lọc phải ⊆ không lọc")
                pipe_rows.append({**_base, "method": label, "kept": len(kept)})

            run("knn", "knn")
            run("zigzag", "zigzag")
            for mode, n in CONFIGS:
                tstat.TSTAT_TREND_DEFAULT_PARAMS.update({**default_params, "TSTAT_MODE": mode, "TSTAT_LOOKBACK": n, "TSTAT_NW_LAG": None})
                run("tstat", f"tstat-{mode}-{n}")
            tstat.TSTAT_TREND_DEFAULT_PARAMS.update(default_params)
        print(f"{symbol} xong ({time.time() - started:.0f}s)", flush=True)

    block = pd.DataFrame(block_rows)
    pipe = pd.DataFrame(pipe_rows)
    block.to_csv(out_dir / "t009_block_stats.csv", index=False)
    pipe.to_csv(out_dir / "t009_pipeline.csv", index=False)

    pd.set_option("display.width", 200)
    summary = block.groupby(["tf", "method"], sort=False)[["up", "down", "neutral", "flips_per_year", "median_run", "agree_knn", "agree_zigzag"]].mean()
    print("\n=== A. Hướng của khối (trung bình các symbol), từ", C.SIGNAL_START_DATE, "===")
    print(summary.round(3).to_string())
    pipe["retention"] = pipe["kept"] / pipe["raw"].where(pipe["raw"] > 0)
    pivot = pipe.groupby(["strategy", "entry", "method"], sort=False).agg(raw=("raw", "sum"), kept=("kept", "sum"))
    pivot["retention"] = pivot["kept"] / pivot["raw"]
    print("\n=== B. Tỷ lệ tín hiệu giữ lại so với không lọc (gộp 5 symbol, từ", args.date_from, ") ===")
    print(pivot["retention"].unstack("method").round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
