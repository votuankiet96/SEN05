"""Persistent metadata owned exclusively by the Dash dashboard.

This store deliberately contains only dashboard concerns: the user's session
title/note, immutable command snapshot, UI lifecycle, and references to the
artifacts owned by ``core_engine``. It never imports core_engine and never
writes under ``bo_workflow/runs``.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from . import signal_sources
except ImportError:  # app.py also runs directly from the dashboard directory.
    import signal_sources

ROOT = Path(__file__).resolve().parent
STATE_ROOT = ROOT / ".session_state"
DB_PATH = STATE_ROOT / "sessions.sqlite"
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  note TEXT NOT NULL,
  state TEXT NOT NULL,
  execution_key TEXT NOT NULL,
  pipeline TEXT NOT NULL,
  experiment TEXT NOT NULL,
  draft_group_id TEXT NOT NULL DEFAULT '',
  resource_group TEXT NOT NULL DEFAULT 'ctrader_cli',
  source_session_id TEXT,
  owner_host TEXT,
  owner_pid INTEGER,
  owner_token TEXT,
  plan_json TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  error_text TEXT NOT NULL DEFAULT '',
  progress_pct REAL NOT NULL DEFAULT 0,
  progress_text TEXT NOT NULL DEFAULT '',
  cancel_requested_utc TEXT,
  created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL,
  started_utc TEXT,
  ended_utc TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_created ON sessions(created_utc DESC);
CREATE INDEX IF NOT EXISTS ix_sessions_state ON sessions(state);
CREATE TABLE IF NOT EXISTS session_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboard_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

RUNNING_STATES = frozenset({"running"})
TERMINAL_STATES = frozenset({"completed", "cached", "cancelled", "failed", "inputs_changed"})
RESOURCE_GROUPS = frozenset({"ctrader_cli", "analysis"})
KIND_RESOURCE_GROUP = {
    "single_backtest": "ctrader_cli",
    "grid_search": "ctrader_cli",
    "walkforward": "ctrader_cli",
    "final_backtest": "ctrader_cli",
    "dsr_pbo": "analysis",
    "monte_carlo": "analysis",
}


class ActiveSessionError(RuntimeError):
    """Dashboard has already claimed its one permitted active CLI run."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    try:
        _ensure_schema(db)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_schema(db: sqlite3.Connection) -> None:
    """Initialise dashboard-owned schema once per process, not per status read."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        db.executescript(_SCHEMA)
        existing = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        migrations = {
            "progress_pct": "REAL NOT NULL DEFAULT 0",
            "progress_text": "TEXT NOT NULL DEFAULT ''",
            "cancel_requested_utc": "TEXT",
            "draft_group_id": "TEXT NOT NULL DEFAULT ''",
            "resource_group": "TEXT NOT NULL DEFAULT 'ctrader_cli'",
            "source_session_id": "TEXT",
            "owner_host": "TEXT",
            "owner_pid": "INTEGER",
            "owner_token": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in existing:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {sql_type}")
        db.execute("CREATE INDEX IF NOT EXISTS ix_sessions_draft_group ON sessions(draft_group_id)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS ix_sessions_resource_state "
            "ON sessions(resource_group, state)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS ix_sessions_source ON sessions(source_session_id)")
        db.commit()
        _SCHEMA_READY = True


def _event(db: sqlite3.Connection, session_id: str, kind: str, detail: str = "") -> None:
    db.execute(
        "INSERT INTO session_events(session_id,at_utc,kind,detail) VALUES (?,?,?,?)",
        (session_id, _now(), kind, detail),
    )


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["plan"] = json.loads(item.pop("plan_json"))
    item["result"] = json.loads(item.pop("result_json") or "{}")
    return item


def get_signal_profile() -> str:
    """Return the VM-wide choice; a missing setting retains legacy DB2."""
    with _db() as db:
        row = db.execute(
            "SELECT value FROM dashboard_settings WHERE key='signal_profile'"
        ).fetchone()
    return signal_sources.require_profile(row["value"] if row else signal_sources.ORIGINAL)


def set_signal_profile(value: str) -> str:
    """Atomically persist the choice for drafts created from any browser tab."""
    profile = signal_sources.require_profile(value)
    with _db() as db:
        db.execute(
            """INSERT INTO dashboard_settings(key,value) VALUES('signal_profile',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (profile,),
        )
    return profile


