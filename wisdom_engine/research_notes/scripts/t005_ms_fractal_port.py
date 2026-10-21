"""T005 (research_notes/topic_2026_ms-fractal-trading-range-trend.md): bản chuyển Python — viết lại từ LOGIC, KHÔNG chép mã —
của chỉ báo TradingView MS_TradingRange_WilliamsFractal (tác giả hphuc0212, đóng nguồn; thư viện WFractal_Lib v48 + chỉ báo chính),
theo mã Pine người dùng gửi ngày 2026-10-15. Chỉ để nghiên cứu, KHÔNG phải mã của hệ thống (chưa có khối trend_methods nào dùng nó).
Dùng: M.run(high, low, close, length) trả về các mảng theo nến; bias_E / bias_L / bias_EL là ba cách đọc "hướng xu hướng" (xem ghi chú).

Đường chạy của chỉ báo chính: processFractals(calc_limit=0, show=True, show_Trend=True, length=wf_length(16), ...,
show_MSS=False, show_TR_Break1=True, show_CHOCH=True, show_BOS=False, confirm_on_close=True), tức:
  1. mỗi nến: ta.pivothigh/pivotlow(length, length) -> pivot chỉ được biết SAU `length` nến (xác nhận tại nến t = i + length);
  2. processHigh/LowPivot: dựng zigzag xen kẽ cao/thấp; pivot cùng loại cao hơn (thấp hơn) thì KÉO DÀI đoạn cuối, thấp hơn thì bỏ qua;
  3. updateTRLine (chỉ chạy khi zigzag đổi): "đoạn Trading Range" TR = đoạn zigzag cuối cùng mà đầu cao có close (hoặc close nến trước nó) vượt đỉnh TR cũ,
     hoặc đầu thấp có close (hoặc close nến trước) thủng đáy TR cũ; cờ TR_High/Low_Crossed được đặt lại khi TR đổi;
  4. checkBreakOutTR (mỗi nến, lúc đóng): close > đỉnh TR VÀ low < đỉnh TR (nến phải CẮT ngang mức) và chưa bị đánh dấu -> sự kiện phá lên:
     TR là đoạn giảm -> CHOCH, TR là đoạn tăng -> BOS; ngược lại cho phá xuống.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def pivots(high: np.ndarray, low: np.ndarray, length: int, tie: str = "strict") -> tuple[np.ndarray, np.ndarray]:
    """ta.pivothigh/pivotlow(src, length, length) -> mảng bool TẠI chỉ số nến pivot (chưa dịch). tie: cách xử lý giá bằng nhau ở bên PHẢI."""
    h, l = pd.Series(high), pd.Series(low)
    lmax = h.shift(1).rolling(length).max()
    rmax = h[::-1].shift(1).rolling(length).max()[::-1]
    lmin = l.shift(1).rolling(length).min()
    rmin = l[::-1].shift(1).rolling(length).min()[::-1]
    if tie == "strict":
        up, dn = (h > lmax) & (h > rmax), (l < lmin) & (l < rmin)
    elif tie == "right_loose":
        up, dn = (h > lmax) & (h >= rmax), (l < lmin) & (l <= rmin)
    elif tie == "both_loose":
        up, dn = (h >= lmax) & (h >= rmax), (l <= lmin) & (l <= rmin)
    else:
        raise ValueError(tie)
    return up.fillna(False).to_numpy(), dn.fillna(False).to_numpy()


def run(high, low, close, length: int = 16, *, straddle: bool = True, tie: str = "strict", log: bool = False) -> dict:
    """Chạy máy trạng thái. Trả về các mảng theo nến (tại lúc ĐÓNG nến t) + nhật ký sự kiện nếu log=True."""
    high, low, close = (np.asarray(x, dtype="float64") for x in (high, low, close))
    n = len(close)
    up, dn = pivots(high, low, length, tie)
    nan = float("nan")
    st = {"last_price": nan, "last_bar": -1, "is_high": False, "last_leg": None, "TR": None, "hx": False, "lx": False}
    out = {
        "tr_hi": np.full(n, nan), "tr_lo": np.full(n, nan), "tr_dir": np.zeros(n, np.int8),
        "hx": np.zeros(n, bool), "lx": np.zeros(n, bool), "event": np.zeros(n, np.int8),   # +1 phá lên, -1 phá xuống (sự kiện tại nến này)
        "event_kind": np.zeros(n, np.int8),                                                # 1 = BOS, 2 = CHOCH
        "adopt": np.zeros(n, bool),                                                        # TR mới được nhận tại nến này
        "bias_E": np.zeros(n, np.int8), "bias_L": np.zeros(n, np.int8), "bias_EL": np.zeros(n, np.int8),
        "missed_up": np.zeros(n, bool), "missed_dn": np.zeros(n, bool),                    # close vượt mức nhưng nến không CẮT ngang (low >= mức / high <= mức)
    }
    logs = {"pivots": [], "legs": [], "adopt": [], "events": []}

    def update_tr(t: int) -> bool:
        """updateTRLine — trả về True nếu TR mới (is_new_left)."""
        leg, tr = st["last_leg"], st["TR"]
        new_tr = False
        if leg is not None and tr is not None:
            tr_low, tr_high = min(tr[1], tr[3]), max(tr[1], tr[3])
            low_idx = leg[0] if leg[1] < leg[3] else leg[2]
            high_idx = leg[0] if leg[1] > leg[3] else leg[2]
            hc, hc_ce = close[high_idx], (close[high_idx - 1] if high_idx - 1 >= 0 else nan)
            lc, lc_ce = close[low_idx], (close[low_idx - 1] if low_idx - 1 >= 0 else nan)
            if (hc > tr_high or hc_ce > tr_high) or (lc < tr_low or lc_ce < tr_low):
                is_new_left = leg[0] != tr[0]
                st["TR"] = leg
                if is_new_left:
                    nl, nh = min(leg[1], leg[3]), max(leg[1], leg[3])
                    st["hx"], st["lx"] = bool(close[t] > nh), bool(close[t] < nl)
                    new_tr = True
        elif leg is not None:        # TR_Line còn na -> nhận đoạn đầu tiên
            st["TR"] = leg
            st["hx"], st["lx"] = False, False
            new_tr = True
        if new_tr and log:
            tr = st["TR"]; logs["adopt"].append((t, tr[0], tr[1], tr[2], tr[3]))
        return new_tr

    def update_state(price: float, bar: int, is_high: bool, t: int) -> bool:
        st["last_price"], st["last_bar"], st["is_high"] = price, bar, is_high
        return update_tr(t)

    cur_e = cur_l = cur_el = 0
    for t in range(n):
        i = t - length
        new_tr_now = False
        if i >= 0 and up[i]:
            price = high[i]
            if log: logs["pivots"].append((t, i, "H", price))
            if st["is_high"]:
                if price > st["last_price"]:
                    if st["last_leg"] is not None:
                        st["last_leg"][2], st["last_leg"][3] = i, price
                    new_tr_now |= update_state(price, i, True, t)
            else:
                if not np.isnan(st["last_price"]):
                    st["last_leg"] = [st["last_bar"], st["last_price"], i, price]
                    if log: logs["legs"].append((t, "new", st["last_leg"][0], st["last_leg"][1], i, price))
                new_tr_now |= update_state(price, i, True, t)
        if i >= 0 and dn[i]:
            price = low[i]
            if log: logs["pivots"].append((t, i, "L", price))
            if not st["is_high"]:
                if price < st["last_price"]:
                    if st["last_leg"] is not None:
                        st["last_leg"][2], st["last_leg"][3] = i, price
                    new_tr_now |= update_state(price, i, False, t)
            else:
                if not np.isnan(st["last_price"]):
                    st["last_leg"] = [st["last_bar"], st["last_price"], i, price]
                    if log: logs["legs"].append((t, "new", st["last_leg"][0], st["last_leg"][1], i, price))
                new_tr_now |= update_state(price, i, False, t)
        # --- checkBreakOutTR (cuối nến) ---
        tr = st["TR"]
        ev = 0
        if tr is not None:
            tr_low, tr_high = min(tr[1], tr[3]), max(tr[1], tr[3])
            is_down = tr[1] > tr[3]
            c = close[t]
            if c > tr_high and not st["hx"]:
                if (low[t] < tr_high) or not straddle:
                    st["hx"] = True; ev = 1
                    out["event_kind"][t] = 2 if is_down else 1
                    if log: logs["events"].append((t, "CHOCH" if is_down else "BOS", 1, tr_high, tr[0] if is_down else tr[2]))
                else:
                    out["missed_up"][t] = True
            if c < tr_low and not st["lx"]:
                if (high[t] > tr_low) or not straddle:
                    st["lx"] = True; ev = -1
                    out["event_kind"][t] = 1 if is_down else 2
                    if log: logs["events"].append((t, "BOS" if is_down else "CHOCH", -1, tr_low, tr[2] if is_down else tr[0]))
                else:
                    out["missed_dn"][t] = True
            out["tr_hi"][t], out["tr_lo"][t], out["tr_dir"][t] = tr_high, tr_low, (-1 if is_down else 1)
        out["hx"][t], out["lx"][t], out["event"][t], out["adopt"][t] = st["hx"], st["lx"], ev, new_tr_now
        # --- các cách đọc "hướng xu hướng" ---
        if new_tr_now:
            leg_dir = -1 if tr[1] > tr[3] else 1
            cur_l = leg_dir
            cur_el = leg_dir
        if tr is not None:
            if st["hx"]:
                cur_l = 1
            elif st["lx"]:
                cur_l = -1
        if ev:
            cur_e = ev
            cur_el = ev
        out["bias_E"][t], out["bias_L"][t], out["bias_EL"][t] = cur_e, cur_l, cur_el
    out["logs"] = logs
    return out
