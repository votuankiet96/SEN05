from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core_engine.models import FailureCode, RunResult, RunSpec, RunStatus

from .bridge_client import BridgeClient, BridgeTimeout
from .contracts import BacktestApiJob, BacktestApiResult

# api_runner.py là ENGINE #2 — đối xứng với cli_engine/cli_runner.py, chỗ DUY
# NHẤT trong api_engine/ mà core_engine/facilitator.py được phép gọi vào. Nó
# KHÔNG gọi lại cli_engine (không "gọi chéo") — chỉ mượn đúng 1 kiểu dữ liệu
# dùng chung (RunResult/RunSpec/RunStatus/FailureCode) từ core_engine.models.
#
# File này ở bo_workflow/api_engine/api_runner.py -> bridge/ nằm ngay cạnh.
BRIDGE_ROOT = Path(__file__).resolve().parent / "bridge"
DEFAULT_TIMEOUT_SECONDS = 1800

# Data\cBots\<cbot_name>\<instance-guid>\Backtesting\log.txt — Backtesting.Start
# (native Plugin API) đăng ký/tra cứu RobotType THEO TÊN, cùng cơ chế GUI dùng,
# nên log vẫn rơi vào đúng thư mục tên cBot như chạy tay, KHÔNG phải thư mục
# đặt tên theo sha256 mà `ctrader-cli backtest <path>` (engine CLI) hay dùng.
CBOTS_DATA_DIR = Path.home() / "Documents" / "cAlgo" / "Data" / "cBots"


def validate_capabilities(specs: list[RunSpec], *, profile: Any, bridge_root: Path = BRIDGE_ROOT) -> None:
    """Preflight của engine API — đối xứng với cli_runner.validate_capabilities.

    2 việc: (1) plugin phải đang sống (đọc heartbeat, không tự spawn được gì
    để "thử" như CLI); (2) chặn tên/giá trị tham số sai NGAY phía Python, dù
    BacktestMapper.cs phía plugin cũng đã tự chặn — chặn sớm ở đây tránh phải
    tốn 1 vòng nộp job/chờ bridge chỉ để nhận lại đúng cùng 1 lỗi."""
    heartbeat = bridge_root / "heartbeat" / "BoBacktestRunner.json"
    if not heartbeat.is_file():
        raise RuntimeError(
            f"Plugin BoBacktestRunner chưa từng chạy (không thấy {heartbeat}) — "
            "cần mở cTrader Desktop và bật plugin trước."
        )
    known = set(profile.params) | set(profile.infra_params)
    for spec in specs:
        unknown = sorted(set(spec.params) - known)
        if unknown:
            raise RuntimeError(f"{spec.label()}: tham số lạ không có trong profile: {unknown}")
        for name, pdef in profile.params.items():
            if pdef.kind != "enum" or name not in spec.params:
                continue
            index = int(spec.params[name])
            if not 0 <= index < len(pdef.enum_values):
                raise RuntimeError(
                    f"{spec.label()}: {name} index {index} ngoài phạm vi "
                    f"[0, {len(pdef.enum_values) - 1}]"
                )


