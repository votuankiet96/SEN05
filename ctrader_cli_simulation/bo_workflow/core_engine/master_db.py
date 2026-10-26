"""Kho SQL master kết quả backtest (SQL Server) — CHỈ phần SQL. Thiết kế + lý do:
research_notes/sql-master-database-plan-2026-10-14.md; lược đồ: bo_workflow/sql/*.sql.

Nhận `payload` do output_util/run_export.run_payload dựng (đọc file → dict thuần) rồi ghi trong MỘT giao dịch.
File trên đĩa là nguồn sự thật, SQL là bản sao dựng lại được: lỗi SQL không được làm hỏng lượt chạy (nơi gọi
bắt lỗi), và `import_experiment` nạp bù cả experiment bất cứ lúc nào (idempotent theo `run_key`).

Không import facilitator/cli_runner (ranh giới module); đăng nhập Windows (Trusted_Connection), không mật khẩu.
"""
from __future__ import annotations

import dataclasses
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pyodbc

from .configuration import MasterDbProfile, load_master_db_profile
from .models import coerce_float
from .output_util import run_export

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
SCHEMA_FILES = ("001_schema.sql", "002_views.sql")   # nạp theo thứ tự; file chưa có thì bỏ qua
COMPLETED_STATUS = "ok"   # RunStatus.OK — chỉ lượt chạy hoàn tất mới vào kho (xem save_run)
_GO = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)
_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+$")

_BOT_COLUMNS = (
    "strategy", "algo_sha256", "algo_file", "build_time_utc", "source_sha256", "source_code", "source_modified_utc",
)
_SNAPSHOT_COLUMNS = (
    "channel", "strategy", "symbol", "timeframe", "redis_key", "content_sha256", "last_bar_time", "rows_compressed",
    "captured_at_utc",
)
_RUN_COLUMNS = (
    "run_key", "batch_name", "method", "window_name", "zone", "selection_rule", "selection_objective", "strategy", "symbol", "timeframe", "start_date", "end_date_requested",
    "last_data_time", "initial_balance", "ksl_atr_multiple", "ktp_atr_multiple", "risk_percent", "leg_count",
    "parameters_json", "bot_build_id", "signal_snapshot_id", "signal_window_sha256", "engine_profile", "engine_json",
    "cli_version", "symbol_spec_json", "symbol_spec_sha256", "margin_rejections", "net_profit", "profit_factor",
    "total_trades", "win_rate", "max_equity_drawdown_percent", "bot_counters_json", "started_utc", "ended_utc",
)
_TRADE_COLUMNS = (
    "trade_number", "direction", "leg_label", "entry_time_utc", "close_time_utc", "entry_price", "close_price", "volume",
    "gross_profit", "commission", "swap", "net_profit", "close_reason",
)
_DAILY_COLUMNS = ("trading_day", "balance_at_close", "lowest_equity")
_DATETIME_COLUMNS = frozenset({
    "build_time_utc", "source_modified_utc", "last_bar_time", "captured_at_utc", "last_data_time",
    "started_utc", "ended_utc", "entry_time_utc", "close_time_utc",
})
_DATE_COLUMNS = frozenset({"start_date", "end_date_requested", "trading_day"})
_INT_COLUMNS = frozenset({"margin_rejections", "total_trades", "leg_count", "trade_number"})
_FLOAT_COLUMNS = frozenset({
    "initial_balance", "ksl_atr_multiple", "ktp_atr_multiple", "risk_percent", "net_profit", "profit_factor", "win_rate",
    "max_equity_drawdown_percent", "entry_price", "close_price", "volume", "gross_profit", "commission", "swap",
    "balance_at_close", "lowest_equity",
})


