/* ============================================================
   02_core_tables.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 2 of 6 — Create all core/dimension/fact tables and seed reference data.

     PREREQUISITE: 01_setup_database.sql must have been run first.

   WHAT THIS FILE CREATES (in plain English):

     1. THREE "LOOKUP" TABLES (dimensions):
        These are small reference tables that translate numeric IDs
        into human-readable names. Every candle in the main table stores
        numbers like SymbolID=33 and TimeframeID=7. These lookup tables
        tell you that 33 = EURUSD and 7 = H1.

        a) Dim_Symbol    — "Which instrument?" (seeded by 05_seed_symbols.sql)
        b) Dim_Timeframe — "Which timeframe?"  (15 rows: M5, H1, D1, etc.)
        c) Dim_Date      — "What day is it?"   (10,227 rows: every day 2008–2035)

     2. THE MAIN DATA TABLE (fact):
        d) Fact_OHLCV    — One row per candle. Millions of rows.
                           This is where ALL price data lives.

   NOTE: DWH.Dim_Symbol is created by this file and seeded by
         05_seed_symbols.sql from the runtime instrument list.

   SAFE TO RE-RUN:
     Tables guarded by IF OBJECT_ID IS NULL (won't recreate if exists).
     Timeframe and Date seeds use MERGE (idempotent on re-run).

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql     ← YOU ARE HERE
     03_staging_tables.sql
     04_business_objects.sql
     05_seed_symbols.sql
     06_verify.sql
   ============================================================ */

USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 3: DIMENSION TABLES (Lookup Tables)

   WHAT IS A "DIMENSION TABLE"?
     In data warehousing, a "dimension" is a small table that provides
     context for the numbers stored in the main data table (the "fact" table).

     For example, the Fact_OHLCV table stores SymbolID = 33.
     That number alone is meaningless. But if you look up 33 in Dim_Symbol,
     you find "EURUSD, FOREX pair." Now the data makes sense.

     Think of dimensions like the labels on the axes of a chart:
       - Dim_Symbol    = the Y-axis label (what instrument)
       - Dim_Timeframe = the zoom level (5-min, 1-hour, daily)
       - Dim_Date      = the X-axis label (which day, month, year)
   ============================================================ */

-- -------------------------------------------------------
-- 3A. DWH.Dim_Symbol — "Which instrument is this candle for?"
--
--     One row per tradable instrument (37 total).
--     Examples: EURUSD, Gold (GOLD), S&P 500 CFD (US500), Bitcoin (BTCUSD)
--
--     KEY COLUMNS:
--       SymbolID  — A stable number (e.g. 33) used in every candle row.
--                   This number NEVER changes, even if the name changes.
--       Symbol    — The runtime TradingView/Capital.com ticker used by Python.
--       RefName   — Legacy or common reference ticker for the same instrument.
--                   e.g. RefName stores "CAC40" while runtime Symbol is "FR40".
--       AssetType — Category: "FOREX", "Indice", "Metal", or "Crypto".
--       IsActive  — 1 = currently traded; 0 = retired/removed.
-- -------------------------------------------------------
IF OBJECT_ID('DWH.Dim_Symbol', 'U') IS NULL  -- 'U' = user table; only create if it doesn't exist
BEGIN
    CREATE TABLE DWH.Dim_Symbol (
        SymbolID    INT          NOT NULL,   -- stable numeric PK from config.py SYMBOLS list
        Symbol      NVARCHAR(20) NOT NULL,   -- runtime TradingView/Capital.com ticker used by Python
        RefName     NVARCHAR(20) NULL,       -- legacy / alternative name (nullable — not all symbols have one)
        AssetType   NVARCHAR(20) NOT NULL,   -- category: FOREX | Indice | Metal | Crypto
        IsActive    BIT          NOT NULL DEFAULT 1,              -- 1 = currently traded; 0 = retired symbol
        CreatedAt   DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME(), -- UTC timestamp of row insert
        CONSTRAINT PK_Dim_Symbol PRIMARY KEY CLUSTERED (SymbolID),  -- clustered on SymbolID for fast FK lookups from fact table
        CONSTRAINT UQ_Dim_Symbol_Symbol UNIQUE (Symbol)             -- enforce one row per ticker string
    );
    PRINT 'Table DWH.Dim_Symbol created.';
END
GO

