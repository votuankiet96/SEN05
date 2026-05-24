import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "data_provider" / "00_sql"


def _read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def test_symbol_seed_uses_runtime_capitalcom_tickers():
    sql = _read_sql("02_core_tables.sql")

    expected_pairs = {
        2: ("FR40", "CAC40"),
        3: ("DE40", "DAX"),
        4: ("HK50", "HSI"),
        5: ("J225", "NI225"),
        6: ("SP35", "IBEX35"),
        7: ("UK100", "UKX"),
        8: ("US500", "SPX"),
        9: ("US100", "NDX"),
        10: ("US30", "DJI"),
        56: ("GOLD", "XAU"),
    }

    for symbol_id, (symbol, ref_name) in expected_pairs.items():
        assert f"({symbol_id:2d}, '{symbol}'" in sql
        assert f"'{ref_name}'" in sql

    stale_primary_symbols = [
        "CAC40",
        "DAX",
        "HSI",
        "NI225",
        "IBEX35",
        "UKX",
        "SPX",
        "NDX",
        "DJI",
        "XAU",
    ]
    stale_primary_re = re.compile(
        r"\(\s*\d+,\s*'("
        + "|".join(re.escape(symbol) for symbol in stale_primary_symbols)
        + r")'\s*,"
    )
    assert not stale_primary_re.search(sql)


def test_timeframe_seed_is_rerunnable_and_current_lineage():
    sql = _read_sql("02_core_tables.sql")

    assert "Description  NVARCHAR(160)" in sql
    assert "CHARACTER_MAXIMUM_LENGTH < 160" in sql
    assert "MERGE DWH.Dim_Timeframe" in sql
    assert "SELECT target.Code, target.Minutes, target.SourceTable, target.Description" in sql
    assert "10 min - direct TradingView/Capital.com; fallback M5 aggregate" in sql
    assert "6 hour - direct TradingView/Capital.com; fallback H3 aggregate" in sql
    assert "COMPUTED:" not in sql


def test_staging_comments_match_direct_default_runtime():
    sql = _read_sql("03_staging_tables.sql")

    assert "current direct-pull" in sql
    assert "ENABLE_COMPUTED_TIMEFRAMES=1" in sql
    assert "COMPUTED from" not in sql


def test_run_all_includes_runtime_lock_table_and_current_lineage():
    sql = _read_sql("00_run_all.sql")

    assert ':r "06_active_task.sql"' in sql
    assert sql.index(':r "06_active_task.sql"') < sql.index(':r "05_verify.sql"')
    assert "pulls all 15 timeframes directly" in sql
    assert "10 direct timeframes" not in sql
    assert "only reads from MART" not in sql


def test_business_objects_match_runtime_contract():
    sql = _read_sql("04_business_objects.sql")

    assert "all 15 timeframes pulled directly from TradingView/Capital.com" in sql
    assert "fallback/rebuild scenarios when ENABLE_COMPUTED_TIMEFRAMES=1" in sql
    assert "10 timeframes" not in sql
    assert "EXCLUSIVELY" not in sql


def test_business_objects_has_one_safe_aggregate_proc_definition():
    sql = _read_sql("04_business_objects.sql")
    marker = "CREATE OR ALTER PROCEDURE DWH.usp_AggregateFromStaging"

    assert sql.count(marker) == 1
    proc_sql = sql.split(marker, 1)[1]
    assert "MERGE DWH.Fact_OHLCV" in proc_sql
    assert "WHERE BarCount = @ExpectedCount" in proc_sql
    assert "PATCHED OK" not in sql
    assert "patched with" not in sql


def test_verify_sql_checks_runtime_drift_guards():
    sql = _read_sql("05_verify.sql")

    assert "StagingTFCount" in sql
    assert "'FR40','DE40','HK50','J225','SP35','UK100','US500','US100','US30','GOLD'" in sql
    assert "'CAC40','DAX','HSI','NI225','IBEX35','UKX','SPX','NDX','DJI','XAU'" in sql
    assert "Description LIKE '%COMPUTED%'" in sql


def test_standalone_aggregate_patch_sets_database_context():
    sql = _read_sql("patch_usp_AggregateFromStaging_safe.sql")

    assert sql.startswith("-- Standalone maintenance/backport patch.")
    assert "USE SEN05_AutoTrading;" in sql.splitlines()[:5]
