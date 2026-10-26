"""facilitator.py — BỘ THỰC THI LÕI dùng chung + phương pháp cơ bản nhất (grid).

`run_experiment()` là nơi DUY NHẤT biết cách biến 1 danh sách `RunSpec` thành
kết quả đã ghi xuống đĩa: kiểm capability, đóng băng input, claim, spawn engine
(cli/api), diễn giải qua evidence, ghi qua store. Mọi "phương pháp" khác —
`run_grid()` ngay trong file này, `walkforward.run_walkforward()` ở file riêng —
đều chỉ khác nhau ở cách DỰNG danh sách RunSpec, rồi cùng gọi vào đây.

KHÔNG tự diễn giải kết quả (việc của evidence/selection), KHÔNG tự đọc lại báo
cáo đã lưu (việc của readout.py/walkforward.py) — chỉ chạy và ghi.

[2026-09-21] Thu hẹp lại đúng vai "điều phối chạy": chọn tổ hợp thắng dời sang
selection/walkforward; đọc-lại-kết-quả dời sang readout.py; đường dẫn tín hiệu
dời sang signal_trans.resolve_signal_path(); validate_metadata đổi thành method
StrategyProfile.validate_metadata().

[2026-09-21 lần 3] Signal đổi hẳn sang Redis-only — `signal_trans.resolve_signal_path()`
giờ LUÔN vật chất hoá tươi từ Redis, không còn nhánh CSV tĩnh/override tay.

[2026-09-22] `_run_experiment` đổi tên thành **`run_experiment`** (bỏ gạch
dưới): từ khi `walkforward.py` tách ra, hàm này có 2 nơi gọi hợp lệ (`run_grid`
ở đây + `run_walkforward` bên kia) nên nó là API CHUNG thật sự, không còn là
chi tiết nội bộ của riêng file này. Tên phải nói đúng bản chất thay vì để file
khác import 1 ký hiệu gạch-dưới (phá vỡ quy ước riêng tư). Cùng đợt: toàn bộ
walk-forward (`run_walkforward`, `WF_*`, `plateau`, `pick`, `walkforward_windows`,
`walkforward_table`) dời sang `walkforward.py`.

[2026-10-10] `config["optimize_chunk_size"]` (mặc định 0 = tắt): >= 2 thì Grid và chặng HỌC của walk-forward chạy theo
hộp tham số bằng `ctrader-cli optimize` (1 lần đăng nhập/hộp) thay vì từng `backtest`; spec lẻ vẫn chạy `backtest`.
Gợi ý 20 (bội số của max_parallel): thư mục pass chỉ được ghi khi lệnh kết thúc nên hộp nhỏ để ngắt chỉ mất 1 hộp.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from . import cli_runner

from . import signal_trans
from .configuration import (
    DEFAULT_REDIS_PROFILE, ctrader_data_dir, load_engine_profile, load_master_db_profile, load_redis_profile,
    load_strategy_profile, resolve_backtest_config, with_signal_overrides,
)
from .models import FailureCode, OptimizeBox, RunStatus, Window
from .output_util import evidence, pipeline_lock, run_export
from .planner import as_date, build_experiment, expand_grid, nonnegative_int, plan_optimize_boxes, positive_int, research_windows, run_name
from .store import ExperimentStore, ProtocolStore, sha256_file

# facilitator.py là orchestrator DUY NHẤT chọn engine thật thi hành — mọi file
# khác trong core_engine/ trung lập, không tự chọn engine (cli_runner SỐNG NGAY
# TRONG core_engine/, không còn là package riêng — xem cli_runner.py mục
# [2026-09-21]); engine chỉ nhìn xuống core_engine.models (RunSpec/RunResult/
# RunStatus/FailureCode).
# [2026-10-13] api_engine đã xoá hẳn — `_ENGINES` chỉ còn backend "cli".
_ENGINES = {"cli": cli_runner}
_RETRYABLE_FAILURES = frozenset({"message_expected", "hard_timeout", "progress_stalled"})

# Trần an toàn cho song song — port tinh thần pipeline/settings.py cũ
# (MAX_FALLBACK_PROCESSES), bị rơi mất khi refactor sang research/ 2026-09.
# Không có trần này, gõ nhầm max_parallel=500 sẽ mở 500 tiến trình ctrader-cli
# cùng lúc. Máy VM-BO20 đo thật: 8 song song vẫn dư CPU/RAM (audit plugin
# 2026-09-11); 16 là trần hợp lý cho tới khi có benchmark cao hơn.
DEFAULT_MAX_PARALLEL = 12
MAX_PARALLEL_CAP = min(16, os.cpu_count() or 1)

# Tally key for specs never started because cancellation arrived first: they
# have no store row and spawned no ctrader-cli process.
NOT_STARTED = "not_started"
# Tally key for specs not started because today's CLI launch budget
# (store.DAILY_CLI_LAUNCH_LIMIT) is used up: no row, no spawn, retryable later.
BUDGET_EXHAUSTED = "budget_exhausted"
# Tally key for specs NOT run because the master SQL database already holds that exact run (config
# `skip_known_in_sql`): no row in this experiment's own store, no spawn, no CLI budget spent. Not a failure, but not
# "done" either — the local store has no result for it, so `complete` stays False and no pipeline lock is written.
KNOWN_IN_SQL = "known_in_sql"
_DONE_STATUSES = frozenset({RunStatus.OK.value, RunStatus.SKIPPED.value})
_NOT_FAILED = _DONE_STATUSES | {
    RunStatus.CANCELLED.value, RunStatus.BUSY.value, NOT_STARTED, BUDGET_EXHAUSTED, KNOWN_IN_SQL,
}
_FINGERPRINT_SIDECAR = "input_fingerprint.json"


class InputFingerprintMismatch(ValueError):
    """The real inputs (.algo, Redis signals) differ from the
    ones an experiment or a frozen dashboard plan was built for."""


def _signal_key(spec: Any) -> str:
    if spec.channels:
        return ";".join(
            f"{item.strategy}|{item.symbol}|{item.timeframe}|{item.risk_percent:g}"
            for item in spec.channels
        )
    return f"{spec.symbol}|{spec.timeframe}"


def _redis_profile_id(config: Mapping[str, Any]) -> str:
    value = config.get("redis_profile", DEFAULT_REDIS_PROFILE)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("redis_profile must select exactly one default source profile (a channel may override it with `source`)")
    return value.strip()


def _fingerprint_end(config: Mapping[str, Any], specs: list[Any]) -> Any:
    return as_date(config["end"]) if config.get("end") else max(spec.end for spec in specs)


def input_fingerprint(config: Mapping[str, Any]) -> dict[str, Any]:
    """Content identity of the real inputs a config would run on.

    {"algo_sha256", "signals": {"<symbol>|<timeframe>": sha}} — signal hashes
    cover the rows up to and including the config end day, because cTrader runs
    the whole end day (see `signal_trans.signal_window_sha256`).
    The CLI is not part of it: cTrader updates it by itself and every run records
    its real version (execution.json). Reads the .algo and Redis (to a temporary
    CSV that is deleted); never backtests and never touches an ExperimentStore."""
    experiment = build_experiment({**dict(config), "method": config.get("method", "grid")})
    profile = with_signal_overrides(load_strategy_profile(experiment.strategy_profile), config)
    engine = load_engine_profile(experiment.engine_profile)
    specs = expand_grid(config, profile, engine, windows=research_windows(config))
    end = _fingerprint_end(config, specs)
    redis_profile = load_redis_profile(_redis_profile_id(config))
    scratch = Path(tempfile.mkdtemp(prefix=".fingerprint_"))
    signals: dict[str, str] = {}
    try:
        materialized: set[Path] = set()
        for spec in specs:
            key = _signal_key(spec)
            if key in signals:
                continue
            path = signal_trans.resolve_signal_path(redis_profile, profile, spec, scratch, materialized=materialized)
            signals[key] = signal_trans.signal_window_sha256(path, end)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"algo_sha256": sha256_file(profile.algo_path), "signals": dict(sorted(signals.items()))}


def resolve_input_fingerprint(config: Mapping[str, Any]) -> dict[str, Any]:
    """`input_fingerprint(config)`, refusing to continue when the config
    carries an `expected_input_fingerprint` (frozen at draft time) that no
    longer matches — nothing has been claimed or spawned at this point."""
    fingerprint = input_fingerprint(config)
    expected = config.get("expected_input_fingerprint")
    if expected is not None and dict(expected) != fingerprint:
        raise InputFingerprintMismatch(
            "inputs changed since this plan was frozen (.algo or Redis signals "
            "inside the backtest window); create a new session instead of rerunning this one"
        )
    return fingerprint


def run_backtest(
    overrides: Mapping[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run the default isolated/shared BacktestBot flow from config.yaml."""
    config = resolve_backtest_config(overrides)
    if not config.get("name") and not config.get("experiment"):
        config["name"] = run_name(config)
    return run_grid(config, on_progress=on_progress, should_cancel=should_cancel)


