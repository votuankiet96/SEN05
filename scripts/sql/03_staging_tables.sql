/* ============================================================
   03_staging_tables.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 3 of 5 — Create all 15 SEN staging tables (one per timeframe).
     The Python pipeline inserts raw OHLCV rows here before ETL processing.

     PREREQUISITE: 01_setup_database.sql must have been run first.

   CONTAINS:
     Block 4-01 — SEN.TF_M5   (5 min  — direct from TradingView/Capital.com)
     Block 4-02 — SEN.TF_M10  (10 min — direct from TradingView/Capital.com)
     Block 4-03 — SEN.TF_M15  (15 min — direct from TradingView/Capital.com)
     Block 4-04 — SEN.TF_M20  (20 min — direct from TradingView/Capital.com)
     Block 4-05 — SEN.TF_M30  (30 min — direct from TradingView/Capital.com)
     Block 4-06 — SEN.TF_M45  (45 min — direct from TradingView/Capital.com)
     Block 4-07 — SEN.TF_H1   (1 hour — direct from TradingView/Capital.com)
     Block 4-08 — SEN.TF_M90  (90 min — direct from TradingView/Capital.com)
     Block 4-09 — SEN.TF_H2   (2 hour — direct from TradingView/Capital.com)
     Block 4-10 — SEN.TF_H3   (3 hour — direct from TradingView/Capital.com)
     Block 4-11 — SEN.TF_H4   (4 hour — direct from TradingView/Capital.com)
     Block 4-12 — SEN.TF_H6   (6 hour — direct from TradingView/Capital.com)
     Block 4-13 — SEN.TF_H8   (8 hour — direct from TradingView/Capital.com)
     Block 4-14 — SEN.TF_D1   (1 day  — direct from TradingView/Capital.com)
     Block 4-15 — SEN.TF_W    (1 week — direct from TradingView/Capital.com)

   STAGING TABLE DESIGN NOTE:
     All 15 SEN.TF_* tables exist and are used by the direct-pull
     TradingView/Capital.com runtime — one staging table per timeframe.
     Keeping one table per timeframe allows inspection and re-processing with
     the same ETL path.

   COLUMN NOTES (all tables share the same structure):
     RawID       — surrogate BIGINT PK; auto-increment; never exposed externally
     SymbolID    — FK to DWH.Dim_Symbol (not enforced here for insert speed; ETL validates)
     BarTime     — candle open timestamp in UTC; DATETIME2(0) = second precision
     [Open] / High / Low / [Close] — DECIMAL(18,8): 8 decimal places
     Volume      — nullable because some instruments (e.g. spot FOREX) have no true volume
     ReceivedAt  — UTC wall-clock time when the row was inserted; useful for latency monitoring
     IsProcessed — 0 = just inserted, not yet moved to Fact_OHLCV
                   1 = Python confirms bar is complete and ready for ETL processing
                   ETL procedures filter WHERE IsProcessed = 1 to skip in-progress/partial bars

   UNIQUE CONSTRAINT UQ_TF_*:
     Prevents the same (SymbolID, BarTime) from being inserted twice.
     Acts as the first line of defence against duplicate data from TradingView/Capital.com retransmissions.

   NONCLUSTERED INDEX IX_TF_*_SBT (SymbolID, BarTime) INCLUDE (OHLCV columns):
     - Covers the exact columns read by ETL procedures: filter on SymbolID+BarTime, return OHLCV.
     - INCLUDE avoids a key lookup back to the clustered index (covering index pattern).

   SAFE TO RE-RUN:
     All tables guarded by IF OBJECT_ID IS NULL.

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql
     03_staging_tables.sql     ← this file
     04_business_objects.sql
     05_active_task.sql
     06_seed_symbols.sql
     07_verify.sql
   ============================================================ */

USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 4: STAGING TABLES  (SEN schema)
   ============================================================ */

-- ---- 4-01. SEN.TF_M5  (5-minute — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_M5', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M5 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),  -- auto-increment surrogate PK; BIGINT handles years of high-frequency data
        SymbolID    INT           NOT NULL,                -- references DWH.Dim_Symbol.SymbolID
        BarTime     DATETIME2(0)  NOT NULL,                -- candle open time UTC; (0) = no sub-second precision needed
        [Open]   DECIMAL(18,8) NOT NULL,                -- 8 decimal places for pip-level currency precision
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,                   -- NULL for instruments without volume (e.g. spot FOREX)
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(), -- UTC insert timestamp; useful for pipeline latency monitoring
        IsProcessed BIT           NOT NULL DEFAULT 0,     -- 0 = raw/unconfirmed; 1 = bar is complete and ready for ETL
        CONSTRAINT PK_TF_M5 PRIMARY KEY CLUSTERED (RawID),       -- clustered on surrogate key for fast sequential inserts
        CONSTRAINT UQ_TF_M5 UNIQUE (SymbolID, BarTime)           -- prevents duplicate candle for same symbol+time
    );
    -- Covering index: ETL reads (SymbolID, BarTime) and needs OHLCV columns → no key lookup needed
    CREATE NONCLUSTERED INDEX IX_TF_M5_SBT
        ON SEN.TF_M5 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M5 created.';
