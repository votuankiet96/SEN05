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
này.

[2026-09-28] `run_dsr()`/`run_pbo()` nhận `config["objective"]` (mặc định
`net_profit`) — tiêu chí dùng để CHỌN tổ hợp (giống walk-forward). DSR tính
thêm cho đúng trial đứng đầu theo tiêu chí đó (luật "best", cùng
`selection.rank`), PBO mô phỏng đúng tiêu chí đó trong mỗi cách chia. Thêm
`run_montecarlo_oos()`: Monte Carlo trên chuỗi đoạn KIỂM nối lại của 1
walk-forward, thay vì trên backtest đã tối ưu trong mẫu."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple
from zoneinfo import ZoneInfo

from . import evidence, pipeline_lock, selection
from .walkforward_readout import METHOD as WALKFORWARD_METHOD, OOS_ZONE, SELECTION_SIDECAR
from ..models import daily_returns_by_day, parse_point_time
from ..optimize import dsr, montecarlo, pbo
from ..store import ExperimentStore, export_release, sha256_file

__all__ = [
    "screen", "run_montecarlo", "run_montecarlo_oos", "run_dsr", "run_pbo", "export_release",
    "SUPPORTED_TRIAL_POLICIES", "SUPPORTED_OBJECTIVES",
]

# Dashboard dò capability qua getattr(readout, "SUPPORTED_TRIAL_POLICIES", ())
# TRƯỚC KHI cho phép chạy diagnostics policy mới — không được đổi tên thuộc
# tính này mà không báo trước cho phía dashboard.
SUPPORTED_TRIAL_POLICIES = selection.TRIAL_POLICIES
# Tiêu chí chọn tổ hợp mà DSR/PBO kiểm được — đúng tập tiêu chí PBO tính được.
SUPPORTED_OBJECTIVES = pbo.OBJECTIVES
DEFAULT_OBJECTIVE = "net_profit"


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
        deps_hash = pipeline_lock.stage_hash(
            "monte_carlo", montecarlo.DUAL_SCHEMA, sha256_file(report_json_gz), dict(config),
        )
        cached = pipeline_lock.check(pipeline, "monte_carlo", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    report = evidence.read_report(report_json_gz)
    result = montecarlo.run_dual((report.get("equity") or {}).get("points") or [], _montecarlo_config(config))
    if pipeline:
        pipeline_lock.record(pipeline, "monte_carlo", deps_hash, result)
    return result


def _montecarlo_config(config: Mapping[str, Any]) -> montecarlo.MonteCarloConfig:
    return montecarlo.MonteCarloConfig(
        paths=int(config.get("paths", 10000)),
        block_days=int(config.get("block_days", 5)),
        seed=int(config.get("seed", 20260911)),
        timezone=str(config.get("timezone", "Europe/Prague")),
        initial_balance=float(config.get("initial_balance", 100000.0)),
        daily_loss_pct=float(config.get("daily_loss_pct", 5.0)),
        max_loss_pct=float(config.get("max_loss_pct", 10.0)),
    )


def run_montecarlo_oos(
    experiment: str, *, rule: str, symbol: str, timeframe: str,
    config: Mapping[str, Any] | None = None, pipeline: str | None = None,
) -> dict[str, Any]:
    """Monte Carlo trên chuỗi đoạn KIỂM (OOS) nối lại của 1 walk-forward đã
    chạy xong, cho đúng 1 luật chọn (`rule`) và 1 (symbol, timeframe).

    Vì sao: backtest toàn kỳ với tham số đã chọn là kết quả TRONG MẪU — tham
    số được chọn từ chính dữ liệu đó nên return bị thổi phồng, Monte Carlo trên
    nó đánh giá thấp drawdown/xác suất vi phạm. Chuỗi OOS nối lại chỉ gồm các
    ngày giao dịch bằng tham số chọn TRƯỚC khi dữ liệu đó tồn tại (Pardo).

    Cách nối (chain-linking như cách GIPS nối return các kỳ con): mỗi đoạn KIỂM
    là 1 backtest riêng bắt đầu lại từ số dư ban đầu; đoạn thứ k được nhân với
    hệ số = số dư nối đến cuối đoạn k−1 / số dư ban đầu của đoạn k (sizing theo
    % số dư nên lãi/lỗ tỷ lệ thuận với số dư). Mỗi đoạn chỉ giữ điểm TRƯỚC 00:00
    UTC ngày đầu đoạn kế tiếp — với walk-forward chạy theo quy ước cũ (2 đoạn
    liền nhau cùng chạy trọn ngày ranh giới) ngày đó chỉ được tính 1 lần, cho
    đoạn sau; với quy ước mới việc cắt này không bỏ điểm nào. Lệnh còn mở khi 1
    đoạn kết thúc không có trong số dư của đoạn đó (giới hạn vốn có của việc
    chạy từng đoạn riêng), lỗ nổi của nó trước lúc kết thúc vẫn nằm trong
    `minEquity`.

    Từ chối (ValueError) nếu có cửa sổ không chọn được tham số, đoạn KIỂM chưa
    chạy xong/thiếu report, hoặc 2 đoạn liền nhau hở ngày — chuỗi thủng không
    được coi là chuỗi OOS liên tục."""
    config = dict(config or {})
    store = ExperimentStore(WALKFORWARD_METHOD, experiment)
    if not store.db_path.is_file():
        raise FileNotFoundError(f"walk-forward experiment not found: {experiment}")
    sidecar = store.read_sidecar(SELECTION_SIDECAR) or {}
    decisions = sorted(
        (
            item for item in sidecar.get("decisions") or []
            if item.get("rule") == rule and item.get("symbol") == symbol and item.get("timeframe") == timeframe
        ),
        key=lambda item: str(item.get("test_start")),
    )
    if not decisions:
        raise ValueError(f"no walk-forward decisions for rule={rule!r} {symbol} {timeframe}")
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash(
            "monte_carlo_oos", montecarlo.DUAL_SCHEMA, sha256_file(store.db_path), sidecar,
            rule, symbol, timeframe, config,
        )
        cached = pipeline_lock.check(pipeline, "monte_carlo_oos", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    missing = [item.get("test_window") for item in decisions if item.get("selected_params") is None]
    if missing:
        raise ValueError(f"OOS series has windows without a selected parameter set: {missing}")

    tests = {}
    for row in store.flat_rows():
        if row.get("zone") != OOS_ZONE or row.get("symbol") != symbol or row.get("timeframe") != timeframe:
            continue
        tags = (json.loads(row.get("spec_json") or "{}") or {}).get("tags") or {}
        if tags.get("rule") == rule:
            tests[str(tags.get("train_window"))] = row

    initial = float(config.get("initial_balance", 100000.0))
    scale_base = initial
    stitched: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    for position, decision in enumerate(decisions):
        row = tests.get(str(decision.get("train_window")))
        label = decision.get("test_window")
        if not row or row.get("status") != "ok":
            raise ValueError(f"OOS window {label} has no completed execution")
        report_path = store.run_dir(str(row["run_id"]), str(row["label"])) / "report.json.gz"
        if not report_path.is_file():
            raise ValueError(f"OOS window {label} has no saved report")
        report = evidence.read_report(report_path)
        start_capital = float(row.get("starting_capital") or (report.get("main") or {}).get("startingCapital") or 0)
        if start_capital <= 0:
            raise ValueError(f"OOS window {label} has no starting balance")
        points = (report.get("equity") or {}).get("points") or []
        cutoff = None
        if position + 1 < len(decisions):
            this_start = date.fromisoformat(str(decision["test_start"]))
            this_end = date.fromisoformat(str(decision["test_end"]))
            next_start = date.fromisoformat(str(decisions[position + 1]["test_start"]))
            if not this_start < next_start <= this_end + timedelta(days=1):
                raise ValueError(f"OOS windows {label} and {decisions[position + 1].get('test_window')} are not contiguous")
            cutoff = datetime.combine(next_start, time(0), tzinfo=dt_timezone.utc)
        kept = [
            point for point in points
            if cutoff is None or parse_point_time(point).astimezone(dt_timezone.utc) < cutoff
        ]
        if not kept:
            raise ValueError(f"OOS window {label} has no equity points")
        factor = scale_base / start_capital
        for point in kept:
            item = {key: point[key] for key in ("time", "timestamp", "date") if key in point}
            for key in ("balance", "minEquity", "maxEquity"):
                if key in point:
                    item[key] = float(point[key]) * factor
            stitched.append(item)
        scale_base = float(kept[-1].get("balance", start_capital)) * factor
        windows.append({
            "test_window": label, "test_start": decision.get("test_start"), "test_end": decision.get("test_end"),
            "selected_params": decision.get("selected_params"),
            "points_used": len(kept), "points_trimmed": len(points) - len(kept),
        })
    result = {
        **montecarlo.run_dual(stitched, _montecarlo_config({**config, "initial_balance": initial})),
        "source": {
            "method": WALKFORWARD_METHOD, "experiment": experiment, "rule": rule,
            "symbol": symbol, "timeframe": timeframe, "windows": windows,
        },
    }
    if pipeline:
        pipeline_lock.record(pipeline, "monte_carlo_oos", deps_hash, result)
    return result


def _resolve_trial_policy(config: Mapping[str, Any]) -> str:
    """Đọc + xác nhận `config["trial_policy"]` — policy lạ RAISE ngay, không
    âm thầm rơi về mặc định. Không truyền gì = strict (hành vi cũ)."""
    policy = str(config.get("trial_policy", selection.STRICT_RESEARCH_POLICY))
    if policy not in selection.TRIAL_POLICIES:
        raise ValueError(f"unknown trial_policy: {policy!r}; supported: {selection.TRIAL_POLICIES}")
    return policy


def _resolve_objective(config: Mapping[str, Any]) -> str:
    """Đọc + xác nhận `config["objective"]` — tiêu chí lạ RAISE ngay (trước cả
    khi kiểm cache), không âm thầm rơi về mặc định. Không truyền gì = lợi nhuận."""
    objective = str(config.get("objective", DEFAULT_OBJECTIVE)).strip().lower()
    if objective not in SUPPORTED_OBJECTIVES:
        raise ValueError(f"unknown objective: {objective!r}; supported: {SUPPORTED_OBJECTIVES}")
    return objective


class _TrialSet(NamedTuple):
    returns: dict[str, tuple[float, ...]]
    # Mỗi trial, mỗi ngày (cùng trục ngày với `returns`): lãi/lỗ ròng các lệnh
    # đóng trong ngày — chỉ nạp khi tiêu chí cần tới từng lệnh.
    trades: dict[str, tuple[tuple[float, ...], ...]] | None
    rows: dict[str, dict[str, Any]]
    diagnostics: dict[str, Any]


def _trades_by_day(
    report: Mapping[str, Any], days: list[str], timezone: str, label: str,
) -> tuple[tuple[float, ...], ...]:
    """Gắn từng lệnh đã đóng vào ĐÚNG ngày của chuỗi return (theo giờ đóng
    lệnh, cùng cách đọc mốc giờ `parse_point_time` với chuỗi return). Lệnh đóng
    ngoài mọi ngày có điểm equity -> raise, không âm thầm bỏ lệnh."""
    tz = ZoneInfo(timezone)
    index = {day: position for position, day in enumerate(days)}
    per_day: list[list[float]] = [[] for _ in days]
    for item in (report.get("history") or {}).get("items") or []:
        day = parse_point_time({"timestamp": item["closeTime"]}).astimezone(tz).date().isoformat()
        if day not in index:
            raise ValueError(f"{label}: trade closed on {day}, outside the equity timeline")
        per_day[index[day]].append(float(item["net"]))
    return tuple(tuple(values) for values in per_day)


def _load_trials(
    method: str,
    experiment: str,
    *,
    policy: str,
    field: str = "balance",
    timezone: str = "Europe/Prague",
    initial_balance: float = 100000.0,
    min_trades: int = 0,
    with_trades: bool = False,
) -> _TrialSet:
    """Đọc mọi run ĐỦ ĐIỀU KIỆN theo `policy` của 1 experiment đã chạy xong.

    `returns` là nguyên liệu cho `run_dsr()`/`run_pbo()`; `diagnostics`
    [2026-09-25] báo cáo rõ quyết định phân loại (tổng dòng, số nhận/loại,
    margin rejection, lý do loại từng trial) để `run_dsr`/`run_pbo` gắn vào kết
    quả trả về — không được để dashboard tự đoán những con số này qua việc đọc
    lại SQLite riêng. [2026-09-28] Thêm `rows` (để chọn trial theo tiêu chí) và
    `trades` (chỉ khi `with_trades`). Riêng tư trong file này — chỉ
    `run_dsr`/`run_pbo` gọi tới."""
    store = ExperimentStore(method, experiment)
    rows = store.flat_rows()
    trials: dict[str, tuple[float, ...]] = {}
    trades: dict[str, tuple[tuple[float, ...], ...]] = {}
    included_rows: dict[str, dict[str, Any]] = {}
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
        by_day = daily_returns_by_day(points, timezone, initial_balance, field_name=field)
        if not by_day:
            excluded.append({"label": label, "reasons": ["no_daily_returns"]})
            continue
        trials[label] = tuple(value for _day, value in by_day)
        included_rows[label] = dict(row)
        if with_trades:
            trades[label] = _trades_by_day(report, [day for day, _value in by_day], timezone, label)
    diagnostics = {
        "trial_policy": policy,
        "total_rows": len(rows),
        "included_trials": len(trials),
        "excluded_trials": len(excluded),
        "margin_rejection_trials": margin_rejection_trials,
        "margin_rejection_events": margin_rejection_events,
        "excluded": excluded,
    }
    return _TrialSet(trials, trades if with_trades else None, included_rows, diagnostics)


def _load_for(method: str, experiment: str, config: Mapping[str, Any], *, with_trades: bool) -> _TrialSet:
    return _load_trials(
        method, experiment,
        policy=config["trial_policy"],
        field=str(config.get("field", "balance")),
        timezone=str(config.get("timezone", "Europe/Prague")),
        initial_balance=float(config.get("initial_balance", 100000.0)),
        min_trades=int(config.get("min_trades", 0)),
        with_trades=with_trades,
    )


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
    cache của nhau dù cùng experiment).

    [2026-09-28] `config["objective"]` (mặc định net_profit): kết quả có thêm
    `selected` = DSR của trial đứng đầu theo tiêu chí đó (luật "best" —
    `selection.rank(by=(objective,))`, đúng luật walk-forward dùng), cùng
    ngưỡng SR0 của cả N trial. Các field cũ (winner_*/dsr) vẫn là trial Sharpe
    cao nhất, giữ làm tham chiếu."""
    config = dict(config or {})
    config["trial_policy"] = _resolve_trial_policy(config)
    config["objective"] = _resolve_objective(config)
    store = ExperimentStore(method, experiment)
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash("dsr", dsr.SCHEMA, sha256_file(store.db_path), config)
        cached = pipeline_lock.check(pipeline, "dsr", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    loaded = _load_for(method, experiment, config, with_trades=False)
    ranked = selection.rank(
        loaded.rows.values(), min_trades=int(config.get("min_trades", 0)),
        by=(config["objective"],), policy=config["trial_policy"],
    )
    selected = str(ranked[0]["label"]) if ranked else None
    result = {
        **dsr.run(loaded.returns, selected=selected),
        "objective": config["objective"],
        **loaded.diagnostics,
    }
    if pipeline:
        pipeline_lock.record(pipeline, "dsr", deps_hash, result)
    return result


def run_pbo(
    method: str, experiment: str, config: Mapping[str, Any] | None = None,
    *, pipeline: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Probability of Backtest Overfitting (CSCV) hậu kỳ trên 1 experiment ĐÃ
    CHẠY XONG — đọc lại N report, thử mọi cách chia đối xứng theo thời gian.
    Không chạy backtest mới. Xem research_notes/dsr-pbo-formula-verification.md.

    [2026-09-22] `pipeline` (tuỳ chọn) — cùng cơ chế `run_dsr()`.
    [2026-09-25] `config["trial_policy"]` — cùng cơ chế `run_dsr()`.
    [2026-09-27] `on_progress(done, total)` — chuyển thẳng xuống `pbo.run()`;
    không nằm trong khoá cache vì không đổi kết quả.
    [2026-09-28] `config["objective"]` — mỗi cách chia chọn winner theo đúng
    tiêu chí này; PF/win rate đọc thêm lịch sử lệnh của từng report."""
    config = dict(config or {})
    config["trial_policy"] = _resolve_trial_policy(config)
    config["objective"] = _resolve_objective(config)
    store = ExperimentStore(method, experiment)
    deps_hash = None
    if pipeline:
        deps_hash = pipeline_lock.stage_hash("pbo", pbo.SCHEMA, sha256_file(store.db_path), config)
        cached = pipeline_lock.check(pipeline, "pbo", deps_hash)
        if cached is not None:
            return {**cached, "cached": True}
    loaded = _load_for(
        method, experiment, config, with_trades=config["objective"] in pbo.TRADE_OBJECTIVES,
    )
    result = {
        **pbo.run(
            loaded.returns, blocks=int(config.get("blocks", pbo.DEFAULT_BLOCKS)),
            on_progress=on_progress, objective=config["objective"], trades=loaded.trades,
        ),
        **loaded.diagnostics,
    }
    if pipeline:
        pipeline_lock.record(pipeline, "pbo", deps_hash, result)
    return result
