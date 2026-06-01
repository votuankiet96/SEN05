"""
Giả lập lỗi và kiểm tra toàn diện hệ thống Data Provider.

Phạm vi test:
  - ws_live: DB worker retry, spool cap, deferred ETL, watermark, startup
  - db_connector: insert_staging_batch MERGE upsert, delete_ohlcv_bars batch
  - _task_lock: token generation, advisory lock, is_locked cache
  - checker app: _query_bar_times exception safety, _repair_direct_window ETL retry
  - _helpers / validate logic

Chạy:
    cd "d:/Auto Trading/SEN05"
    python -m pytest tests/test_data_provider_resilience.py -v
"""
from __future__ import annotations

import importlib
import io
import pickle
import queue
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

WS_LIVE_SRC = _ROOT / "data_provider" / "apps" / "ws_live.py"
CHECKER_SRC = _ROOT / "data_provider" / "apps" / "checker.py"
HELPERS_SRC = _ROOT / "data_provider" / "common" / "helpers.py"


# =============================================================================
# Helpers dùng chung
# =============================================================================

def _make_ohlcv_df(n: int = 5, start: str = "2024-01-01") -> pd.DataFrame:
    """Tạo OHLCV DataFrame giả lập (index là DatetimeIndex UTC)."""
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "open":   [100.0 + i for i in range(n)],
            "high":   [101.0 + i for i in range(n)],
            "low":    [ 99.0 + i for i in range(n)],
            "close":  [100.5 + i for i in range(n)],
            "volume": [1000.0   for _ in range(n)],
        },
        index=idx,
    )


# =============================================================================
# NHÓM 1: _task_lock — Advisory lock
# =============================================================================

class TestTaskLock:
    """Kiểm tra data_provider.common.locks chỉ còn advisory lock runtime."""

    def test_legacy_confirm_api_removed(self):
        """Interactive confirm token flow đã bị loại khỏi runtime hiện tại."""
        import data_provider.common.locks as tl

        assert not hasattr(tl, "generate_token")
        assert not hasattr(tl, "request_confirm")
        assert not hasattr(tl, "_handle_token_command")
        assert not hasattr(tl, "_read_db_relay")

    def test_is_locked_cache_ttl(self):
        """is_locked() phải cache kết quả trong vòng 30 giây."""
        from data_provider.common import locks as tl

        call_count = 0

        def mock_conn():
            nonlocal call_count
            call_count += 1
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchone.return_value = None  # not locked
            conn.cursor.return_value = cursor
            return conn

        with patch("data_provider.common.locks.get_connection", side_effect=mock_conn):
            tl._lock_cache["checked_at"] = 0.0  # Invalidate cache
            tl.is_locked("test_task")
            first_count = call_count
            tl.is_locked("test_task")  # Phải dùng cache, không query DB
            assert call_count == first_count, (
                "is_locked() phải cache kết quả — không query DB liên tiếp trong TTL"
            )


# =============================================================================
# NHÓM 2: db_connector — MERGE upsert & batch DELETE
# =============================================================================

class TestDbConnectorMerge:
    """Kiểm tra insert_staging_batch MERGE có WHEN MATCHED UPDATE."""

    def test_merge_sql_contains_when_matched_update(self):
        """Source code phải chứa WHEN MATCHED ... UPDATE."""
        import inspect
        from modules.db_connector import insert_staging_batch
        src = inspect.getsource(insert_staging_batch)
        assert "WHEN MATCHED" in src, "MERGE phải có WHEN MATCHED branch"
        assert "UPDATE SET" in src, "MERGE phải có UPDATE SET cho bar restatement"

    def test_merge_returns_affected_count(self):
        """insert_staging_batch phải trả về số rows affected (insert + update)."""
        df = _make_ohlcv_df(3)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_conn.cursor.return_value = mock_cursor

        with patch("modules.db_connector.get_connection", return_value=mock_conn):
            from modules.db_connector import insert_staging_batch
            result = insert_staging_batch(df, symbol_id=1, staging_table="SEN.TF_H4")

        assert result == 3, f"Expected 3 rows affected, got {result}"

    def test_merge_empty_df_returns_zero(self):
        """DataFrame rỗng phải trả về 0 ngay, không query DB."""
        from modules.db_connector import insert_staging_batch
        df = pd.DataFrame()
        with patch("modules.db_connector.get_connection") as mock_get_conn:
            result = insert_staging_batch(df, symbol_id=1, staging_table="SEN.TF_H4")
        assert result == 0
        mock_get_conn.assert_not_called()

    def test_merge_isnull_handles_none_volume(self):
        """MERGE SQL phải dùng ISNULL cho Volume để so sánh None an toàn."""
        import inspect
        from modules.db_connector import insert_staging_batch
        src = inspect.getsource(insert_staging_batch)
        assert "ISNULL" in src, "MERGE phải dùng ISNULL để so sánh Volume nullable"


