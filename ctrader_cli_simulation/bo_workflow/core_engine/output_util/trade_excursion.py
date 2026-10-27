"""trade_excursion.py — đo "giá đi xa tới đâu" (MFE) của từng NHÓM lệnh bằng tick thật, như thể KHÔNG có TP.

Một nhóm = 1 tín hiệu = N chân cùng giá vào (lượt lẻ bán niên). Từ lúc vào lệnh, đi theo giá từng tick tới điểm SỚM NHẤT trong 3:
(a) giá chạm mức SL (KSL × ATR từ giá tham chiếu), (b) tín hiệu NGƯỢC HƯỚNG kế tiếp tới lượt xử lý (bot đóng nhóm theo luật đảo
chiều), (c) hết dữ liệu của kỳ. Ghi lại mức thuận lợi tối đa (MFE, đơn vị ATR), kết thúc kiểu gì và kết quả tại điểm kết thúc.
Từ đó, với MỌI mức TP: xác suất chạm = P(MFE ≥ TP), kết quả kỳ vọng = (chạm: +TP; không chạm: kết quả lúc kết thúc) — xem truy
vấn "TP Curve" trong báo cáo Power BI. Chỉ phản ánh GIÁ (chưa phí/swap/trượt giá lúc đóng).

Giá tham chiếu (ref) và ATR lấy từ CSV tín hiệu đã chụp trong SQL (signal_snapshot), đúng như BacktestBot: lệnh STOP (combo) tính SL/TP
từ giá entry của tín hiệu, lệnh MARKET (ma_cross, macd_break) từ giá khớp. Mua dùng giá Bid, bán dùng giá Ask (cTrader chạm SL/TP
và đóng thị trường ở phía đó). Ghép nhóm ↔ tín hiệu và giờ đảo chiều bám đúng luật xử lý của bot (HandleSignal): tín hiệu được
xử lý ở tick đầu tiên của symbol sau `bartime + độ dài khung`; lệnh STOP hết hạn sau 3 khung (tín hiệu tới muộn hơn hạn bị bỏ,
không kích hoạt đảo chiều).

Kết quả ghi vào lược đồ RIÊNG `analysis` (analysis.group_excursion, analysis.excursion_run); bảng do sql/003_analysis.sql tạo
(master_db.ensure_schema — `run` tự gọi). Đây là bước DẪN XUẤT: luồng backtest KHÔNG chạy nó sau mỗi lượt; dựng lại được bất cứ
lúc nào từ SQL + cache tick. Chạy từ thư mục gốc repo:
    python -m bo_workflow.core_engine.output_util.trade_excursion status      # còn bao nhiêu lượt chưa tính (không đọc tick)
    python -m bo_workflow.core_engine.output_util.trade_excursion run [--rebuild] [--symbol HK50.cash] [--workers 10]
    python -m bo_workflow.core_engine.output_util.trade_excursion check       # đối chiếu với kết cục THẬT của backtest
Mặc định `run` chỉ tính lượt chưa có trong analysis.excursion_run (chạy lại sau khi có thêm backtest).
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import gzip
import io
import json
import os
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .. import master_db

TICK_RECORD = np.dtype("<i8")          # bản ghi 24 byte: giờ (ms UTC), bid × 1e5, ask × 1e5
PRICE_SCALE = 1e5
PENDING_LIFETIME_BARS = 3              # = BacktestBot.PendingLifetimeBars (lệnh STOP hết hạn sau 3 khung)
END_SL, END_REVERSAL, END_DATA = 1, 2, 3
SCHEMA = "analysis"
INSERT_CHUNK = 20_000

# Mức TP của 3 chân trong các lượt chuẩn (BacktestBot: Fib2618 / Fib3330 / Fib4236) — chỉ dùng để ĐỐI CHIẾU với kết cục thật.
CHECK_TP_ATR = (2.618, 3.330, 4.236)

# --------------------------------------------------------------------------- #
# Tick
# --------------------------------------------------------------------------- #

def cache_roots() -> list[Path]:
    """Mọi thư mục tài khoản của cache tick: %APPDATA%\\Spotware\\Cache\\*\\BacktestingCache\\V1\\<account>."""
    base = Path(os.environ.get("APPDATA", "")) / "Spotware" / "Cache"
    return [account for broker in base.glob("*/BacktestingCache/V1") for account in broker.iterdir() if account.is_dir()]


def symbol_tick_dir(symbol: str, roots: list[Path] | None = None) -> Path | None:
    """Thư mục t1 của symbol có NHIỀU file nhất (cùng symbol có thể nằm ở nhiều tài khoản demo)."""
    best, best_count = None, 0
    for root in roots if roots is not None else cache_roots():
        folder = root / symbol / "t1"
        if folder.is_dir():
            count = sum(1 for _ in folder.glob("*.zticks"))
            if count > best_count:
                best, best_count = folder, count
    return best


def parse_ticks(blob: bytes) -> np.ndarray:
    """Nội dung một file .zticks (đã giải nén) → mảng (n, 3) int64: giờ ms, bid×1e5, ask×1e5."""
    usable = len(blob) // 24 * 24
    return np.frombuffer(blob[:usable], dtype=TICK_RECORD).reshape(-1, 3)


def _forward_fill_zero(values: np.ndarray) -> np.ndarray:
    """Tick một phía có phía kia = 0 (chỉ cập nhật bid hoặc ask) ⇒ mang giá hợp lệ gần nhất sang."""
    index = np.where(values != 0, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    return values[index]


def load_ticks(folder: Path, start: pd.Timestamp, end: pd.Timestamp) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tick (giờ ms UTC, bid, ask) của các ngày start..end (gồm cả hai đầu). Ngày không có file (cuối tuần) bỏ qua."""
    parts = []
    for day in pd.date_range(start.normalize(), end.normalize(), freq="D"):
        path = folder / f"{day:%Y%m%d}.zticks"
        if path.is_file():
            parts.append(parse_ticks(gzip.decompress(path.read_bytes())))
    if not parts:
        empty = np.empty(0)
        return empty.astype(np.int64), empty, empty
    data = np.concatenate(parts)
    t = data[:, 0]
    bid = _forward_fill_zero(data[:, 1].astype(np.float64)) / PRICE_SCALE
    ask = _forward_fill_zero(data[:, 2].astype(np.float64)) / PRICE_SCALE
    return t, bid, ask


