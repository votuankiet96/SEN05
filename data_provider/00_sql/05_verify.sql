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

     Query 6 — Operational coordination table
               Expected: SEN.ActiveTask exists

     Query 7 — Staging table count
               Expected: 15 SEN.TF_* tables

     Query 8 — Runtime Capital.com symbols
               Expected: 10 rows for renamed index/metal symbols

     Query 9 — Legacy names are no longer primary symbols
               Expected: 0 rows

     Query 10 — Former computed TF descriptions
               Expected: 0 rows containing the old COMPUTED wording

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

-- Query 7: Confirm all 15 timeframe staging tables exist (expect 15)
SELECT COUNT(*) AS StagingTFCount
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'SEN'
  AND TABLE_NAME LIKE 'TF_%';

-- Query 8: Confirm runtime Capital.com symbols are primary symbols (expect 10 rows)
SELECT SymbolID, Symbol, RefName, AssetType
FROM DWH.Dim_Symbol
WHERE Symbol IN ('FR40','DE40','HK50','J225','SP35','UK100','US500','US100','US30','GOLD')
ORDER BY SymbolID;

-- Query 9: Confirm legacy names are not primary symbols anymore (expect 0 rows)
SELECT SymbolID, Symbol, RefName, AssetType
FROM DWH.Dim_Symbol
WHERE Symbol IN ('CAC40','DAX','HSI','NI225','IBEX35','UKX','SPX','NDX','DJI','XAU')
ORDER BY SymbolID;

-- Query 10: Confirm former computed TF descriptions no longer use old wording (expect 0 rows)
SELECT Code, Description
FROM DWH.Dim_Timeframe
WHERE Code IN ('M10','M20','M90','H6','H8')
  AND Description LIKE '%COMPUTED%';

PRINT '=== SETUP COMPLETE — SEN05_AutoTrading is ready ===';
GO
