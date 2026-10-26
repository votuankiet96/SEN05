"""portfolio_composition.py — thành phần của từng danh mục chạy thật (nhiều kênh, chung 1 tài khoản): kênh nào, rủi ro bao nhiêu,
lấy tín hiệu từ NGUỒN nào, cùng các thiết lập SL/TP.

Lượt danh mục lưu trong SQL chỉ ghi bản chụp tín hiệu 'mixed' — không nói kênh nào dùng luồng Redis nào (db0 Original, db1 KNN,
db2 Breakout, db4 ZigZag). Rủi ro % từng kênh nằm trong CSV của bản chụp; nguồn thì suy ra bằng cách so tín hiệu của kênh (giờ nến +
hướng, trong cửa sổ của lượt) với bản chụp ĐƠN của từng nguồn: nguồn nào trùng 100% là nguồn đã dùng. Hai nguồn cùng trùng 100%
(vd KNN không lọc bớt tín hiệu nào của kênh đó) thì không phân biệt được — ghi 'original|knn' để người dùng xác nhận, không đoán.

Kết quả ghi vào lược đồ RIÊNG `analysis` (analysis.portfolio_channel), KHÔNG thuộc dbo nên `ensure_schema` không đụng; dựng lại được từ
SQL. Chạy từ thư mục gốc repo:
    python -m bo_workflow.core_engine.output_util.portfolio_composition run [--rebuild]
    python -m bo_workflow.core_engine.output_util.portfolio_composition show [--batch TÊN]
Mặc định chỉ xử lý batch chưa có trong bảng (chạy lại sau khi có thêm danh mục mới).
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from contextlib import closing
from typing import Any, Iterable, Mapping

import pandas as pd

from .. import master_db

SCHEMA = "analysis"
TABLE = f"{SCHEMA}.portfolio_channel"
SOURCES = ("original", "knn", "breakout", "zigzag")
SNAPSHOTS_PER_SOURCE = 6        # bản chụp đơn gần nhất của mỗi nguồn được đem so
NEAR_MATCH = 0.98               # không nguồn nào trùng 100%: từ ngưỡng này trở lên ghi nhận 'gần khớp'
STRATEGY_NAME = {"combo": "Combo", "ma_cross": "MA Cross", "macd_break": "MACD Break"}

_DDL = (
    f"IF SCHEMA_ID('{SCHEMA}') IS NULL EXEC('CREATE SCHEMA {SCHEMA}')",
    f"""IF OBJECT_ID('{TABLE}') IS NULL
CREATE TABLE {TABLE} (
    batch_name nvarchar(200) NOT NULL,
    strategy varchar(20) NOT NULL,
    symbol varchar(30) NOT NULL,
    timeframe varchar(10) NOT NULL,
    channel_name nvarchar(100) NOT NULL,
    risk_percent float NOT NULL,
    source varchar(40) NOT NULL,
    source_match float NULL,
    source_note nvarchar(200) NULL,
    signals int NOT NULL,
    ksl float NULL,
    tp1 float NULL,
    tp2 float NULL,
    tp3 float NULL,
    legs int NULL,
    CONSTRAINT pk_portfolio_channel PRIMARY KEY (batch_name, strategy, symbol, timeframe))""",
)

_BATCHES_SQL = """
WITH t AS (
    SELECT backtest_run_id FROM dbo.trade WHERE leg_label IS NOT NULL
    GROUP BY backtest_run_id HAVING COUNT(DISTINCT LEFT(leg_label, LEN(leg_label) - 3)) > 1),
x AS (
    SELECT r.batch_name, r.backtest_run_id, r.signal_snapshot_id, r.ksl_atr_multiple, r.ktp_atr_multiple, r.leg_count, r.parameters_json,
           ROW_NUMBER() OVER (PARTITION BY r.batch_name ORDER BY r.backtest_run_id) AS rn,
           CONVERT(varchar(10), MIN(r.start_date) OVER (PARTITION BY r.batch_name), 120) AS win_start,
           CONVERT(varchar(10), MAX(r.end_date_requested) OVER (PARTITION BY r.batch_name), 120) AS win_end
    FROM dbo.backtest_run r JOIN t ON t.backtest_run_id = r.backtest_run_id)
SELECT x.batch_name, x.signal_snapshot_id, x.win_start, x.win_end, x.ksl_atr_multiple AS ksl, x.ktp_atr_multiple AS tp1,
       x.leg_count AS legs, x.parameters_json, s.channel AS snapshot_channel
