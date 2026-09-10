/* DP Program V3 warehouse contract verification (step 6 of 6).

   This script is read-only. It fails the SQLCMD installation when any
   runtime-required object or reviewed metadata contract is missing.
   Unrelated pre-existing objects are ignored and are not treated as part of
   the V3 contract.
*/

USE SEN05_AutoTrading;
GO

SET NOCOUNT ON;

DECLARE @RequiredTables TABLE (
    QualifiedName SYSNAME NOT NULL PRIMARY KEY
);

INSERT INTO @RequiredTables (QualifiedName)
VALUES
    (N'DWH.Dim_Symbol'),
    (N'DWH.Dim_Timeframe'),
    (N'DWH.Dim_Date'),
    (N'DWH.Fact_OHLCV'),
    (N'SEN.TF_M5'),
    (N'SEN.TF_M10'),
    (N'SEN.TF_M15'),
    (N'SEN.TF_M20'),
    (N'SEN.TF_M30'),
    (N'SEN.TF_M45'),
    (N'SEN.TF_H1'),
    (N'SEN.TF_M90'),
    (N'SEN.TF_H2'),
    (N'SEN.TF_H3'),
    (N'SEN.TF_H4'),
    (N'SEN.TF_H6'),
    (N'SEN.TF_H8'),
    (N'SEN.TF_D1'),
    (N'SEN.TF_W');

IF EXISTS (
    SELECT 1
    FROM @RequiredTables
    WHERE OBJECT_ID(QualifiedName, 'U') IS NULL
)
BEGIN
    SELECT QualifiedName AS MissingTable
    FROM @RequiredTables
    WHERE OBJECT_ID(QualifiedName, 'U') IS NULL;
    THROW 51000, 'DP Program V3 warehouse is missing required tables.', 1;
END;

IF (
    SELECT COUNT_BIG(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'SEN'
      AND TABLE_NAME LIKE 'TF[_]%'
) <> 15
    THROW 51001, 'The SEN schema must contain exactly 15 timeframe staging tables.', 1;

DECLARE @ExpectedTimeframes TABLE (
    TimeframeID TINYINT NOT NULL PRIMARY KEY,
    Code VARCHAR(5) NOT NULL,
    Minutes INT NOT NULL,
    SourceTable VARCHAR(15) NOT NULL
);

INSERT INTO @ExpectedTimeframes (TimeframeID, Code, Minutes, SourceTable)
VALUES
    (1, 'M5', 5, 'TF_M5'),
    (2, 'M10', 10, 'TF_M10'),
    (3, 'M15', 15, 'TF_M15'),
    (4, 'M20', 20, 'TF_M20'),
    (5, 'M30', 30, 'TF_M30'),
    (6, 'M45', 45, 'TF_M45'),
    (7, 'H1', 60, 'TF_H1'),
    (8, 'M90', 90, 'TF_M90'),
    (9, 'H2', 120, 'TF_H2'),
    (10, 'H3', 180, 'TF_H3'),
    (11, 'H4', 240, 'TF_H4'),
    (12, 'H6', 360, 'TF_H6'),
    (13, 'H8', 480, 'TF_H8'),
    (14, 'D1', 1440, 'TF_D1'),
    (15, 'W', 10080, 'TF_W');

IF EXISTS (
    SELECT TimeframeID, Code, Minutes, SourceTable
    FROM @ExpectedTimeframes
    EXCEPT
    SELECT TimeframeID, Code, Minutes, SourceTable
    FROM DWH.Dim_Timeframe
)
OR EXISTS (
    SELECT TimeframeID, Code, Minutes, SourceTable
    FROM DWH.Dim_Timeframe
    EXCEPT
    SELECT TimeframeID, Code, Minutes, SourceTable
    FROM @ExpectedTimeframes
)
    THROW 51002, 'DWH.Dim_Timeframe does not match the reviewed 15-timeframe contract.', 1;

IF (SELECT COUNT_BIG(*) FROM DWH.Dim_Symbol) <> 37
    THROW 51003, 'DWH.Dim_Symbol must contain exactly 37 symbols.', 1;

IF (
    SELECT COUNT_BIG(*)
    FROM DWH.Dim_Date
) <> 10227
OR (SELECT MIN(FullDate) FROM DWH.Dim_Date) <> CONVERT(DATE, '2008-01-01')
OR (SELECT MAX(FullDate) FROM DWH.Dim_Date) <> CONVERT(DATE, '2035-12-31')
    THROW 51004, 'DWH.Dim_Date must cover every day from 2008-01-01 through 2035-12-31.', 1;

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.index_columns AS c1
      ON c1.object_id = i.object_id
     AND c1.index_id = i.index_id
     AND c1.key_ordinal = 1
    JOIN sys.columns AS n1
      ON n1.object_id = c1.object_id
     AND n1.column_id = c1.column_id
    JOIN sys.index_columns AS c2
      ON c2.object_id = i.object_id
     AND c2.index_id = i.index_id
     AND c2.key_ordinal = 2
    JOIN sys.columns AS n2
      ON n2.object_id = c2.object_id
     AND n2.column_id = c2.column_id
    JOIN sys.index_columns AS c3
      ON c3.object_id = i.object_id
     AND c3.index_id = i.index_id
     AND c3.key_ordinal = 3
    JOIN sys.columns AS n3
      ON n3.object_id = c3.object_id
     AND n3.column_id = c3.column_id
    WHERE i.object_id = OBJECT_ID('DWH.Fact_OHLCV')
      AND i.is_unique = 1
      AND n1.name = 'SymbolID'
      AND n2.name = 'TimeframeID'
      AND n3.name = 'BarTime'
)
    THROW 51005, 'DWH.Fact_OHLCV is missing its unique business key.', 1;

