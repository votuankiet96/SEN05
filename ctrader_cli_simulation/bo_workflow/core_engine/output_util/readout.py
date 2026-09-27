"""readout.py — tầng ĐỌC lại kết quả đã lưu, TỔNG QUÁT cho mọi phương pháp.

Không chạy backtest gì (việc của facilitator.py), không gắn với riêng phương
pháp nào: `screen()` xếp hạng được cả experiment grid lẫn walk-forward,
`run_montecarlo()`/`run_dsr()`/`run_pbo()` chạy được trên bất kỳ report/
experiment đã lưu nào, `export_release` đóng gói được bất kỳ run_dir nào.

Ranh giới: đọc kết quả RIÊNG của walk-forward (`walkforward_table`,
`walkforward_progress`) nằm ở `output_util/walkforward_readout.py` — cùng tầng
đọc nhưng tách file vì nó gắn với khuôn sidecar chỉ walk-forward sinh ra.

[2026-09-22] Dời vào `output_util/` cùng `evidence.py`/`selection.py` — cả 3 là
nhóm "output thuần" (chỉ tổ chức/trình bày lại số đã có, không mô phỏng/chạy
thêm gì) — phân biệt với `optimize/` (walkforward/montecarlo/dsr/pbo — mô
phỏng hoặc chạy backtest thật, không phải "output"). Xem [[module-boundary-principles]].

[2026-09-22] Thêm `run_dsr()`/`run_pbo()`: 2 module MỚI `optimize/dsr.py` và
`optimize/pbo.py` được thiết kế THUẦN (không biết `store`/report ở đâu — giống
`montecarlo.py`), nên cần đúng 1 "cầu nối" ở đây đọc N report ĐỦ ĐIỀU KIỆN TIN
theo ĐÚNG 1 policy đã đặt tên (`selection.eligible_under_policy()`) của 1
experiment đã chạy, trích chuỗi %lời/lỗ theo ngày mỗi trial
(`models.daily_returns()`, cùng hàm `run_montecarlo()` dùng), rồi mới gọi vào
hàm thuần — đúng khuôn `run_montecarlo()` đã có sẵn, không phải thiết kế mới.

[2026-09-25] `run_dsr()`/`run_pbo()` nhận `config["trial_policy"]` (mặc định
`selection.STRICT_RESEARCH_POLICY` — giữ nguyên hành vi cũ nếu không truyền
gì). `SUPPORTED_TRIAL_POLICIES` export lại `selection.TRIAL_POLICIES` — đây
là chuỗi mà `dashboard/actions.py` đã viết trước, tự dò qua
`getattr(readout, "SUPPORTED_TRIAL_POLICIES", ())` (xem
`completed_execution_policy_available()`), nên phải giữ đúng tên thuộc tính
này."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import evidence, pipeline_lock, selection
from ..models import daily_returns
from ..optimize import dsr, montecarlo, pbo
from ..store import ExperimentStore, export_release, sha256_file

__all__ = [
    "screen", "run_montecarlo", "run_dsr", "run_pbo", "export_release",
    "SUPPORTED_TRIAL_POLICIES",
]

# Dashboard dò capability qua getattr(readout, "SUPPORTED_TRIAL_POLICIES", ())
# TRƯỚC KHI cho phép chạy diagnostics policy mới — không được đổi tên thuộc
# tính này mà không báo trước cho phía dashboard.
SUPPORTED_TRIAL_POLICIES = selection.TRIAL_POLICIES


def screen(
    experiment: str, *, method: str = "grid", min_trades: int = 0,
    policy: str = selection.STRICT_RESEARCH_POLICY,
) -> list[dict[str, Any]]:
    """Xếp hạng nhanh mọi run đã chạy xong của 1 experiment (grid hoặc
    walkforward — truyền đúng `method`), chỉ giữ run đủ điều kiện theo policy
    (mặc định strict, giữ nguyên hành vi cũ)."""
    return selection.rank(
        ExperimentStore(method, experiment).flat_rows(), min_trades=min_trades, policy=policy,
    )


def run_montecarlo(
    report_json_gz: str | Path, config: Mapping[str, Any] | None = None,
    *, pipeline: str | None = None,
) -> dict[str, Any]:
    """Monte Carlo hậu kỳ trên 1 report đã lưu. Dời nguyên trạng từ
    facilitator.py 2026-09-21.

    [2026-09-22] Trả về SONG SONG 2 kết quả (`montecarlo.run_dual`) — "balance_based"
    (vốn đã chốt) và "min_equity_based" (đáy equity mỗi ngày, bắt lỗ nổi) — thay vì
    1 kết quả duy nhất như trước, theo chốt người dùng; xem
    research_notes/montecarlo-equity-vs-balance-daily-return-methodology.md.

    [2026-09-22] `pipeline` (tuỳ chọn) — tên pipeline theo dõi qua
    `pipeline_lock`, giống `facilitator.run_grid()`. Dấu vân tay dùng
    `sha256_file(report_json_gz)` (NỘI DUNG thật của report) thay vì chỉ
    đường dẫn — report đổi nội dung (chạy lại) thì hash đổi theo, dù đường
    dẫn không đổi.

    LƯU Ý (chưa sửa, chỉ ghi nhận): 3 mặc định daily_loss_pct/max_loss_pct/
    initial_balance dưới đây trùng số với `ftmo: ftmo-2step-v1` trong
    config.yaml nhưng là 2 nguồn tách biệt — sửa config.yaml không ảnh hưởng
    gì tới Monte Carlo. Walk-forward/Monte Carlo đang tạm dừng phát triển
    (2026-09-21), nên chưa nối `load_ftmo_profile()` vào đây; xem
    bo-workflow-backtest-pipeline.md."""
    config = config or {}
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash("monte_carlo", sha256_file(report_json_gz), dict(config))
        cached = pipeline_lock.check(pipeline, "monte_carlo", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    report = evidence.read_report(report_json_gz)
    mc_config = montecarlo.MonteCarloConfig(
        paths=int(config.get("paths", 10000)),
        block_days=int(config.get("block_days", 5)),
        seed=int(config.get("seed", 20260911)),
        timezone=str(config.get("timezone", "Europe/Prague")),
        initial_balance=float(config.get("initial_balance", 100000.0)),
        daily_loss_pct=float(config.get("daily_loss_pct", 5.0)),
        max_loss_pct=float(config.get("max_loss_pct", 10.0)),
    )
    result = montecarlo.run_dual((report.get("equity") or {}).get("points") or [], mc_config)
    if pipeline:
        pipeline_lock.record(pipeline, "monte_carlo", deps_hash, result)
    return result


def _resolve_trial_policy(config: Mapping[str, Any]) -> str:
    """Đọc + xác nhận `config["trial_policy"]` — policy lạ RAISE ngay, không
    âm thầm rơi về mặc định. Không truyền gì = strict (hành vi cũ)."""
    policy = str(config.get("trial_policy", selection.STRICT_RESEARCH_POLICY))
    if policy not in selection.TRIAL_POLICIES:
        raise ValueError(f"unknown trial_policy: {policy!r}; supported: {selection.TRIAL_POLICIES}")
    return policy


def _load_trial_returns(
    method: str,
    experiment: str,
    *,
    policy: str,
    field: str = "balance",
    timezone: str = "Europe/Prague",
    initial_balance: float = 100000.0,
    min_trades: int = 0,
) -> tuple[dict[str, tuple[float, ...]], dict[str, Any]]:
    """Đọc mọi run ĐỦ ĐIỀU KIỆN theo `policy` của 1 experiment đã chạy xong.

    Trả về (trials, diagnostics) — `trials` là nguyên liệu cho `run_dsr()`/
    `run_pbo()` như trước; `diagnostics` là phần MỚI [2026-09-25] báo cáo rõ
    quyết định phân loại (tổng dòng, số nhận/loại, margin rejection, lý do
    loại từng trial) để `run_dsr`/`run_pbo` gắn vào kết quả trả về — không
    được để dashboard tự đoán những con số này qua việc đọc lại SQLite riêng.
    Riêng tư trong file này — chỉ `run_dsr`/`run_pbo` gọi tới."""
    store = ExperimentStore(method, experiment)
    rows = store.flat_rows()
    trials: dict[str, tuple[float, ...]] = {}
    excluded: list[dict[str, Any]] = []
    margin_rejection_trials = 0
    margin_rejection_events = 0
    for row in rows:
        classification = selection.classify(row, min_trades=min_trades)
        margin = classification["margin_rejections"]
        if margin:
            margin_rejection_trials += 1
            margin_rejection_events += margin
        label = str(row.get("label"))
        included = selection.trial_included(classification, policy)
        if not included:
            excluded.append({"label": label, "reasons": selection.exclusion_reasons(classification, policy)})
            continue
        report_path = store.run_dir(str(row["run_id"]), label) / "report.json.gz"
        if not report_path.is_file():
            excluded.append({"label": label, "reasons": ["report_missing"]})
            continue
        report = evidence.read_report(report_path)
        points = (report.get("equity") or {}).get("points") or []
        returns = daily_returns(points, timezone, initial_balance, field_name=field)
        if not returns:
            excluded.append({"label": label, "reasons": ["no_daily_returns"]})
            continue
        trials[label] = tuple(returns)
    diagnostics = {
        "trial_policy": policy,
        "total_rows": len(rows),
        "included_trials": len(trials),
        "excluded_trials": len(excluded),
        "margin_rejection_trials": margin_rejection_trials,
        "margin_rejection_events": margin_rejection_events,
        "excluded": excluded,
    }
    return trials, diagnostics


def run_dsr(
    method: str, experiment: str, config: Mapping[str, Any] | None = None,
    *, pipeline: str | None = None,
) -> dict[str, Any]:
    """Deflated Sharpe Ratio hậu kỳ trên 1 experiment (grid hoặc walkforward)
    ĐÃ CHẠY XONG — đọc lại N report, tính DSR của trial thắng. Không chạy
    backtest mới. Xem research_notes/dsr-pbo-formula-verification.md.

    [2026-09-22] `pipeline` (tuỳ chọn) — theo dõi qua `pipeline_lock`. Dấu vân
    tay dùng `sha256_file(store.db_path)` — NỘI DUNG thật của SQLite (đổi 1
    dòng bất kỳ là hash đổi), không chỉ tên experiment (tên không đổi dù dữ
    liệu bên trong có bị chạy lại/sửa).

    [2026-09-25] `config["trial_policy"]` (mặc định strict) chọn cách phân
    loại trial trước khi tính DSR — xem `selection.classify()`. Policy lạ
    raise NGAY, trước cả khi kiểm cache (khoá cache đã gồm chính giá trị
    policy đã chuẩn hoá, nên strict/completed_execution KHÔNG BAO GIỜ trúng
    cache của nhau dù cùng experiment)."""
    config = dict(config or {})
    config["trial_policy"] = _resolve_trial_policy(config)
    store = ExperimentStore(method, experiment)
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash("dsr", sha256_file(store.db_path), config)
        cached = pipeline_lock.check(pipeline, "dsr", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    trials, diagnostics = _load_trial_returns(
        method, experiment,
        policy=config["trial_policy"],
        field=str(config.get("field", "balance")),
        timezone=str(config.get("timezone", "Europe/Prague")),
        initial_balance=float(config.get("initial_balance", 100000.0)),
        min_trades=int(config.get("min_trades", 0)),
    )
    result = {**dsr.run(trials), **diagnostics}
    if pipeline:
        pipeline_lock.record(pipeline, "dsr", deps_hash, result)
    return result


def run_pbo(
    method: str, experiment: str, config: Mapping[str, Any] | None = None,
    *, pipeline: str | None = None,
) -> dict[str, Any]:
    """Probability of Backtest Overfitting (CSCV) hậu kỳ trên 1 experiment ĐÃ
    CHẠY XONG — đọc lại N report, thử mọi cách chia đối xứng theo thời gian.
    Không chạy backtest mới. Xem research_notes/dsr-pbo-formula-verification.md.

    [2026-09-22] `pipeline` (tuỳ chọn) — cùng cơ chế `run_dsr()`.
    [2026-09-25] `config["trial_policy"]` — cùng cơ chế `run_dsr()`."""
    config = dict(config or {})
    config["trial_policy"] = _resolve_trial_policy(config)
    store = ExperimentStore(method, experiment)
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash("pbo", sha256_file(store.db_path), config)
        cached = pipeline_lock.check(pipeline, "pbo", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    trials, diagnostics = _load_trial_returns(
        method, experiment,
        policy=config["trial_policy"],
        field=str(config.get("field", "balance")),
        timezone=str(config.get("timezone", "Europe/Prague")),
        initial_balance=float(config.get("initial_balance", 100000.0)),
        min_trades=int(config.get("min_trades", 0)),
    )
    result = {**pbo.run(trials, blocks=int(config.get("blocks", pbo.DEFAULT_BLOCKS))), **diagnostics}
    if pipeline:
        pipeline_lock.record(pipeline, "pbo", deps_hash, result)
    return result
