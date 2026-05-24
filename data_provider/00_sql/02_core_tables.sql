/* ============================================================
   02_core_tables.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 2 of 5 — Create all core/dimension/fact tables, seed reference
     data, and sync the master symbol list into the DWH dimension.

     PREREQUISITE: 01_setup_database.sql must have been run first.

   WHAT THIS FILE CREATES (in plain English):

     1. THREE "LOOKUP" TABLES (dimensions):
        These are small reference tables that translate numeric IDs
        into human-readable names. Every candle in the main table stores
        numbers like SymbolID=33 and TimeframeID=7. These lookup tables
        tell you that 33 = EURUSD and 7 = H1.

        a) Dim_Symbol    — "Which instrument?" (37 rows: EURUSD, Gold, etc.)
        b) Dim_Timeframe — "Which timeframe?"  (15 rows: M5, H1, D1, etc.)
        c) Dim_Date      — "What day is it?"   (10,227 rows: every day 2008–2035)

     2. THE MAIN DATA TABLE (fact):
        d) Fact_OHLCV    — One row per candle. Millions of rows.
                           This is where ALL price data lives.

     3. THE MASTER SYMBOL LIST:
        e) dbo.Symbol    — The editable master list of 37 instruments.
                           Changes here automatically sync to Dim_Symbol.

   SAFE TO RE-RUN:
     Tables guarded by IF OBJECT_ID IS NULL (won't recreate if exists).
     Symbol data uses MERGE (inserts new rows, updates changed rows).

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql     ← YOU ARE HERE
     03_staging_tables.sql
     04_business_objects.sql
     05_verify.sql
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
        SymbolID    INT          NOT NULL,   -- stable numeric PK; matches Id in dbo.Symbol
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
--     The former computed timeframes remain available as fallback/rebuild
--     targets when ENABLE_COMPUTED_TIMEFRAMES=1:
--       M10 (2×M5)  M20 (4×M5)  M90 (3×M30)  H6 (2×H3)  H8 (2×H4)
--
--     Minutes column drives the bucket formula in usp_AggregateFromStaging.
--     SourceTable is the short staging table name (without schema prefix).
-- -------------------------------------------------------
IF OBJECT_ID('DWH.Dim_Timeframe', 'U') IS NULL
BEGIN
    CREATE TABLE DWH.Dim_Timeframe (
        TimeframeID  TINYINT      NOT NULL,  -- 1-byte compact PK (max 15 rows — TINYINT is sufficient)
        Code         VARCHAR(5)   NOT NULL,  -- short code used in procedure calls and Python config, e.g. 'M5', 'H4', 'W'
        Minutes      INT          NOT NULL,  -- candle duration in minutes; used in bucket arithmetic by usp_AggregateFromStaging
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
        ( 2, 'M10', 10,    'TF_M10', '10 min - direct TradingView/Capital.com; fallback M5 aggregate'),
        ( 3, 'M15', 15,    'TF_M15', '15 min - direct TradingView/Capital.com'),
        ( 4, 'M20', 20,    'TF_M20', '20 min - direct TradingView/Capital.com; fallback M5 aggregate'),
        ( 5, 'M30', 30,    'TF_M30', '30 min - direct TradingView/Capital.com'),
        ( 6, 'M45', 45,    'TF_M45', '45 min - direct TradingView/Capital.com'),
        ( 7, 'H1',  60,    'TF_H1',  '1 hour - direct TradingView/Capital.com'),
        ( 8, 'M90', 90,    'TF_M90', '90 min - direct TradingView/Capital.com; fallback M30 aggregate'),
        ( 9, 'H2',  120,   'TF_H2',  '2 hour - direct TradingView/Capital.com'),
        (10, 'H3',  180,   'TF_H3',  '3 hour - direct TradingView/Capital.com'),
        (11, 'H4',  240,   'TF_H4',  '4 hour - direct TradingView/Capital.com'),
        (12, 'H6',  360,   'TF_H6',  '6 hour - direct TradingView/Capital.com; fallback H3 aggregate'),
        (13, 'H8',  480,   'TF_H8',  '8 hour - direct TradingView/Capital.com; fallback H4 aggregate'),
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
    ( 2, 'M10', 10,    'TF_M10', '10 min - direct TradingView/Capital.com; fallback M5 aggregate'),
    ( 3, 'M15', 15,    'TF_M15', '15 min - direct TradingView/Capital.com'),
    ( 4, 'M20', 20,    'TF_M20', '20 min - direct TradingView/Capital.com; fallback M5 aggregate'),
    ( 5, 'M30', 30,    'TF_M30', '30 min - direct TradingView/Capital.com'),
    ( 6, 'M45', 45,    'TF_M45', '45 min - direct TradingView/Capital.com'),
    ( 7, 'H1',  60,    'TF_H1',  '1 hour - direct TradingView/Capital.com'),
    ( 8, 'M90', 90,    'TF_M90', '90 min - direct TradingView/Capital.com; fallback M30 aggregate'),
    ( 9, 'H2',  120,   'TF_H2',  '2 hour - direct TradingView/Capital.com'),
    (10, 'H3',  180,   'TF_H3',  '3 hour - direct TradingView/Capital.com'),
    (11, 'H4',  240,   'TF_H4',  '4 hour - direct TradingView/Capital.com'),
    (12, 'H6',  360,   'TF_H6',  '6 hour - direct TradingView/Capital.com; fallback H3 aggregate'),
    (13, 'H8',  480,   'TF_H8',  '8 hour - direct TradingView/Capital.com; fallback H4 aggregate'),
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
   ALL 15 timeframes converge here. They are pulled directly by default; fallback
   rebuild jobs may also write derived candles when explicitly enabled.

   COLUMN NOTES:
     FactID      — surrogate BIGINT PK; never reused or exposed externally
     SymbolID    — FK to DWH.Dim_Symbol
     TimeframeID — FK to DWH.Dim_Timeframe (1-byte TINYINT — there are only 15 TFs)
     DateKey     — INT YYYYMMDD, FK to DWH.Dim_Date; enables fast calendar-based filtering
     BarTime     — candle open time in UTC; DATETIME2(0) = second precision
     [Open] / High / Low / [Close] — DECIMAL(18,8) for pip-level accuracy
     Volume      — nullable (FOREX spot has no centralised volume)
     TickCount   — for direct pulls: always 1 (one bar = one raw candle from TradingView/Capital.com)
                   for aggregated candles: count of source bars merged into this candle
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
        TickCount   INT           NULL,                     -- 1 for direct TF pulls; N for aggregated candles (N = number of source bars merged)
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


/* ============================================================
   BLOCK 6: MASTER SYMBOL TABLE  —  dbo.Symbol

   Single source of truth for all tradable instruments.
   Lives in dbo (default schema) because it is a shared reference
   table used by both the pipeline and the DWH layer.

   37 symbols: 9 Indices + 26 FOREX pairs + 1 Metal + 1 Crypto

   ID GAPS ARE INTENTIONAL:
     IDs come from a larger master list (e.g. Id 29–31 are unused FOREX pairs,
     Id 38–40 are other instruments not traded in this strategy).
     Gaps must be preserved — IDs are stored in Fact_OHLCV and cannot change.

   MERGE (not INSERT) is used so this block is safely re-runnable:
     - WHEN NOT MATCHED: insert any symbol not yet in the table
     - WHEN MATCHED AND EXCEPT detects a change: update the differing row
     The EXCEPT pattern (instead of column <> column) is NULL-safe:
     old <> operator would silently skip updates when RefName is NULL.
   ============================================================ */