FROM x JOIN dbo.signal_snapshot s ON s.signal_snapshot_id = x.signal_snapshot_id
WHERE x.rn = 1
"""


# --------------------------------------------------------------------------- #
# Hàm thuần (không chạm SQL)
# --------------------------------------------------------------------------- #

def fib_value(level: Any) -> float | None:
    """'Fib3330' -> 3.33 (tên mức Fib của BacktestBot = số nhân ATR × 1000)."""
    text = str(level or "")
    return int(text[3:]) / 1000 if text.startswith("Fib") and text[3:].isdigit() else None


def channel_name(strategy: str, symbol: str, timeframe: str) -> str:
    """Đúng chuỗi `Trade.channel_name` của mô hình Power BI (nhãn lệnh `cb_HK50_H3`): chiến lược · symbol bỏ '.cash' · khung IN HOA."""
    return f"{STRATEGY_NAME.get(strategy, strategy)} · {symbol.replace('.cash', '')} · {timeframe.upper()}"


def decode_signals(blob: bytes) -> pd.DataFrame:
    """CSV nén gzip của bản chụp tín hiệu -> bảng (bartime, signal, tf, symbol, strategy, risk_percent, ...)."""
    return pd.read_csv(io.BytesIO(gzip.decompress(bytes(blob))))


def channel_signals(frame: pd.DataFrame, win_start: str, win_end: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Gom theo kênh (chiến lược, symbol, khung thường): rủi ro % và tập (giờ nến, hướng) trong cửa sổ. Hướng chuẩn hoá như
    bot: 1 = mua, 2 hoặc -1 = bán."""
    inside = frame[(frame["bartime"] >= win_start) & (frame["bartime"] <= win_end + " 23:59:59")]
    has_risk = "risk_percent" in frame.columns          # bản chụp đơn cũ không có cột rủi ro
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (strategy, symbol, tf), part in frame.groupby(["strategy", "symbol", "tf"]):
        window = inside[(inside["strategy"] == strategy) & (inside["symbol"] == symbol) & (inside["tf"] == tf)]
        sides = window["signal"].map(lambda v: 1 if v == 1 else -1)
        out[(str(strategy), str(symbol), str(tf).lower())] = {
            "risk": float(part["risk_percent"].max()) if has_risk else float("nan"),
            "risk_min": float(part["risk_percent"].min()) if has_risk else float("nan"),
            "signals": frozenset(zip(window["bartime"], sides)),
        }
    return out


def single_channel_signals(frame: pd.DataFrame, key: tuple[str, str, str], win_start: str, win_end: str) -> frozenset:
    """Tập (giờ nến, hướng) của MỘT kênh trong bản chụp đơn. Bản chụp đơn cũ không có cột strategy/symbol/tf (cả bản chụp là 1 kênh)
    thì lấy hết."""
    strategy, symbol, timeframe = key
    if {"strategy", "symbol", "tf"} <= set(frame.columns):
        frame = frame[(frame["strategy"] == strategy) & (frame["symbol"] == symbol) & (frame["tf"].str.lower() == timeframe)]
    inside = frame[(frame["bartime"] >= win_start) & (frame["bartime"] <= win_end + " 23:59:59")]
    return frozenset(zip(inside["bartime"], inside["signal"].map(lambda v: 1 if v == 1 else -1)))


def jaccard(a: frozenset, b: frozenset) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def decide_source(scores: Mapping[str, float], declared: str | None) -> tuple[str, float | None, str]:
    """Từ điểm trùng của từng nguồn (0..1) chọn nguồn của kênh. Trả (nguồn, điểm trùng, ghi chú).
    `declared` = kênh của bản chụp lượt khi lượt chạy từ MỘT nguồn (không phải 'mixed'): tin theo đó."""
    if declared in SOURCES:
        score = scores.get(declared)
        note = "theo bản chụp của lượt" if score is None or score >= 1 else f"theo bản chụp của lượt; trùng {score:.1%}"
        return declared, score, note
    exact = [src for src in SOURCES if scores.get(src, 0.0) >= 1.0]
    if len(exact) == 1:
        return exact[0], 1.0, "trùng 100%"
    if len(exact) > 1:
        return "|".join(exact), 1.0, "tín hiệu của kênh giống hệt ở: " + ", ".join(exact) + " (không phân biệt được)"
    ranked = sorted(((scores.get(src, 0.0), src) for src in SOURCES), reverse=True)
    best_score, best_src = ranked[0] if ranked else (0.0, "")
    if best_score >= NEAR_MATCH:
        return best_src, best_score, f"gần khớp {best_score:.1%}"
    return "unknown", best_score, f"không nguồn nào trùng; cao nhất {best_src} {best_score:.1%}"


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #

def ensure_table(conn) -> None:
    for statement in _DDL:
        conn.execute(statement)
    conn.commit()


def _query(conn, sql: str, *params: Any) -> pd.DataFrame:
    cursor = conn.cursor()
    cursor.execute(sql, *params)
    return pd.DataFrame.from_records([tuple(row) for row in cursor.fetchall()], columns=[col[0] for col in cursor.description])


def _snapshot_frame(conn, snapshot_id: int, cache: dict[int, pd.DataFrame]) -> pd.DataFrame:
    if snapshot_id not in cache:
        row = conn.execute("SELECT rows_compressed FROM dbo.signal_snapshot WHERE signal_snapshot_id = ?", int(snapshot_id)).fetchone()
        cache[snapshot_id] = decode_signals(row[0])
    return cache[snapshot_id]


