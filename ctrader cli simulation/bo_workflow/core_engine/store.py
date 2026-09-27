from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

from .models import RunSpec, RunStatus
from .output_util import evidence

# store.py is the durable writer for research runs. It owns SQLite state,
# snapshots, report compression, and final-test (holdout) guard.
#
# [2026-09-22] runs/ dời RA NGOÀI core_engine/, sống thẳng ở bo_workflow/runs/
# (theo yêu cầu người dùng — không muốn dữ liệu chạy thật nằm lẫn trong thư
# mục mã nguồn). File này ở core_engine/store.py -> parents[1] mới đúng là
# bo_workflow/. Sai chỗ này thì RUNS_ROOT trỏ nhầm thư mục.
ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ROOT / "runs" / "research"
PROTOCOL_DB = RUNS_ROOT / "_protocol.sqlite"

# Cột phẳng cho các metric hay lọc/sắp xếp nhất (khớp đúng field selection.rank()
# dùng) — cho phép tra cứu SQL trực tiếp (sqlite3 CLI / DB browser) không cần
# qua Python parse JSON, quan trọng khi lưới/WFO lên tới hàng nghìn run. Chi
# tiết đầy đủ vẫn giữ nguyên trong metrics_json, đây chỉ là bản sao tiện tra.
_METRIC_COLUMNS = (
    "net_profit", "profit_factor", "total_trades", "win_rate",
    "max_equity_drawdown_pct",
)
_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  param_hash TEXT UNIQUE NOT NULL,
  label TEXT,
  status TEXT NOT NULL,
  failure_code TEXT,
  reason TEXT,
  strategy TEXT, symbol TEXT, timeframe TEXT, zone TEXT, window TEXT,
  start_date TEXT, end_date TEXT, balance REAL, data_mode TEXT,
  net_profit REAL, profit_factor REAL, total_trades INTEGER, win_rate REAL,
  max_equity_drawdown_pct REAL,
  params_json TEXT, spec_json TEXT,
  metrics_json TEXT, flags_json TEXT, execution_json TEXT,
  wall_seconds REAL, started_utc TEXT, ended_utc TEXT,
  claim_token TEXT, claim_owner TEXT
);
"""

# Mỗi lần spawn `ctrader-cli backtest` là 1 lần đăng nhập server mới — FTMO đã
# tạm khoá account <FTMO-ACCOUNT-ID> vì "hyperactivity" ngày 2026-09-15 (1.088 + 825
# backtest/ngày). Trần tổng số lần spawn/ngày do người dùng chốt 2026-09-25.
# "Ngày" tính theo giờ Prague — cùng mốc FTMO dùng để chốt ngày giao dịch.
DAILY_CLI_LAUNCH_LIMIT = 1500
_LAUNCH_DAY_TZ = ZoneInfo("Europe/Prague")

_PROTOCOL_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdouts (
  holdout_id TEXT PRIMARY KEY,
  strategy TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  opened_by TEXT, opened_utc TEXT, experiment TEXT
);
CREATE TABLE IF NOT EXISTS cli_launches (
  launch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  experiment TEXT, label TEXT, owner TEXT
);
CREATE INDEX IF NOT EXISTS ix_cli_launches_day ON cli_launches(day);
"""


@dataclass(frozen=True)
class Claim:
    run_id: str
    disposition: str
    token: str | None = None


class LostClaimError(RuntimeError):
    pass


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _owner_alive(owner: str | None, started_utc: str | None) -> bool:
    """Whether the process that claimed a still-``running`` row may still own it.

    Only a dead owner on THIS host is reported as gone; anything that cannot be
    verified (another host, unparsable owner, access denied) counts as alive so
    a live run is never stolen."""
    host, _, pid_text = str(owner or "").rpartition(":")
    if host != socket.gethostname() or not pid_text.isdigit():
        return True
    pid = int(pid_text)
    if pid == os.getpid():
        return True
    try:
        started = datetime.fromisoformat(str(started_utc)) if started_utc else None
    except ValueError:
        started = None
    return _pid_alive(pid, started)


def _pid_alive(pid: int, claimed_at: datetime | None) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # Windows: os.kill(pid, 0) would TERMINATE the process, so query it instead.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        if code.value != still_active:
            return False
        if claimed_at is None:
            return True
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return True
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        created = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
        # Windows reuses PIDs: a process created after the claim cannot be its owner.
        return created <= claimed_at
    finally:
        kernel32.CloseHandle(handle)