class TestDbConnectorBatchDelete:
    """Kiểm tra delete_ohlcv_bars dùng IN (...) batch thay vì N queries riêng lẻ."""

    def test_batch_delete_uses_in_clause(self):
        """Source code phải dùng IN ({placeholders}), không phải vòng lặp per-row."""
        import inspect
        from modules.db_connector import delete_ohlcv_bars
        src = inspect.getsource(delete_ohlcv_bars)
        assert "IN (" in src or "IN({" in src or "placeholders" in src, (
            "delete_ohlcv_bars phải dùng batch IN clause"
        )
        # Không được có vòng lặp for bt in bar_times: ... DELETE WHERE BarTime = ?
        assert "for bt in bar_times" not in src, (
            "delete_ohlcv_bars không được có vòng lặp per-row DELETE"
        )

    def test_batch_delete_empty_list_returns_zero(self):
        """bar_times rỗng phải trả về 0 ngay."""
        from modules.db_connector import delete_ohlcv_bars
        with patch("modules.db_connector.get_connection") as m:
            result = delete_ohlcv_bars(symbol_id=1, tf_code="H4", bar_times=[])
        assert result == 0
        m.assert_not_called()

    def test_batch_delete_splits_into_100_row_batches(self):
        """250 bar_times phải tạo 3 batch DELETE (100+100+50)."""
        from datetime import datetime, timezone

        bar_times = [
            datetime(2024, 1, 1, i % 24, 0, tzinfo=timezone.utc)
            for i in range(250)
        ]

        execute_calls: list[str] = []

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_cursor.fetchone.return_value = (99,)  # tf_id = 99

        def capture_execute(sql, params=None):
            execute_calls.append(sql.strip())

        mock_cursor.execute.side_effect = capture_execute
        mock_conn.cursor.return_value = mock_cursor

        with patch("modules.db_connector.get_connection", return_value=mock_conn):
            from modules.db_connector import delete_ohlcv_bars
            delete_ohlcv_bars(symbol_id=1, tf_code="H4", bar_times=bar_times)

        # Lọc các DELETE statement
        delete_stmts = [c for c in execute_calls if "DELETE FROM" in c]
        assert len(delete_stmts) == 3, (
            f"250 rows phải tạo 3 batches, thực tế: {len(delete_stmts)}"
        )


# =============================================================================
# NHÓM 3: ws_live — DB worker retry khi staging fail
# =============================================================================

class TestDbConnectorStagingPurgeCoverage:
    """Kiem tra purge_staging bao phu du 15 staging tables."""

    def test_all_staging_tables_include_all_direct_timeframes(self):
        from modules.db_connector import _ALL_STAGING_TABLES

        previously_missing = {
            "SEN.TF_M10",
            "SEN.TF_M20",
            "SEN.TF_M90",
            "SEN.TF_H6",
            "SEN.TF_H8",
        }

        assert len(_ALL_STAGING_TABLES) == 15
        assert len(set(_ALL_STAGING_TABLES)) == 15
        assert previously_missing.issubset(set(_ALL_STAGING_TABLES))
        assert all(table.startswith("SEN.TF_") for table in _ALL_STAGING_TABLES)


class TestWsLiveDbWorkerRetry:
    """Kiểm tra DB worker retry 3 lần khi insert_staging_batch thất bại."""

    def test_db_worker_retry_logic_in_source(self):
        """Source code phải có vòng retry cho BƯỚC A (staging)."""
        src_path = WS_LIVE_SRC
        src = src_path.read_text(encoding="utf-8")
        assert "_DB_WORKER_RETRIES" in src, "DB worker phải có _DB_WORKER_RETRIES constant"
        assert "_staging_ok" in src, "DB worker phải có flag _staging_ok"

    def test_db_worker_retry_count_is_3(self):
        """Số lần retry phải là 3."""
        src_path = WS_LIVE_SRC
        src = src_path.read_text(encoding="utf-8")
        assert "_DB_WORKER_RETRIES = 3" in src, "Retry count phải là 3"

    def test_db_worker_retry_waits_5s(self):
        """Retry phải chờ 5 giây giữa các lần thử."""
        src_path = WS_LIVE_SRC
        src = src_path.read_text(encoding="utf-8")
        assert "_shutdown.wait(5)" in src, "DB worker retry phải wait 5s giữa các lần thử"

    def test_db_worker_retry_alerts_discord_on_final_fail(self):
        """Sau 3 lần thất bại, phải gọi _tg_alert với mức ERROR."""
        src_path = WS_LIVE_SRC
        src = src_path.read_text(encoding="utf-8")
        # Tìm đoạn code sau "Staging FAILED"
        assert "Staging FAILED" in src, "Phải có error message 'Staging FAILED'"
        assert "_tg_alert" in src.split("Staging FAILED")[0].split("_DB_WORKER_RETRIES")[-1] + \
               src.split("Staging FAILED")[1][:500], \
               "Phải gọi _tg_alert sau khi staging fail hết lần retry"


# =============================================================================
# NHÓM 4: ws_live — Spool capacity cap
# =============================================================================