-- -------------------------------------------------------
-- 3B. DWH.Dim_Timeframe
--     One row per timeframe supported by the pipeline.
--     15 timeframes total. Current runtime pulls all 15 directly from
--     TradingView/Capital.com WebSocket:
--
--       M5, M10, M15, M20, M30, M45, H1, M90,
--       H2, H3, H4, H6, H8, D1, W
--
--     Minutes column records the candle duration in minutes.
--     SourceTable is the short staging table name (without schema prefix).
-- -------------------------------------------------------
IF OBJECT_ID('DWH.Dim_Timeframe', 'U') IS NULL
BEGIN
    CREATE TABLE DWH.Dim_Timeframe (
        TimeframeID  TINYINT      NOT NULL,  -- 1-byte compact PK (max 15 rows — TINYINT is sufficient)
        Code         VARCHAR(5)   NOT NULL,  -- short code used in procedure calls and Python config, e.g. 'M5', 'H4', 'W'
        Minutes      INT          NOT NULL,  -- candle duration in minutes
        SourceTable  VARCHAR(15)  NOT NULL,  -- short staging table name (schema not included), e.g. 'TF_M5'
        Description  NVARCHAR(160) NULL,     -- human-readable note: source type and computation method
        CONSTRAINT PK_Dim_Timeframe PRIMARY KEY CLUSTERED (TimeframeID),
        CONSTRAINT UQ_Dim_Timeframe_Code UNIQUE (Code)  -- one row per timeframe code
    );

    -- Seed all 15 timeframes in one INSERT.
    -- TimeframeIDs are fixed and must never change — they are stored in Fact_OHLCV rows.
    INSERT INTO DWH.Dim_Timeframe
        (TimeframeID, Code, Minutes, SourceTable, Description)
    VALUES
        ( 1, 'M5',  5,     'TF_M5',  '5 min - direct TradingView/Capital.com'),
        ( 2, 'M10', 10,    'TF_M10', '10 min - direct TradingView/Capital.com'),
        ( 3, 'M15', 15,    'TF_M15', '15 min - direct TradingView/Capital.com'),
        ( 4, 'M20', 20,    'TF_M20', '20 min - direct TradingView/Capital.com'),
        ( 5, 'M30', 30,    'TF_M30', '30 min - direct TradingView/Capital.com'),
        ( 6, 'M45', 45,    'TF_M45', '45 min - direct TradingView/Capital.com'),
        ( 7, 'H1',  60,    'TF_H1',  '1 hour - direct TradingView/Capital.com'),
        ( 8, 'M90', 90,    'TF_M90', '90 min - direct TradingView/Capital.com'),
        ( 9, 'H2',  120,   'TF_H2',  '2 hour - direct TradingView/Capital.com'),
        (10, 'H3',  180,   'TF_H3',  '3 hour - direct TradingView/Capital.com'),
        (11, 'H4',  240,   'TF_H4',  '4 hour - direct TradingView/Capital.com'),
        (12, 'H6',  360,   'TF_H6',  '6 hour - direct TradingView/Capital.com'),
        (13, 'H8',  480,   'TF_H8',  '8 hour - direct TradingView/Capital.com'),
        (14, 'D1',  1440,  'TF_D1',  '1 day - direct TradingView/Capital.com'),
        (15, 'W',   10080, 'TF_W',   '1 week - direct TradingView/Capital.com');

    PRINT 'Table DWH.Dim_Timeframe created (15 timeframes).';
END
GO

-- Keep existing installations aligned with the current runtime metadata.
-- This is safe to re-run and only widens the description column when needed.
IF EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'DWH'
      AND TABLE_NAME = 'Dim_Timeframe'
      AND COLUMN_NAME = 'Description'
      AND CHARACTER_MAXIMUM_LENGTH < 160
)
BEGIN
    ALTER TABLE DWH.Dim_Timeframe
        ALTER COLUMN Description NVARCHAR(160) NULL;
    PRINT 'Column DWH.Dim_Timeframe.Description widened to NVARCHAR(160).';
END
GO

