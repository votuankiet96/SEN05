"""pipeline_lock.py — sổ ghi "tầng nào đã chạy với đầu vào gì, kết quả ở đâu"
cho 1 pipeline (symbol, timeframe, strategy) đầy đủ (Grid → Walk-forward →
DSR+PBO → Quyết định → Final backtest → Monte Carlo).

Nguyên lý content-addressable (đối chiếu DVC `dvc.lock` — xem
research_notes/pipeline-lock-and-dashboard-architecture.md): mỗi tầng có 1
`deps_hash` (dấu vân tay của MỌI thứ quyết định kết quả tầng đó). Hash trùng
lần ghi trước → dùng lại `outs` đã lưu, KHÔNG chạy/tính lại. Hash khác (hoặc
chưa từng chạy) → phải chạy/tính mới rồi ghi đè.

File này CHỈ là cơ chế đọc/ghi thuần (giống evidence.py/selection.py — nội
tạng của engine, không phải "sinh dữ liệu nghiên cứu mới") — không tự quyết
định KHI NÀO gọi, đó là việc của facilitator.py/walkforward.py/readout.py.

[2026-09-22] Đặt trong output_util/ (không phải core_engine/ gốc, theo yêu
cầu người dùng) — dù có ghi file (khác evidence/selection chỉ đọc), nó ghi
đúng loại "chỉ số/tham chiếu tới dữ liệu đã có", không sinh backtest/mô phỏng
mới nào — cùng bản chất "tổ chức lại output đã có" như evidence/selection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..store import stable_hash

# File này ở core_engine/output_util/pipeline_lock.py -> parents[2] mới đúng
# là bo_workflow/ (output_util -> core_engine -> bo_workflow).
ROOT = Path(__file__).resolve().parents[2]
LOCKS_ROOT = ROOT / "runs" / "pipeline_locks"
SCHEMA = "bo-pipeline-lock/v1"


def _lock_path(pipeline: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in pipeline)[:120]
    return LOCKS_ROOT / f"{safe}.lock.json"


def _read(pipeline: str) -> dict[str, Any]:
    path = _lock_path(pipeline)
    if not path.is_file():
        return {"schema": SCHEMA, "stages": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(pipeline: str, doc: dict[str, Any]) -> None:
    path = _lock_path(pipeline)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def stage_hash(*parts: Any) -> str:
    """Dấu vân tay của 1 tầng — mỗi phần tử tự serialize JSON (sort_keys, ổn
    định thứ tự) trước khi hash, nhận input bất kỳ hình dạng (dict/list/str/
    số/None/bool...), không chỉ chuỗi thuần như `store.stable_hash`."""
    return stable_hash(*(json.dumps(part, sort_keys=True, default=str) for part in parts))


def check(pipeline: str, stage: str, deps_hash: str) -> dict[str, Any] | None:
    """Trả về `outs` đã ghi nếu ĐÚNG `deps_hash` này đã chạy/tính rồi —
    ngược lại None (chưa từng làm, hoặc input đã đổi → phải làm lại)."""
    entry = _read(pipeline)["stages"].get(stage)
    if entry and entry.get("deps_hash") == deps_hash:
        return entry.get("outs")
    return None


def record(pipeline: str, stage: str, deps_hash: str, outs: dict[str, Any]) -> None:
    """Ghi (hoặc ghi đè) kết quả 1 tầng — gọi SAU KHI đã thực sự chạy/tính
    xong, không phải trước."""
    doc = _read(pipeline)
    doc.setdefault("stages", {})[stage] = {"deps_hash": deps_hash, "outs": outs}
    _write(pipeline, doc)


def get_stage(pipeline: str, stage: str) -> dict[str, Any] | None:
    """Đọc nguyên trạng 1 tầng — dùng khi tầng SAU cần dấu vân tay tầng TRƯỚC."""
    return _read(pipeline)["stages"].get(stage)


def read_all(pipeline: str) -> dict[str, Any]:
    """Đọc toàn bộ sổ 1 pipeline — dùng cho dashboard hiển thị."""
    return _read(pipeline)


def list_pipelines() -> list[str]:
    """Liệt kê tên mọi pipeline đã từng ghi sổ."""
    if not LOCKS_ROOT.is_dir():
        return []
    return sorted(p.name[: -len(".lock.json")] for p in LOCKS_ROOT.glob("*.lock.json"))