def source_scores(conn, key: tuple[str, str, str], signals: frozenset, win: tuple[str, str], cache: dict[int, pd.DataFrame],
                  listing: dict[tuple[str, str, str], pd.DataFrame]) -> dict[str, float]:
    """Điểm trùng (Jaccard) của kênh với bản chụp ĐƠN tốt nhất của từng nguồn."""
    strategy, symbol, timeframe = key
    if key not in listing:
        marks = ",".join("?" * len(SOURCES))
        listing[key] = _query(
            conn, f"SELECT signal_snapshot_id, channel FROM dbo.signal_snapshot WHERE strategy = ? AND symbol = ? AND timeframe = ? "
                  f"AND channel IN ({marks}) ORDER BY captured_at_utc DESC", strategy, symbol, timeframe, *SOURCES)
    scores: dict[str, float] = {}
    for source, group in listing[key].groupby("channel"):
        for snapshot_id in group["signal_snapshot_id"].head(SNAPSHOTS_PER_SOURCE):
            theirs = single_channel_signals(_snapshot_frame(conn, int(snapshot_id), cache), key, *win)
            scores[source] = max(scores.get(source, 0.0), jaccard(signals, theirs))
    return scores


def build_rows(conn, batch: Mapping[str, Any], cache: dict[int, pd.DataFrame], listing: dict) -> list[tuple]:
    frame = _snapshot_frame(conn, int(batch["signal_snapshot_id"]), cache)
    win = (str(batch["win_start"]), str(batch["win_end"]))
    params = json.loads(batch["parameters_json"] or "{}")
    legs = int(batch["legs"] or 0)
    tp2 = fib_value(params.get("Leg2Ktp")) if legs >= 2 else None
    tp3 = fib_value(params.get("Leg3Ktp")) if legs >= 3 else None
    declared = batch["snapshot_channel"] if batch["snapshot_channel"] in SOURCES else None
    rows = []
    for key, info in sorted(channel_signals(frame, *win).items()):
        scores = source_scores(conn, key, info["signals"], win, cache, listing)
        source, score, note = decide_source(scores, declared)
        if info["risk"] != info["risk_min"]:
            note = f"{note}; rủi ro đổi từ {info['risk_min']} tới {info['risk']}"
        rows.append((batch["batch_name"], key[0], key[1], key[2], channel_name(*key), info["risk"], source, score, note,
                     len(info["signals"]), float(batch["ksl"]), float(batch["tp1"]), tp2, tp3, legs))
    return rows


def run(*, rebuild: bool = False) -> dict[str, Any]:
    with closing(master_db.connect(autocommit=False)) as conn:
        ensure_table(conn)
        batches = _query(conn, _BATCHES_SQL)
        done = {row[0] for row in conn.execute(f"SELECT DISTINCT batch_name FROM {TABLE}").fetchall()}
        todo = batches if rebuild else batches[~batches["batch_name"].isin(done)]
        cache: dict[int, pd.DataFrame] = {}
        listing: dict = {}
        written = 0
        for batch in todo.to_dict("records"):
            rows = build_rows(conn, batch, cache, listing)
            conn.execute(f"DELETE FROM {TABLE} WHERE batch_name = ?", batch["batch_name"])
            conn.cursor().executemany(
                f"INSERT INTO {TABLE} (batch_name, strategy, symbol, timeframe, channel_name, risk_percent, source, source_match, source_note, "
                "signals, ksl, tp1, tp2, tp3, legs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            written += len(rows)
        return {"batches": len(batches), "processed": len(todo), "rows": written}


def show(batch: str | None = None) -> None:
    with closing(master_db.connect(autocommit=True)) as conn:
        frame = _query(conn, f"SELECT * FROM {TABLE}" + (" WHERE batch_name = ?" if batch else ""), *([batch] if batch else []))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(f"{len(frame)} dòng, {frame['batch_name'].nunique()} danh mục")
    summary = frame.groupby(["batch_name"]).agg(kenh=("channel_name", "size"), rui_ro=("risk_percent", "sum"),
                                                nguon=("source", lambda s: ", ".join(f"{k}:{v}" for k, v in s.value_counts().items())))
    print(summary.to_string())
    odd = frame[~frame["source"].isin(SOURCES)]
    if len(odd):
        print("\nKênh KHÔNG xác định được một nguồn duy nhất:")
        print(odd[["batch_name", "channel_name", "source", "source_note"]].drop_duplicates(["channel_name", "source"]).to_string(index=False))


def main(argv: Iterable[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--rebuild", action="store_true")
    p_show = sub.add_parser("show")
    p_show.add_argument("--batch")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "run":
        print(run(rebuild=args.rebuild))
    else:
        show(args.batch)


if __name__ == "__main__":
    main()
