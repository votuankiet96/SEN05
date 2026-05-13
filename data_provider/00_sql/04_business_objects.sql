/* ============================================================
   04_business_objects.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 4 of 5 — Create all views, mart procedures, and ETL procedures.
     These are the objects that the trading engine and Python pipeline call at runtime.

     PREREQUISITE: 01_setup_database.sql through 03_staging_tables.sql
     must have been run first.

   CONTAINS:
     Block 8A — MART.v_OHLCV               (human-readable joined view)
     Block 8B — MART.usp_GetLatestCandles   (returns latest N candles for a symbol/TF)
     Block 9A — DWH.usp_LoadDirect         (ETL: direct copy from SEN staging → Fact_OHLCV)
     Block 9B — DWH.usp_AggregateFromStaging (ETL: bucket + aggregate → Fact_OHLCV)

   ARCHITECTURE:
     TradingView (tvDatafeed)
         │  Python 01_data_pipeline.py
         ▼
     SEN.TF_*  (staging tables)
         │  usp_LoadDirect          (10 native TFs: direct copy)
         │  usp_AggregateFromStaging (5 computed TFs: bucket + aggregate)
         ▼
     DWH.Fact_OHLCV
         │  MART.v_OHLCV            (human-readable joined view)
         │  MART.usp_GetLatestCandles (latest N candles for a symbol/TF)
         ▼
     Trading Engine / Strategy Code

   SAFE TO RE-RUN:
     CREATE OR ALTER is used for all views and procedures.

   IMPORTANT NOTE:
     The final `CREATE OR ALTER PROCEDURE DWH.usp_AggregateFromStaging`
     later in this file is the safer patched version.
     In SQL Server the last definition wins, so that is the effective
     runtime behavior.

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql
     03_staging_tables.sql
     04_business_objects.sql     ← this file
     05_verify.sql
   ============================================================ */

USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 8: DATA MART — Views and Stored Procedures

   The trading engine and strategy code read EXCLUSIVELY from the MART schema.
   They never touch SEN or DWH tables directly.
   This abstraction lets us restructure the warehouse without breaking consumers.
   ============================================================ */

-- -------------------------------------------------------
-- 8A. MART.v_OHLCV
--     Human-readable OHLCV view. No aggregation — one row = one candle.
--     Joins all three dimension tables onto the fact table, replacing
--     numeric IDs with human-readable strings (Symbol name, Timeframe code, etc.)
--
--     Usage:
--       SELECT * FROM MART.v_OHLCV
--       WHERE Symbol = 'EURUSD' AND Timeframe = 'H1'
--       ORDER BY BarTime DESC
-- -------------------------------------------------------
-- CREATE OR ALTER: updates the view definition in place without dropping/recreating.
GO
CREATE OR ALTER VIEW MART.v_OHLCV AS
SELECT
    s.Symbol,                   -- instrument ticker, e.g. 'EURUSD'
    s.AssetType,                -- category, e.g. 'FOREX'
    tf.Code      AS Timeframe,  -- timeframe code, e.g. 'M5', 'H1', 'W'
    f.BarTime,                  -- candle open time UTC
    f.[Open]  AS [Open],
    f.High  AS [High],
    f.Low   AS [Low],
    f.[Close] AS [Close],
    f.Volume,
    f.TickCount,                -- 1 for direct TF candles; >1 for aggregated candles
    d.Year,
    d.Quarter,
    d.Month,
    d.MonthName,
    d.Week,
    d.DayOfWeek,
    d.DayName,
    d.IsWeekend,
    f.SymbolID,                 -- numeric key retained for programmatic access if needed
    f.TimeframeID               -- numeric key retained for programmatic access if needed
FROM DWH.Fact_OHLCV    f
JOIN DWH.Dim_Symbol     s  ON s.SymbolID     = f.SymbolID
JOIN DWH.Dim_Timeframe  tf ON tf.TimeframeID = f.TimeframeID
JOIN DWH.Dim_Date       d  ON d.DateKey      = f.DateKey;
GO
PRINT 'View MART.v_OHLCV created.';
GO