-- Upsert all 15 timeframes so fresh installs and reruns share the same metadata.
-- TimeframeIDs are fixed and must never change because Fact_OHLCV stores them.
MERGE DWH.Dim_Timeframe AS target
USING (VALUES
    ( 1, 'M5',  5,     'TF_M5',  '5 min - direct TradingView/Capital.com'),
    ( 2, 'M10', 10,    'TF_M10', '10 min - direct TradingView/Capital.com'),
    ( 3, 'M15', 15,    'TF_M15', '15 min - direct TradingView/Capital.com'),
    ( 4, 'M20', 20,    'TF_M20', '20 min - direct TradingView/Capital.com'),
    ( 5, 'M30', 30,    'TF_M30', '30 min - direct TradingView/Capital.com'),
    ( 6, 'M45', 45,    'TF_M45', '45 min - direct TradingView/Capital.com'),
    ( 7, 'H1',  60,    'TF_H1',  '1 hour - direct TradingView/Capital.com'),
    ( 8, 'M90', 90,    'TF_M90', '90 min - direct TradingView/Capital.com'),
    ( 9, 'H2',  120,   'TF_H2',  '2 hour - direct TradingView/Capital.com'),
    (10, 'H3',  180,   'TF_H3',  '3 hour - direct TradingView/Capital.com'),
    (11, 'H4',  240,   'TF_H4',  '4 hour - direct TradingView/Capital.com'),
    (12, 'H6',  360,   'TF_H6',  '6 hour - direct TradingView/Capital.com'),
    (13, 'H8',  480,   'TF_H8',  '8 hour - direct TradingView/Capital.com'),
    (14, 'D1',  1440,  'TF_D1',  '1 day - direct TradingView/Capital.com'),
    (15, 'W',   10080, 'TF_W',   '1 week - direct TradingView/Capital.com')
) AS src (TimeframeID, Code, Minutes, SourceTable, Description)
ON target.TimeframeID = src.TimeframeID
WHEN NOT MATCHED THEN
    INSERT (TimeframeID, Code, Minutes, SourceTable, Description)
    VALUES (src.TimeframeID, src.Code, src.Minutes, src.SourceTable, src.Description)
WHEN MATCHED AND EXISTS (
    SELECT target.Code, target.Minutes, target.SourceTable, target.Description
    EXCEPT
    SELECT src.Code, src.Minutes, src.SourceTable, src.Description
) THEN
    UPDATE SET
        Code = src.Code,
        Minutes = src.Minutes,
        SourceTable = src.SourceTable,
        Description = src.Description;

PRINT 'DWH.Dim_Timeframe metadata merged (15 timeframes).';
GO

