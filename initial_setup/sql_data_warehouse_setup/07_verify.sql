/* ============================================================
   07_verify.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   PURPOSE:
     Verify the SQL data warehouse objects required by core_engine.

   EXPECTED:
     - 16 SEN tables: 15 staging TF tables + SEN.ActiveTask
     - 4 DWH tables : Dim_Symbol, Dim_Timeframe, Dim_Date, Fact_OHLCV
     - 1 DWH proc   : DWH.usp_LoadDirect
     - 37 symbols   : seeded by 06_seed_symbols.sql
     - 15 timeframes
   ============================================================ */

USE SEN05_AutoTrading;
GO

SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA IN ('SEN', 'DWH')
ORDER BY TABLE_SCHEMA, TABLE_NAME;

SELECT ROUTINE_SCHEMA, ROUTINE_TYPE, ROUTINE_NAME
FROM INFORMATION_SCHEMA.ROUTINES
WHERE ROUTINE_SCHEMA = 'DWH'
ORDER BY ROUTINE_NAME;

SELECT COUNT(*) AS SymbolCount
FROM DWH.Dim_Symbol;

SELECT SymbolID, Symbol, RefName, AssetType, IsActive
FROM DWH.Dim_Symbol
ORDER BY AssetType, SymbolID;

SELECT COUNT(*) AS TimeframeCount
FROM DWH.Dim_Timeframe;

SELECT TimeframeID, Code, Minutes, SourceTable, Description
FROM DWH.Dim_Timeframe
ORDER BY TimeframeID;

SELECT COUNT(*) AS TotalDays, MIN(FullDate) AS DateStart, MAX(FullDate) AS DateEnd
FROM DWH.Dim_Date;

SELECT COUNT(*) AS StagingTFCount
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'SEN'
  AND TABLE_NAME LIKE 'TF_%';

SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'SEN'
  AND TABLE_NAME = 'ActiveTask';

SELECT OBJECT_SCHEMA_NAME(object_id) AS SchemaName,
       OBJECT_NAME(object_id) AS ObjectName,
       type_desc AS ObjectType
FROM sys.objects
WHERE object_id IN (
    OBJECT_ID('DWH.Dim_Symbol'),
    OBJECT_ID('DWH.Dim_Timeframe'),
    OBJECT_ID('DWH.Dim_Date'),
    OBJECT_ID('DWH.Fact_OHLCV'),
    OBJECT_ID('DWH.usp_LoadDirect'),
    OBJECT_ID('SEN.ActiveTask')
)
ORDER BY SchemaName, ObjectName;

PRINT '=== SETUP COMPLETE - SEN05_AutoTrading is ready for core_engine ===';
GO