# --------------------------------------------------------------------------- #
# Tín hiệu
# --------------------------------------------------------------------------- #

def bar_duration_ms(timeframe: str) -> int:
    """'H1' / 'm20' / 'D1' → độ dài khung (ms) — cùng bảng với BacktestBot.TryGetNominalBarDuration."""
    unit, count = timeframe.strip()[:1].upper(), int(timeframe.strip()[1:])
    minutes = {"M": 1, "H": 60, "D": 1440, "W": 10080}[unit]
    return count * minutes * 60_000


def parse_signals(blob: bytes, timeframe: str) -> pd.DataFrame:
    """CSV tín hiệu đã chụp (gzip) → bảng avail_ms, direction (+1 mua / -1 bán), atr, entry; xếp theo avail_ms."""
    frame = pd.read_csv(io.BytesIO(gzip.decompress(blob)))
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    bar_ms = pd.to_datetime(frame["bartime"], utc=True).astype("datetime64[ms, UTC]").astype("int64")
    duration = bar_duration_ms(timeframe)
    entry = frame["entry"] if "entry" in frame.columns else pd.Series(0.0, index=frame.index)
    out = pd.DataFrame({
        "bar_ms": bar_ms.to_numpy(),
        "avail_ms": bar_ms.to_numpy() + duration,
        "direction": np.where(frame["signal"].to_numpy() == 1, 1, -1),   # 1 = mua; 2 hoặc -1 = bán (BacktestBot chuẩn hoá)
        "atr": frame["atr"].astype(float).to_numpy(),
        "entry": pd.to_numeric(entry, errors="coerce").fillna(0.0).to_numpy(),
    })
    return out.sort_values("avail_ms", kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Lõi: ghép nhóm ↔ tín hiệu rồi đo MFE
# --------------------------------------------------------------------------- #

def measure_run(
    t: np.ndarray, bid: np.ndarray, ask: np.ndarray, signals: pd.DataFrame, groups: pd.DataFrame, *,
    is_stop_order: bool, bar_ms: int, ksl: float, last_ms: int,
) -> pd.DataFrame:
    """Đo MFE cho các nhóm của MỘT lượt. `groups` cần: direction (+1/-1), entry_ms, fill_price. Trả bảng cùng thứ tự `groups` với
    cột: matched, signal_bar_ms, atr, ref_price, slip_atr, mfe_atr, end_kind, end_exc_atr, end_ms (nhóm không ghép được: matched=False)."""
    n_groups = len(groups)
    out = {
        "matched": np.zeros(n_groups, dtype=bool), "signal_bar_ms": np.zeros(n_groups, dtype=np.int64),
        "atr": np.full(n_groups, np.nan), "ref_price": np.full(n_groups, np.nan), "slip_atr": np.full(n_groups, np.nan),
        "mfe_atr": np.full(n_groups, np.nan), "end_kind": np.zeros(n_groups, dtype=np.int8),
        "end_exc_atr": np.full(n_groups, np.nan), "end_ms": np.zeros(n_groups, dtype=np.int64),
    }
    empty = pd.DataFrame(out, index=groups.index)
    if n_groups == 0 or len(t) == 0 or signals.empty:
        return empty

    # Tín hiệu được bot xử lý ở tick đầu tiên SAU bartime + độ dài khung; lệnh STOP tới muộn hơn hạn (3 khung) bị bỏ ngay,
    # trước cả luật đảo chiều (BacktestBot.HandleSignal) nên cũng không đóng nhóm cũ.
    last_index = int(np.searchsorted(t, last_ms, side="right")) - 1
    avail = signals["avail_ms"].to_numpy()
    exec_index = np.searchsorted(t, avail, side="left")
    seen = exec_index <= last_index
    exec_ms = np.where(seen, t[np.minimum(exec_index, len(t) - 1)], -1)
    expire = avail + PENDING_LIFETIME_BARS * bar_ms if is_stop_order else np.full(len(avail), np.iinfo(np.int64).max)
    handled = seen & (exec_ms < expire)
    if not handled.any():
        return empty
    columns = {name: signals[name].to_numpy()[handled] for name in ("bar_ms", "direction", "atr", "entry")}
    columns.update(exec_ms=exec_ms[handled], exec_index=exec_index[handled], expire=expire[handled])
    sides = {}
    for d in (1, -1):
        mask = columns["direction"] == d
        side = {name: values[mask] for name, values in columns.items()}
        side["first_ms"], side["first_pos"] = np.unique(side["exec_ms"], return_index=True)   # MARKET: tín hiệu đến trước được xử lý trước
        sides[d] = side

    direction = groups["direction"].to_numpy()
    entry_ms = groups["entry_ms"].to_numpy()
    fill = groups["fill_price"].to_numpy(dtype=float)
    series = {1: bid, -1: ask}   # mua chạm SL/TP và đóng thị trường ở Bid, bán ở Ask

    for row in range(n_groups):
        d = int(direction[row])
        side = sides[d]
        if len(side["exec_ms"]) == 0:
            continue
        if is_stop_order:
            hi = int(np.searchsorted(side["exec_ms"], entry_ms[row], side="right"))
            lo = max(0, hi - 8)
            alive = np.flatnonzero(side["expire"][lo:hi] > entry_ms[row]) + lo
            if len(alive) == 0:
                continue
            pos = int(alive[np.argmin(np.abs(side["entry"][alive] - fill[row]))])   # nhiều tín hiệu cùng hướng: lấy mức entry sát giá khớp
            ref = float(side["entry"][pos])
        else:
            k = int(np.searchsorted(side["first_ms"], entry_ms[row]))
            if k >= len(side["first_ms"]) or side["first_ms"][k] != entry_ms[row]:
                continue
            pos = int(side["first_pos"][k])
            ref = float(fill[row])
        atr = float(side["atr"][pos])
        if not atr > 0:
            continue

        opposite = sides[-d]
        k = int(np.searchsorted(opposite["exec_ms"], entry_ms[row], side="right"))
        reversal_index = int(opposite["exec_index"][k]) if k < len(opposite["exec_ms"]) else None

        i0 = int(np.searchsorted(t, entry_ms[row], side="left"))
        stop_index = last_index if reversal_index is None else min(reversal_index, last_index)
        prices = series[d][i0:stop_index + 1]
        if len(prices) == 0:
            continue
        sl_price = ref - d * ksl * atr
        adverse = prices <= sl_price if d > 0 else prices >= sl_price
        hit = int(adverse.argmax())
        if adverse[hit]:
            end_kind, end_pos = END_SL, hit
            favourable = prices[:hit] if hit > 0 else prices[:1]
        else:
            end_kind = END_REVERSAL if reversal_index is not None and reversal_index <= last_index else END_DATA
            end_pos = len(prices) - 1
            favourable = prices
        extreme = favourable.max() if d > 0 else favourable.min()
        out["matched"][row] = True
        out["signal_bar_ms"][row] = int(side["bar_ms"][pos])
        out["atr"][row] = atr
        out["ref_price"][row] = ref
        out["slip_atr"][row] = d * (fill[row] - ref) / atr
        out["mfe_atr"][row] = d * (extreme - ref) / atr
        out["end_kind"][row] = end_kind
        out["end_exc_atr"][row] = d * (prices[end_pos] - ref) / atr
        out["end_ms"][row] = int(t[i0 + end_pos])
    return pd.DataFrame(out, index=groups.index)


# --------------------------------------------------------------------------- #
# Nguồn dữ liệu (SQL) và công việc theo (symbol, kỳ)
# --------------------------------------------------------------------------- #

_RUNS_SQL = """
WITH single_runs AS (
    SELECT t.backtest_run_id FROM dbo.trade t WHERE t.leg_label IS NOT NULL
    GROUP BY t.backtest_run_id HAVING COUNT(DISTINCT LEFT(t.leg_label, LEN(t.leg_label) - 3)) = 1)
SELECT r.backtest_run_id, r.strategy, r.symbol, r.timeframe, r.start_date, r.end_date_requested, r.last_data_time,
       r.ksl_atr_multiple, r.signal_snapshot_id, sn.channel AS signal_channel
FROM dbo.backtest_run r JOIN single_runs s ON s.backtest_run_id = r.backtest_run_id
JOIN dbo.signal_snapshot sn ON sn.signal_snapshot_id = r.signal_snapshot_id
WHERE r.zone = 'half'
"""

_GROUPS_SQL = """
SELECT t.backtest_run_id, t.direction, t.entry_time_utc, MIN(t.entry_price) AS fill_price, COUNT(*) AS legs,
       MAX(CASE WHEN RIGHT(t.leg_label, 2) = 'L1' THEN t.close_reason END) AS r1,
       MAX(CASE WHEN RIGHT(t.leg_label, 2) = 'L2' THEN t.close_reason END) AS r2,
       MAX(CASE WHEN RIGHT(t.leg_label, 2) = 'L3' THEN t.close_reason END) AS r3
FROM dbo.trade t WHERE t.backtest_run_id IN ({ids})
GROUP BY t.backtest_run_id, t.direction, t.entry_time_utc
"""


def _in_list(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def _load_groups(conn: Any, run_ids: list[int]) -> pd.DataFrame:
    parts = [pd.read_sql(_GROUPS_SQL.format(ids=_in_list(run_ids[i:i + 400])), conn) for i in range(0, len(run_ids), 400)]
    groups = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if groups.empty:
        return groups
    groups["entry_ms"] = pd.to_datetime(groups["entry_time_utc"]).astype("datetime64[ms]").astype("int64")
    groups["direction_sign"] = np.where(groups["direction"] == "buy", 1, -1)
    return groups


def _load_signal_blobs(conn: Any, snapshot_ids: list[int]) -> dict[int, bytes]:
    blobs: dict[int, bytes] = {}
    for i in range(0, len(snapshot_ids), 100):
        chunk = snapshot_ids[i:i + 100]
        rows = conn.execute(
            f"SELECT signal_snapshot_id, rows_compressed FROM dbo.signal_snapshot WHERE signal_snapshot_id IN ({_in_list(chunk)})"
        ).fetchall()
        blobs.update({int(r[0]): bytes(r[1]) for r in rows})
    return blobs


def _agree(actual: pd.Series, mfe: np.ndarray, level: float, previous: np.ndarray | None) -> np.ndarray:
    """Cờ 'chạm TPk' tính từ MFE == cờ thật (kết cục chân k là take_profit, và các chân trước cũng vậy)."""
    predicted = mfe >= level
    real = (actual.to_numpy() == "take_profit") if previous is None else ((actual.to_numpy() == "take_profit") & previous)
    return predicted == real


def work_on_window(job: Mapping[str, Any]) -> dict[str, Any]:
    """Một công việc = mọi lượt của MỘT symbol trong MỘT kỳ: nạp tick một lần rồi đo từng lượt. Chạy trong tiến trình con."""
    folder = Path(job["tick_dir"])
    t, bid, ask = load_ticks(folder, pd.Timestamp(job["start"]), pd.Timestamp(job["end"]))
    results, summaries = [], []
    for run in job["runs"]:
        groups = job["groups"][job["groups"]["backtest_run_id"] == run["backtest_run_id"]].reset_index(drop=True)
        signals = parse_signals(job["blobs"][run["signal_snapshot_id"]], run["timeframe"])
        measured = measure_run(
            t, bid, ask, signals,
            pd.DataFrame({"direction": groups["direction_sign"], "entry_ms": groups["entry_ms"], "fill_price": groups["fill_price"]}),
            is_stop_order=run["strategy"] == "combo", bar_ms=bar_duration_ms(run["timeframe"]), ksl=float(run["ksl_atr_multiple"]),
            last_ms=int(pd.Timestamp(run["last_data_time"]).tz_localize("UTC").value // 1_000_000),
        )
        frame = pd.concat([groups, measured], axis=1)
        done = frame[frame["matched"]]
        comparable = done[(done["legs"] == 3) & done[["r1", "r2", "r3"]].notna().all(axis=1)]
        mfe = comparable["mfe_atr"].to_numpy()
        ok1 = _agree(comparable["r1"], mfe, CHECK_TP_ATR[0], None)
        ok2 = _agree(comparable["r2"], mfe, CHECK_TP_ATR[1], (comparable["r1"].to_numpy() == "take_profit"))
        ok3 = _agree(comparable["r3"], mfe, CHECK_TP_ATR[2], (comparable["r1"].to_numpy() == "take_profit") & (comparable["r2"].to_numpy() == "take_profit"))
        summaries.append({
            "backtest_run_id": int(run["backtest_run_id"]), "groups": len(frame), "matched": len(done), "comparable": len(comparable),
            "agree_tp1": int(ok1.sum()), "agree_tp2": int(ok2.sum()), "agree_tp3": int(ok3.sum()),
        })
        results.append(done)
    kept = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return {"symbol": job["symbol"], "window": job["start"], "ticks": len(t), "rows": kept, "summaries": summaries}


# --------------------------------------------------------------------------- #
# SQL ra
# --------------------------------------------------------------------------- #

def _to_dt(ms: pd.Series) -> list:
    return pd.to_datetime(ms, unit="ms").dt.to_pydatetime().tolist()


def save(conn: Any, kept: pd.DataFrame, summaries: list[dict[str, Any]]) -> None:
    """Xoá rồi ghi lại các lượt vừa tính (idempotent theo backtest_run_id)."""
    run_ids = sorted({s["backtest_run_id"] for s in summaries})
    cursor = conn.cursor()
    cursor.fast_executemany = True
    for i in range(0, len(run_ids), 400):
        chunk = _in_list(run_ids[i:i + 400])
        cursor.execute(f"DELETE FROM {SCHEMA}.group_excursion WHERE backtest_run_id IN ({chunk})")
        cursor.execute(f"DELETE FROM {SCHEMA}.excursion_run WHERE backtest_run_id IN ({chunk})")
    if len(kept):
        rows = list(zip(
            kept["backtest_run_id"].astype(int).tolist(), kept["direction"].tolist(), _to_dt(kept["entry_ms"]),
            _to_dt(kept["signal_bar_ms"]), kept["atr"].tolist(), kept["ref_price"].tolist(), kept["fill_price"].astype(float).tolist(),
            kept["slip_atr"].tolist(), kept["mfe_atr"].tolist(), kept["end_kind"].astype(int).tolist(), kept["end_exc_atr"].tolist(),
            _to_dt(kept["end_ms"]),
        ))
        for i in range(0, len(rows), INSERT_CHUNK):
            cursor.executemany(
                f"INSERT INTO {SCHEMA}.group_excursion (backtest_run_id, direction, entry_time_utc, signal_bar_time, atr, ref_price, fill_price, "
                "entry_slip_atr, mfe_atr, end_kind, end_exc_atr, end_time_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows[i:i + INSERT_CHUNK])
    cursor.executemany(
        f"INSERT INTO {SCHEMA}.excursion_run (backtest_run_id, groups, matched, comparable, agree_tp1, agree_tp2, agree_tp3, computed_utc) "
        "VALUES (?,?,?,?,?,?,?, SYSUTCDATETIME())",
        [(s["backtest_run_id"], s["groups"], s["matched"], s["comparable"], s["agree_tp1"], s["agree_tp2"], s["agree_tp3"]) for s in summaries])
    conn.commit()


# --------------------------------------------------------------------------- #
# Lệnh
# --------------------------------------------------------------------------- #

def run(*, rebuild: bool = False, symbol: str | None = None, workers: int = 10, limit_runs: int | None = None) -> dict[str, Any]:
    started = time.time()
    with closing(master_db.connect(autocommit=False)) as conn:
        master_db.ensure_schema(conn)
        runs = pd.read_sql(_RUNS_SQL, conn)
        if symbol:
            runs = runs[runs["symbol"] == symbol]
        if not rebuild:
            done = {int(r[0]) for r in conn.execute(f"SELECT backtest_run_id FROM {SCHEMA}.excursion_run").fetchall()}
            runs = runs[~runs["backtest_run_id"].isin(done)]
        if limit_runs:
            runs = runs.head(limit_runs)
        if runs.empty:
            return {"runs": 0, "groups": 0, "seconds": 0}
        groups = _load_groups(conn, runs["backtest_run_id"].astype(int).tolist())
        blobs = _load_signal_blobs(conn, sorted(runs["signal_snapshot_id"].astype(int).unique().tolist()))

    jobs, missing = [], []
    for (sym, start, end), part in runs.groupby(["symbol", "start_date", "end_date_requested"]):
        folder = symbol_tick_dir(sym)
        if folder is None:
            missing.append(sym)
            continue
        ids = set(part["backtest_run_id"].astype(int))
        sub = groups[groups["backtest_run_id"].isin(ids)]
        jobs.append({
            "symbol": sym, "start": str(start), "end": str(end), "tick_dir": str(folder), "runs": part.to_dict("records"), "groups": sub,
            "blobs": {int(k): blobs[int(k)] for k in part["signal_snapshot_id"].astype(int).unique()}, "size": len(sub),
        })
    if missing:
        print("KHÔNG có cache tick cho:", sorted(set(missing)), file=sys.stderr)
    jobs.sort(key=lambda j: -j["size"])

    all_rows, all_summaries, ticks = [], [], 0
    with futures.ProcessPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(work_on_window, job) for job in jobs]
        for index, future in enumerate(futures.as_completed(pending), 1):
            result = future.result()
            all_rows.append(result["rows"])
            all_summaries.extend(result["summaries"])
            ticks += result["ticks"]
            print(f"  [{index}/{len(jobs)}] {result['symbol']} {result['window']}: {len(result['summaries'])} lượt, "
                  f"{len(result['rows'])} nhóm, {result['ticks']:,} tick, {time.time() - started:.0f}s", flush=True)
    kept = pd.concat([r for r in all_rows if len(r)], ignore_index=True) if any(len(r) for r in all_rows) else pd.DataFrame()
    with closing(master_db.connect(autocommit=False)) as conn:
        save(conn, kept, all_summaries)
    return {"runs": len(all_summaries), "groups": int(sum(s["groups"] for s in all_summaries)), "matched": len(kept), "ticks": ticks,
            "seconds": round(time.time() - started)}


def check() -> dict[str, Any]:
    """Tóm tắt độ khớp giữa 'chạm TPk tính từ tick' và kết cục thật của backtest (analysis.excursion_run)."""
    with closing(master_db.connect(autocommit=True)) as conn:
        row = conn.execute(
            f"SELECT COUNT(*), SUM(groups), SUM(matched), SUM(comparable), SUM(agree_tp1), SUM(agree_tp2), SUM(agree_tp3) FROM {SCHEMA}.excursion_run"
        ).fetchone()
    runs, groups, matched, comparable, a1, a2, a3 = (int(x or 0) for x in row)
    share = lambda part, whole: round(100 * part / whole, 2) if whole else None
    return {"runs": runs, "groups": groups, "matched_pct": share(matched, groups), "comparable": comparable,
            "agree_tp1_pct": share(a1, comparable), "agree_tp2_pct": share(a2, comparable), "agree_tp3_pct": share(a3, comparable)}


def pending_summary(runs: pd.DataFrame, done: set[int]) -> dict[str, Any]:
    """Trong các lượt đủ điều kiện (`runs`), bao nhiêu lượt chưa có trong excursion_run (`done`), đếm theo nguồn tín hiệu."""
    todo = runs[~runs["backtest_run_id"].isin(done)]
    by_source = {str(source): int(len(part)) for source, part in todo.groupby("signal_channel")}
    return {"eligible": int(len(runs)), "done": int(len(runs) - len(todo)), "pending": int(len(todo)), "pending_by_source": by_source}


def status() -> dict[str, Any]:
    """Còn bao nhiêu lượt đủ điều kiện mà CHƯA tính (cần `run`). Chỉ đọc SQL, không đọc tick nên chạy tức thì."""
    with closing(master_db.connect(autocommit=True)) as conn:
        runs = pd.read_sql(_RUNS_SQL, conn)
        done = {int(r[0]) for r in conn.execute(f"SELECT backtest_run_id FROM {SCHEMA}.excursion_run").fetchall()}
    return pending_summary(runs, done)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="còn bao nhiêu lượt chưa tính")
    run_parser = sub.add_parser("run", help="tính các lượt chưa có (mặc định) hoặc tất cả (--rebuild)")
    run_parser.add_argument("--rebuild", action="store_true")
    run_parser.add_argument("--symbol")
    run_parser.add_argument("--workers", type=int, default=10)
    run_parser.add_argument("--limit-runs", type=int)
    sub.add_parser("check", help="độ khớp với kết cục thật")
    args = parser.parse_args(argv)
    if args.command == "run":
        print(json.dumps(run(rebuild=args.rebuild, symbol=args.symbol, workers=args.workers, limit_runs=args.limit_runs), ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
    else:
        print(json.dumps(check(), ensure_ascii=False))


if __name__ == "__main__":
    main()
