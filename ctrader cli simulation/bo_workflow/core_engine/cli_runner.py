from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping

from .models import FailureCode, RunResult, RunSpec, RunStatus

# cli_runner.py — chỗ DUY NHẤT được phép spawn ctrader-cli.exe. Trước
# 2026-09-21 nằm ở package riêng `cli_engine/` (tách khỏi core_engine để đối
# xứng với api_engine/); gộp thẳng vào core_engine/ vì hệ hiện chỉ dùng đúng
# backend CLI (api_engine giữ nguyên, không dùng tới) — tách riêng 1 package
# chỉ để chứa 1 file là thừa. Cũng vẫn to hơn ngưỡng 300 dòng mềm vì gánh luôn:
# auth env, redaction, vòng đời child process, đọc CLI metadata, VÀ capability
# probing (port từ pipeline/core/cli.py 2026-09-12) — không tách thêm module
# thứ 11 chỉ cho riêng phần probe.
#
# File này ở bo_workflow/core_engine/cli_runner.py -> trèo 2 cấp (core_engine ->
# bo_workflow -> Sources/Robots) là tới chỗ Combo.algo/MA Cross.algo thật nằm.
ROBOTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT_SECONDS = 1800
REPORT_GRACE_SECONDS = 10
# Ngưỡng watchdog Progress % — 0% có thể là giai đoạn tải tick cache lạnh và im
# lặng nhiều phút (không phải treo thật), nên ngưỡng khởi động dài hơn nhiều so
# với ngưỡng giữa chừng. Port từ pipeline/settings.py (STALL_SECONDS/
# STARTUP_STALL_SECONDS) — số đo thật trên máy này (JP225/HK50 tick-hang).
DEFAULT_STALL_SECONDS = 240
DEFAULT_STARTUP_STALL_SECONDS = 900


@dataclass(frozen=True)
class ProcResult:
    stdout: str
    stderr: str
    returncode: int | None
    stop: str


def select_cli() -> Path:
    override = os.environ.get("CTRADER_CLI", "").strip()
    if override:
        return Path(override)
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    candidates = list((local / "Spotware" / "cTrader").glob("*/ctrader-cli.exe"))
    standalone = local / "Programs" / "cTrader CLI" / "ctrader-cli.exe"
    if standalone.is_file():
        candidates.append(standalone)
    return max(candidates, key=_version_key) if candidates else standalone


def _version_key(path: Path) -> tuple[int, ...]:
    try:
        proc = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return (-1,)
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?\b", proc.stdout + proc.stderr)
    return tuple(int(part or 0) for part in match.groups()) if match else (-1,)


def _stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=8)
def _cli_version(path: str, stamp: tuple[int, int]) -> str:
    del stamp
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    match = re.search(r"Version:\s*([^\s]+)", proc.stdout + proc.stderr)
    return match.group(1) if match else "unknown"


def cli_version(cli_path: Path | None = None) -> str:
    path = cli_path or select_cli()
    return _cli_version(str(path), _stamp(path)) if path.is_file() else "missing"


def auth_options() -> dict[str, str]:
    missing = []
    values = {
        "CTID": os.environ.get("CTRADER_CTID", "").strip(),
        "PWD-FILE": os.environ.get("CTRADER_PWD_FILE", "").strip(),
        "ACCOUNT": os.environ.get("CTRADER_ACCOUNT", "").strip(),
        "BROKER": os.environ.get("CTRADER_BROKER", "").strip(),
    }
    for key, value in values.items():
        if not value:
            missing.append("CTRADER_" + key.replace("-", "_"))
    if missing:
        raise RuntimeError("Missing cTrader environment: " + ", ".join(missing))
    return values


