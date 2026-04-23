/* ============================================================
   UPDATED NOTE:
     For a full runtime-ready installation, expect `SEN.ActiveTask` to exist.
     That changes Query 1 from 19 rows to 20 rows in total.
   ============================================================ */

/* ============================================================
   05_verify.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 5 of 5 — Verification queries to confirm the full setup completed
     correctly. Run this file last, after all other setup files.

     PREREQUISITE: 01 through 04 must have been run successfully.

   WHAT TO EXPECT FROM EACH QUERY:
     Query 1 — All tables in SEN/DWH/MART schemas
               Expected: 16 SEN + 4 DWH + 0 MART = 20 rows
                         (dbo.Symbol is in dbo schema and will not appear here)

     Query 2 — All procedures and views in DWH and MART schemas
               Expected: 4 objects
                 DWH  | PROCEDURE | usp_AggregateFromStaging
                 DWH  | PROCEDURE | usp_LoadDirect
                 MART | PROCEDURE | usp_GetLatestCandles
                 MART | VIEW      | v_OHLCV

     Query 3 — Master symbol list
               Expected: 37 rows (9 Indices + 26 FOREX + 1 Metal + 1 Crypto)

     Query 4 — Timeframe dimension seed data
               Expected: 15 rows in TimeframeID order (1–15)

     Query 5 — Calendar dimension range and row count
               Expected: 10,227 rows, 2008-01-01 to 2035-12-31

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql
     03_staging_tables.sql
     04_business_objects.sql
     05_verify.sql     ← this file
   ============================================================ */

USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 10: VERIFICATION QUERIES
   ============================================================ */

-- Query 1: List all tables in SEN, DWH, MART schemas (expect 16 SEN + 4 DWH + 0 MART = 20 tables)
SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA IN ('SEN','DWH','MART')
ORDER BY TABLE_SCHEMA, TABLE_NAME;

-- Query 2: List all procedures and views in DWH and MART schemas (expect 4 objects)
SELECT ROUTINE_SCHEMA, ROUTINE_TYPE, ROUTINE_NAME
FROM INFORMATION_SCHEMA.ROUTINES
WHERE ROUTINE_SCHEMA IN ('DWH','MART')
ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME;

-- Query 3: Confirm all 37 symbols are present (expect 37 rows)
SELECT Id, Symbol, RefName, Type FROM dbo.Symbol ORDER BY Type, Id;

-- Query 4: Confirm all 15 timeframes are seeded correctly (expect 15 rows in ID order)
SELECT TimeframeID, Code, Minutes, SourceTable, Description
FROM DWH.Dim_Timeframe ORDER BY TimeframeID;

-- Query 5: Confirm Dim_Date range and row count (expect 10,227 rows for 2008-01-01 to 2035-12-31)
SELECT COUNT(*) AS TotalDays, MIN(FullDate) AS DateStart, MAX(FullDate) AS DateEnd
FROM DWH.Dim_Date;

-- Query 6: Confirm the operational coordination table exists (expect 1 row)
SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'SEN'
  AND TABLE_NAME = 'ActiveTask';

PRINT '=== SETUP COMPLETE — SEN05_AutoTrading is ready ===';
GO