END
GO

-- ---- 4-02. SEN.TF_M10  (10-minute — pulled directly by default) ----
IF OBJECT_ID('SEN.TF_M10', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M10 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M10 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M10 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M10_SBT
        ON SEN.TF_M10 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M10 created.';
END
GO

-- ---- 4-03. SEN.TF_M15  (15-minute — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_M15', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M15 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M15 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M15 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M15_SBT
        ON SEN.TF_M15 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M15 created.';
END
GO

-- ---- 4-04. SEN.TF_M20  (20-minute — pulled directly by default) ----
IF OBJECT_ID('SEN.TF_M20', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M20 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M20 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M20 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M20_SBT
        ON SEN.TF_M20 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M20 created.';
END
GO

-- ---- 4-05. SEN.TF_M30  (30-minute — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_M30', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M30 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M30 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M30 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M30_SBT
        ON SEN.TF_M30 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M30 created.';
END
GO

-- ---- 4-06. SEN.TF_M45  (45-minute — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_M45', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M45 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M45 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M45 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M45_SBT
        ON SEN.TF_M45 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M45 created.';
END
GO

-- ---- 4-07. SEN.TF_H1  (1-hour — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_H1', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H1 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H1 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H1 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H1_SBT
        ON SEN.TF_H1 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H1 created.';
END
GO

-- ---- 4-08. SEN.TF_M90  (90-minute — pulled directly by default) ----
IF OBJECT_ID('SEN.TF_M90', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_M90 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_M90 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_M90 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_M90_SBT
        ON SEN.TF_M90 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_M90 created.';
END
GO

-- ---- 4-09. SEN.TF_H2  (2-hour — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_H2', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H2 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H2 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H2 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H2_SBT
        ON SEN.TF_H2 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H2 created.';
END
GO

-- ---- 4-10. SEN.TF_H3  (3-hour — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_H3', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H3 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H3 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H3 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H3_SBT
        ON SEN.TF_H3 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H3 created.';
END
GO

-- ---- 4-11. SEN.TF_H4  (4-hour — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_H4', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H4 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H4 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H4 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H4_SBT
        ON SEN.TF_H4 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H4 created.';
END
GO

-- ---- 4-12. SEN.TF_H6  (6-hour — pulled directly by default) ----
IF OBJECT_ID('SEN.TF_H6', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H6 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H6 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H6 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H6_SBT
        ON SEN.TF_H6 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H6 created.';
END
GO

-- ---- 4-13. SEN.TF_H8  (8-hour — pulled directly by default) ----
IF OBJECT_ID('SEN.TF_H8', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_H8 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_H8 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_H8 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_H8_SBT
        ON SEN.TF_H8 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_H8 created.';
END
GO

-- ---- 4-14. SEN.TF_D1  (1-day — pulled directly from TradingView/Capital.com) ----
IF OBJECT_ID('SEN.TF_D1', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_D1 (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_D1 PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_D1 UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_D1_SBT
        ON SEN.TF_D1 (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_D1 created.';
END
GO

-- ---- 4-15. SEN.TF_W  (1-week — pulled directly from TradingView/Capital.com) ----
-- BarTime for weekly candles is anchored to Monday 00:00 UTC (ISO 8601 convention).
IF OBJECT_ID('SEN.TF_W', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.TF_W (
        RawID       BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID    INT           NOT NULL,
        BarTime     DATETIME2(0)  NOT NULL,
        [Open]   DECIMAL(18,8) NOT NULL,
        High   DECIMAL(18,8) NOT NULL,
        Low    DECIMAL(18,8) NOT NULL,
        [Close]  DECIMAL(18,8) NOT NULL,
        Volume      DECIMAL(20,4) NULL,
        ReceivedAt  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        IsProcessed BIT           NOT NULL DEFAULT 0,
        CONSTRAINT PK_TF_W PRIMARY KEY CLUSTERED (RawID),
        CONSTRAINT UQ_TF_W UNIQUE (SymbolID, BarTime)
    );
    CREATE NONCLUSTERED INDEX IX_TF_W_SBT
        ON SEN.TF_W (SymbolID, BarTime)
        INCLUDE ([Open], High, Low, [Close], Volume);
    PRINT 'Table SEN.TF_W created.';
END
GO