def run_grid(
    config: Mapping[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Phương pháp cơ bản: quét toàn bộ lưới tham số trên 1 khoảng thời gian,
    chạy 1 lượt. Chỉ khác `walkforward.run_walkforward()` ở cách dựng RunSpec —
    cả hai đều kết thúc bằng `run_experiment()` bên dưới.

    [2026-09-22] `config["pipeline"]` (tuỳ chọn) — tên 1 pipeline đang theo
    dõi qua `pipeline_lock`. Có tên này: trước khi chạy, so dấu vân tay input
    với lần ghi trước — trùng thì trả NGAY kết quả cũ, không chạy CLI. Không
    có (mặc định) — hành vi CŨ, luôn chạy, dùng cho backtest tay/kiểm chứng
    rời rạc không thuộc 1 pipeline theo dõi.
    `config["pipeline_stage"]` (mặc định "grid") — cùng hàm này còn dùng cho
    Stage [5] "backtest cuối" (1 tổ hợp, không sweep) — cần TÊN TẦNG khác
    "grid" để không ghi đè lên đúng ô Stage [1] trong sổ.

    [2026-09-24] Khoá cache giờ gồm cả `input_fingerprint()` (sha .algo/CLI/
    signal trong cửa sổ) — rebuild bot hay Redis tính lại signal cũ thì không
    còn trả nhầm kết quả cũ. Chỉ ghi sổ khi lượt chạy HOÀN TẤT trọn vẹn
    (`complete`): lượt bị huỷ hoặc có tổ hợp lỗi chạy lại được và chỉ bù phần
    thiếu."""
    pipeline = config.get("pipeline")
    stage_name = str(config.get("pipeline_stage", "grid"))
    deps_hash = None
    run_config: Mapping[str, Any] = config
    if pipeline or config.get("expected_input_fingerprint") is not None:
        fingerprint = resolve_input_fingerprint(config)
        # Pin the execution to exactly what was hashed, so the lock can never
        # describe inputs other than the ones actually backtested.
        run_config = {**dict(config), "expected_input_fingerprint": fingerprint}
        if pipeline:
            deps_hash = pipeline_lock.stage_hash(
                stage_name, config.get("symbols"), config.get("timeframe"), config.get("strategy"),
                config.get("strategy_profile"), config.get("engine_profile"),
                config.get("start"), config.get("end"), config.get("balance"),
                config.get("parameter_space"), config.get("fixed_params"), fingerprint,
            )
            cached = pipeline_lock.check(pipeline, stage_name, deps_hash)
            if cached is not None:
                return {**cached, "cached": True}
    experiment = build_experiment({**dict(config), "method": config.get("method", "grid")})
    profile = with_signal_overrides(load_strategy_profile(experiment.strategy_profile), config)
    engine = load_engine_profile(experiment.engine_profile)
    specs = expand_grid(config, profile, engine)
    result = run_experiment(
        run_config,
        experiment.canonical(),
        profile,
        specs,
        engine=engine,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    if pipeline and result.get("complete"):
        pipeline_lock.record(pipeline, stage_name, deps_hash, result)
    return result


def run_experiment(
    config: Mapping[str, Any],
    experiment_doc: Mapping[str, Any],
    profile: Any,
    specs: list[Any],
    *,
    engine: Any = None,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not specs:
        raise ValueError("experiment produced no RunSpec")
    workers = positive_int(config.get("max_parallel", DEFAULT_MAX_PARALLEL), "max_parallel")
    if workers > MAX_PARALLEL_CAP:
        raise ValueError(
            f"max_parallel={workers} vượt trần an toàn {MAX_PARALLEL_CAP}; "
            "chia experiment hoặc sửa MAX_PARALLEL_CAP có chủ đích"
        )
    if engine is None:
        raise ValueError("run_experiment cần engine (EngineProfile) để biết backend cli/api")
    timeout = positive_int(config.get("timeout_seconds", cli_runner.DEFAULT_TIMEOUT_SECONDS), "timeout_seconds")
    # Progress có thể đứng 0% dù CLI còn tải/giải nén Ticks — mặc định ngưỡng
    # dài hơn hard timeout để không báo treo oan; fail-fast theo Progress % là
    # opt-in qua config (port pipeline/methods/backtest.py).
    stall_s = positive_int(config.get("stall_seconds", timeout * 2), "stall_seconds")
    startup_stall_s = positive_int(
        config.get("startup_stall_seconds", timeout * 2), "startup_stall_seconds",
    )
    retries = nonnegative_int(config.get("transient_retries", 1), "transient_retries")
    force = bool(config.get("force", False))
    keep_logs = bool(config.get("keep_logs", False))
    chunk_size = nonnegative_int(config.get("optimize_chunk_size", 0), "optimize_chunk_size")
    use_boxes = engine.backend == "cli" and chunk_size > 1
    # Tra kho SQL master TRƯỚC khi tốn 1 lượt CLI (tuỳ chọn, mặc định tắt — hành vi cũ): lượt có run_key đã nằm trong
    # SQL thì không chạy lại mà báo tên experiment đang giữ kết quả (`result["known_in_sql"]`). `force` luôn chạy lại.
    skip_known = bool(config.get("skip_known_in_sql", False)) and not force
    if skip_known and use_boxes:
        raise ValueError("skip_known_in_sql chưa hỗ trợ optimize_chunk_size >= 2 (hộp optimize không tra kho SQL)")

    # Metadata (contract THẬT của .algo) luôn đọc qua CLI dù batch này chạy
    # backend nào — đây là 1 lệnh đọc nhẹ, gọi đúng 1 lần/batch (không phải N
    # lần như backtest), không đáng kể trong ngân sách request/ngày, và cả 2
    # engine đều phải khớp đúng 1 contract tham số như nhau.
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli not found: {cli_path}")
    metadata = cli_runner.read_metadata(profile.algo_path, cli_path=cli_path)
    profile.validate_metadata(metadata)

    # Không ghi state (store.open/claim) trước khi capability đã xác nhận —
    # port pipeline/methods/backtest.py::_validate_capabilities. Mỗi engine tự
    # biết cách kiểm tra chính nó; facilitator chỉ chọn ĐÚNG MODULE, không biết
    # bên trong làm gì.
    engine_module = _ENGINES[engine.backend]
    if engine.backend == "cli":
        engine_module.validate_capabilities(specs, algo_path=profile.algo_path, cli_path=cli_path, optimize=use_boxes)
        process_env = cli_runner.child_environment(cli_runner.auth_options())

    store = ExperimentStore(str(experiment_doc["method"]), str(experiment_doc["name"]))
    store.open(experiment_doc)
    protocol = ProtocolStore()
    protocol.check_holdouts(specs, experiment=str(experiment_doc["name"]), final_open=bool(config.get("final_test_open")))

    algo_snapshot, algo_sha = store.snapshot(profile.algo_path)
    # .algo không chứa mã nguồn (SourceIncluded=false) → chụp .cs riêng. Chỉ GHI NHẬN (execution.json
    # + inputs/<sha>.cs), không nằm trong param_hash: hash .algo đã đổi mỗi lần build nên khoá vẫn bảo thủ.
    source_sha = None
    source_newer_than_algo = None
    if profile.source_path is not None and profile.source_path.is_file():
        _source_snapshot, source_sha = store.snapshot(profile.source_path)
        source_newer_than_algo = profile.source_path.stat().st_mtime > profile.algo_path.stat().st_mtime
    native_data_dir = ctrader_data_dir()
    # [2026-09-21 lần 3] Redis-only: resolve_signal_path() giờ tự LRANGE/HGETALL
    # + ghi CSV tươi (không còn đọc CSV tĩnh có sẵn). `materialized` dùng CHUNG
    # cho cả batch để 1 lưới quét nhiều KslLevel/KtpLevel trên cùng 1 symbol/
    # timeframe chỉ đọc Redis ĐÚNG 1 LẦN, không phải 1 lần/spec.
    # [2026-09-22] CSV vật chất hoá từ Redis ghi vào thư mục TẠM riêng của đúng
    # batch này (không còn thư mục cố định redis_cache/ — tên cố định có rủi ro
    # race-condition nếu 2 experiment song song cùng đụng 1 symbol/timeframe;
    # bản đóng băng thật phục vụ tái lập đã có sẵn ở store.snapshot()/inputs/,
    # nên thư mục tạm này chỉ cần sống hết batch rồi xoá).
    redis_profile = load_redis_profile(_redis_profile_id(config))
    signal_scratch = Path(tempfile.mkdtemp(prefix=".signal_", dir=store.runs_dir))
    materialized: set[Path] = set()
    # signal_hashes[path] = (snapshot_path, sha256) — tính hash CSV đúng 1 lần
    # dù nhiều symbol/spec dùng chung 1 file; tra theo spec chỉ là lookup dict.
    signal_by_spec: dict[str, Path] = {}
    signal_sha_by_spec: dict[str, str] = {}
    signal_window_by_spec: dict[str, str] = {}
    signal_hashes: dict[Path, tuple[Path, str]] = {}
    window_hashes: dict[tuple[Path, Any], str] = {}
    try:
        for spec in specs:
            path = signal_trans.resolve_signal_path(
                redis_profile, profile, spec, signal_scratch, materialized=materialized,
            )
            if not path.is_file():
                raise FileNotFoundError(f"missing signal CSV: {path}")
            if path not in signal_hashes:
                signal_hashes[path] = store.snapshot(path)
            snapshot_path, digest = signal_hashes[path]
            key = spec.canonical_json()
            signal_by_spec[key] = snapshot_path
            signal_sha_by_spec[key] = digest
            if (snapshot_path, spec.end) not in window_hashes:
                window_hashes[(snapshot_path, spec.end)] = signal_trans.signal_window_sha256(snapshot_path, spec.end)
            signal_window_by_spec[key] = window_hashes[(snapshot_path, spec.end)]
    finally:
        shutil.rmtree(signal_scratch, ignore_errors=True)

    fingerprint_end = _fingerprint_end(config, specs)
    signals: dict[str, str] = {}
    for spec in specs:
        if _signal_key(spec) not in signals:
            signals[_signal_key(spec)] = signal_trans.signal_window_sha256(
                signal_by_spec[spec.canonical_json()], fingerprint_end,
            )
    used_fingerprint = {"algo_sha256": algo_sha, "signals": dict(sorted(signals.items()))}
    expected = config.get("expected_input_fingerprint")
    if expected is not None and dict(expected) != used_fingerprint:
        raise InputFingerprintMismatch(
            "inputs changed between the pre-run check and signal materialization; nothing was run"
        )
    # One experiment = one set of real inputs. Without this, rebuilding the
    # .algo (or Redis recomputing past signals) would add a second full set of
    # rows next to the old ones in the same store (duplicate heatmap cells,
    # doubled DSR/PBO trial counts).
    recorded_fingerprint = store.read_sidecar(_FINGERPRINT_SIDECAR)
    if recorded_fingerprint is None:
        if store.rows():
            raise InputFingerprintMismatch(
                f"experiment {experiment_doc['name']!r} predates input fingerprints; "
                "run it under a new experiment name instead of adding rows to it"
            )
        store.write_sidecar(_FINGERPRINT_SIDECAR, used_fingerprint)
    elif recorded_fingerprint != used_fingerprint:
        raise InputFingerprintMismatch(
            f"experiment {experiment_doc['name']!r} was produced from different inputs "
            "(.algo or Redis signals); use a new experiment name"
        )

    # The window hash (not the whole CSV) identifies a run: signals appended to
    # Redis after the spec's end cannot change its result, so they must not
    # turn an already finished run into a "new" one.
    hashes = [
        store.param_hash(
            spec,
            "bo-research-execution/v1",
            algo_sha,
            signal_window_by_spec[spec.canonical_json()],
            spec.engine_profile,
        )
        for spec in specs
    ]
    start = time.monotonic()
    tally: dict[str, int] = {}
    counts_logins = engine.backend == "cli"
    hash_of = {spec.canonical_json(): digest for spec, digest in zip(specs, hashes)}

    def reserve_launch(label: str) -> bool:
        return protocol.reserve_cli_launch(experiment=str(experiment_doc["name"]), label=label)

    # Kho SQL master (config.yaml `master_db`): bản SAO của mỗi lượt vừa ghi xuống đĩa. File vẫn là nguồn sự thật
    # nên tắt/hỏng SQL không ảnh hưởng lượt chạy. list.append an toàn giữa các luồng của pool.
    master_profile = load_master_db_profile()
    master_outcomes: list[str] = []
    known_runs: list[dict[str, Any]] = []
    known_in_sql: dict[str, dict[str, Any]] = {}
    if skip_known:
        if not master_profile.enabled:
            raise ValueError("skip_known_in_sql cần master_db.enabled = true trong config.yaml")
        known_in_sql = _find_known_runs(master_profile, hashes)

    def crash_result(exc: Exception, work_dir: Path) -> Any:
        """Kết quả giả khi chính pipeline (không phải CLI) ném ngoại lệ."""
        work_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        (work_dir / "cli.log").write_text(cli_runner.redact_text(f"{type(exc).__name__}: {exc}"), encoding="utf-8")
        (work_dir / "bot.log").write_text("", encoding="utf-8")
        return SimpleNamespace(
            status=RunStatus.CLI_ERROR,
            failure_code=FailureCode.PIPELINE_ERROR,
            reason=f"pipeline {type(exc).__name__}: {cli_runner.redact_text(str(exc))}",
            exit_code=None,
            timed_out=False,
            wall_seconds=0.0,
            started_utc=now,
            ended_utc=now,
            cli_path=str(cli_path),
            cli_version=cli_runner.cli_version(cli_path),
            report_json=work_dir / "report.json",
            cli_log=work_dir / "cli.log",
            bot_log=work_dir / "bot.log",
            cbotset=work_dir / "params.cbotset",
        )

    def record_result(spec: Any, param_hash: str, claim: Any, result: Any) -> str:
        report = evidence.read_report(result.report_json) if result and result.report_json.exists() else None
        bot_text = result.bot_log.read_text(encoding="utf-8", errors="replace") if result else ""
        summary = evidence.bot_summary(bot_text)
        flags = evidence.validity_flags(spec, report, summary)
        metrics = evidence.metrics(report) if report else {}
        env = evidence.environment(report) if report else {}
        store.record(
            claim=claim,
            spec=spec,
            param_hash=param_hash,
            run_result=result,
            metrics=metrics,
            flags=flags,
            bot_summary=summary,
            environment=env,
            inputs={
                "algo_sha256": algo_sha,
                "signal_sha256": signal_sha_by_spec[spec.canonical_json()],
                "source_sha256": source_sha,
                "source_newer_than_algo": source_newer_than_algo,
            },
            keep_logs=keep_logs,
        )
        if master_profile.enabled:
            master_outcomes.append(_save_to_master_db(master_profile, store, claim, spec, result.status.value))
        return result.status.value

    def one(item: tuple[Any, str]) -> str:
        spec, param_hash = item
        # Checked before claiming: after a Stop, specs still queued in the pool
        # get no row and never spawn ctrader-cli (each spawn is a server login).
        if cli_runner.cancel_requested(should_cancel):
            return NOT_STARTED
        if counts_logins:
            # A spec that claim() would skip must not spend today's CLI budget.
            if not force and store.is_terminal_ok(param_hash):
                return RunStatus.SKIPPED.value
            if param_hash in known_in_sql:
                known_runs.append({"label": spec.label(), **known_in_sql[param_hash]})
                return KNOWN_IN_SQL
            if not reserve_launch(spec.label()):
                return BUDGET_EXHAUSTED
        claim = store.claim(spec, param_hash, force=force)
        if claim.disposition != "claimed":
            return claim.disposition
        run_dir = store.run_dir(claim.run_id, spec.label())
        work_dir = Path(tempfile.mkdtemp(prefix=".work_", dir=run_dir.parent))
        attempts = 0
        result = None
        while attempts <= retries:
            # A retry is another login; without budget the last failure is
            # recorded as-is and the spec stays retryable.
            if attempts and counts_logins and not reserve_launch(spec.label()):
                break
            attempts += 1
            try:
                if engine.backend == "cli":
                    result = cli_runner.run_backtest(
                        spec,
                        algo_path=algo_snapshot,
                        signal_file=signal_by_spec[spec.canonical_json()],
                        work_dir=work_dir,
                        timeout_s=timeout,
                        stall_s=stall_s,
                        startup_stall_s=startup_stall_s,
                        cli_path=cli_path,
                        env=process_env,
                        native_data_dir=native_data_dir,
                        on_progress=on_progress,
                        should_cancel=should_cancel,
                    )
            except Exception as exc:
                result = crash_result(exc, work_dir)
                break
            if result.status is RunStatus.OK or result.failure_code.value not in _RETRYABLE_FAILURES:
                break
        status = record_result(spec, param_hash, claim, result)
        shutil.rmtree(work_dir, ignore_errors=True)
        return status

    def run_box(box: OptimizeBox) -> list[str]:
        """Cả hộp bằng 1 lệnh `optimize` (1 lần đăng nhập); trả trạng thái từng spec."""
        count = len(box.specs)
        label = f"{box.specs[0].label()} +{count - 1}"
        if cli_runner.cancel_requested(should_cancel):
            return [NOT_STARTED] * count
        if not reserve_launch(label):
            return [BUDGET_EXHAUSTED] * count
        keys = [spec.canonical_json() for spec in box.specs]
        claims = [store.claim(spec, hash_of[key], force=force) for spec, key in zip(box.specs, keys)]
        statuses = [claim.disposition for claim in claims]
        live = [i for i, claim in enumerate(claims) if claim.disposition == "claimed"]
        if not live:
            return statuses
        work_dir = Path(tempfile.mkdtemp(prefix=".work_", dir=store.runs_dir))
        try:
            results: list[Any] = []
            attempts = 0
            while attempts <= retries:
                if attempts and not reserve_launch(label):
                    break
                attempts += 1
                try:
                    results = cli_runner.run_optimize_box(
                        box,
                        profile,
                        algo_path=algo_snapshot,
                        signal_file=signal_by_spec[keys[0]],
                        work_dir=work_dir,
                        cores=workers,
                        timeout_s=timeout,
                        stall_s=stall_s,
                        startup_stall_s=startup_stall_s,
                        cli_path=cli_path,
                        env=process_env,
                        on_progress=on_progress,
                        should_cancel=should_cancel,
                    )
                    if len(results) != count:
                        raise RuntimeError(f"optimize returned {len(results)} results for {count} specs")
                except Exception as exc:
                    results = [crash_result(exc, work_dir / f"s{i}") for i in range(count)]
                    break
                if any(r.status is RunStatus.OK for r in results) or results[0].failure_code.value not in _RETRYABLE_FAILURES:
                    break
            for i in live:
                statuses[i] = record_result(box.specs[i], hash_of[keys[i]], claims[i], results[i])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return statuses

    items = list(zip(specs, hashes))
    if use_boxes:
        # Hộp nào còn đủ mọi spec thì chạy bằng optimize; spec đã xong thì bỏ qua, spec lẻ (hộp thủng) chạy backtest.
        done = set() if force else {digest for _, digest in items if store.is_terminal_ok(digest)}
        items = []
        for box in plan_optimize_boxes(specs, profile, chunk_size):
            todo = [(spec, hash_of[spec.canonical_json()]) for spec in box.specs if hash_of[spec.canonical_json()] not in done]
            for _ in range(len(box.specs) - len(todo)):
                _tick(tally, RunStatus.SKIPPED.value)
            if len(todo) == len(box.specs) > 1:
                for status in run_box(box):
                    _tick(tally, status)
            else:
                items.extend(todo)
    if workers <= 1:
        for item in items:
            _tick(tally, one(item))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for status in pool.map(one, items):
                _tick(tally, status)
    cancelled = bool(tally.get(RunStatus.CANCELLED.value) or tally.get(NOT_STARTED))
    return {
        "experiment": experiment_doc["name"],
        "method": experiment_doc["method"],
        "root": str(store.root),
        "total": len(specs),
        "tally": tally,
        "cancelled": cancelled,
        # complete: every spec ended ok/skipped — the only state a pipeline
        # lock may record. failed: specs that ended in an error status.
        "complete": set(tally) <= _DONE_STATUSES and sum(tally.values()) == len(specs),
        "failed": sum(count for status, count in tally.items() if status not in _NOT_FAILED),
        "daily_cli_budget_exhausted": bool(tally.get(BUDGET_EXHAUSTED)),
        "cli_versions": store.cli_versions(),
        "inputs": used_fingerprint,
        "wall_seconds": round(time.monotonic() - start, 3),
        **({"master_db": dict(Counter(master_outcomes))} if master_profile.enabled else {}),
        **({"known_in_sql": known_runs} if known_runs else {}),
    }


def _save_to_master_db(profile: Any, store: ExperimentStore, claim: Any, spec: Any, status: str) -> str:
    """Ghi lượt vừa lưu xuống đĩa vào kho SQL master: 'saved' / 'already' (run_key đã có) / 'skipped' (lượt CHƯA hoàn tất:
    lỗi, huỷ, treo — kho chỉ nhận lượt hoàn tất) / 'error'. Lỗi SQL chỉ cảnh báo — `master_db.import_experiment` nạp bù
    sau. Import muộn để máy không có pyodbc vẫn chạy được khi kho tắt."""
    run_dir = store.run_dir(claim.run_id, spec.label())
    try:
        from . import master_db

        if status != master_db.COMPLETED_STATUS:
            return "skipped"
        with closing(master_db.connect(profile)) as conn:
            run_id, created = master_db.save_run(conn, run_export.run_payload(store.root, run_dir))
        if run_id is None:
            return "skipped"
        return "saved" if created else "already"
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"master_db: không ghi được {run_dir.name}: {type(exc).__name__}: {exc}"[:400], RuntimeWarning)
        return "error"


def _find_known_runs(profile: Any, run_keys: list[str]) -> dict[str, dict[str, Any]]:
    """Trong các `run_key` đưa vào, cái nào ĐÃ có trong kho SQL master: {run_key: {backtest_run_id, batch_name, method}}.
    Một kết nối cho cả batch. Khác `_save_to_master_db`: ở đây lỗi SQL được NÉM ra — người dùng đã bật tra cứu nên không
    âm thầm bỏ qua rồi chạy tốn lượt CLI. Import muộn để máy không có pyodbc vẫn chạy được khi tính năng tắt."""
    from . import master_db

    found: dict[str, dict[str, Any]] = {}
    with closing(master_db.connect(profile)) as conn:
        for run_key in dict.fromkeys(run_keys):
            info = master_db.describe_run(conn, run_key)
            if info is not None:
                found[run_key] = info
    return found


def _tick(tally: dict[str, int], key: str) -> None:
    tally[key] = tally.get(key, 0) + 1
