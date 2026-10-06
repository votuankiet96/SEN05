"""Kiểm chứng: bộ phát hiện sideway đo TẠI LÚC VÀO LỆNH (nhân quả) có phân biệt được lệnh thắng/thua của
breakout_atr và sma_trend không. Chỉ đọc DP6. Không sửa repo."""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/administrator/Desktop")
from core_python.src import db_connector as db
from core_python.src import indicator as ind
from core_python.src.strategies.breakout_atr import detect_breakout_atr_signals
from core_python.src.strategies.sma_trend import detect_sma_trend_signals

SYMS = ["US30","US500","US100","DE40","UK100","FR40","SP35","HK50","J225","GOLD","BTCUSD"]
TFS = ["M30", "H1", "H4"]
START = "2019-01-01"

def features(d):
    h, l, c = d["high"].astype(float), d["low"].astype(float), d["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    n = 14
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr14 = ind.rma(tr, n)
    pdi = 100 * ind.rma(pd.Series(pdm, index=d.index), n) / atr14
    mdi = 100 * ind.rma(pd.Series(mdm, index=d.index), n) / atr14
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    adx = ind.rma(dx, n)
    chop = 100 * np.log10(tr.rolling(n).sum() / (h.rolling(n).max() - l.rolling(n).min())) / np.log10(n)
    m = 20
    er = (c - c.shift(m)).abs() / c.diff().abs().rolling(m).sum()
    atrpct = (atr14 / c)  # biến động tương đối
    volrank = atrpct.rolling(500, min_periods=200).rank(pct=True)  # phần trăm vị trí so với 500 bar trước (nhân quả)
    return pd.DataFrame({"adx": adx, "chop": chop, "er": er, "volrank": volrank})

def trades_from(out, kind):
    rows = []; open_ = None
    for i, r in out.iterrows():
        if open_ is not None and r["exit_signal"] == 1:
            exitp = r["exit_price"]
            pnl = open_["side"] * (exitp - open_["entry"])
            rows.append({**open_, "exit_time": r["bartime"], "pnl_atr": pnl / open_["atr"], "pnl_pct": pnl / open_["entry"] * 100}); open_ = None
        if r["signal"] != 0:
            open_ = {"time": r["bartime"], "side": int(r["signal"]), "entry": float(r["close"]), "atr": float(r["atr_ref"]), "i": i}
    return rows

def run():
    allrows = []
    for tf in TFS:
        for s in SYMS:
            d = db.load_range(s, tf, START, None)
            if len(d) < 1000: continue
            f = features(d)
            d = d.reset_index(drop=True)
            pb = {"LOOKBACK_BARS": 100, "ATR_PERIOD": 10, "ALLOW_SHORT": True}
            b = detect_breakout_atr_signals(ind.add_breakout_atr_indicators(d, pb), params=pb)
            b["atr_ref"] = b["atr"]
            pt = {"SMA_PERIOD": 200, "ALLOW_SHORT": True}
            t = detect_sma_trend_signals(ind.add_sma_trend_indicators(d, pt), params=pt)
            t["atr_ref"] = ind.atr(t, 14)
            for kind, out in (("breakout_atr", b), ("sma_trend", t)):
                for tr in trades_from(out, kind):
                    fr = f.iloc[tr["i"]]
                    allrows.append({"strategy": kind, "symbol": s, "tf": tf, **tr, **fr.to_dict()})
            print(f"{tf} {s}: {len(d)} bar", flush=True)
    df = pd.DataFrame(allrows)
    df.to_pickle(sys.argv[1]); print("tổng lệnh:", len(df))
run()