def _datetime(value: Any) -> datetime | None:
    """ISO ('2026-10-13T11:08:54.890345Z', '2026-01-06T15:02:28.904+00:00') hoặc 'YYYY-MM-DD HH:MM:SS' → UTC không tz
    (DATETIME2 không có múi giờ). Phần thập phân quá 6 chữ số (manifest MACD Break có 7) bị cắt."""
    if value in (None, ""):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(value).strip().replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _convert(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in _DATETIME_COLUMNS:
        return _datetime(value)
    if column in _DATE_COLUMNS:
        return datetime.fromisoformat(str(value)).date()
    if column in _INT_COLUMNS:
        try:
            return int(value)
        except (TypeError, ValueError):   # vd margin_rejections = "unknown"
            return None
    if column in _FLOAT_COLUMNS:
        return coerce_float(value, None)
    return value


def _values(row: Mapping[str, Any], columns: tuple[str, ...]) -> list[Any]:
    return [_convert(column, row.get(column)) for column in columns]


def _insert_sql(table: str, columns: tuple[str, ...], output: str | None = None) -> str:
    marks = ", ".join("?" for _ in columns)
    returning = f" OUTPUT INSERTED.{output}" if output else ""
    return f"INSERT INTO dbo.{table} ({', '.join(columns)}){returning} VALUES ({marks})"


def connection_string(profile: MasterDbProfile, database: str | None = None) -> str:
    return (
        f"Driver={{{profile.driver}}};Server={profile.server};Database={database or profile.database};"
        "Trusted_Connection=yes;TrustServerCertificate=yes;"
    )


def connect(profile: MasterDbProfile | None = None, *, autocommit: bool = False) -> pyodbc.Connection:
    profile = profile or load_master_db_profile()
    return pyodbc.connect(connection_string(profile), autocommit=autocommit, timeout=15)


def ensure_database(profile: MasterDbProfile | None = None) -> bool:
    """Tạo database nếu chưa có (RECOVERY SIMPLE: kho này sao lưu bằng .bak, không cần log giao dịch phình ra).
    Trả True nếu vừa tạo."""
    profile = profile or load_master_db_profile()
    if not _DATABASE_NAME.match(profile.database):
        raise ValueError(f"tên database không hợp lệ: {profile.database!r}")
    with closing(pyodbc.connect(connection_string(profile, "master"), autocommit=True, timeout=15)) as conn:
        exists = conn.execute("SELECT DB_ID(?)", profile.database).fetchval() is not None
        if not exists:
            conn.execute(f"CREATE DATABASE [{profile.database}]")
            conn.execute(f"ALTER DATABASE [{profile.database}] SET RECOVERY SIMPLE")
    return not exists


def drop_database(profile: MasterDbProfile) -> None:
    """Chỉ để test dọn database thử; KHÔNG bao giờ gọi với database thật."""
    if not _DATABASE_NAME.match(profile.database):
        raise ValueError(f"tên database không hợp lệ: {profile.database!r}")
    with closing(pyodbc.connect(connection_string(profile, "master"), autocommit=True, timeout=15)) as conn:
        conn.execute(f"IF DB_ID(?) IS NOT NULL ALTER DATABASE [{profile.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE", profile.database)
        conn.execute(f"DROP DATABASE IF EXISTS [{profile.database}]")


def ensure_schema(conn: pyodbc.Connection) -> None:
    """Chạy bo_workflow/sql/*.sql theo thứ tự (idempotent: mỗi bảng/chỉ mục/view chỉ tạo khi chưa có)."""
    cursor = conn.cursor()
    for name in SCHEMA_FILES:
        path = SQL_DIR / name
        if not path.is_file():
            continue
        for batch in _GO.split(path.read_text(encoding="utf-8")):
            if batch.strip():
                cursor.execute(batch)
    conn.commit()


def find_run(conn: pyodbc.Connection, run_key: str) -> int | None:
    """backtest_run_id của lượt chạy đã có theo `run_key`, hoặc None — để kiểm "đã chạy chưa" trước khi tốn 1 lượt CLI."""
    row = conn.execute("SELECT backtest_run_id FROM dbo.backtest_run WHERE run_key = ?", run_key).fetchone()
    return row[0] if row else None


def describe_run(conn: pyodbc.Connection, run_key: str) -> dict[str, Any] | None:
    """Như `find_run` nhưng trả thêm thứ để NGƯỜI tìm lại kết quả: id, tên experiment (`batch_name`, thư mục
    `runs/research/<method>/<batch_name>`) và phương pháp. None nếu `run_key` chưa có."""
    row = conn.execute(
        "SELECT backtest_run_id, batch_name, method FROM dbo.backtest_run WHERE run_key = ?", run_key
    ).fetchone()
    if row is None:
        return None
    return {"backtest_run_id": int(row[0]), "batch_name": row[1], "method": row[2]}


# UPDLOCK + HOLDLOCK ở 2 câu SELECT dưới đây: nhiều lượt chạy song song cùng ghi 1 bot/bản chụp MỚI thì lượt đến sau
# phải chờ lượt đến trước ghi xong rồi dùng lại — không có khoá thì cả hai cùng thấy "chưa có", cùng INSERT, một bên
# vi phạm UNIQUE và mất cả lượt (đo 2026-10-14: walk-forward 8 luồng mất 1/66 lượt).
def _ensure_bot_build(cursor: pyodbc.Cursor, bot: Mapping[str, Any]) -> int:
    row = cursor.execute(
        "SELECT bot_build_id, source_sha256 FROM dbo.bot_build WITH (UPDLOCK, HOLDLOCK) WHERE algo_sha256 = ?", bot["algo_sha256"],
    ).fetchone()
    if row:
        # Bản build đã có mà chưa có mã nguồn: bổ sung (chỉ điền chỗ trống, không ghi đè gì đã có).
        if row.source_sha256 is None and bot.get("source_sha256"):
            cursor.execute(
                "UPDATE dbo.bot_build SET source_sha256 = ?, source_code = ?, source_modified_utc = ? WHERE bot_build_id = ?",
                bot["source_sha256"], bot["source_code"], _datetime(bot["source_modified_utc"]), row.bot_build_id,
            )
        return row.bot_build_id
    return cursor.execute(_insert_sql("bot_build", _BOT_COLUMNS, "bot_build_id"), _values(bot, _BOT_COLUMNS)).fetchone()[0]


def _ensure_signal_snapshot(cursor: pyodbc.Cursor, snapshot: Mapping[str, Any]) -> int:
    row = cursor.execute(
        "SELECT signal_snapshot_id FROM dbo.signal_snapshot WITH (UPDLOCK, HOLDLOCK) WHERE channel = ? AND strategy = ? AND symbol = ? "
        "AND timeframe = ? AND content_sha256 = ?",
        snapshot["channel"], snapshot["strategy"], snapshot["symbol"], snapshot["timeframe"], snapshot["content_sha256"],
    ).fetchone()
    if row:
        return row[0]
    return cursor.execute(
        _insert_sql("signal_snapshot", _SNAPSHOT_COLUMNS, "signal_snapshot_id"), _values(snapshot, _SNAPSHOT_COLUMNS),
    ).fetchone()[0]


def save_run(conn: pyodbc.Connection, payload: Mapping[str, Any]) -> tuple[int | None, bool]:
    """Ghi 1 lượt chạy (bot_build + signal_snapshot nếu hash mới, backtest_run, trade, daily_equity) trong MỘT giao dịch.
    Trả (backtest_run_id, đã_ghi_mới). `run_key` đã có → không ghi đè, trả (id cũ, False). Lỗi → hoàn tác, ném lại.
    CHỈ nhận lượt HOÀN TẤT (`status == COMPLETED_STATUS`): lượt lỗi, bị huỷ, treo/timeout hay đang chạy dở không vào
    kho (người dùng chốt 2026-10-14) → trả (None, False), không ghi gì."""
    run = payload["run"]
    if run["status"] != COMPLETED_STATUS:
        return None, False
    existing = find_run(conn, run["run_key"])
    if existing is not None:
        return existing, False
    cursor = conn.cursor()
    try:
        train_run_id = find_run(conn, run["train_run_key"]) if run.get("train_run_key") else None
        run = {**run, "bot_build_id": _ensure_bot_build(cursor, payload["bot_build"]),
               "signal_snapshot_id": _ensure_signal_snapshot(cursor, payload["signal_snapshot"])}
        run_id = cursor.execute(
            _insert_sql("backtest_run", (*_RUN_COLUMNS, "train_run_id"), "backtest_run_id"), [*_values(run, _RUN_COLUMNS), train_run_id],
        ).fetchone()[0]
        for table, rows, columns in (
            ("trade", payload["trades"], _TRADE_COLUMNS), ("daily_equity", payload["daily_equity"], _DAILY_COLUMNS),
        ):
            if rows:
                cursor.fast_executemany = True
                cursor.executemany(
                    _insert_sql(table, ("backtest_run_id", *columns)), [[run_id, *_values(row, columns)] for row in rows],
                )
        conn.commit()
        return run_id, True
    except pyodbc.IntegrityError:
        conn.rollback()
        existing = find_run(conn, run["run_key"])   # hai tiến trình cùng ghi 1 run_key: bên kia thắng
        if existing is None:
            raise
        return existing, False
    except BaseException:
        conn.rollback()
        raise


def import_experiment(conn: pyodbc.Connection, experiment_dir: str | Path) -> dict[str, Any]:
    """Nạp (hoặc nạp bù) cả một experiment từ đĩa: mỗi lượt qua run_export.run_payload → save_run, theo thứ tự thư mục
    (lượt học có số nhỏ hơn lượt kiểm nên `train_run_id` của lượt kiểm trỏ được). Idempotent: chạy lại chỉ thêm phần còn
    thiếu. Lượt lỗi khi đọc/ghi không làm dừng cả experiment — được liệt kê trong kết quả."""
    experiment_dir = Path(experiment_dir)
    saved = existing = not_completed = 0
    errors: list[dict[str, str]] = []
    for run_dir in sorted((experiment_dir / "runs").iterdir()):
        if not (run_dir.is_dir() and (run_dir / "execution.json").is_file()):
            continue
        try:
            run_id, created = save_run(conn, run_export.run_payload(experiment_dir, run_dir))
        except Exception as exc:  # noqa: BLE001 — báo từng lượt, không bỏ cả experiment
            errors.append({"run_dir": run_dir.name, "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        if run_id is None:
            not_completed += 1
        else:
            saved, existing = saved + created, existing + (not created)
    return {"saved": saved, "already_present": existing, "not_completed": not_completed, "errors": errors}


def with_database(profile: MasterDbProfile, database: str) -> MasterDbProfile:
    """Bản sao profile trỏ sang database khác (test dùng database thử)."""
    return dataclasses.replace(profile, database=database)