-- -------------------------------------------------------
-- 8B. MART.usp_GetLatestCandles
--     Returns the most recent @Rows candles for a given symbol and timeframe.
--     Encapsulates the query so the trading engine doesn't need to know
--     the underlying table structure.
--
--     Usage:
--       EXEC MART.usp_GetLatestCandles @Symbol='EURUSD', @Timeframe='H1', @Rows=200
-- -------------------------------------------------------
CREATE OR ALTER PROCEDURE MART.usp_GetLatestCandles
    @Symbol    NVARCHAR(20),        -- instrument ticker, e.g. 'EURUSD'
    @Timeframe VARCHAR(5),          -- timeframe code, e.g. 'H1'
    @Rows      INT = 500            -- number of candles to return; default 500
AS
BEGIN
    SET NOCOUNT ON;  -- suppress "N rows affected" messages; reduces network overhead

    -- Returns the most recent @Rows candles, newest first (row 1 = latest bar).
    -- Trading strategies typically iterate from row 1 backward in time.
    SELECT TOP (@Rows)   -- TOP with a variable requires parentheses
        f.BarTime,
        f.[Open]  AS [Open],
        f.High  AS [High],
        f.Low   AS [Low],
        f.[Close] AS [Close],
        f.Volume,
        f.TickCount
    FROM DWH.Fact_OHLCV    f
    JOIN DWH.Dim_Symbol     s  ON s.SymbolID     = f.SymbolID
    JOIN DWH.Dim_Timeframe  tf ON tf.TimeframeID = f.TimeframeID
    WHERE s.Symbol  = @Symbol      -- filter by human-readable ticker
      AND tf.Code   = @Timeframe   -- filter by human-readable TF code
    ORDER BY f.BarTime DESC;       -- newest first; covered by IX_Fact_Sym_TF_Time index
END
GO
PRINT 'Procedure MART.usp_GetLatestCandles created.';
GO


/* ============================================================
   BLOCK 9A: ETL PROCEDURE — DWH.usp_LoadDirect

   PURPOSE:
     Moves completed (IsProcessed=1) raw rows from a SEN staging table
     directly into DWH.Fact_OHLCV without any transformation.
     Used for the 10 timeframes that are pulled natively from TradingView.

   CALL PATTERN (from Python pipeline):
     EXEC DWH.usp_LoadDirect
         @SymbolID     = 11,
         @TFCode       = 'M5',
         @StagingTable = 'SEN.TF_M5',
         @FromTime     = '2024-01-01'   -- optional; defaults to 2008-01-01

   WHY DYNAMIC SQL?
     The staging table name varies per call (@StagingTable parameter).
     SQL Server requires the table name to be known at parse time for static SQL,
     so we build the query string at runtime and execute via sp_executesql.

   WHY NOT EXISTS instead of EXCEPT for duplicate detection?
     EXCEPT compares ALL columns including Volume and TickCount.
     If TradingView retransmits the same bar with a slightly different volume,
     EXCEPT treats it as a new row → duplicate key error on UQ_Fact_OHLCV.
     NOT EXISTS checks only the unique key (SymbolID, TimeframeID, BarTime),
     which is the correct idempotency guard for re-runs and partial reloads.

   SECURITY — WHITELIST VALIDATION:
     @StagingTable is concatenated into the SQL string, which is a SQL injection
     risk if the parameter is ever passed from untrusted input.
     We validate against the known 15 staging table names before building the SQL.
   ============================================================ */

GO
CREATE OR ALTER PROCEDURE DWH.usp_LoadDirect
    @SymbolID     INT,              -- target symbol; must exist in DWH.Dim_Symbol
    @TFCode       VARCHAR(5),       -- timeframe code matching DWH.Dim_Timeframe.Code, e.g. 'M5'
    @StagingTable VARCHAR(50),      -- fully qualified staging table name, e.g. 'SEN.TF_M5'
    @FromTime     DATETIME2 = NULL  -- optional lower bound; rows with BarTime < @FromTime are skipped