def run_backtest(
    spec: RunSpec,
    *,
    algo_path: Path,
    signal_file: Path,
    work_dir: Path,
    profile: Any,
    timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
    bridge_root: Path = BRIDGE_ROOT,
) -> RunResult:
    """Tương đương cli_runner.run_backtest nhưng đi qua Plugin/bridge thay vì
    spawn ctrader-cli.exe. Trả về ĐÚNG khuôn RunResult để facilitator.py dùng
    lại nguyên vẹn evidence.py/store.py phía sau, không cần biết backend nào
    vừa chạy."""
    work_dir.mkdir(parents=True, exist_ok=True)
    report_json = work_dir / "report.json"
    bot_log = work_dir / "bot.log"
    cli_log = work_dir / "api.log"
    cbotset = work_dir / "params.cbotset"
    cbotset.write_text("", encoding="utf-8")  # engine API không dùng .cbotset — giữ path tồn tại cho store.record

    # RunSpec stores enum values as ordinals so the CLI and store have one
    # canonical representation.  The 5.9.16 Plugin API cannot resolve an
    # ordinal without the robot's enum type, so its job contract must carry
    # the enum member names instead (e.g. ``Fib1000`` rather than ``2``).
    api_params = _api_parameters(spec.params, profile)
    api_params["SignalFilePath"] = str(signal_file)

    job = BacktestApiJob(
        job_id=f"core-{uuid.uuid4().hex}",
        robot_name=profile.cbot_name,
        algo_path=str(algo_path),
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        start_utc=datetime.combine(spec.start, datetime.min.time(), tzinfo=timezone.utc),
        end_utc=datetime.combine(spec.end, datetime.min.time(), tzinfo=timezone.utc),
        balance=spec.balance,
        data_mode=spec.data_mode,
        precise_conversion=spec.precise_conversion,
        commission_auto=spec.commission_auto,
        parameters=api_params,
        data_file=spec.data_file,
        commission=spec.commission,
        commission_type=spec.commission_type,
        spread=spec.spread,
    )

    client = BridgeClient(bridge_root)
    started = datetime.now(timezone.utc)
    try:
        result = client.submit_and_wait(job, timeout_seconds=timeout_s, poll_seconds=1.0)
    except BridgeTimeout:
        ended = datetime.now(timezone.utc)
        cli_log.write_text(f"BridgeTimeout after {timeout_s}s waiting for job {job.job_id}", encoding="utf-8")
        bot_log.write_text("", encoding="utf-8")
        return RunResult(
            status=RunStatus.TIMEOUT, failure_code=FailureCode.HARD_TIMEOUT,
            reason=f"bridge timeout after {timeout_s}s", exit_code=None, timed_out=True,
            wall_seconds=round((ended - started).total_seconds(), 1),
            started_utc=started.isoformat(), ended_utc=ended.isoformat(),
            cli_path=str(bridge_root), cli_version="api",
            report_json=report_json, cli_log=cli_log, bot_log=bot_log, cbotset=cbotset,
        )
    ended = datetime.now(timezone.utc)

    if result.json_report:
        report_json.write_text(result.json_report, encoding="utf-8")
    bot_log.write_text(_find_bot_log(profile.cbot_name, spec, started), encoding="utf-8")
    cli_log.write_text(
        f"status={result.status} error={result.error} backtestingError={result.backtesting_error}\n"
        f"capabilities={result.capabilities}\n",
        encoding="utf-8",
    )

    status, code, reason = _classify(result)
    return RunResult(
        status=status, failure_code=code, reason=reason,
        exit_code=0 if status is RunStatus.OK else None,
        timed_out=False,
        wall_seconds=result.wall_seconds or round((ended - started).total_seconds(), 1),
        started_utc=started.isoformat(), ended_utc=ended.isoformat(),
        cli_path=str(bridge_root), cli_version="api",
        report_json=report_json, cli_log=cli_log, bot_log=bot_log, cbotset=cbotset,
    )


def _classify(result: BacktestApiResult) -> tuple[RunStatus, FailureCode, str]:
    if result.status == "ok":
        return RunStatus.OK, FailureCode.NONE, ""
    if result.status == "unsupported_capability":
        return RunStatus.CLI_ERROR, FailureCode.UNSUPPORTED_CAPABILITY, result.error or "unsupported capability"
    if result.status == "invalid_job":
        return RunStatus.CLI_ERROR, FailureCode.INVALID_JOB, result.error or "invalid job"
    if result.status == "report_mismatch":
        return RunStatus.PERIOD_MISMATCH, FailureCode.REPORT_MISMATCH, result.error or "report mismatch"
    return RunStatus.NO_REPORT, FailureCode.NO_REPORT, result.error or result.backtesting_error or "unknown plugin error"


def _api_parameters(params: Mapping[str, str], profile: Any) -> dict[str, str]:
    """Adapt canonical RunSpec parameter values to the Plugin API contract."""
    out = dict(params)
    for name, value in params.items():
        pdef = profile.params.get(name)
        if pdef is None or pdef.kind != "enum":
            continue
        if str(value).isdigit():
            index = int(value)
            try:
                out[name] = str(pdef.enum_values[index])
            except IndexError as exc:
                raise ValueError(f"{name}: enum index out of range: {index}") from exc
    return out


def _find_bot_log(cbot_name: str, spec: RunSpec, started: datetime) -> str:
    """Best-effort: quét Data\\cBots\\<cbot_name>\\*\\Backtesting\\log.txt tìm
    bản mới nhất được ghi SAU thời điểm job này bắt đầu. Không tìm được thì trả
    rỗng — evidence.py coi như không có bot log, giống hệt hành vi khi CLI thật
    sự không tạo ra bot.log, KHÔNG suy diễn/giả lập nội dung."""
    root = CBOTS_DATA_DIR / cbot_name
    if not root.is_dir():
        return ""
    cutoff = started.timestamp() - 5.0  # trừ hao lệch đồng hồ nhỏ
    best_path: Path | None = None
    best_mtime = cutoff
    for log_path in root.glob("*/Backtesting/log.txt"):
        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime, best_path = mtime, log_path
    if best_path is None:
        return ""
    try:
        return best_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
