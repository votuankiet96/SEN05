/* ============================================================
   04_business_objects.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 4 of 6 — Create the runtime ETL procedure.
     This is the SQL object that the backend engine calls at runtime.

     PREREQUISITE: 01_setup_database.sql through 03_staging_tables.sql
     must have been run first.

   CONTAINS:
     Block 9A — DWH.usp_LoadDirect (ETL: direct copy from SEN staging → Fact_OHLCV)

   ARCHITECTURE:
     TradingView/Capital.com WebSocket
         │  core_engine live/historical loaders
         ▼
     SEN.TF_*  (15 staging tables)
         │  usp_LoadDirect  (direct copy for all 15 TFs)
         ▼
     DWH.Fact_OHLCV
         ▼
     Trading Engine / Strategy Code

   SAFE TO RE-RUN:
     CREATE OR ALTER is used for all views and procedures.

   CONTRACT VERSION:
     This procedure carries an extended property `DPContractVersion` that
     the Python side reads at startup (core_engine.warehouse.connection.
     verify_database_contract) and refuses to run if it does not match
     EXPECTED_CONTRACT_VERSION. Bump the version below AND the Python
     constant together whenever the return shape or call signature of
     usp_LoadDirect changes. History:
       v1 - INSERT-only, no result set (pre-2026-07 deployments; NEVER
            re-introduce this shape - it silently breaks the Python ETL
            caller, which expects a 3-column row-count result set).
       v2 - UPDATE (incl. Volume) + INSERT, explicit transaction with
            XACT_ABORT, UPDLOCK/HOLDLOCK on the idempotency check, returns
            (UpdatedRows, InsertedRows, AffectedRows).

     Existing production databases were found running a stale v1-shaped
     procedure (INSERT-only, no result set) that predates this file. This
     script alone does NOT fix an already-deployed v1 procedure - it must
     be re-run against that database through a controlled deploy. See
     scripts/sql/08_migration_usp_loaddirect_v2.sql for the standalone,
     explicitly-controlled migration meant for that purpose.

   RUN ORDER:
     01_setup_database.sql
     02_core_tables.sql
     03_staging_tables.sql
     04_business_objects.sql     ← this file
     05_active_task.sql
     06_seed_symbols.sql
     07_verify.sql
   ============================================================ */

USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 9A: ETL PROCEDURE — DWH.usp_LoadDirect

   PURPOSE:
     Moves completed (IsProcessed=1) raw rows from a SEN staging table
     directly into DWH.Fact_OHLCV without any transformation.
     Used for all 15 timeframes pulled directly from TradingView/Capital.com.

   CALL PATTERN (from Python pipeline):
     EXEC DWH.usp_LoadDirect
         @SymbolID     = 11,
         @TFCode       = 'M5',
         @StagingTable = 'SEN.TF_M5',
         @FromTime     = '2024-01-01'   -- optional; defaults to 2008-01-01

   RETURNS:
     Exactly one result set, one row: (UpdatedRows, InsertedRows,
     AffectedRows). The Python caller (core_engine.warehouse.writer.
     run_etl_direct) always does cursor.fetchone() on this - a procedure
     shape that does not return this result set breaks every call.

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

   WHY UPDLOCK/HOLDLOCK ON THE NOT EXISTS CHECK?
     Live and historical are supposed to be serialized against the same
     (SymbolID, TFCode) pair by the Python-side warehouse-write lock, but
     that lock is advisory (SEN.ActiveTask), not enforced by SQL Server.
     Under READ COMMITTED (the default), two concurrent calls could both
     pass the NOT EXISTS check before either commits, and the second
     INSERT would then fail on the UQ_Fact_OHLCV unique-key violation.
     UPDLOCK/HOLDLOCK forces the second caller to wait for the first
     transaction's key range lock to release, turning that race into a
     safe INSERT of zero *new* rows for the caller that arrives second.

   WHY XACT_ABORT + explicit transaction?
     Without XACT_ABORT ON, a mid-statement error inside the dynamic SQL
     (e.g. a unique-key violation despite the UPDLOCK/HOLDLOCK guard) does
     not necessarily roll back a UPDATE that already committed as its own
     implicit transaction, leaving Fact_OHLCV partially updated with no
     corresponding INSERT. Wrapping both statements in one explicit
     transaction with XACT_ABORT ON makes this procedure all-or-nothing.

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
    SET XACT_ABORT ON;

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
    DECLARE @UpdatedRows INT = 0;
    DECLARE @InsertedRows INT = 0;

    DECLARE @sql NVARCHAR(MAX) = N'
        UPDATE f
        SET f.[Open]  = s.[Open],
            f.High    = s.High,
            f.Low     = s.Low,
            f.[Close] = s.[Close],
            f.Volume  = s.Volume
        FROM DWH.Fact_OHLCV AS f
        INNER JOIN ' + @StagingTable + N' AS s
            ON s.SymbolID = f.SymbolID
           AND s.BarTime = f.BarTime
        WHERE f.SymbolID = @SymbolID
          AND f.TimeframeID = @TimeframeID
          AND s.BarTime >= @FromTime
          AND s.IsProcessed = 1
          AND (
              f.[Open] <> s.[Open]
              OR f.High <> s.High
              OR f.Low <> s.Low
              OR f.[Close] <> s.[Close]
              OR ISNULL(f.Volume, -1) <> ISNULL(s.Volume, -1)
          );

        SET @UpdatedRows = @@ROWCOUNT;

        INSERT INTO DWH.Fact_OHLCV
            (SymbolID, TimeframeID, DateKey, BarTime,
             [Open], High, Low, [Close], Volume, TickCount)
        SELECT @SymbolID, @TimeframeID,
            -- DateKey: convert BarTime to DATE, format as YYYYMMDD string, cast to INT
            -- e.g. 2024-03-15 → ''20240315'' → 20240315
            CONVERT(INT, CONVERT(VARCHAR, CAST(BarTime AS DATE), 112)),
            BarTime, [Open], High, Low, [Close], Volume,
            1   -- TickCount = 1 for all direct pulls (one staging row = one raw candle)
        FROM ' + @StagingTable + N' AS src
        WHERE src.SymbolID    = @SymbolID    -- only load rows for the requested symbol
          AND src.BarTime    >= @FromTime    -- skip bars before the requested start date
          AND src.IsProcessed = 1            -- only load bars flagged as complete by the Python pipeline
          AND NOT EXISTS (
              -- Idempotency guard: skip the row if this candle already exists in the fact table.
              -- Checks only the unique key (SymbolID, TimeframeID, BarTime) — not all columns —
              -- so a re-submission with a slightly different Volume does not cause a duplicate key error.
              -- UPDLOCK/HOLDLOCK: serialize concurrent ETL calls for the same key range so two
              -- callers racing on the same (SymbolID, TimeframeID) cannot both pass this check
              -- and collide on the UQ_Fact_OHLCV unique-key constraint.
              SELECT 1 FROM DWH.Fact_OHLCV f WITH (UPDLOCK, HOLDLOCK)
              WHERE f.SymbolID    = @SymbolID
                AND f.TimeframeID = @TimeframeID
                AND f.BarTime     = src.BarTime
          );

        SET @InsertedRows = @@ROWCOUNT;
    ';

    BEGIN TRY
        BEGIN TRANSACTION;

        -- Step 5: Execute with parameterised values (safe from injection for these values).
        EXEC sp_executesql @sql,
            N'@SymbolID INT, @TimeframeID TINYINT, @FromTime DATETIME2,
              @UpdatedRows INT OUTPUT, @InsertedRows INT OUTPUT',
            @SymbolID, @TimeframeID, @FromTime,
            @UpdatedRows OUTPUT, @InsertedRows OUTPUT;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH

    SELECT @UpdatedRows AS UpdatedRows,
           @InsertedRows AS InsertedRows,
           @UpdatedRows + @InsertedRows AS AffectedRows;

    PRINT 'usp_LoadDirect OK: TF=' + @TFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR)
          + ' Updated=' + CAST(@UpdatedRows AS VARCHAR)
          + ' Inserted=' + CAST(@InsertedRows AS VARCHAR);
END
GO

-- Contract version marker: core_engine.warehouse.connection.verify_database_contract
-- reads this at startup and refuses to run against a mismatched/missing version.
IF EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE major_id = OBJECT_ID('DWH.usp_LoadDirect')
      AND minor_id = 0
      AND class = 1
      AND name = 'DPContractVersion'
)
    EXEC sp_updateextendedproperty @name = N'DPContractVersion', @value = N'2',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
ELSE
    EXEC sp_addextendedproperty @name = N'DPContractVersion', @value = N'2',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
GO

PRINT 'Procedure DWH.usp_LoadDirect created (contract v2).';
GO