AS
BEGIN
    SET NOCOUNT ON;

    -- Step 1: Resolve TimeframeID from the human-readable code.
    -- If the code doesn't exist in Dim_Timeframe, fail immediately with a clear error.
    DECLARE @TimeframeID TINYINT;
    SELECT @TimeframeID = TimeframeID
    FROM DWH.Dim_Timeframe WHERE Code = @TFCode;

    IF @TimeframeID IS NULL
    BEGIN
        -- RAISERROR severity 16 = user error (not a system fault); state 1 = arbitrary
        RAISERROR('usp_LoadDirect: TFCode [%s] not found in Dim_Timeframe.', 16, 1, @TFCode);
        RETURN;
    END

    -- Step 2: Default @FromTime to the earliest possible data date if not supplied.
    IF @FromTime IS NULL SET @FromTime = '2008-01-01';

    -- Step 3: Whitelist validation — only allow the 15 known staging table names.
    -- This prevents SQL injection if @StagingTable were ever passed from untrusted input,
    -- since the value is concatenated directly into the dynamic SQL string below.
    IF @StagingTable NOT IN (
        'SEN.TF_M5', 'SEN.TF_M10','SEN.TF_M15','SEN.TF_M20','SEN.TF_M30',
        'SEN.TF_M45','SEN.TF_H1', 'SEN.TF_M90','SEN.TF_H2', 'SEN.TF_H3',
        'SEN.TF_H4', 'SEN.TF_H6', 'SEN.TF_H8', 'SEN.TF_D1', 'SEN.TF_W'
    )
    BEGIN
        RAISERROR('usp_LoadDirect: Invalid staging table [%s].', 16, 1, @StagingTable);
        RETURN;
    END

    -- Step 4: Build dynamic SQL.
    -- The staging table name cannot be parameterised in sp_executesql (only values can),
    -- so we concatenate it into the SQL string after the whitelist check above.
    -- All other variable values (@SymbolID, @TimeframeID, @FromTime) are passed as
    -- proper parameters to sp_executesql to avoid any injection risk from those values.
    DECLARE @sql NVARCHAR(MAX) = N'
        INSERT INTO DWH.Fact_OHLCV
            (SymbolID, TimeframeID, DateKey, BarTime,
             [Open], High, Low, [Close], Volume, TickCount)
        SELECT @SymbolID, @TimeframeID,
            -- DateKey: convert BarTime to DATE, format as YYYYMMDD string, cast to INT
            -- e.g. 2024-03-15 → ''20240315'' → 20240315
            CONVERT(INT, CONVERT(VARCHAR, CAST(BarTime AS DATE), 112)),
            BarTime, [Open], High, Low, [Close], Volume,
            1   -- TickCount = 1 for all direct pulls (one staging row = one raw candle)
        FROM ' + @StagingTable + N'
        WHERE SymbolID    = @SymbolID    -- only load rows for the requested symbol
          AND BarTime    >= @FromTime    -- skip bars before the requested start date
          AND IsProcessed = 1            -- only load bars flagged as complete by the Python pipeline
          AND NOT EXISTS (
              -- Idempotency guard: skip the row if this candle already exists in the fact table.
              -- Checks only the unique key (SymbolID, TimeframeID, BarTime) — not all columns —
              -- so a re-submission with a slightly different Volume does not cause a duplicate key error.
              SELECT 1 FROM DWH.Fact_OHLCV f
              WHERE f.SymbolID    = @SymbolID
                AND f.TimeframeID = @TimeframeID
                AND f.BarTime     = ' + @StagingTable + N'.BarTime
          );
    ';

    -- Step 5: Execute with parameterised values (safe from injection for these values).
    EXEC sp_executesql @sql,
        N'@SymbolID INT, @TimeframeID TINYINT, @FromTime DATETIME2',
        @SymbolID, @TimeframeID, @FromTime;

    PRINT 'usp_LoadDirect OK: TF=' + @TFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR);
