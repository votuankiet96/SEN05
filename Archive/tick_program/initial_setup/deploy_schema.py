"""Deploy the tick_engine SQL schema into SEN05_AutoTrading.

Idempotent: safe to re-run. Uses the program's own DB connection
(tick_engine.data_storage.db_connector) so credentials come from config and are
never passed on the command line.

Usage:
    python initial_setup/deploy_schema.py              # deploy tickdata_setup.sql
    python initial_setup/deploy_schema.py --check      # check schema health only (no changes)
    python initial_setup/deploy_schema.py other.sql    # run a specific file
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from tick_engine.data_storage.db_connector import get_connection  # noqa: E402

_SQL_DIR = _APP_ROOT / "sql"
_DEFAULT_FILE = "tickdata_setup.sql"

_REQUIRED_TABLES = [
    "tick.SymbolMap", "tick.IngestRun", "tick.IngestState",
    "tick.FR40", "tick.DE40", "tick.HK50", "tick.J225", "tick.SP35",
    "tick.UK100", "tick.US500", "tick.US100", "tick.US30", "tick.GOLD", "tick.BTCUSD",
    "SEN.ActiveTask",
]
_REQUIRED_VIEWS = [
    "tick.v_IngestHealth", "tick.v_LatestQuote",
    "tick.v_FR40_Quote", "tick.v_DE40_Quote", "tick.v_HK50_Quote",
    "tick.v_J225_Quote", "tick.v_SP35_Quote", "tick.v_UK100_Quote",
    "tick.v_US500_Quote", "tick.v_US100_Quote", "tick.v_US30_Quote",
    "tick.v_GOLD_Quote", "tick.v_BTCUSD_Quote",
]
_REQUIRED_SYMBOL_IDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 56, 81]

_GO = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE)


def check_schema_health(cursor) -> list[str]:
    """Return a list of missing objects. Empty list = schema is ready."""
    issues: list[str] = []

    cursor.execute(
        "SELECT CASE WHEN OBJECT_ID('DWH.Dim_Symbol','U') IS NOT NULL THEN 1 ELSE 0 END"
    )
    if cursor.fetchone()[0] != 1:
        issues.append("MISSING prerequisite: DWH.Dim_Symbol")
        return issues

    placeholders = ",".join(str(i) for i in _REQUIRED_SYMBOL_IDS)
    cursor.execute(f"SELECT SymbolID FROM DWH.Dim_Symbol WHERE SymbolID IN ({placeholders})")
    have = {r[0] for r in cursor.fetchall()}
    missing_ids = [i for i in _REQUIRED_SYMBOL_IDS if i not in have]
    if missing_ids:
        issues.append(f"MISSING SymbolIDs in DWH.Dim_Symbol: {missing_ids}")

    for obj in _REQUIRED_TABLES:
        cursor.execute(
            "SELECT CASE WHEN OBJECT_ID(?, 'U') IS NOT NULL THEN 1 ELSE 0 END", (obj,)
        )
        if cursor.fetchone()[0] != 1:
            issues.append(f"MISSING table: {obj}")

    for obj in _REQUIRED_VIEWS:
        cursor.execute(
            "SELECT CASE WHEN OBJECT_ID(?, 'V') IS NOT NULL THEN 1 ELSE 0 END", (obj,)
        )
        if cursor.fetchone()[0] != 1:
            issues.append(f"MISSING view: {obj}")

    return issues


def _split_batches(sql_text: str) -> list[str]:
    batches: list[str] = []
    current: list[str] = []
    for line in sql_text.splitlines():
        if _GO.match(line):
            batch = "\n".join(current).strip()
            if batch:
                batches.append(batch)
            current = []
        else:
            current.append(line)
    tail = "\n".join(current).strip()
    if tail:
        batches.append(tail)
    return batches


def _drain_messages(cursor) -> None:
    for msg in getattr(cursor, "messages", None) or []:
        text = msg[-1] if isinstance(msg, (list, tuple)) else msg
        print("    [sql] " + str(text))


def _run_file(cursor, path: Path) -> None:
    print(f"== {path.name} ==")
    for i, batch in enumerate(_split_batches(path.read_text(encoding="utf-8-sig")), 1):
        try:
            cursor.execute(batch)
            _drain_messages(cursor)
            while cursor.nextset():
                pass
        except Exception as exc:
            print(f"  ERROR in batch {i}: {exc}")
            raise


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    files = [a for a in argv if not a.startswith("--")] or [_DEFAULT_FILE]

    conn = get_connection()
    conn.autocommit = True
    cursor = conn.cursor()
    try:
        if check_only:
            print("== Schema health check ==")
            issues = check_schema_health(cursor)
            if issues:
                print(f"  SCHEMA NOT READY — {len(issues)} issue(s):")
                for issue in issues:
                    print(f"  - {issue}")
                print()
                print("  To fix, run:")
                print("    python initial_setup/deploy_schema.py")
                return 2
            else:
                print("  OK — all required tables and views are present.")
                return 0

        issues = check_schema_health(cursor)
        if issues:
            print(f"Pre-flight: {len(issues)} missing object(s) — will be created by setup script.")

        for name in files:
            _run_file(cursor, _SQL_DIR / name)

        issues_after = check_schema_health(cursor)
        if issues_after:
            print(f"WARNING: {len(issues_after)} object(s) still missing after setup:")
            for issue in issues_after:
                print(f"  - {issue}")
            return 1

        cursor.execute(
            "SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
            "WHERE s.name='tick' ORDER BY t.name"
        )
        print("tick tables:", [r[0] for r in cursor.fetchall()])
        print("Schema is ready.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