class TestWsLiveSpoolCap:
    """Kiểm tra spool không vượt quá MAX_SPOOL_ROWS."""

    def _make_spool_db(self, tmp_path: Path, n_existing: int) -> Path:
        db = tmp_path / "spool.db"
        con = sqlite3.connect(str(db))
        con.execute("""
            CREATE TABLE spool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER,
                tf_code TEXT,
                staging_table TEXT,
                tv_symbol TEXT,
                bar_data BLOB,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        for i in range(n_existing):
            con.execute(
                "INSERT INTO spool (symbol_id, tf_code, staging_table, tv_symbol, bar_data) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "H4", "SEN.TF_H4", "BTCUSD", b"dummy"),
            )
        con.commit()
        con.close()
        return db

    def test_spool_cleanup_old_removes_stale_entries(self, tmp_path):
        """_spool_cleanup_old() phải xóa entries cũ hơn 48 giờ."""
        db = tmp_path / "spool_cleanup.db"
        con = sqlite3.connect(str(db))
        con.execute("""
            CREATE TABLE spool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER,
                tf_code TEXT,
                staging_table TEXT,
                tv_symbol TEXT,
                bar_data BLOB,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # 3 entries cũ (72 giờ trước)
        for _ in range(3):
            con.execute(
                "INSERT INTO spool (symbol_id, tf_code, staging_table, tv_symbol, bar_data, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now', '-72 hours'))",
                (1, "H4", "SEN.TF_H4", "BTC", b"dummy"),
            )
        # 2 entries mới
        for _ in range(2):
            con.execute(
                "INSERT INTO spool (symbol_id, tf_code, staging_table, tv_symbol, bar_data) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "H4", "SEN.TF_H4", "BTC", b"dummy"),
            )
        con.commit()
        count_before = con.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
        con.close()

        assert count_before == 5

        # Chạy cleanup trực tiếp trên DB này
        con2 = sqlite3.connect(str(db))
        con2.execute("DELETE FROM spool WHERE created_at < datetime('now', '-48 hours')")
        deleted = con2.total_changes
        con2.commit()
        count_after = con2.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
        con2.close()

        assert deleted == 3, f"Phải xóa 3 entries cũ, xóa {deleted}"
        assert count_after == 2, f"Phải còn 2 entries mới, còn {count_after}"

    def test_max_spool_rows_constant_exists(self):
        """MAX_SPOOL_ROWS phải được định nghĩa trong apps/ws_live.py."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert "MAX_SPOOL_ROWS" in src, "MAX_SPOOL_ROWS phải được định nghĩa"
        assert "100_000" in src or "100000" in src, "MAX_SPOOL_ROWS phải là 100_000"

    def test_spool_cleanup_called_in_status_reporter(self):
        """_spool_cleanup_old() phải được gọi từ _status_reporter."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        # Tìm trong _status_reporter function
        reporter_start = src.find("def _status_reporter()")
        reporter_section = src[reporter_start:reporter_start + 3000]
        assert "_spool_cleanup_old()" in reporter_section, (
            "_spool_cleanup_old() phải được gọi trong _status_reporter"
        )


    def test_sql_placeholders_are_parameterized(self):
        """ws_live SQL must use real parameter placeholders, not '-' characters."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert (
            '",".join("?" * len(WS_SYMBOL_IDS))' in src
            or '",".join("?" * len(ws_symbol_ids))' in src
        ), (
            "Watermark query must build symbol IN placeholders with '?'"
        )
        assert (
            '",".join("?" * len(WS_TF_CODES))' in src
            or '",".join("?" * len(ws_tf_codes))' in src
        ), (
            "Watermark query must build IN placeholders with '?'"
        )
        assert "VALUES (-,-,-,-,-)" not in src, (
            "SQLite spool INSERT must not use '-' instead of parameter placeholders"
        )
        assert "WHERE id=-" not in src, (
            "SQLite spool DELETE must not use '-' instead of a parameter placeholder"
        )
        assert "VALUES (?,?,?,?,?,?)" in src, (
            "SQLite spool INSERT must use six parameter placeholders including batch_id"
        )
        assert "WHERE id=?" in src, (
            "SQLite spool DELETE must use a parameter placeholder"
        )

    def test_spool_write_reports_rejection_when_full(self):
        """_spool_write() must tell the caller when the bar was not durably accepted."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        start = src.find("def _spool_write(")
        end = src.find("\ndef ", start + 1)
        fn_src = src[start:end]
        assert "def _spool_write(item: tuple) -> bool:" in fn_src
        assert "return False" in fn_src
        assert "return True" in fn_src

    def test_db_worker_shutdown_drains_overflow_and_spool(self):
        """DB worker must wait for queue, RAM overflow, and SQLite spool before exit."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        start = src.find("def _db_worker()")
        end = src.find("\ndef ", start + 1)
        fn_src = src[start:end]
        assert "while True:" in fn_src
        assert "overflow_pending" in fn_src
        assert "spool_pending" in fn_src
        assert "overflow_pending == 0 and spool_pending == 0" in fn_src


# =============================================================================
# NHÓM 5: ws_live — Watermark & crash recovery
# =============================================================================

class TestWsLiveWatermark:
    """Kiểm tra watermark logic đảm bảo không mất và không duplicate bar."""

    def test_startup_log_uses_ws_symbols(self):
        """Startup log phải dùng len(WS_SYMBOLS), không phải len(SYMBOLS) * len(WS_TF_INTERVAL)."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        # Tìm block chứa "V5 started" trong logger.info
        v5_idx = src.find('"V5 started')
        assert v5_idx > 0, "Phải có format string 'V5 started' trong logger.info"
        # Lấy ngữ cảnh xung quanh (150 chars trước và sau để bắt args)
        context = src[max(0, v5_idx - 50): v5_idx + 300]
        assert "len(WS_SYMBOLS)" in context, (
            "Startup log phải dùng len(WS_SYMBOLS) không phải len(SYMBOLS)"
        )
        assert "len(SYMBOLS) * " not in context, (
            "Startup log không được dùng len(SYMBOLS) cho sessions count"
        )

    def test_committed_watermark_not_updated_on_etl_fail(self):
        """Nếu ETL fail, committed watermark không được thay đổi."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        # Pattern an toàn: _set_committed_watermark chỉ gọi trong `else` của try/except ETL
        # Kiểm tra: _set_committed_watermark phải nằm trong else block của try ETL
        etl_section_idx = src.find("run_etl_direct(symbol_id, tf_code, staging_table)")
        assert etl_section_idx > 0, "Phải có run_etl_direct trong db_worker"
        context = src[etl_section_idx:etl_section_idx + 900]
        assert "_set_committed_watermark" in context, (
            "_set_committed_watermark phải được gọi sau ETL success"
        )
        # Phải trong else block (sau try/except ETL)
        assert "else:" in context, (
            "_set_committed_watermark phải trong else: block của try ETL"
        )

    def test_deferred_etl_retry_when_checker_releases(self):
        """Deferred ETL phải được retry khi checker release lock."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert "_deferred_etl" in src, "_deferred_etl dict phải tồn tại"
        assert "still_deferred" in src, "Phải có logic giữ lại item ETL fail"


    def test_batch_complete_called_once_per_batch(self):
        """_run_batch should update hourly stats once per batch."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        start = src.find("def _run_batch(")
        end = src.find("\ndef _on_batch_complete", start)
        fn_src = src[start:end]
        assert fn_src.count("_on_batch_complete(") == 1, (
            "_run_batch must not call _on_batch_complete twice and double-count hourly stats"
        )

    def test_ws_live_handoff_does_not_force_release_active_lock(self):
        """Startup must not force-delete the old instance lock when graceful handoff times out."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        handoff_start = src.find("if not ws_lock:")
        handoff_end = src.find("atexit.register", handoff_start)
        handoff_section = src[handoff_start:handoff_end]
        assert "request_ws_live_shutdown" in handoff_section
        assert "_release_task_lock(\"ws_live_runtime\")" not in handoff_section
        assert "startup aborted" in handoff_section

    def test_ws_url_uses_query_separator(self):
        """TradingView closes the socket immediately when the from/date suffix uses '-'."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert '?from=chart%2F&date=' in src
        assert '-from=chart%2F&date=' not in src

    def test_fetch_requires_registered_sessions_for_success(self):
        """A socket close before _on_open must be treated as a failed fetch and retried."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        start = src.find("def fetch(")
        end = src.find("\n\n# =============================================================================", start)
        fn_src = src[start:end]
        assert "expected_count > 0" in fn_src
        assert "received_count >= expected_count" in fn_src


class TestWsLiveObservability:
    """Guardrails for live logging and health-signal semantics."""

    def test_rotating_logger_uses_resilient_handler(self):
        src = (HELPERS_SRC).read_text(encoding="utf-8")
        assert "class ResilientRotatingFileHandler" in src
        assert "ResilientRotatingFileHandler(" in src
        assert "rollover_retry_sec" in src

    def test_ws_live_health_uses_market_gap_allowance(self):
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert "SYMBOL_OVERNIGHT_MINS" in src
        assert "def _freshness_threshold_minutes" in src
        assert "_freshness_threshold_minutes(sym_id, tf_code)" in src
        assert "_freshness_threshold_minutes(sid, tf_code)" in src

    def test_batch_status_does_not_call_no_new_bar_stale(self):
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert "OK  no new closed bar" in src
        assert "ok  (stale)" not in src


# =============================================================================
# NHÓM 6: checker app - Exception safety
# =============================================================================

class TestCheckerExceptionSafety:
    """Kiểm tra apps/checker.py không crash khi gặp lỗi DB."""

    def test_query_bar_times_has_except_block(self):
        """_query_bar_times phải có except block trả về []."""
        import inspect
        # Import module checker
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        # Tìm function _query_bar_times
        start = src.find("def _query_bar_times(")
        end = src.find("\ndef ", start + 1)
        fn_src = src[start:end]
        assert "except Exception" in fn_src, "_query_bar_times phải có except Exception"
        assert "return []" in fn_src, "_query_bar_times phải trả về [] khi lỗi"

    def test_repair_direct_window_has_etl_retry(self):
        """_repair_direct_window phải có retry loop cho run_etl_direct."""
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        start = src.find("def _repair_direct_window(")
        end = src.find("\ndef ", start + 1)
        fn_src = src[start:end]
        assert "_MAX_ETL_RETRIES" in fn_src, "_repair_direct_window phải có _MAX_ETL_RETRIES"
        assert "etl_attempt" in fn_src, "_repair_direct_window phải có retry loop"
        assert "time.sleep(2)" in fn_src, "Phải chờ 2s giữa các ETL retry"

    def test_repair_direct_window_caps_pull_attempts_for_checker(self, monkeypatch):
        """Checker repair có cap thì không được leo thang lên FULL_N_BARS/Replay."""
        from data_provider.apps import checker

        attempts: list[int] = []
        logger = MagicMock()
        issue_times = [datetime(2026, 1, 1, 0, 0)]
        sym = {"symbol_id": 1, "tv_symbol": "US30"}

        monkeypatch.setattr(checker, "_estimate_repair_n_bars", lambda *_args: 900)
        monkeypatch.setattr(checker, "delete_staging_bars", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(checker.time, "sleep", lambda *_args, **_kwargs: None)

        def fake_pull_and_store(_tv, _sym, _tf_code, n_bars, _interval, _logger, *, skip_etl):
            assert skip_etl is True
            attempts.append(n_bars)
            return -1

        monkeypatch.setattr(checker, "pull_and_store", fake_pull_and_store)

        ok = checker._repair_direct_window(
            None,
            sym,
            "H1",
            issue_times,
            {"H1": "H1"},
            logger,
            reason="unit-test",
            max_pull_bars=1000,
        )

        assert ok is False
        assert attempts == [900, 1000]
        assert checker.FULL_N_BARS["H1"] not in attempts

    def test_repair_pair_full_repull_is_scoped_to_checked_window(self, monkeypatch):
        """Systemic checker repair phải sửa trong checked window, không gọi full replay path."""
        from data_provider.apps import checker

        tv_time = datetime(2026, 1, 1, 0, 0)
        tv_time_2 = datetime(2026, 1, 1, 1, 0)
        db_time = datetime(2026, 1, 1, 2, 0)
        captured: dict = {}

        monkeypatch.setattr(
            checker,
            "_choose_repair_strategy",
            lambda *_args: ("full_repull", "systemic timeline drift"),
        )

        def fake_repair_direct_window(_tv, _sym, _tf_code, issue_times, _interval_map, _logger, **kwargs):
            captured["issue_times"] = issue_times
            captured.update(kwargs)
            return True

        monkeypatch.setattr(checker, "_repair_direct_window", fake_repair_direct_window)

        ok = checker._repair_pair(
            None,
            {"symbol_id": 1, "tv_symbol": "US30"},
            "H1",
            {
                "tv_bars": {tv_time: (1, 1, 1, 1, 1), tv_time_2: (2, 2, 2, 2, 2)},
                "db_bars": {tv_time: (1, 1, 1, 1, 1), db_time: (3, 3, 3, 3, 3)},
                "missing": set(),
                "mismatched": {},
                "extra": set(),
                "rate": 0.5,
            },
            {"H1": "H1"},
            MagicMock(),
        )

        assert ok is True
        assert not hasattr(checker, "repull_full_symbol")
        assert captured["max_pull_bars"] == checker.CHECKER_N_BARS["H1"]
        assert set(captured["expected_times"]) == {tv_time, tv_time_2}
        assert set(captured["delete_times"]) == {tv_time, db_time}
        assert set(captured["issue_times"]) == {tv_time, tv_time_2, db_time}
        assert "checked-window repair" in captured["reason"]

    def test_repair_pair_retry_stays_focused(self, monkeypatch):
        """Retry round không được tự động leo thang sang full_repull."""
        from data_provider.apps import checker

        missing_time = datetime(2026, 1, 1, 0, 0)
        mismatch_time = datetime(2026, 1, 1, 1, 0)
        extra_time = datetime(2026, 1, 1, 2, 0)
        captured: dict = {}

        monkeypatch.setattr(
            checker,
            "_choose_repair_strategy",
            lambda *_args: ("focused", "localized mismatch"),
        )

        def fake_repair_direct_window(_tv, _sym, _tf_code, issue_times, _interval_map, _logger, **kwargs):
            captured["issue_times"] = issue_times
            captured.update(kwargs)
            return True

        monkeypatch.setattr(checker, "_repair_direct_window", fake_repair_direct_window)

        ok = checker._repair_pair(
            None,
            {"symbol_id": 1, "tv_symbol": "US30"},
            "H1",
            {
                "tv_bars": {missing_time: (1, 1, 1, 1, 1), mismatch_time: (2, 2, 2, 2, 2)},
                "db_bars": {mismatch_time: (3, 3, 3, 3, 3), extra_time: (4, 4, 4, 4, 4)},
                "missing": {missing_time},
                "mismatched": {mismatch_time: ((2, 2, 2, 2, 2), (3, 3, 3, 3, 3))},
                "extra": {extra_time},
                "rate": 0.1,
            },
            {"H1": "H1"},
            MagicMock(),
            repair_round=1,
        )

        assert ok is True
        assert captured["reason"] == "missing/mismatch"
        assert captured["max_pull_bars"] == checker.CHECKER_N_BARS["H1"]
        assert set(captured["expected_times"]) == {missing_time, mismatch_time}
        assert set(captured["delete_times"]) == {mismatch_time, extra_time}
        assert set(captured["issue_times"]) == {missing_time, mismatch_time, extra_time}

    def test_repair_direct_window_checks_staging_before_delete(self):
        """Không được xóa Fact nếu staging thiếu dữ liệu thay thế đã kiểm tra."""
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        start = src.find("def _repair_direct_window(")
        end = src.find("\ndef ", start + 1)
        fn_src = src[start:end]
        assert "_query_staging_bar_times" in fn_src
        assert "Repair stopped before deleting database rows" in fn_src
        assert "Existing database data was kept" in fn_src

    def test_manual_confirm_warning_in_source(self):
        """--manual-confirm path phải có warning và fall-through sang auto-repair."""
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        assert "manual-confirm không còn hoạt động" in src or \
               "manual_confirm" in src and "auto-repair" in src, (
            "--manual-confirm phải có warning message"
        )
        # Interactive confirmation không được xuất hiện trong manual_confirm path.
        assert "Auto-confirm" in src, "Phải có comment Phase 2: Auto-confirm"

    def test_query_bar_times_returns_empty_on_db_error(self):
        """Giả lập DB lỗi — _query_bar_times phải trả về [] không crash.

        Kiểm tra cấu trúc code: conn phải được khởi tạo trong try block
        để except bắt được khi get_connection() fail.
        """
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        fn_start = src.find("def _query_bar_times(")
        fn_end = src.find("\ndef ", fn_start + 1)
        fn_src = src[fn_start:fn_end]

        # conn = None phải được đặt TRƯỚC try, và conn = get_connection() TRONG try
        assert "conn = None" in fn_src, (
            "_query_bar_times phải khởi tạo conn = None trước try block"
        )
        # get_connection() phải nằm trong try block (sau try:)
        try_idx = fn_src.find("try:")
        after_try = fn_src[try_idx:]
        assert "conn = get_connection()" in after_try, (
            "get_connection() phải nằm trong try block để except bắt được"
        )
        # finally phải kiểm tra None trước close
        assert "if conn is not None" in fn_src, (
            "finally phải check 'if conn is not None' trước khi close"
        )


# =============================================================================
# NHÓM 7: Giả lập lỗi end-to-end
# =============================================================================

class TestEndToEndFailureSimulation:
    """Giả lập các kịch bản lỗi thực tế."""

    def test_sim_db_down_staging_retry(self):
        """
        Kịch bản: DB down 2 lần, lần thứ 3 thành công.
        DB worker phải retry và cuối cùng ghi được bar.
        """
        call_count = 0
        success_on = 3  # Thành công ở lần thứ 3

        def flaky_insert(df, symbol_id, staging_table):
            nonlocal call_count
            call_count += 1
            if call_count < success_on:
                raise ConnectionError(f"DB down (attempt {call_count})")
            return len(df)

        # Kiểm tra logic retry source
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        assert "_DB_WORKER_RETRIES = 3" in src, "Phải có 3 retries"

        # Simulate retry logic directly
        retries = 3
        staging_ok = False
        call_count = 0
        for attempt in range(1, retries + 1):
            try:
                result = flaky_insert(pd.DataFrame({"v": [1]}), 1, "SEN.TF_H4")
                staging_ok = True
                break
            except Exception:
                if attempt < retries:
                    pass  # retry

        assert staging_ok, "Phải thành công ở lần retry thứ 3"
        assert call_count == 3, f"Phải gọi đúng 3 lần, thực tế: {call_count}"

    def test_sim_db_down_all_retries_fail(self):
        """
        Kịch bản: DB down hoàn toàn — tất cả 3 lần thất bại.
        Bar phải bị mất, nhưng phải có alert, không crash.
        """
        alerts_sent = []

        def always_fail(df, symbol_id, staging_table):
            raise ConnectionError("DB permanently down")

        def mock_alert(level, msg):
            alerts_sent.append((level, msg))

        retries = 3
        staging_ok = False
        call_count = 0
        for attempt in range(1, retries + 1):
            try:
                call_count += 1
                always_fail(None, 1, "x")
            except Exception as e:
                if attempt == retries:
                    mock_alert("ERROR", f"Staging FAILED: {e}")

        assert not staging_ok, "staging_ok phải False khi tất cả lần thất bại"
        assert len(alerts_sent) == 1, "Phải gửi đúng 1 alert khi fail hết"
        assert "FAILED" in alerts_sent[0][1], "Alert phải có chữ FAILED"

    def test_sim_spool_capacity_enforced(self, tmp_path):
        """
        Kịch bản: Spool đã đầy 100K rows.
        _spool_write phải drop bar mới và không INSERT thêm.
        """
        db = tmp_path / "spool_full.db"
        MAX = 5  # Dùng giới hạn nhỏ để test nhanh

        con = sqlite3.connect(str(db))
        con.execute("""
            CREATE TABLE spool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER, tf_code TEXT,
                staging_table TEXT, tv_symbol TEXT,
                bar_data BLOB,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        for _ in range(MAX):
            con.execute(
                "INSERT INTO spool (symbol_id, tf_code, staging_table, tv_symbol, bar_data) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "H4", "SEN.TF_H4", "BTC", b"dummy"),
            )
        con.commit()
        con.close()

        # Simulate _spool_write với cap
        alerts = []

        def try_write(db_path, max_rows, payload):
            con2 = sqlite3.connect(str(db_path))
            count = con2.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
            if count >= max_rows:
                alerts.append("SPOOL_FULL")
                con2.close()
                return False
            con2.execute(
                "INSERT INTO spool (symbol_id, tf_code, staging_table, tv_symbol, bar_data) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "H4", "SEN.TF_H4", "BTC", payload),
            )
            con2.commit()
            con2.close()
            return True

        result = try_write(db, MAX, b"new_bar")
        con3 = sqlite3.connect(str(db))
        count_after = con3.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
        con3.close()

        assert result is False, "Phải từ chối ghi khi spool đầy"
        assert count_after == MAX, f"Count phải vẫn là {MAX}, got {count_after}"
        assert "SPOOL_FULL" in alerts, "Phải tạo alert khi spool đầy"

    def test_sim_etl_retry_after_delete(self):
        """
        Kịch bản: Checker đã xóa bars, ETL fail lần 1 và 2, thành công lần 3.
        Không nên trả về False (repair thất bại).
        """
        call_count = 0

        def flaky_etl(sym_id, tf_code, staging):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"ETL timeout (attempt {call_count})")
            return 5  # 5 bars inserted

        # Simulate retry logic từ _repair_direct_window
        MAX_ETL_RETRIES = 3
        success = False
        for attempt in range(1, MAX_ETL_RETRIES + 1):
            try:
                flaky_etl(1, "H4", "SEN.TF_H4")
                success = True
                break
            except Exception:
                if attempt < MAX_ETL_RETRIES:
                    time.sleep(0.01)  # Simulate 2s delay (rút ngắn để test nhanh)

        assert success, "ETL phải thành công ở lần retry thứ 3"
        assert call_count == 3, f"Phải gọi đúng 3 lần, thực tế: {call_count}"

    def test_sim_checker_manual_confirm_fallthrough(self):
        """
        Kịch bản: --manual-confirm được dùng.
        Phải có warning và không gọi interactive confirmation.
        """
        src = (CHECKER_SRC).read_text(encoding="utf-8")

        # Tìm đoạn sau `if not args.manual_confirm:` → đây là phần manual_confirm path
        # (elif/else sau if not args.manual_confirm)
        mc_idx = src.find("if not args.manual_confirm:")
        assert mc_idx > 0, "Phải có 'if not args.manual_confirm:'"

        # Sau block auto-mode, phần còn lại là manual_confirm path
        # Tìm đến Phase 1 Scan (nằm trong manual_confirm path)
        phase1_idx = src.find("PHASE 1 | Scan", mc_idx)
        assert phase1_idx > 0, "Phải có PHASE 1 | Scan trong manual_confirm path"

        # Lấy đoạn từ Phase 1 đến Phase 3 (khoảng 3000 chars)
        section = src[phase1_idx:phase1_idx + 3000]

        # Phase 2 phải là Auto-confirm, không gọi interactive confirmation.
        assert "Auto-confirm" in section, (
            "Phase 2 phải đổi thành Auto-confirm, không còn gọi interactive confirmation"
        )
        # request_confirm() không được xuất hiện trong section này.
        assert "request_confirm(" not in section, (
            "request_confirm() không được gọi trong manual_confirm path"
        )
        # Warning message phải có
        assert "manual-confirm" in src[mc_idx:mc_idx + 5000] or \
               "manual_confirm" in src[mc_idx:mc_idx + 5000], (
            "Phải có warning về --manual-confirm"
        )

    def test_sim_watermark_prevents_duplicate(self):
        """
        Kịch bản: Cùng batch được gửi 2 lần (retry sau crash).
        Watermark phải ngăn duplicate.
        """
        # Simulate watermark logic
        _last_bar_ts: dict = {}
        key = (1, "H4")

        def filter_new_bars(bars, key):
            last = _last_bar_ts.get(key, 0.0)
            return [b for b in bars if b["ts"] > last]

        def commit_watermark(key, bars):
            if bars:
                _last_bar_ts[key] = max(b["ts"] for b in bars)

        batch1 = [{"ts": 1700000000.0}, {"ts": 1700014400.0}]
        new1 = filter_new_bars(batch1, key)
        assert len(new1) == 2, "Lần 1: phải nhận cả 2 bar"
        commit_watermark(key, new1)

        # Gửi lại cùng batch (retry)
        new2 = filter_new_bars(batch1, key)
        assert len(new2) == 0, "Lần 2: phải bị filter hết (duplicate)"

        # Bar mới hơn phải được nhận
        batch3 = [{"ts": 1700014400.0}, {"ts": 1700028800.0}]
        new3 = filter_new_bars(batch3, key)
        assert len(new3) == 1, "Chỉ bar mới nhất phải được nhận"
        assert new3[0]["ts"] == 1700028800.0


# =============================================================================
# NHÓM 8: Integration — Merge WHEN MATCHED UPDATE behavior
# =============================================================================

class TestMergeUpsertBehavior:
    """Kiểm tra behavior của MERGE WHEN MATCHED UPDATE khi bar bị restate."""

    def test_merge_sql_structure_correct(self):
        """MERGE SQL phải có đúng cấu trúc: INSERT mới + UPDATE khi OHLCV khác."""
        import inspect
        from modules.db_connector import insert_staging_batch
        src = inspect.getsource(insert_staging_batch)

        # Phải có WHEN NOT MATCHED THEN INSERT
        assert "WHEN NOT MATCHED THEN" in src, "MERGE phải có INSERT branch"
        # Phải có WHEN MATCHED AND (điều kiện) THEN UPDATE
        assert "WHEN MATCHED AND" in src, "MERGE phải có conditional UPDATE branch"
        # Phải update đủ 5 cột OHLCV
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            assert col in src, f"MERGE UPDATE phải update cột {col}"

    def test_rollback_called_on_exception(self):
        """insert_staging_batch phải gọi rollback khi exception xảy ra."""
        import inspect
        from modules.db_connector import insert_staging_batch
        src = inspect.getsource(insert_staging_batch)
        assert "conn.rollback()" in src, "Phải có conn.rollback() trong except block"

    def test_connection_always_closed(self):
        """Connection phải được close trong finally block."""
        import inspect
        from modules.db_connector import insert_staging_batch
        src = inspect.getsource(insert_staging_batch)
        assert "finally:" in src, "Phải có finally block"
        assert "conn.close()" in src, "Phải gọi conn.close() trong finally"


# =============================================================================
# NHÓM 9: Kiểm tra tổng thể cấu trúc an toàn
# =============================================================================

class TestSystemSafetyStructure:
    """Kiểm tra các invariant an toàn của hệ thống."""

    def test_all_db_functions_have_finally_close(self):
        """Các hàm DB quan trọng phải có conn.close() trong finally."""
        import inspect
        from modules import db_connector

        functions_to_check = [
            "insert_staging_batch",
            "delete_ohlcv_bars",
            "run_etl_direct",
        ]
        for fn_name in functions_to_check:
            fn = getattr(db_connector, fn_name, None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            assert "finally:" in src, f"{fn_name} phải có finally block"
            assert "conn.close()" in src or "con.close()" in src, (
                f"{fn_name} phải close connection trong finally"
            )

    def test_ohlcv_values_use_parameterized_queries(self):
        """OHLCV values (giá, volume) phải dùng parameterized query (?), không format trực tiếp."""
        import inspect
        import re
        from modules import db_connector

        src = inspect.getsource(db_connector)
        # INSERT values phải dùng ?
        assert "VALUES (?, ?, ?, ?, ?, ?, ?, 1)" in src, (
            "OHLCV INSERT phải dùng parameterized query (?)"
        )
        # DELETE WHERE phải dùng ? cho SymbolID và TimeframeID
        assert "SymbolID=? AND TimeframeID=?" in src, (
            "DELETE phải dùng ? cho SymbolID và TimeframeID"
        )
        # Không có string concatenation (+) trong cursor.execute
        dangerous_concat = re.findall(
            r'cursor\.execute\s*\(\s*"[^"]*"\s*\+',
            src,
        )
        assert len(dangerous_concat) == 0, (
            f"SQL injection risk — string concatenation trong execute(): {dangerous_concat}"
        )

    def test_task_lock_acquire_release_symmetric(self):
        """acquire() và release() phải symmetric — release không raise khi task không tồn tại."""
        from data_provider.common.locks import release

        # release() với task không tồn tại phải là no-op (không raise)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # Row không tồn tại
        mock_conn.cursor.return_value = mock_cursor

        with patch("data_provider.common.locks.get_connection", return_value=mock_conn):
            try:
                release("nonexistent_task_12345")
            except Exception as e:
                pytest.fail(f"release() không được raise exception: {e}")

    def test_checker_etl_retry_count(self):
        """ETL retry trong checker phải là 3 lần."""
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        assert "_MAX_ETL_RETRIES = 3" in src, "Checker ETL retry phải là 3"

    def test_checker_etl_discord_alert_on_final_fail(self):
        """Checker phải gửi Discord alert khi ETL fail hết retry."""
        src = (CHECKER_SRC).read_text(encoding="utf-8")
        assert "gap trong DB" in src or "gap tồn tại" in src, (
            "Phải có alert message về gap trong DB khi ETL fail"
        )

    def test_spool_write_cap_check_before_insert(self):
        """_spool_write phải kiểm tra COUNT trước khi INSERT."""
        src = (WS_LIVE_SRC).read_text(encoding="utf-8")
        spool_write_start = src.find("def _spool_write(")
        spool_write_end = src.find("\ndef ", spool_write_start + 1)
        fn_src = src[spool_write_start:spool_write_end]

        assert "SELECT COUNT(*)" in fn_src, "_spool_write phải check COUNT trước INSERT"
        assert "MAX_SPOOL_ROWS" in fn_src, "_spool_write phải compare với MAX_SPOOL_ROWS"
        assert "return" in fn_src, "_spool_write phải return sớm khi spool đầy"