-- -------------------------------------------------------
-- 3C. DWH.Dim_Date
--     Pre-built calendar table covering 2008-01-01 to 2035-12-31.
--
--     WHY a calendar table instead of computing date parts on the fly?
--       - Avoids repeated function calls in analytical queries.
--       - Allows adding business-day / holiday flags without touching fact data.
--       - DateKey (INT YYYYMMDD) is a compact, range-sortable FK in Fact_OHLCV.
--
--     WHY start from 2008?
--       - FOREX weekly candles (W) are anchored to Monday midnight.
--         If the earliest data is 2010-01-04 (Monday), the week bucket
--         could theoretically floor to 2009-12-28. Starting from 2008
--         gives ample buffer for any weekly boundary edge cases.
--
--     WHY DATEPART(ISO_WEEK) and not DATEPART(WEEK)?
--       - ISO 8601 week starts on Monday, which matches FOREX market structure.
--       - DATEPART(WEEK) uses a US calendar (week starts Sunday) and is
--         affected by the server's SET DATEFIRST setting, making it unreliable.
-- -------------------------------------------------------
IF OBJECT_ID('DWH.Dim_Date', 'U') IS NULL
BEGIN
    CREATE TABLE DWH.Dim_Date (
        DateKey     INT          NOT NULL,   -- YYYYMMDD integer — used as FK in Fact_OHLCV; sortable and human-readable
        FullDate    DATE         NOT NULL,   -- actual DATE value for date arithmetic
        Year        SMALLINT     NOT NULL,   -- calendar year, e.g. 2024
        Quarter     TINYINT      NOT NULL,   -- 1–4
        Month       TINYINT      NOT NULL,   -- 1–12
        MonthName   NVARCHAR(10) NOT NULL,   -- e.g. 'January'
        Week        TINYINT      NOT NULL,   -- ISO 8601 week number (1–53); week starts Monday
        DayOfWeek   TINYINT      NOT NULL,   -- SQL Server default: 1=Sunday ... 7=Saturday
        DayName     NVARCHAR(10) NOT NULL,   -- e.g. 'Monday'
        IsWeekend   BIT          NOT NULL,   -- 1 if Saturday or Sunday, else 0
        IsHoliday   BIT          NOT NULL DEFAULT 0,  -- reserved for future holiday marking; default 0 (not a holiday)
        CONSTRAINT PK_Dim_Date PRIMARY KEY CLUSTERED (DateKey)  -- clustered on DateKey for fast range scans
    );

    -- Loop through every calendar day from 2008-01-01 to 2035-12-31
    -- and insert one row per day. This runs once at setup time only.
    DECLARE @d DATE = '2008-01-01';  -- loop start date
    DECLARE @e DATE = '2035-12-31';  -- loop end date
    WHILE @d <= @e
    BEGIN
        INSERT INTO DWH.Dim_Date
            (DateKey, FullDate, Year, Quarter, Month,
             MonthName, Week, DayOfWeek, DayName, IsWeekend, IsHoliday)
        VALUES (
            -- CONVERT(VARCHAR, @d, 112) formats the date as 'YYYYMMDD' string; CONVERT(INT,...) turns it into a sortable integer key
            CONVERT(INT, CONVERT(VARCHAR, @d, 112)), @d,
            YEAR(@d),                                -- extract 4-digit year
            DATEPART(QUARTER,  @d),                  -- 1–4
            MONTH(@d),                               -- 1–12
            DATENAME(MONTH,    @d),                  -- full month name string
            DATEPART(ISO_WEEK, @d),                  -- ISO 8601 week number (Monday-anchored); fixes V3 bug where DATEPART(WEEK) used US calendar
            DATEPART(WEEKDAY,  @d),                  -- day-of-week integer (1=Sun by default SQL Server setting)
            DATENAME(WEEKDAY,  @d),                  -- day name string, e.g. 'Tuesday'
            -- IsWeekend: use DATENAME instead of DATEPART(WEEKDAY) to be independent of SET DATEFIRST
            CASE WHEN DATENAME(WEEKDAY, @d) IN ('Saturday', 'Sunday') THEN 1 ELSE 0 END,
            0   -- IsHoliday defaults to 0; update manually for specific market holidays if needed
        );
        -- Advance to the next calendar day
        SET @d = DATEADD(DAY, 1, @d);
    END
    PRINT 'Table DWH.Dim_Date created (2008-01-01 to 2035-12-31).';
END
GO


/* ============================================================
   BLOCK 5: FACT TABLE  —  DWH.Fact_OHLCV

   This is the central table of the data warehouse.
   One row = one fully processed OHLCV candle for a specific symbol and timeframe.
   ALL 15 timeframes converge here. Every timeframe is pulled directly from
   TradingView/Capital.com.

   COLUMN NOTES:
     FactID      — surrogate BIGINT PK; never reused or exposed externally
     SymbolID    — FK to DWH.Dim_Symbol
     TimeframeID — FK to DWH.Dim_Timeframe (1-byte TINYINT — there are only 15 TFs)
     DateKey     — INT YYYYMMDD, FK to DWH.Dim_Date; enables fast calendar-based filtering
     BarTime     — candle open time in UTC; DATETIME2(0) = second precision
     [Open] / High / Low / [Close] — DECIMAL(18,8) for pip-level accuracy
     Volume      — nullable (FOREX spot has no centralised volume)
     TickCount   — always 1 (one bar = one raw candle from TradingView/Capital.com)
     CreatedAt   — UTC timestamp when the row was written to Fact_OHLCV

   UNIQUE CONSTRAINT UQ_Fact_OHLCV (SymbolID, TimeframeID, BarTime):
     The business key — prevents the same candle from being inserted twice.
     Used by NOT EXISTS guards in ETL procedures to make inserts idempotent.

   FOREIGN KEYS:
     Enforced at DB level for data integrity. ETL procs guarantee FK validity
     before inserting, so FK checks are mostly a safety net.

   INDEX STRATEGY:
     IX_Fact_Sym_TF_Time  (SymbolID, TimeframeID, BarTime DESC) INCLUDE(OHLCV):
       - Covers the most common query pattern: "give me the last N candles for symbol X, TF Y"
       - DESC on BarTime because ORDER BY BarTime DESC is the standard access pattern
       - INCLUDE avoids key lookups for all price columns

     IX_Fact_DateKey (DateKey, TimeframeID, SymbolID):
       - Covers date-range analytical queries: "all candles for a date range across all symbols"
       - No INCLUDE needed here because such queries typically use the clustered index for prices
   ============================================================ */