# Danh sách đầy đủ mọi option runner có thể phát ra (port từ pipeline/core/
# cli.py::_CLI_OPTION_ENV_NAMES) — ``-e`` đọc mọi option từ environment, thiếu
# 1 tên ở đây là 1 đường rò rỉ: biến shell cha trùng tên âm thầm đè lên argv.
_OPTION_ENV = {
    "CTRADER_CTID", "CTRADER_PWD_FILE", "CTRADER_ACCOUNT", "CTRADER_BROKER",
    "CTID", "PWD-FILE", "PWD_FILE", "PASSWORD", "ACCOUNT", "BROKER",
    "SYMBOL", "PERIOD", "TIMEFRAME", "START", "END", "DATA-MODE", "DATA_MODE",
    "DATA_FILE", "DATA-FILE", "DATA-DIR", "DATA_DIR", "BALANCE", "COMMISSION",
    "COMMISSION-TYPE", "COMMISSION_TYPE", "COMMISSION-AUTO", "COMMISSION_AUTO",
    "SPREAD", "REPORT", "REPORT-JSON", "REPORT_JSON",
    "PRECISE-CONVERSION", "PRECISE_CONVERSION", "FULL-ACCESS", "FULL_ACCESS",
    "EXIT-ON-STOP", "EXIT_ON_STOP",
}


