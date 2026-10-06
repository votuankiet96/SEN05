"""T001 — phân tích lệnh theo bộ phát hiện sideway (đọc trades.pkl do t001_sideway_regime_test.py tạo).
IS = trước 2023-01-01, OOS = từ 2023-01-01; ngưỡng tercile tính trên IS rồi áp cho OOS."""
import sys

import numpy as np
import pandas as pd

df = pd.read_pickle(sys.argv[1]).dropna(subset=["adx", "chop", "er", "pnl_atr"])
df["period"] = np.where(pd.to_datetime(df["time"]) < "2023-01-01", "IS", "OOS")
def stat(x):
    x = x.dropna(); n = len(x)
    if n < 30: return f"n={n:5d}  (ít)"
    m, sd = x.mean(), x.std(ddof=1)
    return f"n={n:5d} TB={m:+.3f} thắng={(x>0).mean()*100:4.1f}% t={m/sd*np.sqrt(n):+5.1f}"
def bucket(x, feat, q1, q2, trend_high):
    """Chia lệnh theo tercile (ngưỡng q1/q2 tính trên IS): trả (mask 'sideway', mask 'trend').
    volrank: nhóm biến động CAO đặt ở vị trí 'sideway', THẤP ở vị trí 'trend' (để kiểm hiệu ứng LeBaron)."""
    lo, hi = x[feat] <= q1, x[feat] > q2
    if feat == "volrank":
        return hi, lo
    return (lo, hi) if trend_high else (hi, lo)
def welch(a, b):
    a, b = a.dropna(), b.dropna()
    return (a.mean() - b.mean()) / np.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
for strat in ("breakout_atr", "sma_trend"):
    for tf in ("M30", "H1", "H4"):
        g = df[(df.strategy == strat) & (df.tf == tf)]
        isg, oos = g[g.period == "IS"], g[g.period == "OOS"]
        print(f"\n=================== {strat} {tf} | toàn bộ IS: {stat(isg.pnl_atr)} | OOS: {stat(oos.pnl_atr)}")
        for feat, trend_high in (("adx", True), ("er", True), ("chop", False), ("volrank", False)):
            q1, q2 = isg[feat].quantile([1/3, 2/3])
            out = []
            for name, part in (("IS", isg), ("OOS", oos)):
                side, trend = bucket(part, feat, q1, q2, trend_high)
                out.append((name, part[side].pnl_atr, part[trend].pnl_atr))
            lab = {"adx": "ADX thấp | ADX cao", "er": "ER thấp | ER cao", "chop": "CHOP cao | CHOP thấp", "volrank": "biến động CAO | biến động THẤP"}[feat]
            print(f"  {feat:8} ngưỡng IS {q1:.3f}/{q2:.3f}  [{lab}]")
            for name, s_, t_ in out:
                print(f"     {name:3}  'sideway': {stat(s_)}  ||  'trend': {stat(t_)}  ||  chênh trend−sideway t={welch(t_, s_):+.1f}")