def confirmed_signal_profile(selected: str) -> str:
    """Reject a stale form rather than silently creating drafts from another source."""
    profile = signal_sources.require_profile(selected)
    if profile != get_signal_profile():
        raise ValueError(
            "Signal source is still being applied or changed in another tab. "
            "Refresh the page, then create the draft again."
        )
    return profile


def create_single_backtest(plan: dict[str, Any]) -> dict[str, Any]:
    """Create one draft session from an already validated, immutable plan."""
    return create_draft_group([plan])[0]


def create_grid_search(plan: dict[str, Any]) -> dict[str, Any]:
    """Persist one Grid Search draft without invoking core_engine or cTrader."""
    if plan.get("schema") != "bo-dashboard-grid-search/v1":
        raise ValueError("session_store accepts only valid Grid Search plans")
    return _create_session(plan, kind="grid_search")


def create_walkforward(plan: dict[str, Any]) -> dict[str, Any]:
    """Persist one independent Walk-forward draft without invoking core_engine."""
    if plan.get("schema") != "bo-dashboard-walkforward/v1":
        raise ValueError("session_store accepts only valid Walk-forward plans")
    return _create_session(plan, kind="walkforward")


def create_walkforward_group(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist all objective drafts in one transaction, or none of them."""
    if not plans or any(plan.get("schema") != "bo-dashboard-walkforward/v1" for plan in plans):
        raise ValueError("session_store accepts only valid Walk-forward plans")
    group_id = uuid.uuid4().hex
    now = _now()
    session_ids: list[str] = []
    with _db() as db:
        for plan in plans:
            session_id = uuid.uuid4().hex
            session_ids.append(session_id)
            db.execute(
                """INSERT INTO sessions(
                    session_id,kind,title,note,state,execution_key,pipeline,experiment,draft_group_id,
                    resource_group,source_session_id,plan_json,created_utc,updated_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, "walkforward", str(plan["title"]), str(plan.get("note") or ""),
                    "draft", str(plan["execution_key"]), str(plan["pipeline"]),
                    str(plan["experiment"]), group_id, "ctrader_cli", None,
                    json.dumps(plan, sort_keys=True, ensure_ascii=False), now, now,
                ),
            )
            _event(db, session_id, "created", "walkforward draft created")
        placeholders = ",".join("?" for _ in session_ids)
        rows = db.execute(
            f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", session_ids
        ).fetchall()
    by_id = {str(row["session_id"]): _row(row) or {} for row in rows}
    return [by_id[session_id] for session_id in session_ids]


def create_grid_diagnostics(plan: dict[str, Any]) -> dict[str, Any]:
    """Persist one read-only DSR/PBO analysis draft linked to its Grid source."""
    if plan.get("schema") != "bo-dashboard-grid-diagnostics/v1":
        raise ValueError("session_store accepts only valid Grid diagnostics plans")
    source_session_id = str((plan.get("input") or {}).get("source_session_id") or "")
    if not source_session_id:
        raise ValueError("Grid diagnostics require a source_session_id")
    return _create_session(
        plan,
        kind="dsr_pbo",
        resource_group="analysis",
        source_session_id=source_session_id,
    )