END
GO
PRINT 'Procedure DWH.usp_LoadDirect created.';
GO


/* ============================================================
   BLOCK 9B: ETL PROCEDURE — DWH.usp_AggregateFromStaging

   PURPOSE:
     Computes larger-timeframe candles by bucketing source staging rows,
     then inserts the aggregated candles into DWH.Fact_OHLCV.
     Used for the 5 timeframes not natively available in tvDatafeed:
       M10  ← SEN.TF_M5   (group every 2 consecutive M5 bars)
       M20  ← SEN.TF_M5   (group every 4 consecutive M5 bars)
       M90  ← SEN.TF_M30  (group every 3 consecutive M30 bars)
       H6   ← SEN.TF_H3   (group every 2 consecutive H3 bars)
       H8   ← SEN.TF_H4   (group every 2 consecutive H4 bars)

     NOTE: This procedure inserts only into DWH.Fact_OHLCV.
     The 5 computed SEN.TF_* staging tables (TF_M10, TF_M20, TF_M90, TF_H6, TF_H8)
     are NOT populated by this procedure — they remain available for manual inspection
     or future use if a direct data source becomes available.

   CALL PATTERN (from Python pipeline):
     EXEC DWH.usp_AggregateFromStaging
         @SymbolID     = 11,
         @SourceTable  = 'SEN.TF_M5',
         @TargetTFCode = 'M10',
         @FromTime     = '2024-01-01'

   BUCKET FORMULA:
     BucketTime = 2000-01-01 + FLOOR(minutesSinceEpoch / targetMinutes) * targetMinutes
     This aligns all bars to fixed grid boundaries (e.g. every 10 minutes from midnight),
     ensuring consistent candle open times regardless of which bars happen to arrive first.
     The epoch anchor '2000-01-01' is arbitrary but consistent — it just needs to be
     a fixed reference point earlier than all actual data.

   OHLCV AGGREGATION LOGIC (inside the CTE chain):
     CTE Bucketed    — assigns each source bar a BucketTime (the aligned candle start time)
     CTE WithOHLC    — uses window functions to find the correct Open and Close per bucket:
                         FIRST_VALUE([Open]) ORDER BY BarTime ASC  → first bar's open = candle open
                         LAST_VALUE([Close])  ORDER BY BarTime ASC  → last bar's close = candle close
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                         ensures the window covers ALL rows in the partition (not just preceding rows)
     CTE Aggregated  — groups by BucketTime to produce final OHLCV:
                         Open  = MIN(CandleOpen)  — all rows in the bucket share the same CandleOpen value
                                                    (set by FIRST_VALUE window), so MIN = that value
                         High  = MAX(High)   — highest high across all source bars in the bucket
                         Low   = MIN(Low)    — lowest low across all source bars in the bucket
                         Close = MIN(CandleClose) — all rows share the same CandleClose value
                                                    (set by LAST_VALUE window), so MIN = that value
                         Vol   = SUM(Volume)      — total volume across all source bars
                         TickCount = COUNT(*)     — number of source bars merged (stored in Fact_OHLCV.TickCount)

   SECURITY — same whitelist pattern as usp_LoadDirect.
   ============================================================ */

GO
CREATE OR ALTER PROCEDURE DWH.usp_AggregateFromStaging
    @SymbolID        INT,              -- target symbol; must exist in DWH.Dim_Symbol
    @SourceTable     VARCHAR(50),      -- source staging table, e.g. 'SEN.TF_M5'
    @TargetTFCode    VARCHAR(5),       -- target timeframe code, e.g. 'M10'
    @FromTime        DATETIME2 = NULL  -- optional lower bound; defaults to 2008-01-01