class ProtocolStore:
    def __init__(self, path: Path = PROTOCOL_DB):
        self.path = path
        self._lock = threading.Lock()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_PROTOCOL_SCHEMA)

    def check_holdouts(self, specs: list[RunSpec], *, experiment: str, final_open: bool = False) -> None:
        self.open()
        with self._db() as db:
            holdouts = db.execute("SELECT * FROM holdouts").fetchall()
        blocked = []
        for spec in specs:
            for row in holdouts:
                if (
                    row["strategy"] == spec.strategy
                    and row["symbol"] == spec.symbol
                    and row["timeframe"] == spec.timeframe
                    and spec.start.isoformat() <= row["end_date"]
                    and spec.end.isoformat() >= row["start_date"]
                    and not (final_open and row["experiment"] == experiment)
                ):
                    blocked.append(row["holdout_id"])
        if blocked:
            raise RuntimeError("Run intersects locked final-test holdout(s): " + ", ".join(sorted(set(blocked))))

    def reserve_cli_launch(self, *, experiment: str, label: str, limit: int = DAILY_CLI_LAUNCH_LIMIT) -> bool:
        """Đếm + ghi 1 lần spawn CLI trong CÙNG 1 transaction; False (không
        ghi gì) nếu hôm nay đã đủ `limit`. BEGIN IMMEDIATE để nhiều worker/tiến
        trình song song không cùng lọt qua ở lượt cuối."""
        self.open()
        now = datetime.now(timezone.utc)
        day = now.astimezone(_LAUNCH_DAY_TZ).date().isoformat()
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COUNT(*) FROM cli_launches WHERE day=?", (day,)).fetchone()[0]
            if used >= limit:
                return False
            db.execute(
                "INSERT INTO cli_launches(day,at_utc,experiment,label,owner) VALUES (?,?,?,?,?)",
                (day, now.isoformat(), experiment, label, _owner()),
            )
            return True

    def cli_launches_today(self) -> int:
        """Số lần spawn CLI đã ghi trong ngày hiện tại (giờ Prague). Chỉ đọc —
        không tạo DB/bảng nếu chưa có (dùng được từ tầng hiển thị)."""
        if not self.path.is_file():
            return 0
        day = datetime.now(timezone.utc).astimezone(_LAUNCH_DAY_TZ).date().isoformat()
        try:
            with self._db() as db:
                return int(db.execute("SELECT COUNT(*) FROM cli_launches WHERE day=?", (day,)).fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


# Field config chỉ đổi CÁCH chạy 1 batch (tốc độ, log, kiểm tra input) chứ không
# đổi KẾT QUẢ — bỏ qua khi so với experiment.json đã khoá, để chạy tiếp 1
# experiment dở dang với số worker khác không bị chặn. Input thật (.algo/CLI/
# signal) vẫn được chặn riêng bởi sidecar input_fingerprint.json (facilitator).
EXECUTION_ONLY_CONFIG_KEYS = frozenset({
    "max_parallel", "timeout_seconds", "stall_seconds", "startup_stall_seconds",
    "transient_retries", "keep_logs", "force", "expected_input_fingerprint",
})


def _lock_view(doc: Mapping[str, Any]) -> dict[str, Any]:
    config = doc.get("config") or {}
    return {
        **dict(doc),
        "config": {key: value for key, value in config.items() if key not in EXECUTION_ONLY_CONFIG_KEYS},
    }


class ExperimentStore:
    def __init__(self, method: str, name: str):
        self.method = method
        self.name = name
        self.root = RUNS_ROOT / method / name
        self.runs_dir = self.root / "runs"
        self.inputs_dir = self.root / "inputs"
        self.db_path = self.root / "index.sqlite"
        self._lock = threading.Lock()

    def open(self, experiment: Mapping[str, Any]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        exp_path = self.root / "experiment.json"
        text = json.dumps(experiment, indent=2, sort_keys=True, default=str)
        if not exp_path.exists():
            self._write_text_atomic(exp_path, text)
        elif _lock_view(json.loads(exp_path.read_text(encoding="utf-8"))) != _lock_view(json.loads(text)):
            # Khoa chi con hieu luc khi experiment khai locked=True. An toan cua
            # TUNG RUN khong dua vao khoa nay ma dua vao param_hash — hash do gom
            # ca spec (ngay, balance, engine, params) lan sha cua .algo/signal/CLI,
            # nen doi bat cu thu gi la sinh run moi, khong the tai dung nham ket
            # qua cu. Khoa o day chi bao ve Y NGHIA cua cai TEN thu muc — nen chi
            # so cac field quyet dinh ket qua, khong so field chi doi toc do chay.
            if bool(experiment.get("locked", True)):
                raise RuntimeError("experiment.json is locked and differs from requested config")
            self._write_text_atomic(exp_path, text)
        with self._db() as db:
            db.execute(_RUN_SCHEMA)
            # Migration nhẹ cho db cũ (port tinh thần pipeline/core/store.py::
            # _RUN_COLUMN_MIGRATIONS) — schema đổi không được làm mất run đã có.
            existing = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            for column in _METRIC_COLUMNS:
                if column not in existing:
                    sql_type = "INTEGER" if column == "total_trades" else "REAL"
                    db.execute(f"ALTER TABLE runs ADD COLUMN {column} {sql_type}")
            db.execute("PRAGMA journal_mode=WAL")

    def snapshot(self, path: Path) -> tuple[Path, str]:
        digest = sha256_file(path)
        suffix = path.suffix or ".bin"
        dest = self.inputs_dir / f"{digest}{suffix}"
        if not dest.exists():
            temp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copy2(path, temp)
            os.replace(temp, dest)
        return dest, digest

    def param_hash(self, spec: RunSpec, *hash_parts: str) -> str:
        return stable_hash(spec.canonical_json(), *hash_parts)

    def is_terminal_ok(self, param_hash: str) -> bool:
        """Run này đã xong (ok/skipped) chưa — chỉ đọc, để bên gọi khỏi tốn 1
        lượt ngân sách CLI/ngày cho spec mà claim() sẽ trả 'skipped'."""
        with self._db() as db:
            row = db.execute("SELECT status FROM runs WHERE param_hash=?", (param_hash,)).fetchone()
        return bool(row) and RunStatus(row["status"]).terminal_ok

    def claim(self, spec: RunSpec, param_hash: str, *, force: bool = False) -> Claim:
        token = uuid.uuid4().hex
        owner = _owner()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT run_id,status,claim_owner,started_utc FROM runs WHERE param_hash=?", (param_hash,)
            ).fetchone()
            if row:
                status = RunStatus(row["status"])
                if status.terminal_ok and not force:
                    return Claim(row["run_id"], "skipped")
                # A 'running' row whose owner process died (crash, killed
                # dashboard job) would otherwise stay 'busy' forever and a
                # rerun would silently skip it; such a row is reclaimed below.
                if status is RunStatus.RUNNING and _owner_alive(row["claim_owner"], row["started_utc"]):
                    return Claim(row["run_id"], "busy")
                run_id = row["run_id"]
                db.execute(
                    """UPDATE runs SET status=?,failure_code='',reason='',
                       claim_token=?,claim_owner=?,started_utc=?,ended_utc=NULL,
                       net_profit=NULL,profit_factor=NULL,total_trades=NULL,
                       win_rate=NULL,max_equity_drawdown_pct=NULL
                       WHERE run_id=?""",
                    (RunStatus.RUNNING.value, token, owner, now, run_id),
                )
                return Claim(run_id, "claimed", token)
            next_id = db.execute(
                "SELECT COALESCE(MAX(CAST(run_id AS INTEGER)), 0) + 1 FROM runs"
            ).fetchone()[0]
            run_id = f"{next_id:04d}"
            db.execute(
                """INSERT INTO runs
                   (run_id,param_hash,label,status,strategy,symbol,timeframe,zone,window,
                    start_date,end_date,balance,data_mode,params_json,spec_json,
                    claim_token,claim_owner,started_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, param_hash, spec.label(), RunStatus.RUNNING.value,
                    spec.strategy, spec.symbol, spec.timeframe, spec.zone, spec.window,
                    spec.start.isoformat(), spec.end.isoformat(), spec.balance, spec.data_mode,
                    json.dumps(dict(spec.params), sort_keys=True),
                    spec.canonical_json(), token, owner, now,
                ),
            )
            return Claim(run_id, "claimed", token)

    def run_dir(self, run_id: str, label: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in label)[:120]
        return self.runs_dir / f"{run_id}__{safe}"

    def record(
        self,
        *,
        claim: Claim,
        spec: RunSpec,
        param_hash: str,
        run_result: Any,
        metrics: Mapping[str, Any],
        flags: Mapping[str, Any],
        bot_summary: Mapping[str, Any],
        environment: Mapping[str, Any],
        inputs: Mapping[str, Any] | None = None,
        keep_logs: bool = False,
    ) -> None:
        if not claim.token:
            raise LostClaimError("cannot record without a claim token")
        run_dir = self.run_dir(claim.run_id, spec.label())
        run_dir.mkdir(parents=True, exist_ok=True)
        inputs = inputs or {}
        execution = {
            "schema": "bo-research-execution/v1",
            "run_id": claim.run_id,
            "param_hash": param_hash,
            "experiment": self.name,
            "spec": spec.canonical(),
            "status": run_result.status.value,
            "failure_code": run_result.failure_code.value,
            "reason": run_result.reason,
            "exit_code": run_result.exit_code,
            "timed_out": run_result.timed_out,
            "timing": {
                "started_utc": run_result.started_utc,
                "ended_utc": run_result.ended_utc,
                "wall_seconds": run_result.wall_seconds,
            },
            "inputs": {
                "algo_sha256": inputs.get("algo_sha256"),
                "signal_sha256": inputs.get("signal_sha256"),
            },
            "cli": {
                "path": run_result.cli_path, "version": run_result.cli_version,
                "sha256": inputs.get("cli_sha256"),
            },
            "environment": environment,
            "bot_summary": bot_summary,
            "validity_flags": flags,
        }
        self._write_text_atomic(run_dir / "spec.json", json.dumps(spec.canonical(), indent=2, sort_keys=True))
        self._write_text_atomic(run_dir / "execution.json", json.dumps(execution, indent=2, sort_keys=True, default=str))
        if run_result.cbotset.exists():
            shutil.copy2(run_result.cbotset, run_dir / "params.cbotset")
        self._compress_report(run_result.report_json, run_dir / "report.json.gz")
        bad = run_result.status is not RunStatus.OK or _has_bad_flag(flags)
        if keep_logs or bad:
            shutil.copy2(run_result.bot_log, run_dir / "bot.log")
            if run_result.status is not RunStatus.OK:
                shutil.copy2(run_result.cli_log, run_dir / "cli.log")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute(
                """SELECT 1 FROM runs WHERE run_id=? AND param_hash=?
                   AND status=? AND claim_token=?""",
                (claim.run_id, param_hash, RunStatus.RUNNING.value, claim.token),
            ).fetchone()
            if not owned:
                raise LostClaimError(f"lost claim for run {claim.run_id}")
            db.execute(
                f"""UPDATE runs SET status=?,failure_code=?,reason=?,
                   {",".join(f"{c}=?" for c in _METRIC_COLUMNS)},
                   metrics_json=?,flags_json=?,execution_json=?,wall_seconds=?,
                   started_utc=?,ended_utc=?,claim_token=NULL WHERE run_id=?""",
                (
                    run_result.status.value, run_result.failure_code.value, run_result.reason,
                    *(metrics.get(c) for c in _METRIC_COLUMNS),
                    json.dumps(dict(metrics), sort_keys=True, default=str),
                    json.dumps(dict(flags), sort_keys=True, default=str),
                    json.dumps(execution, sort_keys=True, default=str),
                    run_result.wall_seconds, run_result.started_utc, run_result.ended_utc,
                    claim.run_id,
                ),
            )

    def write_sidecar(self, name: str, payload: Any) -> Path:
        """Ghi mot file JSON phu ben canh index.sqlite (vd quyet dinh chon tham
        so cua walk-forward). Di qua day chu khong ghi thang tu facilitator de
        giu dung mot cua ghi xuong dia — cung co ghi nguyen tu nhu moi thu khac.
        """
        path = self.root / name
        self._write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True, default=str))
        return path

    def read_sidecar(self, name: str) -> Any:
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def rows(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY CAST(run_id AS INTEGER)").fetchall()
        return [dict(row) for row in rows]

    def flat_rows(self) -> list[dict[str, Any]]:
        """Đọc bảng runs và trải phẳng metrics/flags/params ra cùng 1 cấp —
        dạng mà selection.py cần. Dời từ facilitator._store_rows() 2026-09-21
        (đọc state là việc của store, không phải của orchestrator)."""
        out = []
        for row in self.rows():
            metrics = json.loads(row.get("metrics_json") or "{}")
            flags = json.loads(row.get("flags_json") or "{}")
            params = json.loads(row.get("params_json") or "{}")
            out.append({**row, **metrics, "validity_flags": flags, "params": params})
        return out

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _compress_report(src: Path, dest: Path) -> None:
        if not src.exists():
            return
        temp = dest.with_suffix(dest.suffix + ".tmp")
        with open(src, "rb") as reader, gzip.open(temp, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        os.replace(temp, dest)


def _has_bad_flag(flags: Mapping[str, Any]) -> bool:
    return not evidence.report_ok(flags)


def export_release(run_dir: str | Path, dest: str | Path) -> dict[str, str]:
    """Copy đúng bộ file cần cho 1 release (spec/execution/params đã chọn) từ
    1 run_dir sang nơi khác. Dời từ facilitator.py 2026-09-21 — thao tác thuần
    trên khuôn thư mục run_dir/ mà store.py sở hữu, không phải orchestration."""
    run_dir = Path(run_dir)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("spec.json", "execution.json"):
        shutil.copy2(run_dir / name, dest / name)
    cbotset = run_dir / "params.cbotset"
    if cbotset.exists():
        shutil.copy2(cbotset, dest / "selected.cbotset")
    return {"dest": str(dest)}
