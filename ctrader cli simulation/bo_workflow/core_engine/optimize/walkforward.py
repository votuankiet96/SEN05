"""walkforward.py — TOÀN BỘ phương pháp walk-forward, gom về đúng 1 file.

Walk-forward là 1 PHƯƠNG PHÁP nghiên cứu xây trên bộ thực thi lõi
(`facilitator.run_experiment`), không phải 1 cách chạy khác. Nó trả lời câu hỏi
khác hẳn `run_grid()`: không phải "tổ hợp nào lãi nhất trên toàn bộ dữ liệu"
(dễ khớp quá khứ), mà "tổ hợp chọn được trên đoạn HỌC có còn lãi trên đoạn KIỂM
mà nó CHƯA HỀ nhìn thấy hay không".

Mọi thứ chỉ phục vụ walk-forward đều nằm ở đây, không rải ra file khác
(gom 2026-09-22):
  - `walkforward_windows()` + `_month_add()`  — chia cửa sổ trượt HỌC:KIỂM
  - `WF_*`                                    — tham số mặc định của luật chọn
  - `plateau()`, `pick()`                     — luật chọn tổ hợp thắng mỗi cửa sổ
  - `run_walkforward()`                       — điều phối 4 chặng

Đọc lại kết quả (`walkforward_table`, `walkforward_progress`) nằm ở
`output_util/walkforward_readout.py` [2026-09-25] — thuần đọc, không import
facilitator, để tầng đọc (dashboard) dùng được mà không kéo theo bộ thực thi.
Khuôn zone/sidecar dùng chung hằng số định nghĩa ở đó.

Những gì file này ĐI MƯỢN đều là hạ tầng CHUNG thật sự, đã public sẵn ở đúng
nhà của nó (không import ký hiệu riêng tư của file khác):
  - `facilitator.run_experiment()` — bộ thực thi lõi, `run_grid()` cũng dùng
  - `planner.*`                    — dựng RunSpec, validate config
  - `selection.rank/eligible`      — xếp hạng/lọc dùng được cho mọi phương pháp
  - `models.coerce_float`          — ép số an toàn
  - `store.ExperimentStore`        — đọc/ghi state
"""
from __future__ import annotations

import math
from calendar import monthrange
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Iterable, Mapping

from ..configuration import load_engine_profile, load_strategy_profile
from ..facilitator import resolve_input_fingerprint, run_experiment
from ..models import Window, coerce_float
from ..output_util import pipeline_lock, selection
from ..output_util.walkforward_readout import OOS_ZONE, SELECTION_SIDECAR, TRAIN_ZONE
from ..planner import as_date, build_experiment, expand_grid, nonnegative_int, positive_int
from ..store import ExperimentStore

# Luat chon to hop thang o moi cua so HOC. Day la diem cot loi cua ca phuong
# phap: luat phai chot TRUOC khi nhin ket qua, neu khong thi chinh buoc "chon"
# lai la mot vong khop qua khu nua, va so lieu doan KIEM het sach y nghia.
# CHUA dua ra config.yaml — mỗi giá trị vẫn override được qua config của từng
# experiment (`min_trades`, `plateau_dimensions`, `selection_rules`).
WF_MIN_TRADES = 30                 # duoi nguong nay thi so dep chi la ngau nhien
WF_DIMENSIONS = ("KslLevel", "KtpLevel")
WF_PLATEAU_MIN_NEIGHBORS = 4       # o ria luoi co it hang xom hon o giua
WF_PLATEAU_PERCENTILE = 0.25       # cham o bang gia tri KEM thu 25% cua ca xom
WF_RULES = ("plateau", "best")


