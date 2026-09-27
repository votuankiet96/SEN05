"""walkforward_readout.py — đọc lại kết quả walk-forward, THUẦN ĐỌC.

Không import facilitator hay optimize/: tầng đọc (dashboard/data_access.py,
readout...) dùng được mà không kéo theo bộ thực thi backtest. Khuôn dữ liệu của
walk-forward — zone của 2 chặng và sidecar quyết định chọn — định nghĩa ở đây là
nguồn DUY NHẤT; `optimize/walkforward.py` ghi theo đúng các hằng số này, nên bên
đọc không phải tự chép lại chuỗi "train"/"oos"/"selection.json".
"""
from __future__ import annotations

import json
from typing import Any

from ..store import ExperimentStore

METHOD = "walkforward"
TRAIN_ZONE = "train"
OOS_ZONE = "oos"
SELECTION_SIDECAR = "selection.json"
_METRICS = ("net_profit", "profit_factor", "total_trades", "win_rate", "max_equity_drawdown_pct")


def walkforward_table(experiment: str) -> list[dict[str, Any]]:
    """Một dòng mỗi (cửa sổ x luật): tham số đã chọn, kết quả HỌC, kết quả KIỂM.

    Ghép quyết định chọn (sidecar) với kết quả thật của đoạn kiểm (bảng runs).
    Dòng HỌC ghép theo `run_id` (label không phân biệt được cửa sổ); dòng KIỂM
    ghép theo tags (rule, train_window) của spec."""
    store = ExperimentStore(METHOD, experiment)
    if not store.db_path.is_file():
        return []
    sidecar = store.read_sidecar(SELECTION_SIDECAR) or {}
    rows = store.flat_rows()
    tests: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        tags = (json.loads(row.get("spec_json") or "{}") or {}).get("tags") or {}
        if tags.get("rule"):
            tests[(tags["rule"], tags.get("train_window"),
                   row.get("symbol"), row.get("timeframe"))] = row
    trains = {str(row.get("run_id")): row for row in rows}
    out = []
    for decision in sidecar.get("decisions", []):
        test = tests.get((decision["rule"], decision["train_window"],
                          decision["symbol"], decision["timeframe"])) or {}
        train = trains.get(str(decision.get("train_run_id"))) or {}
        out.append({
            **{key: decision.get(key) for key in (
                "symbol", "timeframe", "rule", "train_window", "test_window",
                "train_start", "train_end", "test_start", "test_end",
                "candidates", "eligible", "selected_params", "plateau_score")},
            **{f"train_{key}": train.get(key) for key in _METRICS},
            **{f"test_{key}": test.get(key) for key in _METRICS},
            "test_status": test.get("status"),
            "test_run_id": test.get("run_id"),
            "test_label": test.get("label"),
        })
    return out


def walkforward_progress(experiment: str) -> dict[str, Any]:
    """Số dòng đã có của từng chặng (HỌC/KIỂM) theo trạng thái, cùng số quyết
    định chọn nếu chặng HỌC đã xong.

    Mẫu số của chặng HỌC (số cửa sổ x lưới) nằm ở plan của bên gọi, không ở
    store. Mẫu số chặng KIỂM = `selection["selected"]` khi sidecar đã có
    (sidecar chỉ được ghi khi chặng HỌC trọn vẹn); trước đó chưa biết."""
    store = ExperimentStore(METHOD, experiment)
    phases = {zone: {"rows": 0, "finished": 0, "running": 0, "counts": {}} for zone in (TRAIN_ZONE, OOS_ZONE)}
    if not store.db_path.is_file():
        return {"exists": False, "train": phases[TRAIN_ZONE], "oos": phases[OOS_ZONE], "selection": None}
    for row in store.rows():
        phase = phases.get(str(row.get("zone")))
        if phase is None:
            continue
        status = str(row.get("status") or "unknown")
        phase["rows"] += 1
        phase["counts"][status] = phase["counts"].get(status, 0) + 1
        if status == "running":
            phase["running"] += 1
        else:
            phase["finished"] += 1
    sidecar = store.read_sidecar(SELECTION_SIDECAR)
    selection = None
    if sidecar:
        decisions = sidecar.get("decisions") or []
        selected = sum(1 for decision in decisions if decision.get("selected_params") is not None)
        selection = {"decisions": len(decisions), "selected": selected, "without_pick": len(decisions) - selected}
    return {"exists": True, "train": phases[TRAIN_ZONE], "oos": phases[OOS_ZONE], "selection": selection}