def _create_session(
    plan: dict[str, Any],
    *,
    kind: str,
    resource_group: str | None = None,
    source_session_id: str | None = None,
) -> dict[str, Any]:
    group = resource_group or KIND_RESOURCE_GROUP.get(kind, "ctrader_cli")
    if group not in RESOURCE_GROUPS:
        raise ValueError(f"Unknown dashboard resource group: {group!r}")
    session_id = uuid.uuid4().hex
    now = _now()
    with _db() as db:
        db.execute(
            """INSERT INTO sessions(
                session_id,kind,title,note,state,execution_key,pipeline,experiment,draft_group_id,
                resource_group,source_session_id,plan_json,created_utc,updated_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                kind,
                str(plan["title"]),
                str(plan.get("note") or ""),
                "draft",
                str(plan["execution_key"]),
                str(plan["pipeline"]),
                str(plan["experiment"]),
                "",
                group,
                source_session_id,
                json.dumps(plan, sort_keys=True, ensure_ascii=False),
                now,
                now,
            ),
        )
        _event(db, session_id, "created", f"{kind} draft created")
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(row) or {}


def create_draft_group(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomically persist a related set of dashboard-only draft sessions.

    A group is metadata only: it records that the form expanded one multi-select
    request into several independent one-process backtest drafts.  It does not
    create a core artifact, claim a run slot, or invoke cTrader CLI.
    """
    if not plans:
        raise ValueError("At least one draft plan is required")
    if any(plan.get("schema") != "bo-dashboard-single-backtest/v1" for plan in plans):
        raise ValueError("session_store accepts only valid Single Backtest plans")
    group_id = uuid.uuid4().hex
    now = _now()
    session_ids: list[str] = []
    with _db() as db:
        for plan in plans:
            session_id = uuid.uuid4().hex
            session_ids.append(session_id)
            db.execute(
                """INSERT INTO sessions(
                    session_id,kind,title,note,state,execution_key,pipeline,experiment,draft_group_id,
                    resource_group,source_session_id,plan_json,created_utc,updated_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    "single_backtest",
                    str(plan["title"]),
                    str(plan.get("note") or ""),
                    "draft",
                    str(plan["execution_key"]),
                    str(plan["pipeline"]),
                    str(plan["experiment"]),
                    group_id,
                    "ctrader_cli",
                    None,
                    json.dumps(plan, sort_keys=True, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            _event(db, session_id, "created", "draft group created")
        placeholders = ",".join("?" for _ in session_ids)
        rows = db.execute(
            f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", session_ids
        ).fetchall()
    rows_by_id = {str(row["session_id"]): _row(row) or {} for row in rows}
    return [rows_by_id[session_id] for session_id in session_ids]


def get(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    with _db() as db:
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(row)


def get_many(session_ids: list[str]) -> list[dict[str, Any]]:
    """Read a selection with one SQLite query while preserving selection order."""
    unique = []
    seen: set[str] = set()
    for raw in session_ids:
        session_id = str(raw).strip()
        if session_id and session_id not in seen:
            seen.add(session_id)
            unique.append(session_id)
    if not unique:
        return []
    placeholders = ",".join("?" for _ in unique)
    with _db() as db:
        rows = db.execute(
            f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", unique
        ).fetchall()
    by_id = {str(row["session_id"]): _row(row) or {} for row in rows}
    return [by_id[session_id] for session_id in unique if session_id in by_id]


def list_sessions(limit: int = 500, *, kind: str | None = None) -> list[dict[str, Any]]:
    with _db() as db:
        if kind is None:
            rows = db.execute(
                "SELECT * FROM sessions ORDER BY created_utc DESC LIMIT ?", (int(limit),)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM sessions WHERE kind=? ORDER BY created_utc DESC LIMIT ?",
                (str(kind), int(limit)),
            ).fetchall()
    return [_row(row) or {} for row in rows]


def active_session(*, kind: str) -> dict[str, Any] | None:
    """Read the active dashboard session of one kind, without claiming a run."""
    with _db() as db:
        row = db.execute(
            """SELECT * FROM sessions WHERE kind=? AND state='running'
               ORDER BY started_utc DESC LIMIT 1""",
            (str(kind),),
        ).fetchone()
    return _row(row)


def claim_run(session_id: str) -> dict[str, Any]:
    """Atomically claim the one-active-session slot for this resource group."""
    now = _now()
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _reconcile_abandoned(db)
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} was not found")
        resource_group = str(row["resource_group"] or KIND_RESOURCE_GROUP.get(row["kind"], "ctrader_cli"))
        active = db.execute(
            """SELECT session_id,title FROM sessions
               WHERE state='running' AND resource_group=? AND session_id<>?""",
            (resource_group, session_id),
        ).fetchone()
        if active:
            raise ActiveSessionError(
                f"Session {active['title']!r} is running; resource group "
                f"{resource_group!r} permits only one active session"
            )
        if row["state"] == "running":
            raise ActiveSessionError("This session has already been claimed by the dashboard")
        if row["state"] not in {"draft", "failed", "cancelled", "completed", "cached"}:
            raise ValueError(f"Session cannot run from state: {row['state']}")
        owner_host = socket.gethostname()
        owner_pid = os.getpid()
        owner_token = uuid.uuid4().hex
        db.execute(
            """UPDATE sessions SET state='running',error_text='',progress_pct=0,progress_text='Preparing run',
               cancel_requested_utc=NULL,started_utc=?,ended_utc=NULL,updated_utc=?,
               owner_host=?,owner_pid=?,owner_token=? WHERE session_id=?""",
            (now, now, owner_host, owner_pid, owner_token, session_id),
        )
        _event(
            db,
            session_id,
            "run_claimed",
            f"resource_group={resource_group}; owner={owner_host}:{owner_pid}",
        )
        claimed = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(claimed) or {}


def complete(session_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if bool(result.get("cancelled")):
        state = "cancelled"
    elif result.get("complete") is False:
        state = "failed"
    elif bool(result.get("cached")):
        state = "cached"
    else:
        state = "completed"
    now = _now()
    progress_pct = 100.0 if state in {"cached", "completed"} else 0.0
    failed_count = int(result.get("failed") or 0)
    if "train" in result and "test" in result:
        not_started = sum(int(((result.get(phase) or {}).get("tally") or {}).get("not_started") or 0)
                          for phase in ("train", "test"))
    else:
        not_started = int((result.get("tally") or {}).get("not_started") or 0)
    if state == "cancelled":
        progress_text = f"Cancelled by user; {not_started:,} work items were not started"
    elif state == "failed":
        progress_text = (
            "Daily CLI budget reached - retry after 00:00 Europe/Prague"
            if result.get("daily_cli_budget_exhausted")
            else f"Incomplete run; {failed_count:,} work items failed"
        )
    elif state == "completed":
        progress_text = "Completed"
    else:
        progress_text = "Loaded cached result"
    error_text = progress_text if state == "failed" else ""
    with _db() as db:
        cursor = db.execute(
            """UPDATE sessions SET state=?,result_json=?,error_text=?,progress_pct=?,progress_text=?,ended_utc=?,updated_utc=?
               WHERE session_id=? AND state='running'""",
            (
                state,
                json.dumps(result, sort_keys=True, default=str),
                error_text,
                progress_pct,
                progress_text,
                now,
                now,
                session_id,
            ),
        )
        if cursor.rowcount:
            _event(db, session_id, state)
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(row) or {}


def fail(session_id: str, error: Exception | str) -> dict[str, Any]:
    now = _now()
    message = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    with _db() as db:
        cursor = db.execute(
            """UPDATE sessions SET state='failed',error_text=?,ended_utc=?,updated_utc=?
               WHERE session_id=? AND state='running'""",
            (message, now, now, session_id),
        )
        if cursor.rowcount:
            _event(db, session_id, "failed", message)
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(row) or {}


def mark_inputs_changed(session_id: str, error: Exception | str) -> dict[str, Any]:
    """Retire a Walk-forward draft whose frozen inputs no longer match reality."""
    now = _now()
    message = f"Inputs changed: {error}. Create a new Walk-forward session."
    with _db() as db:
        cursor = db.execute(
            """UPDATE sessions SET state='inputs_changed',error_text=?,ended_utc=?,updated_utc=?
               WHERE session_id=? AND kind='walkforward' AND state='running'""",
            (message, now, now, session_id),
        )
        if cursor.rowcount:
            _event(db, session_id, "inputs_changed", message)
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(row) or {}


def update_progress(session_id: str, percent: float, detail: str) -> None:
    """Persist latest real CLI progress for refresh/reconnect; never creates an event per poll."""
    bounded = max(0.0, min(100.0, float(percent)))
    with _db() as db:
        db.execute(
            """UPDATE sessions SET progress_pct=?,progress_text=?,updated_utc=?
               WHERE session_id=? AND state='running'""",
            (bounded, str(detail), _now(), session_id),
        )


def request_cancel(session_id: str) -> dict[str, Any]:
    """Request cooperative cancellation; the core runner owns process-tree termination."""
    now = _now()
    with _db() as db:
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} was not found")
        if row["state"] != "running":
            raise ValueError("Only a running session can be stopped")
        if not row["cancel_requested_utc"]:
            db.execute(
                """UPDATE sessions SET cancel_requested_utc=?,progress_text=?,updated_utc=?
                   WHERE session_id=?""",
                (now, "Cancellation requested", now, session_id),
            )
            _event(db, session_id, "cancel_requested")
        updated = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(updated) or {}


def cancel_analysis(session_id: str) -> dict[str, Any]:
    """Immediately close an analysis session whose Dash worker is cancelled."""
    now = _now()
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} was not found")
        if row["resource_group"] != "analysis" or row["state"] != "running":
            raise ValueError("Only a running analysis session can be cancelled immediately")
        db.execute(
            """UPDATE sessions SET state='cancelled',cancel_requested_utc=?,progress_pct=0,
               progress_text='Cancelled by user',ended_utc=?,updated_utc=? WHERE session_id=?""",
            (now, now, now, session_id),
        )
        _event(db, session_id, "cancelled", "analysis worker cancellation requested")
        updated = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return _row(updated) or {}


def is_cancel_requested(session_id: str) -> bool:
    with _db() as db:
        row = db.execute(
            "SELECT cancel_requested_utc FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
    return bool(row and row["cancel_requested_utc"])


def delete_sessions(session_ids: list[str]) -> int:
    """Delete dashboard metadata for explicitly selected sessions that are not running."""
    unique = sorted({str(session_id) for session_id in session_ids if session_id})
    if not unique:
        return 0
    placeholders = ",".join("?" for _ in unique)
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _reconcile_abandoned(db)
        running = db.execute(
            f"""SELECT session_id FROM sessions WHERE session_id IN ({placeholders})
                AND state='running'""",
            unique,
        ).fetchall()
        if running:
            raise ValueError("A running session cannot be deleted. Stop it first.")
        dependents = db.execute(
            f"""SELECT session_id,title FROM sessions
                 WHERE source_session_id IN ({placeholders})
                   AND session_id NOT IN ({placeholders})""",
            (*unique, *unique),
        ).fetchall()
        if dependents:
            titles = ", ".join(str(row["title"]) for row in dependents[:3])
            suffix = "..." if len(dependents) > 3 else ""
            raise ValueError(
                "This session is a source for dependent dashboard sessions. "
                f"Delete the dependents first: {titles}{suffix}"
            )
        db.execute(f"DELETE FROM session_events WHERE session_id IN ({placeholders})", unique)
        cursor = db.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", unique)
    return int(cursor.rowcount)


def reconcile_abandoned_sessions() -> list[str]:
    """Fail locally owned running sessions whose background owner has died."""
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        return _reconcile_abandoned(db)


def _reconcile_abandoned(db: sqlite3.Connection) -> list[str]:
    local_host = socket.gethostname().casefold()
    rows = db.execute(
        """SELECT session_id,owner_host,owner_pid FROM sessions
           WHERE state='running' AND owner_host IS NOT NULL AND owner_pid IS NOT NULL"""
    ).fetchall()
    abandoned: list[str] = []
    now = _now()
    for row in rows:
        if str(row["owner_host"]).casefold() != local_host:
            continue
        owner_pid = int(row["owner_pid"])
        if _process_alive(owner_pid):
            continue
        session_id = str(row["session_id"])
        reason = f"abandoned: owner process {row['owner_host']}:{owner_pid} is no longer alive"
        cursor = db.execute(
            """UPDATE sessions SET state='failed',error_text=?,progress_pct=0,
               progress_text='Abandoned run',ended_utc=?,updated_utc=?
               WHERE session_id=? AND state='running'""",
            (reason, now, now, session_id),
        )
        if cursor.rowcount:
            abandoned.append(session_id)
            _event(db, session_id, "abandoned", reason)
    return abandoned


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def events(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _db() as db:
        rows = db.execute(
            """SELECT at_utc,kind,detail FROM session_events
               WHERE session_id=? ORDER BY event_id DESC LIMIT ?""",
            (session_id, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]