IF OBJECT_ID('DWH.Fact_OHLCV', 'U') IS NULL
BEGIN
    CREATE TABLE DWH.Fact_OHLCV (
        FactID      BIGINT        NOT NULL IDENTITY(1,1),   -- auto-increment surrogate PK
        SymbolID    INT           NOT NULL,                  -- which instrument
        TimeframeID TINYINT       NOT NULL,                  -- which timeframe (1–15)
        DateKey     INT           NOT NULL,                  -- YYYYMMDD integer; FK to Dim_Date for calendar joins
        BarTime     DATETIME2(0)  NOT NULL,                  -- candle open time (UTC); second precision is sufficient
        [Open]   DECIMAL(18,8) NOT NULL,                  -- first tick price of the candle
        High   DECIMAL(18,8) NOT NULL,                  -- highest tick during the candle period
        Low    DECIMAL(18,8) NOT NULL,                  -- lowest tick during the candle period
        [Close]  DECIMAL(18,8) NOT NULL,                  -- last tick price of the candle
        Volume      DECIMAL(20,4) NULL,                     -- total traded volume; NULL for instruments without volume
        TickCount   INT           NULL,                     -- always 1 (one staging row = one raw direct-pull candle)
        CreatedAt   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(), -- UTC time row was written to fact table

        CONSTRAINT PK_Fact_OHLCV PRIMARY KEY CLUSTERED (FactID),
        -- Business uniqueness: one candle per symbol+timeframe+timestamp combination.
        -- ETL procedures use NOT EXISTS against this constraint to avoid duplicate inserts.
        CONSTRAINT UQ_Fact_OHLCV UNIQUE (SymbolID, TimeframeID, BarTime),
        -- Referential integrity: all dimension keys must exist before a fact row is inserted.
        CONSTRAINT FK_Fact_Symbol
            FOREIGN KEY (SymbolID)    REFERENCES DWH.Dim_Symbol    (SymbolID),
        CONSTRAINT FK_Fact_Timeframe
            FOREIGN KEY (TimeframeID) REFERENCES DWH.Dim_Timeframe (TimeframeID),
        CONSTRAINT FK_Fact_Date
            FOREIGN KEY (DateKey)     REFERENCES DWH.Dim_Date      (DateKey)
    );

    -- Primary read index: covers "latest N candles for symbol X, TF Y" queries.
    -- BarTime DESC matches the ORDER BY used by usp_GetLatestCandles and the trading engine.
    -- INCLUDE avoids key lookups when fetching OHLCV price columns.
    CREATE NONCLUSTERED INDEX IX_Fact_Sym_TF_Time
        ON DWH.Fact_OHLCV (SymbolID, TimeframeID, BarTime DESC)
        INCLUDE ([Open], High, Low, [Close], Volume);

    -- Secondary index for date-range analytical queries across symbols and timeframes.
    CREATE NONCLUSTERED INDEX IX_Fact_DateKey
        ON DWH.Fact_OHLCV (DateKey, TimeframeID, SymbolID);

    PRINT 'Table DWH.Fact_OHLCV created.';
END
GO

-- Durable V3 bootstrap completion. Presence of a pair means its configured
-- full-history request committed successfully; absence means bootstrap is
-- still pending. The row is committed atomically with the corresponding Fact
-- load, so process restarts can safely retry without losing state.
IF OBJECT_ID('SEN.DP_BackfillState', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.DP_BackfillState (
        SymbolID             INT          NOT NULL,
        TimeframeID          TINYINT      NOT NULL,
        BootstrapCompletedAt DATETIME2(0) NOT NULL,
        CONSTRAINT PK_DP_BackfillState
            PRIMARY KEY CLUSTERED (SymbolID, TimeframeID),
        CONSTRAINT FK_DP_BackfillState_Symbol
            FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol (SymbolID),
        CONSTRAINT FK_DP_BackfillState_Timeframe
            FOREIGN KEY (TimeframeID) REFERENCES DWH.Dim_Timeframe (TimeframeID)
    );
    PRINT 'Table SEN.DP_BackfillState created.';
END
GO


-- NOTE: DWH.Dim_Symbol is seeded by 05_seed_symbols.sql.
-- The DP Program V3 runtime instrument source is configuration.py::_SYMBOLS.