def child_environment(options: Mapping[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in _OPTION_ENV}
    env.update({str(key): str(value) for key, value in options.items()})
    return env


def redact_text(text: str) -> str:
    for value in auth_options().values() if _auth_env_available() else ():
        if value:
            text = text.replace(str(value), "<redacted>")
    return text


def _auth_env_available() -> bool:
    return all(os.environ.get(name) for name in (
        "CTRADER_CTID", "CTRADER_PWD_FILE", "CTRADER_ACCOUNT", "CTRADER_BROKER"
    ))


def redact_argv(argv: list[str]) -> str:
    secret_prefixes = ("--ctid=", "--pwd-file=", "--account=", "--broker=", "--password=")
    return " ".join(
        arg.split("=", 1)[0] + "=<redacted>" if arg.startswith(secret_prefixes) else arg
        for arg in argv
    )


@lru_cache(maxsize=16)
def _metadata_text(cli: str, cli_stamp: tuple[int, int], algo: str, algo_stamp: tuple[int, int]) -> str:
    del cli_stamp, algo_stamp
    proc = subprocess.run(
        [cli, "metadata", algo],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError("ctrader-cli metadata failed: " + redact_text(proc.stderr or proc.stdout))
    return proc.stdout


def read_metadata(algo_path: Path, cli_path: Path | None = None) -> dict:
    cli = cli_path or select_cli()
    raw = _metadata_text(str(cli), _stamp(cli), str(algo_path.resolve()), _stamp(algo_path))
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("Parameters"), list):
        raise RuntimeError("ctrader-cli metadata returned an unexpected schema")
    return data


# --------------------------------------------------------------------------- #
# Capability probing (port từ pipeline/core/cli.py) — hỏi thẳng binary xem nó
# có hỗ trợ command/option/data-mode TRƯỚC khi chạy hàng loạt. CLI 5.9.x không
# liệt kê hết option trong --help (--data-dir từng là ví dụ đo được: hoạt động
# thật nhưng không có trong help) -> help chỉ đủ để nói CÓ, không đủ để nói
# KHÔNG; khi help im lặng phải hỏi thật bằng 1 lần parse argv (không login).
# --------------------------------------------------------------------------- #

class CliProbeError(RuntimeError):
    """Không xác định được capability của binary; không được suy thành False."""


@lru_cache(maxsize=8)
def _command_reference_for(executable: str, _stamp: tuple[int, int]) -> str:
    errors: list[str] = []
    for flag in ("--help", "--commands"):
        try:
            proc = subprocess.run(
                [executable, flag], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{flag}: {type(exc).__name__}")
            continue
        output = proc.stdout + proc.stderr
        if output.strip() and ("cTrader Command Line Interface" in output
                               or "Interactive shell commands" in output):
            return output
        errors.append(f"{flag}: output không nhận diện được (exit={proc.returncode})")
    raise CliProbeError("Không đọc được capability ctrader-cli: " + "; ".join(errors))


def _command_reference(cli_path: Path) -> str:
    return _command_reference_for(str(cli_path), _stamp(cli_path))


def _match_command(text: str, name: str) -> bool:
    prefix = r"(?:ctrader-cli(?:\.exe)?\s+)?"
    return bool(re.search(
        rf"^\s*{prefix}{re.escape(name)}(?:\s|\.|$)", text, re.MULTILINE | re.IGNORECASE,
    ))


def _match_option(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-])--{re.escape(name)}(?=[=\s,|]|$)", text, re.IGNORECASE))


def cli_has_command(name: str, *, cli_path: Path | None = None) -> bool:
    return _match_command(_command_reference(cli_path or select_cli()), name)


def cli_has_option(name: str, *, cli_path: Path | None = None) -> bool:
    return _match_option(_command_reference(cli_path or select_cli()), name)


def advertised_data_modes(*, cli_path: Path | None = None) -> frozenset[str]:
    """Data mode chính binary công bố — không lấy danh sách tài liệu mới áp lên
    binary cũ (vd 5.9.12 chưa có tick-csv dù reference 5.10 đã có)."""
    known = ("ticks", "m1", "m1-csv", "tick-csv", "open")
    lines = [line.lower() for line in _command_reference(cli_path or select_cli()).splitlines()
             if "--data-mode" in line]
    return frozenset(
        mode for mode in known
        if any(re.search(rf"(?<![\w-]){re.escape(mode)}(?![\w-])", line) for line in lines)
    )


_REJECTION_MARKERS = (
    "Unknown command line parameters found:",   # không có cờ này
    "Invalid parameters:",                       # bị hiểu thành tham số cBot
)


@lru_cache(maxsize=64)
def _option_probe_for(executable: str, _exe_stamp: tuple[int, int],
                      algo_path: str, _algo_stamp: tuple[int, int], token: str) -> bool:
    """Hỏi binary xem có nhận ``token`` không, chỉ qua giai đoạn parse argv (không
    login, không chạm mạng). Lỗi validation KHÁC 2 marker dưới nghĩa là argv đã
    qua được parser, tức cờ tồn tại."""
    name = token.lstrip("-").split("=", 1)[0]
    try:
        proc = subprocess.run(
            [executable, "backtest", algo_path, token],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CliProbeError(f"probe option {name!r} thất bại: {type(exc).__name__}") from exc
    output = proc.stdout + proc.stderr
    if not output.strip():
        raise CliProbeError(f"probe option {name!r} không trả output")
    markers = "|".join(re.escape(marker) for marker in _REJECTION_MARKERS)
    rejected = re.compile(rf"(?:{markers})[^\r\n]*(?<![\w-]){re.escape(name)}(?![\w-])")
    return not rejected.search(output)


def cli_option_supported(token: str, *, algo_path: Path, cli_path: Path | None = None) -> bool:
    """``token`` đúng dạng sẽ đưa vào argv thật (``--data-dir=<path>`` /
    ``--precise-conversion``). Help thắng khi nó CÓ công bố; chỉ khi help im
    lặng mới probe thật, nên 1 lần chạy mặc định không tốn thêm process nào."""
    cli_path = cli_path or select_cli()
    name = token.lstrip("-").split("=", 1)[0]
    if cli_has_option(name, cli_path=cli_path):
        return True
    algo = Path(algo_path).resolve()
    return _option_probe_for(str(cli_path), _stamp(cli_path), str(algo), _stamp(algo), token)


def validate_capabilities(specs: list[RunSpec], *, algo_path: Path, cli_path: Path) -> None:
    """Preflight riêng của engine CLI — dời từ facilitator._validate_capabilities
    2026-09-17 khi tách 2 engine (facilitator.py giờ chỉ gọi đúng 1 tên hàm này,
    không tự biết CLI/API kiểm tra ra sao). Hỏi thẳng binary xem có hỗ trợ
    command/data-mode/option mà batch này cần KHÔNG, trước khi mở experiment/
    claim run nào. Một CLI thiếu ``--precise-conversion`` hay data_mode chưa
    công bố phải chặn NGAY, không để từng run tự báo lỗi sau khi đã tốn thời
    gian chạy thật."""
    if not cli_has_command("backtest", cli_path=cli_path):
        raise RuntimeError(f"Binary không công bố command backtest: {cli_path}")
    advertised = advertised_data_modes(cli_path=cli_path)
    unsupported = sorted({spec.data_mode for spec in specs} - advertised)
    if unsupported:
        raise RuntimeError(
            f"CLI không công bố data_mode {unsupported}; hỗ trợ hiện thấy: {sorted(advertised)}"
        )
    # Token phải ĐÚNG dạng runner sẽ phát ra thật, chỉ probe những cờ ít nhất 1
    # spec trong batch thật sự cần — cờ luôn cần dùng giá trị mẫu vô hại.
    requested = {
        "--start=01/01/2026 00:00": True,
        "--end=02/01/2026 00:00": True,
        "--balance=100000": True,
        "--symbol=probe": True,
        "--period=h1": True,
        "--environment-variables": True,
        "--full-access": True,
        "--exit-on-stop": True,
        "--report-json=probe.json": True,
        "--data-file=probe.csv": any(spec.data_file is not None for spec in specs),
        "--precise-conversion": any(spec.precise_conversion for spec in specs),
        "--commission=0": any(spec.commission is not None for spec in specs),
        "--commission-auto": any(spec.commission_auto for spec in specs),
        "--commission-type=UsdPerOneLot": any(
            spec.commission_type is not None for spec in specs
        ),
        "--spread=0": any(spec.spread is not None for spec in specs),
    }
    missing = sorted(
        token.lstrip("-").split("=", 1)[0]
        for token, needed in requested.items()
        if needed and not cli_option_supported(token, algo_path=algo_path, cli_path=cli_path)
    )
    if missing:
        raise RuntimeError(f"CLI không hỗ trợ option: {missing}")


def _cli_date(value: date) -> str:
    return f"{value:%d/%m/%Y} 00:00"


def write_cbotset(path: Path, spec: RunSpec, signal_file: Path) -> None:
    path.write_text(json.dumps({
        "Chart": {"Symbol": spec.symbol, "Period": spec.timeframe},
        "Parameters": {"SignalFilePath": str(signal_file), **dict(spec.params)},
    }, indent=4), encoding="utf-8")


def build_argv(
    spec: RunSpec,
    *,
    cli_path: Path,
    algo_path: Path,
    cbotset: Path,
    report_json: Path,
) -> list[str]:
    argv = [
        str(cli_path), "backtest", str(algo_path), str(cbotset),
        f"--start={_cli_date(spec.start)}",
        # spec.end la moc DUNG (exclusive), dung y nhu o "To" cua GUI va co
        # --end cua CLI -> khong quy doi gi ca. Truoc 2026-09-13 cho nay cong
        # them 1 ngay (end hieu la "bao gom"), lam notebook va GUI lech 1 ngay.
        f"--end={_cli_date(spec.end)}",
        f"--data-mode={spec.data_mode}",
        f"--balance={spec.balance:g}",
        f"--symbol={spec.symbol}",
        f"--period={spec.timeframe}",
        "-e",
        "--full-access",
        "--exit-on-stop",
    ]
    if spec.data_file:
        argv.append(f"--data-file={spec.data_file}")
    if spec.precise_conversion:
        argv.append("--precise-conversion")
    if spec.commission_auto:
        argv.append("--commission-auto")
    elif spec.commission is not None:
        argv.append(f"--commission={spec.commission:g}")
        if spec.commission_type:
            argv.append(f"--commission-type={spec.commission_type}")
    if spec.spread is not None:
        argv.append(f"--spread={spec.spread:g}")
    argv.append(f"--report-json={report_json}")
    return argv


PollFn = Callable[[list[str], list[str]], str]
ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


def _notify_progress(callback: ProgressFn | None, percent: float, detail: str) -> None:
    """Deliver best-effort progress without allowing UI observers to stop a run."""
    if callback is None:
        return
    try:
        callback(max(0.0, min(100.0, float(percent))), detail)
    except Exception:
        pass


def cancel_requested(should_cancel: CancelFn | None) -> bool:
    """True only when an observer explicitly asks to stop.

    A lost or failing dashboard observer must never terminate a valid run, so
    an exception is read as "keep running". Shared by the process watchdog,
    the pre-spawn check below and facilitator's scheduler."""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def run_process(
    argv: list[str],
    *,
    timeout_s: int,
    cwd: Path,
    env: Mapping[str, str],
    poll: PollFn | None = None,
    interval: float = 0.5,
) -> ProcResult:
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    out: list[str] = []
    err: list[str] = []
    threads = [
        threading.Thread(target=lambda s, sink: sink.extend(s), args=(proc.stdout, out), daemon=True),
        threading.Thread(target=lambda s, sink: sink.extend(s), args=(proc.stderr, err), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_s
    stop = ""
    try:
        while proc.poll() is None:
            if poll and (reason := poll(out, err)):
                stop = reason
                break
            if time.monotonic() > deadline:
                stop = "timeout"
                break
            time.sleep(interval)
    finally:
        if proc.poll() is None:
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=8)
        for thread in threads:
            thread.join(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
    return ProcResult("".join(out), "".join(err), proc.returncode, stop)


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True, check=False)


def run_backtest(
    spec: RunSpec,
    *,
    algo_path: Path,
    signal_file: Path,
    work_dir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
    stall_s: int = DEFAULT_STALL_SECONDS,
    startup_stall_s: int = DEFAULT_STARTUP_STALL_SECONDS,
    cli_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> RunResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "cli.log", "bot.log", "params.cbotset"):
        (work_dir / name).unlink(missing_ok=True)
    cli = cli_path or select_cli()
    report = work_dir / "report.json"
    cbotset = work_dir / "params.cbotset"
    if cancel_requested(should_cancel):
        # Every ctrader-cli process is a fresh server login (FTMO hyperactivity
        # 2026-09-15), so a cancelled run must not spawn one just to kill it.
        now = datetime.now(timezone.utc).isoformat()
        (work_dir / "cli.log").write_text("# cancelled before ctrader-cli was started\n", encoding="utf-8")
        (work_dir / "bot.log").write_text("", encoding="utf-8")
        return RunResult(
            status=RunStatus.CANCELLED,
            failure_code=FailureCode.CANCELLED,
            reason="cancelled before ctrader-cli was started",
            exit_code=None,
            timed_out=False,
            wall_seconds=0.0,
            started_utc=now,
            ended_utc=now,
            cli_path=str(cli),
            cli_version="",
            report_json=report,
            cli_log=work_dir / "cli.log",
            bot_log=work_dir / "bot.log",
            cbotset=cbotset,
        )
    write_cbotset(cbotset, spec, signal_file)
    argv = build_argv(spec, cli_path=cli, algo_path=algo_path, cbotset=cbotset, report_json=report)
    process_env = dict(env) if env is not None else child_environment(auth_options())
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    _notify_progress(on_progress, 0.0, "Starting cTrader CLI")
    result = run_process(
        argv, timeout_s=timeout_s, cwd=ROBOTS_DIR, env=process_env,
        poll=_make_poll(
            report,
            stall_s=stall_s,
            startup_stall_s=startup_stall_s,
            on_progress=on_progress,
            should_cancel=should_cancel,
        ),
    )
    wall = round(time.monotonic() - t0, 1)
    ended = datetime.now(timezone.utc)
    stdout = redact_text(result.stdout)
    stderr = redact_text(result.stderr)
    (work_dir / "cli.log").write_text(
        f"# {started.isoformat()} cwd={ROBOTS_DIR}\n# {redact_argv(argv)}\n\n"
        + stdout + "\n--- STDERR ---\n" + stderr,
        encoding="utf-8",
    )
    (work_dir / "bot.log").write_text(_bot_log(stdout), encoding="utf-8")
    _redact_report(report)
    status, reason, code = _classify(result, report, spec)
    return RunResult(
        status=status,
        failure_code=code,
        reason=reason,
        exit_code=result.returncode,
        timed_out=result.stop in {"timeout", "stalled"},
        wall_seconds=wall,
        started_utc=started.isoformat(),
        ended_utc=ended.isoformat(),
        cli_path=str(cli),
        cli_version=cli_version(cli),
        report_json=report,
        cli_log=work_dir / "cli.log",
        bot_log=work_dir / "bot.log",
        cbotset=cbotset,
    )


@lru_cache(maxsize=8)
def _progress_re(phase: str) -> re.Pattern[str]:
    return re.compile(rf"Progress \| {re.escape(phase)} \| ([\d.]+) %")


def last_progress_pct(lines: list[str], phase: str = "Backtesting") -> float:
    """% tiến trình gần nhất của `phase` trong stdout, hoặc -1 nếu chưa có.
    Port từ pipeline/core/cli.py — dùng để bắt watchdog Progress % kẹt."""
    rx = _progress_re(phase)
    for line in reversed(lines):
        match = rx.search(line)
        if match:
            return float(match.group(1))
    return -1.0


def _make_poll(
    report: Path,
    *,
    stall_s: int = DEFAULT_STALL_SECONDS,
    startup_stall_s: int = DEFAULT_STARTUP_STALL_SECONDS,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> PollFn:
    """Watchdog: report.json hợp lệ -> chờ grace rồi dừng; trước đó canh
    Progress % (port từ pipeline/core/runner.py::_make_poll). 0% gồm cả giai
    đoạn tải tick cache lạnh, có thể im lặng nhiều phút -> ngưỡng khởi động dài
    hơn nhiều ngưỡng giữa chừng, tránh báo treo oan lúc tải tick lần đầu."""
    state = {
        "ready_at": None,
        "best_pct": -1.0,
        "best_pct_t": time.monotonic(),
        "last_sent_pct": None,
    }

    def poll(out: list[str], _err: list[str]) -> str:
        now = time.monotonic()
        if cancel_requested(should_cancel):
            return "cancelled"
        if state["ready_at"] is None and _report_ready(report):
            state["ready_at"] = now
        if state["ready_at"] is not None:
            return "report_ready" if now - state["ready_at"] >= REPORT_GRACE_SECONDS else ""
        pct = last_progress_pct(out)
        if pct >= 0 and pct != state["last_sent_pct"]:
            _notify_progress(on_progress, pct, "Backtesting")
            state["last_sent_pct"] = pct
        if pct > state["best_pct"]:
            state["best_pct"], state["best_pct_t"] = pct, now
        threshold = startup_stall_s if state["best_pct"] <= 0 else stall_s
        if 0 <= pct < 99 and now - state["best_pct_t"] > threshold:
            return "stalled"
        return ""

    return poll


def _report_ready(report: Path) -> bool:
    if not report.exists():
        return False
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        return bool(data.get("main", {}).get("testingPeriod"))
    except Exception:
        return False


def _bot_log(stdout: str) -> str:
    markers = (" | Info | ", " | Trade | ", " | Warning | ", " | Error | ", "Info | ")
    lines = [line for line in stdout.splitlines() if any(marker in line for marker in markers)]
    return "\n".join(lines) + ("\n" if lines else "")


def _classify(result: ProcResult, report: Path, spec: RunSpec) -> tuple[RunStatus, str, FailureCode]:
    if result.stop == "cancelled":
        return RunStatus.CANCELLED, "cancelled by user", FailureCode.CANCELLED
    if _report_ready(report):
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            period = data["main"]["testingPeriod"]
            actual_start = datetime.fromtimestamp(period["startDate"] / 1000, timezone.utc).date()
            actual_end = datetime.fromtimestamp(period["endDate"] / 1000, timezone.utc).date()
        except Exception:
            return RunStatus.NO_REPORT, "report.json exists but cannot be parsed", FailureCode.NO_REPORT
        expected_end = spec.end
        if actual_start != spec.start or actual_end != expected_end:
            return (
                RunStatus.PERIOD_MISMATCH,
                f"testingPeriod {actual_start}..{actual_end} != {spec.start}..{expected_end}",
                FailureCode.PERIOD_MISMATCH,
            )
        return RunStatus.OK, "", FailureCode.NONE
    low = (result.stdout + result.stderr).lower()
    if "message expected" in low:
        return RunStatus.CLI_ERROR, "Message expected", FailureCode.MESSAGE_EXPECTED
    if "additional accessrights" in low:
        return RunStatus.CLI_ERROR, "requires --full-access", FailureCode.ACCESS_REQUIRED
    if "invalid password" in low or "authentication failed" in low or "account cannot be found" in low:
        return RunStatus.CLI_ERROR, "authentication/account failed", FailureCode.AUTH_FAILED
    if result.stop == "timeout":
        return RunStatus.TIMEOUT, "hard timeout before report.json", FailureCode.HARD_TIMEOUT
    if result.stop == "stalled":
        return RunStatus.STALLED, "progress stalled", FailureCode.PROGRESS_STALLED
    return RunStatus.NO_REPORT, f"no valid report.json (exit={result.returncode})", FailureCode.NO_REPORT


def _redact_report(report: Path) -> None:
    if not report.exists():
        return
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception:
        return
    sensitive_keys = {"authornickname", "brokertitle", "accountnumber", "accountid", "ctid"}

    def clean(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: ("<redacted>" if str(key).lower() in sensitive_keys else clean(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return redact_text(value) if isinstance(value, str) else value

    cleaned = clean(data)
    if cleaned != data:
        report.write_text(json.dumps(cleaned, separators=(",", ":")), encoding="utf-8")