IF OBJECT_ID('DWH.usp_LoadDirect', 'P') IS NULL
    THROW 51006, 'DWH.usp_LoadDirect is missing.', 1;

DECLARE @ContractVersion NVARCHAR(50);
SELECT @ContractVersion = CAST(value AS NVARCHAR(50))
FROM sys.extended_properties
WHERE major_id = OBJECT_ID('DWH.usp_LoadDirect')
  AND minor_id = 0
  AND class = 1
  AND name = 'DPContractVersion';

IF ISNULL(@ContractVersion, N'') <> N'4'
    THROW 51007, 'DWH.usp_LoadDirect must expose DPContractVersion 4.', 1;

DECLARE @LoaderDefinition NVARCHAR(MAX) =
    OBJECT_DEFINITION(OBJECT_ID('DWH.usp_LoadDirect'));

IF @LoaderDefinition NOT LIKE N'%INNER JOIN DWH.Dim_Date%'
OR @LoaderDefinition NOT LIKE N'%WITH (UPDLOCK, HOLDLOCK)%'
OR @LoaderDefinition NOT LIKE N'%OPTION (RECOMPILE, MAXDOP 1)%'
OR @LoaderDefinition NOT LIKE N'%@UpdatedRows AS UpdatedRows%'
OR @LoaderDefinition NOT LIKE N'%@InsertedRows AS InsertedRows%'
OR @LoaderDefinition NOT LIKE N'%AS AffectedRows%'
    THROW 51008, 'DWH.usp_LoadDirect body does not match the required v4 safety contract.', 1;

SELECT
    N'PASS' AS Verification,
    (SELECT COUNT_BIG(*) FROM DWH.Dim_Symbol) AS SymbolCount,
    (SELECT COUNT_BIG(*) FROM DWH.Dim_Timeframe) AS TimeframeCount,
    (SELECT COUNT_BIG(*) FROM DWH.Dim_Date) AS DateCount,
    (SELECT COUNT_BIG(*) FROM INFORMATION_SCHEMA.TABLES
     WHERE TABLE_SCHEMA = 'SEN' AND TABLE_NAME LIKE 'TF[_]%') AS StagingTableCount,
    @ContractVersion AS LoaderContractVersion;

PRINT 'DP Program V3 SQL warehouse verification passed.';
GO