IF OBJECT_ID('dbo.Symbol', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Symbol (
        Id      INT          NOT NULL,           -- stable numeric ID; used as SymbolID throughout the DWH
        Symbol  NVARCHAR(20) NOT NULL,           -- runtime TradingView/Capital.com ticker used in Python data pull calls
        RefName NVARCHAR(20) NULL,               -- broker or common alternative name (nullable — not all instruments have one)
        Type    NVARCHAR(20) NOT NULL,           -- instrument category: FOREX | Indice | Metal | Crypto
        CONSTRAINT PK_Symbol PRIMARY KEY CLUSTERED (Id)
    );
    PRINT 'Table dbo.Symbol created.';
END
GO

-- Upsert all 37 symbols. Safe to re-run: new symbols are inserted, changed ones are updated.
MERGE dbo.Symbol AS target
USING (VALUES
    -- ── Indices ──────────────────────────────────────────────────────────────
    -- Symbol = runtime TradingView/Capital.com ticker used by config.py
    -- RefName = legacy/common ticker kept for reference / display
    ( 2, 'FR40',  'CAC40',  'Indice'),   -- CAC 40 — Capital.com CFD ticker
    ( 3, 'DE40',  'DAX',    'Indice'),   -- DAX 40 — Capital.com CFD ticker
    ( 4, 'HK50',  'HSI',    'Indice'),   -- Hang Seng 50 — Capital.com CFD ticker
    ( 5, 'J225',  'NI225',  'Indice'),   -- Nikkei 225 — Capital.com CFD ticker
    ( 6, 'SP35',  'IBEX35', 'Indice'),   -- IBEX 35 — Capital.com CFD ticker
    ( 7, 'UK100', 'UKX',    'Indice'),   -- FTSE 100 — Capital.com CFD ticker
    ( 8, 'US500', 'SPX',    'Indice'),   -- S&P 500 — Capital.com CFD ticker
    ( 9, 'US100', 'NDX',    'Indice'),   -- Nasdaq 100 — Capital.com CFD ticker
    (10, 'US30',  'DJI',    'Indice'),   -- Dow Jones 30 — Capital.com CFD ticker
    -- ── FOREX pairs ──────────────────────────────────────────────────────────
    -- All pulled from Capital.com via TradingView WebSocket
    (11, 'AUDCAD', 'AUDCAD', 'FOREX'),
    (12, 'AUDJPY', 'AUDJPY', 'FOREX'),
    (13, 'AUDNZD', 'AUDNZD', 'FOREX'),
    (14, 'AUDCHF', 'AUDCHF', 'FOREX'),
    (15, 'AUDUSD', 'AUDUSD', 'FOREX'),
    (16, 'GBPAUD', 'GBPAUD', 'FOREX'),
    (17, 'GBPCAD', 'GBPCAD', 'FOREX'),
    (18, 'GBPJPY', 'GBPJPY', 'FOREX'),
    (19, 'GBPNZD', 'GBPNZD', 'FOREX'),
    (20, 'GBPCHF', 'GBPCHF', 'FOREX'),
    (21, 'GBPUSD', 'GBPUSD', 'FOREX'),
    (22, 'CADJPY', 'CADJPY', 'FOREX'),
    (23, 'CADCHF', 'CADCHF', 'FOREX'),
    (24, 'EURAUD', 'EURAUD', 'FOREX'),
    (25, 'EURGBP', 'EURGBP', 'FOREX'),
    (26, 'EURCAD', 'EURCAD', 'FOREX'),
    (27, 'EURJPY', 'EURJPY', 'FOREX'),
    (28, 'EURNZD', 'EURNZD', 'FOREX'),
    -- IDs 29–31 intentionally skipped (other instruments not in this strategy)
    (32, 'EURCHF', 'EURCHF', 'FOREX'),
    (33, 'EURUSD', 'EURUSD', 'FOREX'),
    (34, 'NZDCAD', 'NZDCAD', 'FOREX'),
    (35, 'NZDJPY', 'NZDJPY', 'FOREX'),
    (36, 'NZDUSD', 'NZDUSD', 'FOREX'),
    (37, 'USDCAD', 'USDCAD', 'FOREX'),
    -- IDs 38–40 intentionally skipped
    (41, 'USDJPY', 'USDJPY', 'FOREX'),
    -- IDs 42–47 intentionally skipped
    (48, 'USDCHF', 'USDCHF', 'FOREX'),
    -- ── Metal & Crypto ───────────────────────────────────────────────────────
    (56, 'GOLD',   'XAU',    'Metal'),   -- Gold CFD — Capital.com ticker
    (81, 'BTCUSD', 'BTCUSD', 'Crypto')   -- Bitcoin / USD — Capital.com ticker
) AS src (Id, Symbol, RefName, Type)
ON target.Id = src.Id   -- match on stable numeric ID (never changes)
-- Insert row if it doesn't exist yet
WHEN NOT MATCHED THEN
    INSERT (Id, Symbol, RefName, Type)
    VALUES (src.Id, src.Symbol, src.RefName, src.Type)
-- Update row only if any column value has changed.
-- EXCEPT is NULL-safe: it treats NULL = NULL as equal (no spurious updates)
-- and correctly detects NULL → value changes that <> would silently miss.
WHEN MATCHED AND EXISTS (
    SELECT target.Symbol, target.RefName, target.Type
    EXCEPT
    SELECT src.Symbol,   src.RefName,   src.Type
) THEN
    UPDATE SET Symbol=src.Symbol, RefName=src.RefName, Type=src.Type;

PRINT '37 symbols merged into dbo.Symbol.';
GO


/* ============================================================
   BLOCK 7: SYNC dbo.Symbol → DWH.Dim_Symbol

   Propagates the master symbol list into the DWH dimension table.
   This runs after Block 6 so Dim_Symbol always reflects dbo.Symbol.

   WHY TWO TABLES (dbo.Symbol AND DWH.Dim_Symbol)?
     dbo.Symbol is the operational master — edited by the pipeline team.
     DWH.Dim_Symbol is the warehouse copy — adds DWH-specific columns
     (IsActive, AssetType label, CreatedAt audit column) and enforces
     referential integrity for Fact_OHLCV foreign keys.
     Keeping them separate means changes to the master don't break
     the warehouse schema, and the DWH layer can be rebuilt independently.

   Same NULL-safe EXCEPT pattern as Block 6.
   ============================================================ */

MERGE DWH.Dim_Symbol AS target
USING (SELECT Id, Symbol, RefName, Type FROM dbo.Symbol) AS src  -- read from master table
ON target.SymbolID = src.Id   -- join on numeric ID
WHEN NOT MATCHED THEN
    INSERT (SymbolID, Symbol, RefName, AssetType)   -- note: Type → AssetType column mapping
    VALUES (src.Id, src.Symbol, src.RefName, src.Type)
-- NULL-safe change detection: EXCEPT handles NULL RefName correctly
-- (old <> operator would never fire when RefName IS NULL on either side)
WHEN MATCHED AND EXISTS (
    SELECT target.Symbol, target.RefName, target.AssetType
    EXCEPT
    SELECT src.Symbol,   src.RefName,   src.Type
) THEN
    UPDATE SET Symbol=src.Symbol, RefName=src.RefName, AssetType=src.Type;

PRINT 'DWH.Dim_Symbol synced from dbo.Symbol.';
GO