AS
BEGIN
    SET NOCOUNT ON;

    -- Step 1: Resolve target TimeframeID and its duration in minutes.
    -- @TFMinutes drives the bucket formula — how many minutes wide each computed candle is.
    DECLARE @TFMinutes   INT;
    DECLARE @TimeframeID TINYINT;
    SELECT @TFMinutes = Minutes, @TimeframeID = TimeframeID
    FROM DWH.Dim_Timeframe WHERE Code = @TargetTFCode;

    IF @TimeframeID IS NULL
    BEGIN
        RAISERROR('usp_AggregateFromStaging: TFCode [%s] not found.', 16, 1, @TargetTFCode);
        RETURN;
    END

    -- Step 2: Default @FromTime if not supplied.
    IF @FromTime IS NULL SET @FromTime = '2008-01-01';

    -- Step 3: Whitelist validation — only the 4 tables that act as aggregation sources.
    -- (TF_M5 → M10/M20,  TF_M30 → M90,  TF_H3 → H6,  TF_H4 → H8)
    IF @SourceTable NOT IN ('SEN.TF_M5','SEN.TF_M30','SEN.TF_H3','SEN.TF_H4')
    BEGIN
        RAISERROR('usp_AggregateFromStaging: Invalid source table [%s].', 16, 1, @SourceTable);
        RETURN;
    END

    -- Step 4: Build and execute dynamic SQL.
    -- Three-CTE pipeline: Bucketed → WithOHLC → Aggregated → INSERT
    DECLARE @sql NVARCHAR(MAX) = N'
    ;WITH Bucketed AS (
        -- Assign each source bar to a fixed-width time bucket.
        -- DATEDIFF(MINUTE, anchor, BarTime) gives total minutes since anchor.
        -- Integer division (/ @TFMinutes) floors to the nearest bucket boundary.
        -- DATEADD adds that many minutes back to the anchor to get the bucket start time.
        SELECT BarTime, [Open], High, Low, [Close], Volume,
            DATEADD(MINUTE,
                (DATEDIFF(MINUTE, ''2000-01-01'', BarTime) / @TFMinutes) * @TFMinutes,
                ''2000-01-01'') AS BucketTime   -- aligned candle open time for the computed TF
        FROM ' + @SourceTable + N'
        WHERE SymbolID = @SymbolID AND BarTime >= @FromTime AND IsProcessed = 1
    ),
    WithOHLC AS (
        -- Use window functions to tag every row with the correct Open and Close for its bucket.
        -- FIRST_VALUE over all rows in the partition (ORDER BY BarTime ASC) = [Open] of the earliest bar.
        -- LAST_VALUE  over all rows in the partition (ORDER BY BarTime ASC) = [Close] of the latest bar.
        -- ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING is required for LAST_VALUE to
        -- correctly reach the final row (without it, the default frame stops at the current row).
        SELECT BucketTime, BarTime, High, Low, Volume,
            FIRST_VALUE([Open]) OVER (
                PARTITION BY BucketTime ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleOpen,    -- all rows in this bucket will have the same CandleOpen value
            LAST_VALUE([Close]) OVER (
                PARTITION BY BucketTime ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleClose    -- all rows in this bucket will have the same CandleClose value
        FROM Bucketed
    ),
    Aggregated AS (
        -- Collapse each bucket into a single OHLCV candle row.
        -- Because CandleOpen and CandleClose are identical across all rows in a BucketTime
        -- partition (set by the window functions above), MIN() simply returns that shared value.
        SELECT BucketTime,
            MIN(BarTime)     AS FirstBarTime, -- actual timestamp of the first source bar in bucket
            MIN(CandleOpen)  AS [Open],   -- first bar''s open (all rows equal → MIN = that value)
            MAX(High)   AS High,   -- highest high across all source bars in the bucket
            MIN(Low)    AS Low,    -- lowest low across all source bars in the bucket
            MIN(CandleClose) AS [Close],  -- last bar''s close (all rows equal → MIN = that value)
            SUM(Volume)      AS Volume,      -- total volume over the bucket period
            COUNT(*)         AS BarCount     -- number of source bars merged (written to TickCount)
        FROM WithOHLC GROUP BY BucketTime
    )
    INSERT INTO DWH.Fact_OHLCV
        (SymbolID, TimeframeID, DateKey, BarTime,
         [Open], High, Low, [Close], Volume, TickCount)
    SELECT @SymbolID, @TimeframeID,
        -- Use FirstBarTime (not BucketTime) so BarTime matches the actual first source bar.
        -- BucketTime is anchored to 2000-01-01 UTC midnight and can be 1-5h early when
        -- source bars are offset (e.g. EURUSD H3 starts 01:00 UTC, XAU H4 starts 01:00 UTC).
        CONVERT(INT, CONVERT(VARCHAR, CAST(FirstBarTime AS DATE), 112)),
        FirstBarTime, [Open], High, Low, [Close], Volume, BarCount
    FROM Aggregated
    WHERE NOT EXISTS (
        -- Idempotency guard: skip candles already in the fact table.
        -- Prevents duplicate key errors on UQ_Fact_OHLCV when proc is re-run.
        SELECT 1 FROM DWH.Fact_OHLCV f
        WHERE f.SymbolID = @SymbolID AND f.TimeframeID = @TimeframeID
          AND f.BarTime = FirstBarTime
    );';

    -- Execute with all variable values as proper parameters (safe from injection).
    EXEC sp_executesql @sql,
        N'@SymbolID INT, @TimeframeID TINYINT, @TFMinutes INT, @FromTime DATETIME2',
        @SymbolID, @TimeframeID, @TFMinutes, @FromTime;

    PRINT 'usp_AggregateFromStaging OK: ' + @SourceTable + ' → ' + @TargetTFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR);
END
GO
PRINT 'Procedure DWH.usp_AggregateFromStaging created.';
GO

/* ---------------------------------------------------------------------------
   PATCH: safer aggregate proc for computed TFs
   - only writes fully formed buckets
   - updates existing rows instead of insert-only behavior
   - keeps DB-level callers aligned with the Python safe path
--------------------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE DWH.usp_AggregateFromStaging
    @SymbolID        INT,
    @SourceTable     VARCHAR(50),
    @TargetTFCode    VARCHAR(5),
    @FromTime        DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @TFMinutes     INT;
    DECLARE @TimeframeID   TINYINT;
    DECLARE @ExpectedCount INT;

    SELECT @TFMinutes = Minutes, @TimeframeID = TimeframeID
    FROM DWH.Dim_Timeframe
    WHERE Code = @TargetTFCode;

    IF @TimeframeID IS NULL
    BEGIN
        RAISERROR('usp_AggregateFromStaging: TFCode [%s] not found.', 16, 1, @TargetTFCode);
        RETURN;
    END

    IF @FromTime IS NULL
        SET @FromTime = '2008-01-01';

    IF @SourceTable NOT IN ('SEN.TF_M5', 'SEN.TF_M30', 'SEN.TF_H3', 'SEN.TF_H4')
    BEGIN
        RAISERROR('usp_AggregateFromStaging: Invalid source table [%s].', 16, 1, @SourceTable);
        RETURN;
    END

    SET @ExpectedCount = CASE @TargetTFCode
        WHEN 'M10' THEN 2
        WHEN 'M20' THEN 4
        WHEN 'M90' THEN 3
        WHEN 'H6'  THEN 2
        WHEN 'H8'  THEN 2
        ELSE NULL
    END;

    IF @ExpectedCount IS NULL
    BEGIN
        RAISERROR('usp_AggregateFromStaging: Unsupported computed TF [%s].', 16, 1, @TargetTFCode);
        RETURN;
    END

    DECLARE @sql NVARCHAR(MAX) = N'
    ;WITH Bucketed AS (
        SELECT
            BarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            DATEADD(
                MINUTE,
                (DATEDIFF(MINUTE, ''2000-01-01'', BarTime) / @TFMinutes) * @TFMinutes,
                ''2000-01-01''
            ) AS BucketTime
        FROM ' + @SourceTable + N'
        WHERE SymbolID = @SymbolID
          AND BarTime >= @FromTime
          AND IsProcessed = 1
    ),
    WithOHLC AS (
        SELECT
            BucketTime,
            BarTime,
            High,
            Low,
            Volume,
            FIRST_VALUE([Open]) OVER (
                PARTITION BY BucketTime
                ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleOpen,
            LAST_VALUE([Close]) OVER (
                PARTITION BY BucketTime
                ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleClose
        FROM Bucketed
    ),
    Aggregated AS (
        SELECT
            BucketTime,
            MIN(BarTime)     AS FirstBarTime,
            MIN(CandleOpen)  AS [Open],
            MAX(High)        AS High,
            MIN(Low)         AS Low,
            MIN(CandleClose) AS [Close],
            SUM(Volume)      AS Volume,
            COUNT(*)         AS BarCount
        FROM WithOHLC
        GROUP BY BucketTime
    ),
    ValidAggregated AS (
        SELECT
            FirstBarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            BarCount
        FROM Aggregated
        WHERE BarCount = @ExpectedCount
    )
    MERGE DWH.Fact_OHLCV AS tgt
    USING (
        SELECT
            @SymbolID AS SymbolID,
            @TimeframeID AS TimeframeID,
            CONVERT(INT, CONVERT(VARCHAR, CAST(FirstBarTime AS DATE), 112)) AS DateKey,
            FirstBarTime AS BarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            BarCount
        FROM ValidAggregated
    ) AS src
      ON tgt.SymbolID = src.SymbolID
     AND tgt.TimeframeID = src.TimeframeID
     AND tgt.BarTime = src.BarTime
    WHEN MATCHED AND (
        ISNULL(tgt.[Open], 0) <> ISNULL(src.[Open], 0)
        OR ISNULL(tgt.High, 0) <> ISNULL(src.High, 0)
        OR ISNULL(tgt.Low, 0) <> ISNULL(src.Low, 0)
        OR ISNULL(tgt.[Close], 0) <> ISNULL(src.[Close], 0)
        OR ISNULL(tgt.Volume, 0) <> ISNULL(src.Volume, 0)
        OR ISNULL(tgt.TickCount, -1) <> ISNULL(src.BarCount, -1)
    ) THEN
        UPDATE SET
            tgt.DateKey   = src.DateKey,
            tgt.[Open]    = src.[Open],
            tgt.High      = src.High,
            tgt.Low       = src.Low,
            tgt.[Close]   = src.[Close],
            tgt.Volume    = src.Volume,
            tgt.TickCount = src.BarCount
    WHEN NOT MATCHED THEN
        INSERT (
            SymbolID, TimeframeID, DateKey, BarTime,
            [Open], High, Low, [Close], Volume, TickCount
        )
        VALUES (
            src.SymbolID, src.TimeframeID, src.DateKey, src.BarTime,
            src.[Open], src.High, src.Low, src.[Close], src.Volume, src.BarCount
        );';

    EXEC sp_executesql
        @sql,
        N'@SymbolID INT, @TimeframeID TINYINT, @TFMinutes INT, @ExpectedCount INT, @FromTime DATETIME2',
        @SymbolID, @TimeframeID, @TFMinutes, @ExpectedCount, @FromTime;

    PRINT 'usp_AggregateFromStaging PATCHED OK: ' + @SourceTable + ' -> ' + @TargetTFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR);
END
GO

PRINT 'Procedure DWH.usp_AggregateFromStaging patched with full-bucket MERGE logic.';
GO