# --------------------------------------------------------------------------- #
# 1. Chia cửa sổ trượt HỌC:KIỂM
# --------------------------------------------------------------------------- #
def _month_add(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def walkforward_windows(config: Mapping[str, Any]) -> list[tuple[Window, Window]]:
    """Chia khoảng thời gian thành các cặp (cửa sổ HỌC, cửa sổ KIỂM) trượt dần.

    Mặc định 12 tháng học : 3 tháng kiểm, trượt 3 tháng/lần — đổi qua
    `config["walkforward"] = {"is_months":…, "oos_months":…, "step_months":…}`.
    """
    wf = config.get("walkforward") or {}
    base = config.get("zones") or {}
    start, end = map(as_date, base.get("walkforward_range", [config["start"], config["end"]]))
    is_months = positive_int(wf.get("is_months", 12), "is_months")
    oos_months = positive_int(wf.get("oos_months", 3), "oos_months")
    step_months = positive_int(wf.get("step_months", oos_months), "step_months")
    pairs: list[tuple[Window, Window]] = []
    cursor = start
    index = 1
    while True:
        # Moc ket thuc la LOAI TRU (giong o "To" cua GUI va co --end cua CLI, doi
        # 2026-09-13). Vi vay KHONG tru 1 ngay o day: `is_end` vua la ngay dau
        # tien KHONG thuoc doan hoc, vua la ngay dau tien CUA doan kiem — hai
        # doan giap nhau khit, khong chong lan va khong ho ngay nao.
        # Ban cu (viet theo quy uoc cu "end la ngay bao gom") tru 1 ngay o ca hai
        # moc, nen moi cua so am tham mat ngay cuoi cung.
        is_end = _month_add(cursor, is_months)
        oos_end = _month_add(is_end, oos_months)
        if oos_end > end:
            break
        pairs.append((
            Window(f"wf{index:02d}_is", cursor, is_end, TRAIN_ZONE),
            Window(f"wf{index:02d}_oos", is_end, oos_end, OOS_ZONE),
        ))
        cursor = _month_add(cursor, step_months)
        index += 1
    return pairs


# --------------------------------------------------------------------------- #
# 2. Luật chọn tổ hợp thắng trên mỗi cửa sổ HỌC
# --------------------------------------------------------------------------- #
def plateau(
    rows: Iterable[Mapping[str, Any]],
    *,
    dimensions: tuple[str, ...],
    objective: str = "net_profit",
    min_neighbors: int = WF_PLATEAU_MIN_NEIGHBORS,
    percentile: float = WF_PLATEAU_PERCENTILE,
) -> list[dict[str, Any]]:
    """Chấm mỗi ô bằng giá trị KÉM trong đám hàng xóm quanh nó — ô thắng phải
    nằm giữa 1 vùng cùng tốt, không phải đỉnh cô lập (chống khớp quá khứ)."""
    raw: dict[tuple[int, ...], dict[str, Any]] = {}
    for row in rows:
        params = row.get("params") or {}
        try:
            coord = tuple(int(params[name]) for name in dimensions)
        except (KeyError, ValueError, TypeError):
            continue
        if selection.eligible(row):
            raw[coord] = dict(row)
    # "Hang xom" phai la muc KE NHAU TRONG SO CAC MUC DA QUET, khong phai so
    # thu tu enum lien nhau. Quet cach muc (vd enum 0, 2, 4) thi theo so enum
    # se khong o nao ke o nao -> ham tra ve rong ma khong bao gi. Danh lai so
    # thu tu dac cho tung chieu truoc khi do khoang cach.
    axes = [sorted({coord[i] for coord in raw}) for i in range(len(dimensions))]
    ranks = [{value: index for index, value in enumerate(axis)} for axis in axes]
    grid = {tuple(ranks[i][coord[i]] for i in range(len(dimensions))): row
            for coord, row in raw.items()}
    scored: list[dict[str, Any]] = []
    for coord, row in grid.items():
        neighbors = []
        for other_coord, other in grid.items():
            if max(abs(a - b) for a, b in zip(coord, other_coord)) <= 1:
                value = coerce_float(other.get(objective), math.nan)
                if math.isfinite(value):
                    neighbors.append(value)
        if len(neighbors) < min_neighbors:
            continue
        neighbors.sort()
        idx = min(len(neighbors) - 1, max(0, int((len(neighbors) - 1) * percentile)))
        item = dict(row)
        item["plateau_score"] = neighbors[idx]
        item["plateau_neighbors"] = len(neighbors)
        scored.append(item)
    return sorted(scored, key=lambda row: row["plateau_score"], reverse=True)


def pick(
    rows: list[Mapping[str, Any]], rule: str, *,
    dimensions: tuple[str, ...], min_trades: int, objective: str,
) -> dict[str, Any] | None:
    """Một cửa sổ HỌC -> một tổ hợp thắng, theo đúng một luật đã đặt tên.

    "plateau" ưu tiên vùng cao nguyên (tham số bền, lệch 1 nấc vẫn chạy được).
    "best" chỉ lấy ô lãi nhất — giữ làm ĐỐI CHỨNG để đo bằng số xem plateau có
    thật sự tốt hơn không.
    """
    pool = [row for row in rows if int(row.get("total_trades") or 0) >= min_trades]
    if rule == "plateau":
        ranked = plateau(pool, dimensions=dimensions, objective=objective)
    elif rule == "best":
        ranked = selection.rank(pool, min_trades=min_trades, by=(objective,))
    else:
        raise ValueError(f"unknown selection rule: {rule!r}")
    return ranked[0] if ranked else None


# --------------------------------------------------------------------------- #
# 3. Điều phối 4 chặng
# --------------------------------------------------------------------------- #
def run_walkforward(
    config: Mapping[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Walk-forward đầy đủ 4 chặng, không phải chỉ quét lưới trên nhiều cửa sổ.

    1. chia cửa sổ (học 12 tháng : kiểm 3 tháng, trượt 3 tháng — đổi ở config)
    2. quét TOÀN BỘ lưới trên từng đoạn HỌC
    3. mỗi đoạn học chọn ra tổ hợp thắng theo từng luật trong `selection_rules`
    4. chạy ĐÚNG MỘT lượt cho tổ hợp đó trên đoạn KIỂM kế tiếp

    Chặng 4 tuyệt đối không quét gì: đúng một lệnh chạy cho một tổ hợp đã chọn.
    Quét ở đây là mất sạch ý nghĩa out-of-sample.

    [2026-09-22] `config["pipeline"]` (tuỳ chọn) — giống hệt cơ chế
    `facilitator.run_grid()`, xem output_util/pipeline_lock.py.

    [2026-09-24] Nhận `on_progress`/`should_cancel` (cùng hợp đồng run_grid)
    và truyền xuống CẢ chặng HỌC lẫn chặng KIỂM. Kiểm tra cửa sổ chạy TRƯỚC
    cache (kiểm config thuần, không tốn gì) vì khoá cache cần
    `resolve_input_fingerprint()`. Chặng HỌC chưa trọn vẹn (bị huỷ hoặc có
    tổ hợp lỗi) thì DỪNG, không chọn tham số trên lưới thiếu ô — chọn trên lưới
    thiếu sẽ ra "tổ hợp thắng" khác với lưới đủ. Chỉ ghi sổ khi cả 2 chặng
    hoàn tất trọn vẹn."""
    pairs = walkforward_windows(config)
    if not pairs:
        raise ValueError(
            "khong du du lieu cho mot cua so nao — can it nhat is_months + oos_months "
            "thang giua start va end"
        )
    pipeline = config.get("pipeline")
    deps_hash = None
    run_config: Mapping[str, Any] = config
    if pipeline or config.get("expected_input_fingerprint") is not None:
        fingerprint = resolve_input_fingerprint(config)
        # Both phases must run on exactly the hashed inputs.
        run_config = {**dict(config), "expected_input_fingerprint": fingerprint}
        if pipeline:
            deps_hash = pipeline_lock.stage_hash(
                "walkforward", config.get("symbols"), config.get("timeframe"), config.get("strategy"),
                config.get("strategy_profile"), config.get("engine_profile"),
                config.get("start"), config.get("end"), config.get("balance"),
                config.get("parameter_space"), config.get("fixed_params"), config.get("walkforward"),
                config.get("selection_rules"), config.get("plateau_dimensions"),
                config.get("min_trades"), config.get("objective"), config.get("zones"), fingerprint,
            )
            cached = pipeline_lock.check(pipeline, "walkforward", deps_hash)
            if cached is not None:
                return {**cached, "cached": True}
    experiment = build_experiment({**dict(config), "method": "walkforward"})
    profile = load_strategy_profile(experiment.strategy_profile)
    engine = load_engine_profile(experiment.engine_profile)
    doc = experiment.canonical()
    min_trades = nonnegative_int(config.get("min_trades", WF_MIN_TRADES), "min_trades")
    objective = str(config.get("objective", "net_profit"))
    dimensions = tuple(config.get("plateau_dimensions") or WF_DIMENSIONS)
    rules = tuple(config.get("selection_rules") or WF_RULES)

    # --- Chặng 2: quét lưới trên mỗi đoạn HỌC ------------------------------ #
    train_specs = expand_grid(config, profile, engine, windows=[pair[0] for pair in pairs])
    train = run_experiment(
        run_config, doc, profile, train_specs, engine=engine,
        on_progress=on_progress, should_cancel=should_cancel,
    )
    store = ExperimentStore(str(doc["method"]), str(doc["name"]))
    if not train.get("complete"):
        return {
            "experiment": doc["name"], "method": doc["method"], "root": str(store.root),
            "windows": len(pairs), "rules": list(rules),
            "train": {"total": train["total"], "tally": train["tally"]},
            "test": {"total": 0, "tally": {}},
            "phase_stopped": "train",
            "cancelled": bool(train.get("cancelled")),
            "complete": False,
            "failed": int(train.get("failed") or 0),
            "daily_cli_budget_exhausted": bool(train.get("daily_cli_budget_exhausted")),
            "inputs": train.get("inputs"),
            "wall_seconds": train["wall_seconds"],
        }

    # --- Chặng 3: chọn tổ hợp thắng cho từng (symbol, khung, cửa sổ) ------- #
    by_window: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in store.flat_rows():
        key = (str(row.get("symbol")), str(row.get("timeframe")), str(row.get("window")))
        by_window.setdefault(key, []).append(row)
    templates = {(spec.symbol, spec.timeframe): spec for spec in train_specs}

    decisions: list[dict[str, Any]] = []
    test_specs: list[Any] = []
    for is_window, oos_window in pairs:
        for (symbol, timeframe), template in templates.items():
            pool = by_window.get((symbol, timeframe, is_window.name), [])
            for rule in rules:
                winner = pick(
                    pool, rule, dimensions=dimensions,
                    min_trades=min_trades, objective=objective,
                )
                decisions.append({
                    "symbol": symbol, "timeframe": timeframe, "rule": rule,
                    "train_window": is_window.name, "test_window": oos_window.name,
                    "train_start": is_window.start, "train_end": is_window.end,
                    "test_start": oos_window.start, "test_end": oos_window.end,
                    "candidates": len(pool),
                    "eligible": sum(1 for row in pool if selection.eligible(row)),
                    "selected_params": dict(winner["params"]) if winner else None,
                    "train_run_id": winner.get("run_id") if winner else None,
                    "plateau_score": winner.get("plateau_score") if winner else None,
                })
                if winner is None:
                    continue
                test_specs.append(replace(
                    template,
                    start=oos_window.start, end=oos_window.end,
                    zone=oos_window.zone, window=oos_window.name,
                    params=dict(winner["params"]),
                    tags={"rule": rule, "train_window": is_window.name},
                ))
    store.write_sidecar(SELECTION_SIDECAR, {
        "schema": "bo-research-walkforward-selection/v1",
        "min_trades": min_trades, "objective": objective,
        "dimensions": list(dimensions), "rules": list(rules),
        "decisions": decisions,
    })

    # --- Chặng 4: chạy đoạn KIỂM, ĐÚNG MỘT lượt cho mỗi tổ hợp đã chọn ----- #
    empty = {"total": 0, "tally": {}, "wall_seconds": 0.0, "complete": True, "cancelled": False, "failed": 0}
    test = run_experiment(
        run_config, doc, profile, test_specs, engine=engine,
        on_progress=on_progress, should_cancel=should_cancel,
    ) if test_specs else empty
    complete = bool(test.get("complete"))
    result = {
        "experiment": doc["name"], "method": doc["method"], "root": str(store.root),
        "windows": len(pairs), "rules": list(rules),
        "train": {"total": train["total"], "tally": train["tally"]},
        "test": {"total": test["total"], "tally": test["tally"]},
        "windows_without_a_pick": sum(1 for d in decisions if d["selected_params"] is None),
        "phase_stopped": None if complete else "test",
        "cancelled": bool(test.get("cancelled")),
        "complete": complete,
        "failed": int(test.get("failed") or 0),
        "daily_cli_budget_exhausted": bool(test.get("daily_cli_budget_exhausted")),
        "inputs": train.get("inputs"),
        "wall_seconds": round(train["wall_seconds"] + test["wall_seconds"], 3),
    }
    if pipeline and complete:
        pipeline_lock.record(pipeline, "walkforward", deps_hash, result)
    return result
